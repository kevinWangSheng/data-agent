"""`data-agent` command line.

Exit codes of `run`: 0 success, 1 the agent did not finish, 2 config or infrastructure error.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from data_agent.agent import ConfigError, Task, run
from data_agent.gateway import DuckDBGateway, Gateway, PostgresGateway
from data_agent.mcp_server import build_server
from data_agent.run_config import CONFIG_NAMES, load_config

EXIT_OK, EXIT_UNFINISHED, EXIT_ERROR = 0, 1, 2


def add_database(parser: argparse.ArgumentParser) -> None:
    """The data source: a DuckDB file, or a Postgres connection string read from the environment
    variable `--postgres-env` names, so the string (password included) stays out of argv."""
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument("--duckdb", type=Path, help="DuckDB file to query")
    database.add_argument(
        "--postgres-env", metavar="VAR", help="environment variable holding a Postgres URL"
    )


def check_database(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.duckdb is not None and not args.duckdb.is_file():
        parser.error(f"DuckDB file not found: {args.duckdb}")
    if args.postgres_env is not None and not os.environ.get(args.postgres_env):
        parser.error(f"environment variable {args.postgres_env} (--postgres-env) is not set")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="data-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("mcp-server", help="serve the data tools over MCP stdio")
    serve.add_argument("--config", required=True, choices=CONFIG_NAMES)
    add_database(serve)
    serve.add_argument(
        "--workspace", type=Path, help="keep the tools' file reads inside this directory"
    )
    serve.add_argument(
        "--no-verifier-tools",
        action="store_true",
        help="leave out the verifier tools (for a run without the verifier subagents)",
    )

    run_cmd = commands.add_parser("run", help="run the agent on one task")
    task_arg = run_cmd.add_mutually_exclusive_group(required=True)
    task_arg.add_argument("--task", help="task text")
    task_arg.add_argument("--task-file", type=Path, help="file holding the task text")
    run_cmd.add_argument("--type", required=True, choices=("dbt", "query"))
    run_cmd.add_argument("--workspace", required=True, type=Path, help="working directory")
    add_database(run_cmd)
    run_cmd.add_argument("--config", required=True, choices=CONFIG_NAMES)
    run_cmd.add_argument("--events-dir", required=True, type=Path, help="where the event file goes")

    args = parser.parse_args(argv)
    command = serve if args.command == "mcp-server" else run_cmd
    try:
        config = load_config(args.config)
    except ValueError as exc:
        command.error(str(exc))
    check_database(command, args)
    if args.command == "mcp-server":
        if args.workspace is not None and not args.workspace.is_dir():
            serve.error(f"workspace directory not found: {args.workspace}")
        gateway: Gateway = (
            DuckDBGateway(args.duckdb)
            if args.duckdb is not None
            else PostgresGateway(os.environ[args.postgres_env])
        )
        build_server(gateway, config, args.workspace, not args.no_verifier_tools).run("stdio")
        return

    if not args.workspace.is_dir():
        run_cmd.error(f"workspace directory not found: {args.workspace}")
    text: str = args.task
    if args.task_file is not None:
        if not args.task_file.is_file():
            run_cmd.error(f"task file not found: {args.task_file}")
        text = args.task_file.read_text()
    task = Task(
        text=text,
        type=args.type,
        workspace=args.workspace,
        duckdb=args.duckdb,
        postgres_env=args.postgres_env,
    )
    try:
        outcome = asyncio.run(run(task, config, args.events_dir, os.environ))
    except ConfigError as exc:
        run_cmd.error(str(exc))
    print(outcome.answer)
    for error in outcome.errors:
        print(f"error: {error}", file=sys.stderr)
    print(f"events: {outcome.events_path}", file=sys.stderr)
    if outcome.infra_error:
        sys.exit(EXIT_ERROR)
    sys.exit(EXIT_OK if outcome.finished else EXIT_UNFINISHED)
