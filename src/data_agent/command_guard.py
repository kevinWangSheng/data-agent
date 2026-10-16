"""Let Bash in a dbt run do only what the dbt workflow needs: dbt and the dbt-workflow scripts.

Any other command (a database CLI, `python3 -c`, `rm`, `cat ~/...`) would bypass the read-only
gateway or leave the workspace, so a `PreToolUse` hook denies it. Allowed: one command, optionally
`2>&1` and a pipe into `tail` or `head`. dbt subcommands that run ad-hoc SQL (`show`, `--inline`,
`run-operation`) are denied too, so ad-hoc reads go through the gateway.

Known limit: dbt itself runs whatever the project holds. A model, a Python model, an `on-run-start`
hook or a profile the agent edits can run any SQL or code under `dbt run`; that is the dbt write
path (CONTEXT: dbt write), reviewable as project code, not something this guard can see.
"""

import re
import shlex
from collections.abc import Sequence
from pathlib import Path

from claude_agent_sdk import HookCallback, HookContext, HookInput, HookJSONOutput

from data_agent.workspace_guard import inside, resolve

DBT_SUBCOMMANDS = frozenset(
    {"run", "build", "test", "seed", "snapshot", "compile", "parse", "ls", "list", "deps", "debug"}
)
# Flags naming a directory dbt reads from or writes to; each must stay inside the workspace.
DIR_FLAGS = ("--project-dir", "--profiles-dir", "--target-path", "--log-path")
PAGERS = frozenset({"tail", "head"})
PAGER_ARG = re.compile(r"-n|-?\d+|-n\d+|--lines=\d+")
OPERATOR_CHARS = frozenset("();<>|&")
# Characters Bash acts on after the guard has read the words: expansions ($, `, braces, globs) and
# `#`, which shlex reads as a comment mid-word where Bash does not. Quoting would make some of them
# literal, but shlex drops the quotes, so they are refused everywhere.
FORBIDDEN_CHARS = frozenset("$`#{}*?[]\n\r")
# {scripts} names the dbt-workflow scripts only when the run offers them (the Skill layer is on).
RULE = (
    "Bash may only run one dbt command, subcommand first (run, build, test, seed, snapshot, "
    "compile, parse, ls, deps, debug){scripts}, from the workspace, optionally followed by 2>&1 "
    "and a pipe into tail or head; no braces, globs, $ or #. Query data with the query_database "
    "tool."
)
SCRIPTS_RULE = ", or the dbt-workflow scan/validate script"


def _is_operator(token: str) -> bool:
    return bool(token) and set(token) <= OPERATOR_CHARS


def _split(command: str) -> list[list[str]] | None:
    """The command's pipeline segments, or None if it uses anything beyond `2>&1` and `|`."""
    if FORBIDDEN_CHARS & set(command):
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            segments.append([])
        else:
            segments[-1].append(token)
    for segment in segments:
        if segment[-3:] == ["2", ">&", "1"]:
            del segment[-3:]
        if not segment or any(_is_operator(token) for token in segment):
            return None
    return segments


def _dbt_denial(args: list[str], workspace: Path) -> str | None:
    # The subcommand must come first: a global flag's value (`--log-level debug`) could otherwise
    # pass for it.
    subcommand = args[0] if args else None
    if subcommand not in DBT_SUBCOMMANDS:
        return f"dbt subcommand {subcommand!r} is not allowed"
    for i, arg in enumerate(args):
        if arg == "--inline" or arg.startswith("--inline="):
            return "dbt --inline runs ad-hoc SQL outside the gateway"
        flag, _, value = arg.partition("=")
        if flag in DIR_FLAGS:
            if not value:
                value = args[i + 1] if i + 1 < len(args) else ""
            if not inside(resolve(workspace, value), workspace):
                return f"{flag} must stay inside the workspace"
    return None


def denial(command: str, workspace: Path, scripts: Sequence[Path]) -> str | None:
    """Why `command` may not run, or None when it may. `workspace` is already resolved."""
    segments = _split(command)
    if segments is None:
        return "only a single command, 2>&1 and a pipe into tail or head are allowed"
    (program, *args), *pagers = segments
    for pager, *pager_args in pagers:
        if pager not in PAGERS or not all(PAGER_ARG.fullmatch(arg) for arg in pager_args):
            return f"only tail or head with a line count may follow a pipe, not {pager!r}"
    if program == "dbt":
        return _dbt_denial(args, workspace)
    if program == "python3" and args and scripts:
        allowed = {script.resolve() for script in scripts}
        if resolve(workspace, args[0]) not in allowed:
            return f"python3 may only run {', '.join(sorted(s.name for s in allowed))}"
        # The scripts take the project directory and, for validate, a timeout in seconds.
        for arg in args[1:]:
            if not arg.isdigit() and not inside(resolve(workspace, arg), workspace):
                return "the dbt-workflow scripts may only read the workspace"
        return None
    return f"{program!r} is not allowed"


def command_guard(workspace: Path, scripts: Sequence[Path]) -> HookCallback:
    root = workspace.resolve()
    rule = RULE.format(scripts=SCRIPTS_RULE if scripts else "")

    async def guard(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if hook_input["hook_event_name"] != "PreToolUse" or hook_input["tool_name"] != "Bash":
            return {}
        reason = denial(str(hook_input["tool_input"].get("command", "")), root, scripts)
        if reason is None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{rule} Denied: {reason}.",
            }
        }

    return guard
