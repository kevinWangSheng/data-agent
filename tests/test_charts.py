"""Answer charts beyond the acceptance tests (ticket 119): the id line each tool set names, one
store for sandbox_exec and display_chart, the edges of the checks, the prompts with and without
`--charts`, and the web UI's dbt runs. tests/acceptance/test_charts_*.py hold the contract."""

import datetime
import json
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from claude_agent_sdk import ResultMessage
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from data_agent import agent
from data_agent.agent import Task
from data_agent.charts import ChartError, ChartSeries, chart_payload
from data_agent.gateway import DuckDBGateway, QueryResult
from data_agent.mcp_server import build_server
from data_agent.run_config import load_config
from data_agent.sandbox import LocalSandbox, QueryResults
from data_agent.verifiers import verifier_agents
from data_agent.web.launcher import Launcher
from data_agent.web.runs import RunsDir
from data_agent.web.sources import DataSource
from evals import dab

# --- the id line -------------------------------------------------------------------------------


def note(data_files: bool, charts: bool) -> str:
    results = QueryResults(data_files=data_files, charts=charts)
    return results.note(results.add(QueryResult(["a"], [(1,), (2,)])))


def test_the_sandbox_id_line_is_what_it_was() -> None:
    expected = (
        "[result id: query_1; list it in sandbox_exec's data_files to read all 2 rows as "
        "data/query_1.csv]"
    )
    assert note(data_files=True, charts=False) == expected


def test_without_a_sandbox_the_id_line_names_only_display_chart() -> None:
    line = note(data_files=False, charts=True)
    assert line.startswith("[result id: query_1;")
    assert "display_chart" in line
    assert "sandbox_exec" not in line


def test_with_both_tools_the_id_line_names_both() -> None:
    line = note(data_files=True, charts=True)
    assert "sandbox_exec's data_files" in line and "display_chart" in line
    assert line.count("]") == 1 and line.endswith("]")


# --- one store for sandbox_exec and display_chart -----------------------------------------------


def text_of(result: Any) -> str:
    assert isinstance(result, CallToolResult)
    assert not result.is_error, result
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


