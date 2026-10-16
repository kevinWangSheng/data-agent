"""`data-agent` command line.

Exit codes of `run`: 0 success, 1 the agent did not finish, 2 config or infrastructure error.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from data_agent.agent import ConfigError, Task, run
from data_agent.gateway import DuckDBGateway
from data_agent.mcp_server import build_server
from data_agent.run_config import CONFIG_NAMES, load_config

EXIT_OK, EXIT_UNFINISHED, EXIT_ERROR = 0, 1, 2


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="data-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("mcp-server", help="serve the data tools over MCP stdio")
    serve.add_argument("--config", required=True, choices=CONFIG_NAMES)
    serve.add_argument("--duckdb", required=True, type=Path, help="DuckDB file to query")

    run_cmd = commands.add_parser("run", help="run the agent on one task")
    task_arg = run_cmd.add_mutually_exclusive_group(required=True)
    task_arg.add_argument("--task", help="task text")
    task_arg.add_argument("--task-file", type=Path, help="file holding the task text")
    run_cmd.add_argument("--type", required=True, choices=("dbt", "query"))
    run_cmd.add_argument("--workspace", required=True, type=Path, help="working directory")
    run_cmd.add_argument("--duckdb", required=True, type=Path, help="DuckDB file to query")
    run_cmd.add_argument("--config", required=True, choices=CONFIG_NAMES)
    run_cmd.add_argument("--events-dir", required=True, type=Path, help="where the event file goes")

    args = parser.parse_args(argv)
    if args.command == "mcp-server":
        if not args.duckdb.is_file():
            serve.error(f"DuckDB file not found: {args.duckdb}")
        build_server(DuckDBGateway(args.duckdb)).run("stdio")
        return

    if not args.duckdb.is_file():
        run_cmd.error(f"DuckDB file not found: {args.duckdb}")
    if not args.workspace.is_dir():
        run_cmd.error(f"workspace directory not found: {args.workspace}")
    text: str = args.task
    if args.task_file is not None:
        if not args.task_file.is_file():
            run_cmd.error(f"task file not found: {args.task_file}")
        text = args.task_file.read_text()
    task = Task(text=text, type=args.type, workspace=args.workspace, duckdb=args.duckdb)
    try:
        outcome = asyncio.run(run(task, load_config(args.config), args.events_dir, os.environ))
    except ConfigError as exc:
        run_cmd.error(str(exc))
    print(outcome.answer)
    print(f"events: {outcome.events_path}", file=sys.stderr)
    if outcome.infra_error:
        sys.exit(EXIT_ERROR)
    sys.exit(EXIT_OK if outcome.finished else EXIT_UNFINISHED)
