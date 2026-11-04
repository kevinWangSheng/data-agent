"""Let Bash in a dbt run do only what the dbt workflow needs: dbt and the dbt-workflow scripts.

Any other command (a database CLI, `python3 -c`, `rm`, `cat ~/...`) would bypass the read-only
gateway or leave the workspace, so a `PreToolUse` hook denies it. Allowed: one command, optionally
`2>&1` and a pipe into `tail` or `head`, in the foreground. The command may be preceded by one
`cd <dir> &&` where `<dir>` stays inside the workspace, so dbt runs from the project directory and a
profile's relative paths resolve there; a bare `cd` is denied, so the working directory itself never
moves. Beside that, exactly `echo $VAR` or `printenv VAR` for the variables the harness names
(the benchmark tasks ask the agent to print `DB_TYPE`); every other `$` is still refused. dbt
subcommands that run ad-hoc SQL (`show`, `--inline`, `run-operation`) are denied too, so ad-hoc
reads go through the gateway, and so are those that write anything but the project's models
(`build`, `seed`, `snapshot`, `test --store-failures`, `--record-timing-info`,
`--partial-parse-file-path`): the only write is `dbt run` of the edited project.

Known limit: dbt itself runs whatever the project holds. A model, a Python model, an `on-run-start`
hook, a profile or a `store_failures` test config the agent edits can run any SQL or code under
`dbt run` or `dbt test`; that is the dbt write path (CONTEXT: dbt write), reviewable as project
code, not something this guard can see.
"""

import re
import shlex
from collections.abc import Sequence
from pathlib import Path

from claude_agent_sdk import HookCallback, HookContext, HookInput, HookJSONOutput

from data_agent.workspace_guard import inside, resolve

DBT_SUBCOMMANDS = frozenset({"run", "test", "compile", "parse", "ls", "list", "deps", "debug"})
# `dbt test` writes failing rows into tables with these.
STORE_FAILURES_FLAGS = ("--store-failures", "--store-failures-as")
# Options that write a file at the path they are given, anywhere (dbt 1.10 `dbt.cli.params`).
FILE_WRITING_FLAGS = ("--record-timing-info", "--partial-parse-file-path")
# Short form of --record-timing-info; click also takes it bundled (`-xr f`) or with the value
# attached (`-rf`), so any single-dash word holding an `r` is refused.
RECORD_TIMING_SHORT = "r"
# Flags naming a directory dbt reads from or writes to; each must stay inside the workspace.
DIR_FLAGS = ("--project-dir", "--profiles-dir", "--target-path", "--log-path")
PAGERS = frozenset({"tail", "head"})
PAGER_ARG = re.compile(r"-n|-?\d+|-n\d+|--lines=\d+")
OPERATOR_CHARS = frozenset("();<>|&")
# Characters Bash acts on after the guard has read the words: expansions ($, `, braces, globs) and
# `#`, which shlex reads as a comment mid-word where Bash does not. Quoting would make some of them
# literal, but shlex drops the quotes, so they are refused everywhere.
FORBIDDEN_CHARS = frozenset("$`#{}*?[]\n\r")
# {scripts} names the dbt-workflow scripts only when the run offers them (the Skill layer is on);
# {variables} the printable variables only when the harness names some.
RULE = (
    "Bash may only run one dbt command in the foreground, subcommand first (run, test, compile, "
    "parse, ls, deps, debug){scripts}, from the workspace or after `cd <dir> &&` with <dir> inside "
    "it, optionally followed by 2>&1 and a pipe into tail or head{variables}; no braces, globs, $ "
    "or #. Query data with the query_database tool."
)
SCRIPTS_RULE = ", or the dbt-workflow scan/validate script"
VARIABLES_RULE = ", or exactly `echo ${var}` or `printenv {var}`"


def _is_operator(token: str) -> bool:
    return bool(token) and set(token) <= OPERATOR_CHARS


def _split(command: str) -> tuple[str | None, list[list[str]]] | None:
    """The directory of a leading `cd <dir> &&` and the pipeline segments after it, or None if
    the command uses anything beyond that, `2>&1` and `|`."""
    if FORBIDDEN_CHARS & set(command):
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    directory = None
    if len(tokens) > 3 and tokens[0] == "cd" and tokens[2] == "&&":
        directory = tokens[1]
        del tokens[:3]
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
    return directory, segments