async def test_sandbox_exec_and_display_chart_read_the_same_ids(tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = build_server(
        DuckDBGateway(db),
        load_config("no-context"),
        workspace,
        sandbox=LocalSandbox(workspace, Path("/opt/skills")),
        charts=True,
    )
    await server.call_tool("query_database", {"sql": "SELECT 'a' AS k, 1 AS v"})
    await server.call_tool("query_database", {"sql": "SELECT 'b' AS k, 2 AS v"})
    listed = await server.call_tool(
        "sandbox_exec", {"command": "cat data/query_2.csv", "data_files": ["query_2"]}
    )
    drawn = await server.call_tool(
        "display_chart",
        {
            "query_id": "query_2",
            "chart_type": "bar",
            "x": "k",
            "series": [{"column": "v"}],
            "title": "t",
        },
    )
    assert "k,v\nb,2" in text_of(listed)
    assert json.loads(text_of(drawn))["data"] == [{"k": "b", "v": 2}]


async def test_without_the_switch_a_server_has_no_display_chart(tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    duckdb.connect(str(db)).close()
    server: MCPServer = build_server(DuckDBGateway(db), load_config("no-context"))
    assert "display_chart" not in {tool.name for tool in await server.list_tools()}


# --- the checks at their edges ------------------------------------------------------------------

RESULT = QueryResult(
    ["day", "flag", "amount", "empty"],
    [
        (datetime.date(2024, 1, 1), True, Decimal("1.50"), None),
        (datetime.date(2024, 1, 2), False, Decimal("-2"), None),
    ],
)


def payload(**arguments: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "query_id": "query_1",
        "chart_type": "bar",
        "x": "day",
        "series": [ChartSeries(column="amount")],
        "title": "t",
    }
    return chart_payload({"query_1": RESULT}, **(defaults | arguments))


def test_a_bool_column_is_not_numeric() -> None:
    with pytest.raises(ChartError, match="flag"):
        payload(series=[ChartSeries(column="flag")])


def test_an_all_null_series_draws_nulls() -> None:
    data = payload(series=[ChartSeries(column="empty")])["data"]
    assert data == [{"day": "2024-01-01", "empty": None}, {"day": "2024-01-02", "empty": None}]


def test_decimals_are_numbers_and_dates_are_iso_text() -> None:
    data = payload()["data"]
    assert data == [{"day": "2024-01-01", "amount": 1.5}, {"day": "2024-01-02", "amount": -2.0}]
    assert all(type(row["amount"]) is float for row in data)


def test_with_no_query_yet_the_error_says_so() -> None:
    with pytest.raises(ChartError, match="none yet"):
        chart_payload({}, "query_1", "bar", "day", [ChartSeries(column="amount")], "t")


# --- the prompts ---------------------------------------------------------------------------------


def test_the_charting_rule_covers_the_spec_points() -> None:
    rule = " ".join(agent.CHARTS_PROMPT.split())
    assert "`display_chart`" in rule
    # When to chart, and when not.
    for kind in ("compare categories", "trend over time", "splits into parts", "values are spread"):
        assert kind in rule
    assert "single number" in rule and "two or three rows" in rule
    assert "at most one or two charts" in rule
    # The text is complete; the chart only adds.
    assert "complete without the charts" in rule and '"see the chart"' in rule
    # Ids are this run's (this turn's) only.
    assert "this run's queries" in rule and "this turn's" in rule


def test_without_the_switch_the_system_prompt_has_no_charting_rule(tmp_path: Path) -> None:
    task = Task(text="t", type="query", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    for config in ("full", "baseline"):
        plain = agent.system_prompt(task, load_config(config))
        assert "display_chart" not in plain
        charted = agent.system_prompt(task, load_config(config), charts=True)
        assert charted == plain + agent.CHARTS_PROMPT


def test_the_dab_prompt_and_the_sandbox_rule_are_unchanged() -> None:
    assert agent.SANDBOX_RULE == (
        "When query results need computing, combining or plotting, do it in `sandbox_exec` and "
        "pass it the results with `data_files`.\n"
    )
    assert agent.SANDBOX_RULE in dab.TASK_PROMPT
    assert agent.SANDBOX_RULE in agent.QUERY_PROMPT
    assert "display_chart" not in dab.TASK_PROMPT + agent.QUERY_PROMPT


def test_the_charts_query_prompt_differs_only_in_the_sandbox_line() -> None:
    assert agent.SANDBOX_RULE not in agent.CHARTS_QUERY_PROMPT
    assert "plotting, do it in `sandbox_exec`" not in agent.CHARTS_QUERY_PROMPT
    assert "`display_chart`, not by plotting in `sandbox_exec`" in agent.CHARTS_QUERY_PROMPT
    assert (
        agent.CHARTS_QUERY_PROMPT.replace(agent.CHARTS_SANDBOX_RULE, agent.SANDBOX_RULE)
        == agent.QUERY_PROMPT
    )


DONE = ResultMessage(
    subtype="success",
    duration_ms=1,
    duration_api_ms=1,
    is_error=False,
    num_turns=1,
    session_id="s",
    usage={},
    total_cost_usd=0.0,
    result="done",
)


async def run_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, charts: bool) -> str:
    """The task prompt a query run sends, against a scripted SDK (as test_task_file does)."""
    seen: dict[str, str] = {}

    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        seen["prompt"] = prompt
        yield DONE

    monkeypatch.setattr(agent, "query", fake_query)
    workspace = tmp_path / f"ws-{charts}"
    workspace.mkdir()
    db = tmp_path / f"x-{charts}.duckdb"
    duckdb.connect(str(db)).close()
    task = Task(text="Revenue by month", type="query", workspace=workspace, duckdb=db)
    await agent.run(
        task,
        load_config("no-context"),
        tmp_path / f"events-{charts}",
        {"DATA_AGENT_API_KEY": "k"},
        charts=charts,
    )
    return seen["prompt"]


async def test_a_charts_run_sends_the_charts_sandbox_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plain = await run_prompt(monkeypatch, tmp_path, charts=False)
    charted = await run_prompt(monkeypatch, tmp_path, charts=True)
    assert agent.SANDBOX_RULE in plain and agent.CHARTS_SANDBOX_RULE not in plain
    assert agent.CHARTS_SANDBOX_RULE in charted and agent.SANDBOX_RULE not in charted
    restored = charted.replace(agent.CHARTS_SANDBOX_RULE, agent.SANDBOX_RULE)
    assert restored.replace("ws-True", "ws-False") == plain


def test_the_verifier_subagents_never_get_display_chart() -> None:
    """A dbt run from the web UI has `--charts`; its verifiers list their own tools."""
    for definition in verifier_agents(agent.MCP_SERVER).values():
        assert definition.tools is not None
        assert not any(tool.endswith("display_chart") for tool in definition.tools)


# --- the web UI's dbt runs (ticket 119, 待人定 2: the spec's letter, every form run) -------------


@dataclass
class Recorder:
    """A `start_run` that records the arguments of each run and starts one that exits."""

    calls: list[list[str]] = field(default_factory=list)
    processes: list["subprocess.Popen[bytes]"] = field(default_factory=list)

    def __call__(self, args: Sequence[str], cwd: Path) -> "subprocess.Popen[bytes]":
        self.calls.append(list(args))
        process = subprocess.Popen([sys.executable, "-c", "pass"], cwd=cwd, stdin=subprocess.PIPE)
        self.processes.append(process)
        return process


@pytest.fixture
def launcher(tmp_path: Path) -> Iterator[tuple[Launcher, Recorder]]:
    project = tmp_path / "proj"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: p\n")
    shop = tmp_path / "shop.duckdb"
    shop.write_bytes(b"")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    recorder = Recorder()
    source = DataSource("shop", duckdb=shop, dbt_project=project)
    try:
        yield Launcher(RunsDir(runs_dir), [source], recorder), recorder
    finally:
        for process in recorder.processes:
            process.wait(timeout=30)


def test_a_dbt_run_from_the_run_form_has_the_switch(
    launcher: tuple[Launcher, Recorder],
) -> None:
    form, recorder = launcher
    form.submit("Build a model of monthly revenue", "dbt", "shop", "full")
    [args] = recorder.calls
    assert args[args.index("--type") + 1] == "dbt"
    assert "--charts" in args
