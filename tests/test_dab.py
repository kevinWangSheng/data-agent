"""DAB runner (ticket 13): DAB's own tools served over MCP, and the runner around our agent.

DAB itself is replaced by `tests/fixtures/fake_dab`, a stand-in with the same module paths and tool
interface (no DAB code), so these run without a DAB clone or its dependencies. The real runner is
proven by a real run on a DAB query (ticket 13 Comments).
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from data_agent.run_config import load_config
from evals import dab
from evals.__main__ import main as evals_main
from evals.dab_server import RESULT_CHARS, STORAGE_DIR

FAKE_DAB = Path(__file__).parent / "fixtures" / "fake_dab"
ENV = {"DATA_AGENT_API_KEY": "sk-test-secret"}
GROUND_TRUTH = "GOLD-ANSWER-7731"


def make_dab(root: Path, question: Any = "Which index is most volatile?") -> Path:
    """A DAB checkout with one dataset `toyset`: a SQLite db, a mongo db, one query."""
    shutil.copytree(FAKE_DAB, root)
    dataset = root / "query_toyset"
    (dataset / "query_dataset").mkdir(parents=True)
    with sqlite3.connect(dataset / "query_dataset" / "prices.db") as conn:
        conn.execute("CREATE TABLE prices (ticker TEXT, close REAL)")
        conn.executemany(
            "INSERT INTO prices VALUES (?, ?)", [(f"T{i:03d}", float(i)) for i in range(300)]
        )
    (dataset / "dump").mkdir()
    (dataset / "dump" / "articles.bson").write_bytes(b"x" * 10)
    config = {
        "db_clients": {
            "prices_db": {"db_type": "sqlite", "db_path": "query_dataset/prices.db"},
            "articles_db": {"db_type": "mongo", "db_name": "articles", "dump_folder": "dump"},
            "other_db": {"db_type": "mysql", "db_path": "query_dataset/other.db"},
        }
    }
    (dataset / "db_config.yaml").write_text(json.dumps(config))
    (dataset / "db_description.txt").write_text("prices_db holds daily closes.\n")
    (dataset / "db_description_withhint.txt").write_text("Hint: close is in USD.\n")
    query = dataset / "query1"
    query.mkdir()
    (query / "query.json").write_text(json.dumps(question))
    (query / "ground_truth.csv").write_text(GROUND_TRUTH)
    (query / "validate.py").write_text(f"GT = {GROUND_TRUTH!r}\n")
    return root


@pytest.fixture
def dab_root(tmp_path: Path) -> Path:
    return make_dab(tmp_path / "dab")


# --- the MCP server around DAB's tools ---------------------------------------------------------


@asynccontextmanager
async def dab_server(root: Path, log: Path) -> AsyncIterator[ClientSession]:
    config = root / "query_toyset" / "db_config.yaml"
    workspace = log.parent / "workspace"
    workspace.mkdir(exist_ok=True)
    params = StdioServerParameters(
        command=sys.executable,
        args=[*dab.server_args(root, config, log, workspace)],
        env={**os.environ, **dab.server_env()},
        cwd=str(workspace),  # as for the agent: not the repo root
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def text_of(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def data_of(result: CallToolResult) -> Any:
    """The JSON result under the line that names its variable."""
    note, _, data = text_of(result).partition("\n")
    assert note.startswith("Result `var_"), note
    return json.loads(data)


def logged_calls(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


async def test_server_exposes_dab_tools_under_our_tool_names(
    dab_root: Path, tmp_path: Path
) -> None:
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
    assert set(tools) == {"query_database", "list_tables", "execute_python"}
    # The descriptions are DAB's own, read from its tool specs at run time.
    assert tools["query_database"].description == "FAKE-DAB query description."
    assert tools["list_tables"].description == "FAKE-DAB list description."
    sql_arg = tools["query_database"].input_schema["properties"]["sql"]
    assert sql_arg["description"] == "FAKE-DAB query argument."
    # DAB's agent states its print format in its prompt; ours says it in the tool description,
    # with DAB's result marker.
    exec_description = tools["execute_python"].description or ""
    assert exec_description.startswith("FAKE-DAB exec description.")
    assert "FAKE-RESULT:" in exec_description
    code_arg = tools["execute_python"].input_schema["properties"]["code"]
    assert code_arg["description"] == "FAKE-DAB code argument."


async def test_query_runs_through_the_dab_tool(dab_root: Path, tmp_path: Path) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        result = await session.call_tool(
            "query_database",
            {"db_name": "prices_db", "sql": "SELECT ticker FROM prices WHERE close = 2"},
        )
        tables = await session.call_tool("list_tables", {"db_name": "prices_db"})
    assert not result.is_error
    assert data_of(result) == [{"ticker": "T002"}]
    assert data_of(tables) == ["prices"]
    assert [call["tool_name"] for call in logged_calls(log)] == ["query_db", "list_db"]


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE prices",
        "DELETE FROM prices",
        "SELECT 1; DELETE FROM prices",
        "ATTACH DATABASE '/tmp/x.db' AS x",
    ],
)
async def test_gateway_blocks_writes_before_they_reach_dab(
    dab_root: Path, tmp_path: Path, sql: str
) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        result = await session.call_tool("query_database", {"db_name": "prices_db", "sql": sql})
    assert result.is_error
    assert "read-only gateway" in text_of(result)
    assert logged_calls(log) == []


async def test_gateway_caps_the_rows_it_sends_to_dab(dab_root: Path, tmp_path: Path) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        await session.call_tool(
            "query_database", {"db_name": "prices_db", "sql": "SELECT * FROM prices"}
        )
    sent = logged_calls(log)[0]["args"]["query"]
    assert "LIMIT 10000" in sent.upper()


async def test_dab_errors_come_back_as_tool_errors(dab_root: Path, tmp_path: Path) -> None:
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        unknown = await session.call_tool("query_database", {"db_name": "nope", "sql": "SELECT 1"})
        bad = await session.call_tool(
            "query_database", {"db_name": "prices_db", "sql": "SELECT missing FROM prices"}
        )
    assert unknown.is_error
    assert "nope" in text_of(unknown)
    assert bad.is_error
    assert "missing" in text_of(bad)


async def test_a_database_type_with_no_sql_dialect_is_refused(
    dab_root: Path, tmp_path: Path
) -> None:
    """Only MongoDB skips the SQL check; any other type the gateway cannot parse is refused."""
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        result = await session.call_tool(
            "query_database", {"db_name": "other_db", "sql": "SELECT 1"}
        )
    assert result.is_error
    assert "mysql" in text_of(result)
    assert logged_calls(log) == []


@pytest.mark.parametrize("budget", ["NaN", "-1", "0", "inf", "lots"])
def test_cli_rejects_a_bad_budget(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], budget: str
) -> None:
    code = evals_main(
        [
            *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
            *("--config", "full", "--out", str(tmp_path / "out"), "--budget", budget),
        ]
    )
    assert code == 2
    assert "budget" in capsys.readouterr().err


def test_cli_stops_when_spend_cannot_be_totalled(
    dab_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    broken = dab.run_dir(out, "other", "1", 0)
    broken.mkdir(parents=True)
    (broken / "events.jsonl").write_text("not json\n")
    code = evals_main(
        [
            *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
            *("--config", "full", "--out", str(out), "--budget", "1"),
        ]
    )
    assert code == 2
    assert "not JSON" in capsys.readouterr().err


async def test_mongo_queries_skip_the_sql_check(dab_root: Path, tmp_path: Path) -> None:
    query = json.dumps({"collection": "articles", "filter": {}, "limit": 1})
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        result = await session.call_tool("query_database", {"db_name": "articles_db", "sql": query})
    assert not result.is_error
    assert data_of(result) == [{"echo": query}]


PRINT = "import json\nprint('FAKE-RESULT:' + json.dumps({}))"


async def test_results_are_variables_in_python(dab_root: Path, tmp_path: Path) -> None:
    log = tmp_path / "tools.jsonl"
    async with dab_server(dab_root, log) as session:
        prices = await session.call_tool(
            "query_database", {"db_name": "prices_db", "sql": "SELECT * FROM prices LIMIT 3"}
        )
        articles = await session.call_tool(
            "query_database", {"db_name": "articles_db", "sql": '{"collection": "articles"}'}
        )
        # A join across two databases, done outside both.
        code = PRINT.format("[p['ticker'] for p in var_1] + var_2")
        joined = await session.call_tool("execute_python", {"code": code})
        again = await session.call_tool("execute_python", {"code": PRINT.format("len(var_3)")})
    assert text_of(prices).startswith("Result `var_1`")
    assert text_of(articles).startswith("Result `var_2`")
    assert data_of(joined) == ["T000", "T001", "T002", {"echo": '{"collection": "articles"}'}]
    assert text_of(joined).startswith("Result `var_3`")
    assert data_of(again) == 4
    assert [call["tool_name"] for call in logged_calls(log)][-2:] == ["execute_python"] * 2


async def test_non_finite_numbers_are_kept_as_null(dab_root: Path, tmp_path: Path) -> None:
    # DAB's exec tool writes each variable's repr into the code; a NaN there is an undefined name
    # that would break every later execute_python call.
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        code = PRINT.format("[float('nan'), {'x': float('inf')}, -float('inf'), 1.5]")
        odd = await session.call_tool("execute_python", {"code": code})
        later = await session.call_tool("execute_python", {"code": PRINT.format("var_1")})
    assert data_of(odd) == [None, {"x": None}, None, 1.5]
    assert not later.is_error, text_of(later)
    assert data_of(later) == [None, {"x": None}, None, 1.5]


async def test_long_results_are_saved_in_the_workspace_with_a_preview(
    dab_root: Path, tmp_path: Path
) -> None:
    sql = "SELECT p.ticker FROM prices p, prices q LIMIT 5000"
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        result = await session.call_tool("query_database", {"db_name": "prices_db", "sql": sql})
        code = PRINT.format("len(json.load(open(var_1)))")
        counted = await session.call_tool("execute_python", {"code": code})
    text = text_of(result)
    saved = tmp_path / "workspace" / STORAGE_DIR / "var_1.json"
    assert f"`{STORAGE_DIR}/var_1.json`" in text
    assert len(text) < RESULT_CHARS + 500
    assert len(json.loads(saved.read_text())) == 5000
    assert data_of(counted) == 5000


@pytest.mark.parametrize("link", ["file", "directory"])
async def test_saved_results_never_follow_links_out_of_the_workspace(
    dab_root: Path, tmp_path: Path, link: str
) -> None:
    # execute_python code runs as root on the workspace and could plant a link where the next
    # long result is saved; the server must not write through it.
    target = tmp_path / "outside"
    target.mkdir()
    (target / "var_2.json").write_text("keep")
    plant = {
        "file": f"os.makedirs('file_storage', exist_ok=True)\n"
        f"os.symlink({str(target / 'var_2.json')!r}, 'file_storage/var_2.json')",
        "directory": f"os.symlink({str(target)!r}, 'file_storage')",
    }[link]
    sql = "SELECT p.ticker FROM prices p, prices q LIMIT 5000"
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        planted = await session.call_tool(
            "execute_python", {"code": f"import os\n{plant}\n" + PRINT.format("1")}
        )
        result = await session.call_tool("query_database", {"db_name": "prices_db", "sql": sql})
    assert not planted.is_error
    assert result.is_error
    assert (target / "var_2.json").read_text() == "keep"


async def test_python_errors_come_back_as_tool_errors(dab_root: Path, tmp_path: Path) -> None:
    async with dab_server(dab_root, tmp_path / "tools.jsonl") as session:
        failed = await session.call_tool("execute_python", {"code": "1 / 0"})
        later = await session.call_tool("execute_python", {"code": PRINT.format("var_2")})
    assert failed.is_error
    assert "ZeroDivisionError" in text_of(failed)
    # A failed call stores nothing, so the next result is var_1 and var_2 does not exist.
    assert later.is_error
    assert "var_2" in text_of(later)


# --- loading a query and sizing its data --------------------------------------------------------


def test_load_query_reads_question_description_and_databases(dab_root: Path) -> None:
    query = dab.load_query(dab_root, "toyset", "1", hints=False)
    assert query.question == "Which index is most volatile?"
    assert query.description == "prices_db holds daily closes."
    assert query.databases == {
        "prices_db": "sqlite",
        "articles_db": "mongo",
        "other_db": "mysql",
    }


def test_hints_append_the_official_hint_file(dab_root: Path) -> None:
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    assert query.description == "prices_db holds daily closes.\n\nHint: close is in USD."


def test_question_may_be_an_object_with_a_query_field(tmp_path: Path) -> None:
    root = make_dab(tmp_path / "dab", question={"query": "How many tickers?"})
    assert dab.load_query(root, "toyset", "1", hints=False).question == "How many tickers?"


def test_unknown_query_is_rejected(dab_root: Path) -> None:
    with pytest.raises(ValueError, match="query9"):
        dab.load_query(dab_root, "toyset", "9", hints=False)


def test_data_size_counts_database_files_and_dump_folders(dab_root: Path) -> None:
    query = dab.load_query(dab_root, "toyset", "1", hints=False)
    db_file = dab_root / "query_toyset" / "query_dataset" / "prices.db"
    assert query.data_bytes == db_file.stat().st_size + 10


def test_large_databases_get_the_large_turn_limit(dab_root: Path, tmp_path: Path) -> None:
    config = load_config("full")
    query = dab.load_query(dab_root, "toyset", "1", hints=False)
    assert dab.max_turns(query, config) == config.max_turns["query"]
    big = dab_root / "query_toyset" / "dump" / "big.bson"
    with big.open("wb") as f:
        f.truncate(dab.LARGE_DB_BYTES)  # sparse: no real disk use
    query = dab.load_query(dab_root, "toyset", "1", hints=False)
    assert dab.max_turns(query, config) == config.max_turns["large"] == 75


# --- the runner -------------------------------------------------------------------------------


def result(answer: str, cost: float = 0.01) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=3,
        session_id="s",
        usage={"input_tokens": 3, "cache_read_input_tokens": 5, "output_tokens": 7},
        total_cost_usd=cost,
        result=answer,
    )


def scripted(answers: list[str], seen: list[dict[str, Any]]) -> Callable[..., AsyncIterator[Any]]:
    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen.append({"prompt": prompt, "options": options})
        yield result(answers[len(seen) - 1])

    return fake_query


async def run_dab(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, out: Path, runs: int = 2, **kwargs: Any
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted([f"answer {i}" for i in range(runs)], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config("full"), out, runs, ENV, **kwargs)
    return seen


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_each_run_writes_a_trace_and_a_submission_entry(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=2)
    submission = json.loads((out / "submission.json").read_text())
    assert submission == [
        {"dataset": "toyset", "query": "1", "run": "0", "answer": "answer 0"},
        {"dataset": "toyset", "query": "1", "run": "1", "answer": "answer 1"},
    ]
    for run in (0, 1):
        events = read_events(dab.run_dir(out, "toyset", "1", run) / "events.jsonl")
        assert events[0]["type"] == "version"
        assert events[0]["config"] == "full"
        assert events[0]["dab"] == {
            "commit": "unknown",  # the fake DAB root is not a repository
            "dataset": "toyset",
            "query": "1",
            "run": run,
            "hints": True,
            "large": False,
            "max_turns": 50,
            "skills": ["sql-workflow"],  # no sqlite-sql Skill yet
        }
        assert events[-1]["type"] == "result"
        assert events[-1]["cost_usd"] > 0


async def test_the_agent_gets_the_task_and_dab_tools_but_no_answer(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    seen = await run_dab(monkeypatch, dab_root, out, runs=1)
    prompt, options = seen[0]["prompt"], seen[0]["options"]
    assert "Which index is most volatile?" in prompt
    assert "Hint: close is in USD." in prompt
    assert "prices_db (sqlite)" in prompt
    assert "articles_db (mongo)" in prompt
    assert GROUND_TRUTH not in prompt
    assert GROUND_TRUTH not in options.system_prompt
    # The workspace (the file tools' only reach) is an empty directory inside the run.
    workspace = Path(options.cwd)
    assert workspace == (dab.run_dir(out, "toyset", "1", 0) / "workspace").resolve()
    assert dab_root.resolve() not in workspace.parents
    server = options.mcp_servers["data"]
    assert server["args"][:2] == ["-m", "evals.dab_server"]
    assert server["env"] == dab.server_env()
    assert server["args"][-2:] == ["--workspace", str(workspace)]
    assert options.max_turns == 50
    assert options.max_budget_usd == 1.0


async def test_skills_follow_the_run_config(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["a", "b"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config("full"), tmp_path / "full", 1, ENV)
    await dab.run_query(dab_root, query, load_config("baseline"), tmp_path / "base", 1, ENV)
    full, baseline = (s["options"].system_prompt for s in seen)
    assert "name: sql-workflow" in full
    assert "name: sql-workflow" not in baseline


async def test_existing_runs_are_never_overwritten(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    # A run with a trace but no submission entry (it broke) is not redone over its trace.
    (out / "submission.json").write_text("[]")
    with pytest.raises(FileExistsError):
        await run_dab(monkeypatch, dab_root, out, runs=1)


async def test_rerun_skips_finished_runs_and_redoes_removed_ones(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=2)
    shutil.rmtree(dab.run_dir(out, "toyset", "1", 1))
    submission = json.loads((out / "submission.json").read_text())
    (out / "submission.json").write_text(json.dumps(submission[:1]))
    seen = await run_dab(monkeypatch, dab_root, out, runs=2)
    assert len(seen) == 1  # only run 1 ran again
    assert [e["run"] for e in json.loads((out / "submission.json").read_text())] == ["0", "1"]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"config": "baseline"}, "config"),
        ({"env": {**ENV, "DATA_AGENT_MODEL": "other-model"}}, "model"),
        ({"hints": False}, "hints"),
        ({"commit": "f00d"}, "DAB commit"),
    ],
)
async def test_rerun_under_other_conditions_refuses_to_mix_them(
    monkeypatch: pytest.MonkeyPatch,
    dab_root: Path,
    tmp_path: Path,
    change: dict[str, Any],
    field: str,
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    monkeypatch.setattr(dab, "dab_commit", lambda _root: change.get("commit", "unknown"))
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["x", "y"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=change.get("hints", True))
    config = load_config(change.get("config", "full"))
    with pytest.raises(dab.ConditionsChanged, match=field):
        await dab.run_query(dab_root, query, config, out, 2, change.get("env", ENV))
    assert seen == []  # nothing ran, not even the missing run 1
    assert len(json.loads((out / "submission.json").read_text())) == 1


async def test_a_finished_run_without_its_trace_cannot_be_checked(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    (dab.run_dir(out, "toyset", "1", 0) / "events.jsonl").unlink()
    with pytest.raises(dab.ConditionsChanged, match="trace"):
        await run_dab(monkeypatch, dab_root, out, runs=1)


async def test_another_query_under_other_conditions_refuses_to_join_the_submission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = make_dab(tmp_path / "dab")
    shutil.copytree(root / "query_toyset" / "query1", root / "query_toyset" / "query2")
    out = tmp_path / "out"
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["one", "two"], seen))
    query1 = dab.load_query(root, "toyset", "1", hints=True)
    await dab.run_query(root, query1, load_config("full"), out, 1, ENV)
    query2 = dab.load_query(root, "toyset", "2", hints=True)
    with pytest.raises(dab.ConditionsChanged, match="config"):
        await dab.run_query(root, query2, load_config("baseline"), out, 1, ENV)
    assert len(seen) == 1


def test_cli_fails_when_the_out_directory_holds_other_conditions(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["x", "y"], seen))
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test-secret")
    out = tmp_path / "out"
    args = [
        *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
        *("--runs", "1", "--out", str(out)),
    ]
    assert evals_main([*args, "--config", "full"]) == 0
    assert evals_main([*args, "--config", "baseline"]) == 2
    assert len(seen) == 1


class FakeContainer:
    def __init__(self, name: str, source: Path) -> None:
        self.name = name
        self.attrs = {"Mounts": [{"Source": str(source), "Destination": "/workspace"}]}
        self.removed = False

    def remove(self, force: bool = False) -> None:
        self.removed = force


class FakeDocker:
    def __init__(self, containers: list[FakeContainer]) -> None:
        self.containers = self
        self.listed: list[dict[str, Any]] = []
        self._containers = containers

    def list(self, **kwargs: Any) -> list[FakeContainer]:
        self.listed.append(kwargs)
        return self._containers


def test_leftover_exec_containers_of_the_workspace_are_removed(tmp_path: Path) -> None:
    ours = FakeContainer("ours", tmp_path / "run0" / "workspace")
    other = FakeContainer("other", tmp_path / "run1" / "workspace")
    client = FakeDocker([ours, other])
    removed = dab.remove_exec_containers(tmp_path / "run0" / "workspace", client)
    assert removed == ["ours"]
    assert ours.removed and not other.removed
    assert client.listed == [{"all": True, "filters": {"ancestor": dab.EXEC_IMAGE}}]


async def test_each_run_removes_its_exec_container_after_the_session(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # The SDK kills the tool server (SIGINT, SIGTERM, then for good) within about half a second,
    # before DAB's own clean-up can stop the container; the runner removes it instead.
    cleaned: list[Path] = []
    monkeypatch.setattr(dab, "remove_exec_containers", lambda ws: cleaned.append(ws) or [])

    async def broken_query(**_: Any) -> AsyncIterator[Any]:
        raise RuntimeError("endpoint unreachable")
        yield

    out = tmp_path / "out"
    await run_dab(monkeypatch, dab_root, out, runs=1)
    monkeypatch.setattr(dab, "query", broken_query)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config("full"), out, 2, ENV)
    assert cleaned == [dab.run_dir(out, "toyset", "1", r) / "workspace" for r in (0, 1)]


async def test_a_dab_server_that_failed_to_start_is_an_infra_error(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    async def server_down(**_: Any) -> AsyncIterator[Any]:
        servers = [{"name": "data", "status": "failed"}]
        yield SystemMessage(subtype="init", data={"mcp_servers": servers, "tools": []})
        yield result("The database is unavailable.")

    monkeypatch.setattr(dab, "query", server_down)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    out = tmp_path / "out"
    errors = await dab.run_query(dab_root, query, load_config("full"), out, 1, ENV)
    assert errors == 1
    assert json.loads((out / "submission.json").read_text()) == []
    events = read_events(dab.run_dir(out, "toyset", "1", 0) / "events.jsonl")
    assert any(e["type"] == "error" and e["source"] == "mcp" for e in events)


async def test_submission_keeps_entries_from_earlier_queries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = make_dab(tmp_path / "dab")
    shutil.copytree(root / "query_toyset" / "query1", root / "query_toyset" / "query2")
    out = tmp_path / "out"
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["one", "two"], seen))
    config = load_config("full")
    for query_id in ("2", "1"):
        query = dab.load_query(root, "toyset", query_id, hints=True)
        await dab.run_query(root, query, config, out, 1, ENV)
    submission = json.loads((out / "submission.json").read_text())
    assert [(e["query"], e["answer"]) for e in submission] == [("1", "two"), ("2", "one")]


async def test_budget_guard_stops_before_the_next_run(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # A scripted run costs about $0.00001 at the default model's price.
    seen = await run_dab(monkeypatch, dab_root, tmp_path / "out", runs=3, budget=Decimal(1))
    assert len(seen) == 3
    with pytest.raises(dab.BudgetReached):
        await run_dab(monkeypatch, dab_root, tmp_path / "out2", runs=3, budget=Decimal("0.00001"))
    assert not dab.run_dir(tmp_path / "out2", "toyset", "1", 1).exists()


async def test_answers_are_masked_like_the_trace(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["key sk-test-secret here"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config("full"), tmp_path / "out", 1, ENV)
    submission = (tmp_path / "out" / "submission.json").read_text()
    assert "sk-test-secret" not in submission
    assert "key [REDACTED] here" in submission


async def test_dab_logs_are_masked_like_the_trace(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # DAB's tools write their own logs, unmasked; the runner masks them once the session is over.
    async def leaky_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        log = Path(
            options.mcp_servers["data"]["args"][
                options.mcp_servers["data"]["args"].index("--log") + 1
            ]
        )
        log.write_text(json.dumps({"result": "error at sk-test-secret"}) + "\n")
        log.with_name("execute_python_artifacts.jsonl").write_text("sk-test-secret\n")
        yield result("done")

    monkeypatch.setattr(dab, "query", leaky_query)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    await dab.run_query(dab_root, query, load_config("full"), tmp_path / "out", 1, ENV)
    run = dab.run_dir(tmp_path / "out", "toyset", "1", 0)
    for name in ("dab_tool_calls.jsonl", "execute_python_artifacts.jsonl"):
        text = (run / name).read_text()
        assert "sk-test-secret" not in text
        assert "[REDACTED]" in text


async def test_dab_logs_mask_secrets_in_their_json_escaped_form(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # In a JSON log a password with a quote, a backslash or a non-ASCII letter is escaped.
    password = 'pa"ss\\wörd'

    async def leaky_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        args = options.mcp_servers["data"]["args"]
        log = Path(args[args.index("--log") + 1])
        entry = {"result": f"bad {password}"}
        lines = [json.dumps(entry, ensure_ascii=ascii) for ascii in (True, False)]
        log.write_text("\n".join(lines) + "\n")
        yield result("done")

    monkeypatch.setattr(dab, "query", leaky_query)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    env = {**ENV, "PG_PASSWORD": password}
    await dab.run_query(dab_root, query, load_config("full"), tmp_path / "out", 1, env)
    log = dab.run_dir(tmp_path / "out", "toyset", "1", 0) / "dab_tool_calls.jsonl"
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries == [{"result": "bad [REDACTED]"}] * 2


async def test_dab_logs_are_masked_when_the_run_is_interrupted(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    # Ctrl-C cancels the session; DAB's log still gets masked.
    async def interrupted_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        args = options.mcp_servers["data"]["args"]
        Path(args[args.index("--log") + 1]).write_text('{"result": "sk-test-secret"}\n')
        raise asyncio.CancelledError
        yield

    monkeypatch.setattr(dab, "remove_exec_containers", lambda ws: [])
    monkeypatch.setattr(dab, "query", interrupted_query)
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    with pytest.raises(asyncio.CancelledError):
        await dab.run_query(dab_root, query, load_config("full"), tmp_path / "out", 1, ENV)
    log = dab.run_dir(tmp_path / "out", "toyset", "1", 0) / "dab_tool_calls.jsonl"
    assert log.read_text() == '{"result": "[REDACTED]"}\n'


def test_database_connection_settings_are_masked_too() -> None:
    env = {
        **ENV,
        "PG_HOST": "pg.example.internal",
        "PG_PASSWORD": "pg-pass-123",
        "MONGO_URI": "mongodb://mongo.example.internal:27017/",
        "MYSQL_HOST": "localhost",  # a loopback name is no server address and stays readable
    }
    secrets = dab.secrets_of(env)
    text = dab.mask(
        "could not connect to pg.example.internal with pg-pass-123; "
        "mongodb://mongo.example.internal:27017/ and mongo.example.internal; localhost",
        secrets,
    )
    assert text == (
        "could not connect to [REDACTED] with [REDACTED]; [REDACTED] and [REDACTED]; localhost"
    )


async def test_the_trace_masks_database_connection_settings(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["pg.example.internal is down"], seen))
    query = dab.load_query(dab_root, "toyset", "1", hints=True)
    env = {**ENV, "PG_HOST": "pg.example.internal"}
    await dab.run_query(dab_root, query, load_config("full"), tmp_path / "out", 1, env)
    trace = (dab.run_dir(tmp_path / "out", "toyset", "1", 0) / "events.jsonl").read_text()
    assert "pg.example.internal" not in trace
    assert "pg.example.internal" not in (tmp_path / "out" / "submission.json").read_text()


def test_infra_errors_are_left_out_of_the_submission_and_fail_the_cli(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    async def broken_query(**_: Any) -> AsyncIterator[Any]:
        raise RuntimeError("endpoint unreachable")
        yield  # an async generator that ends before any ResultMessage

    monkeypatch.setattr(dab, "query", broken_query)
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test-secret")
    out = tmp_path / "out"
    code = evals_main(
        [
            *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
            *("--runs", "2", "--config", "full", "--out", str(out)),
        ]
    )
    assert code == 2
    assert json.loads((out / "submission.json").read_text()) == []
    # The traces stay, so the failure can be looked at before the runs are redone.
    assert (dab.run_dir(out, "toyset", "1", 1) / "events.jsonl").is_file()


def test_cli_runs_the_requested_queries(
    monkeypatch: pytest.MonkeyPatch, dab_root: Path, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(dab, "query", scripted(["x", "y"], seen))
    monkeypatch.setenv("DATA_AGENT_API_KEY", "sk-test-secret")
    out = tmp_path / "out"
    code = evals_main(
        [
            *("dab", "--dab-root", str(dab_root), "--dataset", "toyset", "--query", "1"),
            *("--runs", "2", "--config", "baseline", "--out", str(out)),
        ]
    )
    assert code == 0
    assert len(json.loads((out / "submission.json").read_text())) == 2
    assert "Hint: close is in USD." in seen[0]["prompt"]  # hints are on by default


def test_cli_rejects_a_missing_dab_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = evals_main(
        [
            *("dab", "--dab-root", str(tmp_path / "none"), "--dataset", "toyset", "--query", "1"),
            *("--config", "full", "--out", str(tmp_path / "out")),
        ]
    )
    assert code == 2
    assert "none" in capsys.readouterr().err
