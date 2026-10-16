"""`data-agent` command line."""

import argparse
from pathlib import Path

from data_agent.gateway import DuckDBGateway
from data_agent.mcp_server import build_server

# Run config names (spec "运行配置"). Until ticket 05 adds the layer switches, every name means
# all layers on.
CONFIG_NAMES = ("baseline", "full", "no-skill", "no-verifiers", "no-context", "full-old-skill")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="data-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("mcp-server", help="serve the data tools over MCP stdio")
    serve.add_argument("--config", required=True, choices=CONFIG_NAMES)
    serve.add_argument("--duckdb", required=True, type=Path, help="DuckDB file to query")
    args = parser.parse_args(argv)

    if not args.duckdb.is_file():
        serve.error(f"DuckDB file not found: {args.duckdb}")
    build_server(DuckDBGateway(args.duckdb)).run("stdio")