def _dbt_denial(args: list[str], base: Path, workspace: Path) -> str | None:
    # The subcommand must come first: a global flag's value (`--log-level debug`) could otherwise
    # pass for it.
    subcommand = args[0] if args else None
    if subcommand not in DBT_SUBCOMMANDS:
        return f"dbt subcommand {subcommand!r} is not allowed"
    for i, arg in enumerate(args):
        if arg == "--inline" or arg.startswith("--inline="):
            return "dbt --inline runs ad-hoc SQL outside the gateway"
        flag, _, value = arg.partition("=")
        if flag in STORE_FAILURES_FLAGS:
            return f"{flag} writes test failures into the database"
        if flag in FILE_WRITING_FLAGS or (
            arg.startswith("-") and not arg.startswith("--") and RECORD_TIMING_SHORT in arg
        ):
            return f"{flag} writes a file outside the project's models"
        if flag in DIR_FLAGS:
            if not value:
                value = args[i + 1] if i + 1 < len(args) else ""
            if not inside(resolve(base, value), workspace):
                return f"{flag} must stay inside the workspace"
    return None


def _prints_variable(command: str, variables: Sequence[str]) -> bool:
    """`command` is exactly `echo $VAR` or `printenv VAR` for one of `variables`."""
    words = command.split()
    return any(words in (["echo", f"${var}"], ["printenv", var]) for var in variables)


def denial(
    command: str, workspace: Path, scripts: Sequence[Path], variables: Sequence[str] = ()
) -> str | None:
    """Why `command` may not run, or None when it may. `workspace` is already resolved."""
    if _prints_variable(command, variables):
        return None
    split = _split(command)
    if split is None:
        return "only a single command, 2>&1 and a pipe into tail or head are allowed"
    directory, segments = split
    # Paths in the command resolve from the directory entered, which must exist: Bash would stop
    # at a missing one, and only a real directory can be checked past its symlinks.
    base = workspace if directory is None else resolve(workspace, directory)
    if not (inside(base, workspace) and base.is_dir()):
        return f"cd {directory} leaves the workspace or names no directory in it"
    (program, *args), *pagers = segments
    for pager, *pager_args in pagers:
        if pager not in PAGERS or not all(PAGER_ARG.fullmatch(arg) for arg in pager_args):
            return f"only tail or head with a line count may follow a pipe, not {pager!r}"
    if program == "dbt":
        return _dbt_denial(args, base, workspace)
    if program == "python3" and args and scripts:
        allowed = {script.resolve() for script in scripts}
        if resolve(base, args[0]) not in allowed:
            return f"python3 may only run {', '.join(sorted(s.name for s in allowed))}"
        # The scripts take the project directory and, for validate, a timeout in seconds. The
        # project is always checked as a path: `123` may be a symlink out of the workspace.
        project, *timeout = args[1:] or ["."]
        if len(timeout) > 1 or (timeout and not timeout[0].isdigit()):
            return "the dbt-workflow scripts take a project directory and a timeout in seconds"
        if not inside(resolve(base, project), workspace):
            return "the dbt-workflow scripts may only read the workspace"
        return None
    if program == "cd":
        return "cd may only precede a dbt command, as `cd <dir> && dbt ...`"
    return f"{program!r} is not allowed"


def command_guard(
    workspace: Path, scripts: Sequence[Path], variables: Sequence[str] = ()
) -> HookCallback:
    """`variables` are the environment variables Bash may print, exactly as `echo $VAR` or
    `printenv VAR`; the harness names them."""
    root = workspace.resolve()
    rule = RULE.format(
        scripts=SCRIPTS_RULE if scripts else "",
        variables="".join(VARIABLES_RULE.format(var=var) for var in variables),
    )

    async def guard(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if hook_input["hook_event_name"] != "PreToolUse" or hook_input["tool_name"] != "Bash":
            return {}
        tool_input = hook_input["tool_input"]
        reason: str | None
        if tool_input.get("run_in_background"):
            reason = "Bash may not run in the background"
        else:
            reason = denial(str(tool_input.get("command", "")), root, scripts, variables)
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
