"""The Skills preloaded for query runs name only tools this agent's MCP server really has."""

import re
from pathlib import Path

from data_agent.agent import MCP_SERVER, QUERY_SKILLS, SKILLS_DIR
from data_agent.gateway import DuckDBGateway
from data_agent.mcp_server import build_server

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
    server = build_server(DuckDBGateway(tmp_path / "unused.duckdb"))
    served = {tool.name for tool in await server.list_tools()}
    text = "\n".join((SKILLS_DIR / name / "SKILL.md").read_text() for name in QUERY_SKILLS)

    prefixed = re.findall(r"mcp__(\w+?)__(\w+)", text)
    assert {(MCP_SERVER, tool) for _, tool in prefixed} == set(prefixed)
    assert {tool for _, tool in prefixed} <= served
    assert "connection_name" not in text
    named = set(re.findall(r"`(\w+)`", text)) | set(re.findall(r"\b(\w+)\s*\(", text))
    assert not (named & (UPSTREAM_TOOLS - served))
