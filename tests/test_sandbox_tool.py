"""The `sandbox_exec` MCP tool and the result ids `query_database` gives with it (ticket 97), on the
local backend in a temp directory. Called in-process, as test_catalog_cache does."""

import csv
import datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from data_agent.gateway import DuckDBGateway
from data_agent.mcp_server import build_multi_server, build_server
from data_agent.run_config import CONFIG_NAMES, load_config
from data_agent.sandbox import KEEP_BYTES, LocalSandbox

SKILLS = Path("/opt/skills")


def make_db(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE items AS SELECT * FROM (VALUES "
            "(1, 'plain', 2.5, DATE '2026-01-02'), "
            "(2, 'with, comma \"and quote\"', NULL, NULL)) AS t(id, name, price, day)"
        )
    finally:
        con.close()
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def server(tmp_path: Path, workspace: Path) -> MCPServer:
    gateway = DuckDBGateway(make_db(tmp_path / "x.duckdb"))
    return build_server(
        gateway, load_config("full"), workspace, sandbox=LocalSandbox(workspace, SKILLS)
    )


def text_of(result: Any) -> str:
    assert isinstance(result, CallToolResult)
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def call(server: MCPServer, tool: str, arguments: dict[str, Any]) -> str:
    result = await server.call_tool(tool, arguments)
    assert not result.is_error, text_of(result)  # pyright: ignore[reportAttributeAccessIssue]
    return text_of(result)


async def call_error(server: MCPServer, tool: str, arguments: dict[str, Any]) -> str:
    try:
        result = await server.call_tool(tool, arguments)
    except ToolError as exc:
        return str(exc)
    assert result.is_error, text_of(result)  # pyright: ignore[reportAttributeAccessIssue]
    return text_of(result)


async def listing(server: MCPServer) -> dict[str, dict[str, Any]]:
    return {tool.name: tool.model_dump() for tool in await server.list_tools()}


@pytest.mark.parametrize("config", CONFIG_NAMES)
async def test_without_a_sandbox_the_tools_are_unchanged(
    config: str, tmp_path: Path, workspace: Path
) -> None:
    gateway = DuckDBGateway(make_db(tmp_path / "x.duckdb"))
    without = await listing(build_server(gateway, load_config(config), workspace))
    with_sandbox = await listing(
        build_server(
            gateway, load_config(config), workspace, sandbox=LocalSandbox(workspace, SKILLS)
        )
    )
    assert "sandbox_exec" not in without
    assert {name: tool for name, tool in with_sandbox.items() if name != "sandbox_exec"} == without
    assert set(with_sandbox) - set(without) == {"sandbox_exec"}


async def test_without_a_sandbox_query_results_carry_no_id(tmp_path: Path, workspace: Path) -> None:
    server = build_server(DuckDBGateway(make_db(tmp_path / "x.duckdb")), load_config("full"))
    text = await call(server, "query_database", {"sql": "SELECT id FROM items ORDER BY id"})
    assert text == "id\n--\n1\n2\n(2 rows)"


async def test_data_files_write_each_listed_result_as_a_csv(
    server: MCPServer, workspace: Path
) -> None:
    first = await call(server, "query_database", {"sql": "SELECT * FROM items ORDER BY id"})
    second = await call(server, "query_database", {"sql": "SELECT count(*) AS n FROM items"})
    assert "query_1" in first and "query_2" in second
    reply = await call(
        server,
        "sandbox_exec",
        {"command": "ls data; wc -l < data/query_1.csv", "data_files": ["query_1", "query_2"]},
    )
    assert "query_1.csv\nquery_2.csv\n" in reply
    with (workspace / "data" / "query_1.csv").open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows == [
        ["id", "name", "price", "day"],
        ["1", "plain", "2.5", str(datetime.date(2026, 1, 2))],
        ["2", 'with, comma "and quote"', "", ""],
    ]
    with (workspace / "data" / "query_2.csv").open(newline="") as f:
        assert list(csv.reader(f)) == [["n"], ["2"]]


async def test_an_unknown_result_id_is_an_error_and_nothing_runs(
    server: MCPServer, workspace: Path
) -> None:
    await call(server, "query_database", {"sql": "SELECT 1 AS x"})
    error = await call_error(
        server, "sandbox_exec", {"command": "touch ran", "data_files": ["query_1", "query_9"]}
    )
    assert "query_9" in error
    assert not (workspace / "ran").exists()
    assert not (workspace / "data").exists()


async def test_data_files_never_write_through_a_link_the_sandbox_made(
    server: MCPServer, workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    await call(server, "query_database", {"sql": "SELECT 1 AS x"})
    await call(server, "sandbox_exec", {"command": f"ln -s {outside} data"})
    error = await call_error(server, "sandbox_exec", {"command": "true", "data_files": ["query_1"]})
    assert "data" in error
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("make", ["mkfifo data/query_1.csv", "mkdir -p data/query_1.csv/x"])
async def test_data_files_replace_what_the_sandbox_left_at_the_path(
    server: MCPServer, workspace: Path, make: str
) -> None:
    """Codex review, round 1: opening a FIFO there blocked the server; the file is never opened,
    only replaced by a new one (a directory there is an error)."""
    await call(server, "query_database", {"sql": "SELECT 1 AS x"})
    await call(server, "sandbox_exec", {"command": f"mkdir -p data && {make}"})
    target = workspace / "data" / "query_1.csv"
    if target.is_dir():
        error = await call_error(
            server, "sandbox_exec", {"command": "true", "data_files": ["query_1"]}
        )
        assert "query_1.csv" in error
        return
    await call(server, "sandbox_exec", {"command": "true", "data_files": ["query_1"]})
    assert target.is_file() and target.read_text() == "x\n1\n"
    assert [p.name for p in (workspace / "data").iterdir()] == ["query_1.csv"]


async def test_a_stream_past_what_is_kept_says_so(server: MCPServer, workspace: Path) -> None:
    command = f"head -c {KEEP_BYTES + 1000} /dev/zero | tr '\\0' o"
    reply = await call(server, "sandbox_exec", {"command": command})
    [saved] = (workspace / "sandbox_logs").glob("*.stdout.txt")
    assert saved.stat().st_size == KEEP_BYTES
    assert f"the first {KEEP_BYTES:,} bytes" in reply and "1,000 more" in reply


async def test_reply_holds_exit_code_stdout_and_stderr(server: MCPServer) -> None:
    reply = await call(server, "sandbox_exec", {"command": "echo out; echo err >&2; exit 4"})
    assert reply == "exit_code: 4\nstdout:\nout\n\nstderr:\nerr\n"


async def test_output_past_the_limit_is_cut_and_saved_whole(
    server: MCPServer, workspace: Path
) -> None:
    command = (
        "python3 -c \"print('o' * 40000); import sys; print('e' * 20000 + 'END', file=sys.stderr)\""
    )
    reply = await call(server, "sandbox_exec", {"command": command})
    assert len(reply) < 31_000
    [stdout_file] = (workspace / "sandbox_logs").glob("*.stdout.txt")
    [stderr_file] = (workspace / "sandbox_logs").glob("*.stderr.txt")
    assert stdout_file.read_text() == "o" * 40000 + "\n"
    assert stderr_file.read_text().endswith("END\n")
    assert str(stdout_file.relative_to(workspace)) in reply
    # The end of stderr, where an error's traceback ends, is what shows.
    assert "END" in reply


async def test_a_command_past_its_timeout_is_a_timeout_error(server: MCPServer) -> None:
    error = await call_error(
        server, "sandbox_exec", {"command": "echo started; sleep 30", "timeout_seconds": 1}
    )
    assert "timed out after 1 seconds" in error and "started" in error


@pytest.mark.parametrize("timeout", [0, 901])
async def test_timeout_is_bounded(server: MCPServer, timeout: int) -> None:
    error = await call_error(
        server, "sandbox_exec", {"command": "true", "timeout_seconds": timeout}
    )
    assert "900" in error


async def test_result_ids_are_unique_across_databases(tmp_path: Path, workspace: Path) -> None:
    gateways = {
        "a": DuckDBGateway(make_db(tmp_path / "a.duckdb")),
        "b": DuckDBGateway(make_db(tmp_path / "b.duckdb")),
    }
    server = build_multi_server(
        gateways, load_config("full"), workspace, sandbox=LocalSandbox(workspace, SKILLS)
    )
    first = await call(server, "query_database", {"database": "a", "sql": "SELECT 1 AS x"})
    second = await call(server, "query_database", {"database": "b", "sql": "SELECT 2 AS x"})
    assert "query_1" in first and "query_2" in second
    reply = await call(
        server, "sandbox_exec", {"command": "cat data/query_2.csv", "data_files": ["query_2"]}
    )
    assert "x\n2\n" in reply


async def test_output_files_lists_created_and_updated_files_sorted_and_omits_unchanged(
    server: MCPServer,
) -> None:
    """Ticket 103: files created or updated under outputs/ are listed in POSIX sorted order;
    unchanged existing files and commands without outputs produce no output_files section."""
    created = await call(
        server,
        "sandbox_exec",
        {
            "command": (
                "mkdir -p outputs/sub && printf 'a,1\\n' > outputs/b.csv "
                "&& printf 'x' > outputs/sub/chart.png && printf '0' > outputs/a.csv"
            )
        },
    )
    assert created.endswith(
        "output_files:\n- outputs/a.csv\n- outputs/b.csv\n- outputs/sub/chart.png"
    )

    no_changes = await call(server, "sandbox_exec", {"command": "echo hi"})
    assert no_changes == "exit_code: 0\nstdout:\nhi\n"
    assert "output_files:" not in no_changes

    updated = await call(
        server,
        "sandbox_exec",
        {"command": "printf 'a,2\\n' >> outputs/b.csv"},
    )
    assert updated == "exit_code: 0\noutput_files:\n- outputs/b.csv"


async def test_output_files_ignores_symlinks_pointing_outside_workspace(
    server: MCPServer, tmp_path: Path
) -> None:
    """Ticket 103: symlinks under outputs/ (or outputs/ itself) pointing outside the workspace
    are never followed or listed."""
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("top-secret\n")
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "leaked.csv").write_text("k,v\n1,2\n")

    reply = await call(
        server,
        "sandbox_exec",
        {
            "command": (
                f"mkdir -p outputs && ln -s {outside_file} outputs/secret_link.txt "
                f"&& ln -s {outside_dir} outputs/linked_dir "
                "&& printf 'ok\\n' > outputs/real.csv"
            )
        },
    )
    assert reply == "exit_code: 0\noutput_files:\n- outputs/real.csv"

    linked_root = await call(
        server,
        "sandbox_exec",
        {"command": f"rm -rf outputs && ln -s {outside_dir} outputs"},
    )
    assert linked_root == "exit_code: 0"
    assert "output_files:" not in linked_root


async def test_output_files_are_reported_on_timeout(server: MCPServer) -> None:
    error = await call_error(
        server,
        "sandbox_exec",
        {
            "command": "mkdir -p outputs && printf '1\\n' > outputs/partial.csv && sleep 30",
            "timeout_seconds": 1,
        },
    )
    assert "timed out after 1 seconds" in error
    assert error.endswith("output_files:\n- outputs/partial.csv")
