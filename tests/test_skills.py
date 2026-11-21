"""The Skills preloaded for query runs: real tools only, no scoring hints, correct DuckDB facts."""

import re
import shutil
from pathlib import Path

import duckdb

from data_agent.agent import MCP_SERVER, QUERY_SKILLS, SKILLS_DIR
from data_agent.gateway import DuckDBGateway
from data_agent.mcp_server import build_server
from data_agent.run_config import load_config

# SignalPilot MCP tools the upstream Skills call (docs/research/2026-09-21-signalpilot-mcp-reuse.md)
UPSTREAM_TOOLS = {
    "describe_table",
    "explore_column",
    "explore_columns",
    "explore_table",
    "schema_overview",
    "schema_statistics",
    "get_date_boundaries",
    "find_join_path",
    "get_relationships",
    "validate_sql",
    "debug_cte_query",
    "explain_query",
    "plan_query",
}


async def test_query_skills_name_only_tools_the_server_has(tmp_path: Path) -> None:
    server = build_server(DuckDBGateway(tmp_path / "unused.duckdb"), load_config("full"))
    served = {tool.name for tool in await server.list_tools()}
    text = "\n".join((SKILLS_DIR / name / "SKILL.md").read_text() for name in QUERY_SKILLS)

    prefixed = re.findall(r"mcp__(\w+?)__(\w+)", text)
    assert {(MCP_SERVER, tool) for _, tool in prefixed} == set(prefixed)
    assert {tool for _, tool in prefixed} <= served
    assert "connection_name" not in text
    named = set(re.findall(r"`(\w+)`", text)) | set(re.findall(r"\b(\w+)\s*\(", text))
    assert not (named & (UPSTREAM_TOOLS - served))


def test_query_skills_say_nothing_about_how_answers_are_scored() -> None:
    """ADR-0005: no benchmark or evaluator conventions such as result files or tolerance."""
    for name in QUERY_SKILLS:
        text = (SKILLS_DIR / name / "SKILL.md").read_text().lower()
        body = text.split("-->", 1)[-1]  # the modification note may name what was removed
        for term in ("benchmark", "evaluator", "tolerance", "result.csv", "result.sql"):
            assert term not in body, (name, term)


def test_the_turn_budget_section_uses_what_the_agent_is_told() -> None:
    """Ticket 69: the agent is never told an N; section 6 works from the limit the task prompt
    states and the turns-left reminders."""
    text = (SKILLS_DIR / "sql-workflow" / "SKILL.md").read_text()
    section = text.split("## 6. Turn Budget Management", 1)[1].split("\n## ", 1)[0]
    assert not re.search(r"\bN\b", section)
    assert "Turn limit" in section
    assert "turns left" in section
    assert "ANSWER IMMEDIATELY" in section


def test_duckdb_skill_states_division_as_duckdb_does_it() -> None:
    text = (SKILLS_DIR / "duckdb-sql" / "SKILL.md").read_text()
    assert duckdb.sql("SELECT 5 / 2, 5 // 2").fetchone() == (2.5, 2)
    assert "`5 / 2 = 2.5`" in text
    assert "`5 // 2 = 2`" in text
    assert "5/2 = 2`" not in text


# The 12 DataAgentBench datasets (ticket 105, ADR-0005): no Skill may name them.
DAB_DATASETS = (
    "agnews",
    "bookreview",
    "crmarenapro",
    "DEPS_DEV_V1",
    "GITHUB_REPOS",
    "googlelocal",
    "music_brainz_20k",
    "PANCANCER_ATLAS",
    "PATENTS",
    "stockindex",
    "stockmarket",
    "yelp",
)


def dataset_names_in(root: Path) -> list[tuple[str, str]]:
    """(file, dataset) for every file under root that names a DAB dataset, case-insensitive."""
    hits = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        text = path.read_text(errors="ignore").lower()
        hits += [(str(path.relative_to(root)), n) for n in DAB_DATASETS if n.lower() in text]
    return hits


def test_skills_name_no_dab_dataset() -> None:
    assert dataset_names_in(SKILLS_DIR) == []


def test_the_dataset_name_check_catches_a_planted_name(tmp_path: Path) -> None:
    shutil.copytree(SKILLS_DIR, tmp_path / "skills")
    planted = tmp_path / "skills" / "sql-workflow" / "notes.md"
    planted.write_text("Join the Yelp reviews to businesses.\n")
    assert dataset_names_in(tmp_path / "skills") == [("sql-workflow/notes.md", "yelp")]
