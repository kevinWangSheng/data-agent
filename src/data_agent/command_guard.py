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
# What a denied program's job is done with instead (ticket 55: t47-screen trials kept trying
# `ls`, `cat` and `cp` because the denial named no alternative).
FILE_READING_PROGRAMS = frozenset(
    {"ls", "cat", "head", "tail", "less", "more", "find", "grep", "rg", "tree", "wc", "file"}
)
FILE_READING_HINT = "list, read or search files with the Glob, Read or Grep tool"
FILE_WRITING_PROGRAMS = frozenset({"cp", "mv", "mkdir", "touch", "tee", "sed"})
FILE_WRITING_HINT = "create or change files with the Write or Edit tool"
SHAPE_RULE = "Bash runs one command per call, optionally with 2>&1 and a pipe into tail or head"
CHAR_NAMES = {"\n": "a newline", "\r": "a carriage return", "`": "a backtick"}
# Operators after which a new program starts; after `|` only one other than tail or head counts.
SEPARATORS = frozenset({";", "&&", "||", "&", "|"})


def _is_operator(token: str) -> bool:
    return bool(token) and set(token) <= OPERATOR_CHARS


def _tokens(command: str) -> list[str] | None:
    """The words and operators of `command` as `_split` reads them, or None when a quote is left
    open. Only the denial text uses it; `_split` keeps its own lexer, as it decides."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


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


def _dbt_denial(args: list[str], cwd: Path, workspace: Path) -> str | None:
    """`cwd` is where the command runs (the workspace or the directory entered with `cd`)."""
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
            if not inside(resolve(cwd, value), workspace):
                return f"{flag} must stay inside the workspace"
    return None


def _prints_variable(command: str, variables: Sequence[str]) -> bool:
    """`command` is exactly `echo $VAR` or `printenv VAR` for one of `variables`. Compared as a
    string: split on whitespace, `printenv\\nDB_TYPE` would pass and dump the environment."""
    return any(command.strip() in (f"echo ${var}", f"printenv {var}") for var in variables)


def _program_hints(programs: Sequence[str]) -> list[str]:
    """What to use instead of the denied `programs`."""
    hints: list[str] = []
    if FILE_READING_PROGRAMS & set(programs):
        hints.append(FILE_READING_HINT)
    if FILE_WRITING_PROGRAMS & set(programs):
        hints.append(FILE_WRITING_HINT)
    return hints


def _operators(tokens: list[str]) -> list[str]:
    """The operators in `tokens` beyond a leading `cd <dir> &&`, `2>&1` and `|`, as written; a
    redirection into /dev/null is named whole (`2>/dev/null`)."""
    found: list[str] = []
    for i, token in enumerate(tokens):
        before = tokens[i - 1] if i else ""
        after = tokens[i + 1] if i + 1 < len(tokens) else ""
        if (
            not _is_operator(token)
            or token == "|"
            or (token == ">&" and before == "2" and after == "1")
            or (token == "&&" and i == 2 and tokens[0] == "cd")
        ):
            continue
        if token.startswith(">") and after == "/dev/null":
            # A digit right before is the stream redirected, unless it ends a `2>&1`.
            stream = before.isdigit() and not (i > 1 and _is_operator(tokens[i - 2]))
            token = f"{before if stream else ''}{token}/dev/null"
        found.append(f"`{token}`")
    return list(dict.fromkeys(found))


def _shape_denial(command: str, variables: Sequence[str]) -> str:
    """Why `command` is not one command with at most `2>&1` and a pipe into tail or head: what in
    it the guard refuses, and the form that would run."""
    causes: list[str] = []
    echoed = [var for var in variables if f"${var}" in command]
    names = {**CHAR_NAMES, "$": f"`$` outside a lone `echo ${echoed[0]}`"} if echoed else CHAR_NAMES
    chars = [names.get(char, f"`{char}`") for char in sorted(FORBIDDEN_CHARS & set(command))]
    if chars:
        causes.append(f"{', '.join(chars)} {'is' if len(chars) == 1 else 'are'} not allowed")
    tokens = _tokens(command)
    if tokens is None:
        causes.append("a quote is left open")
        programs = command.split()[:1]
    else:
        programs = [
            token
            for i, token in enumerate(tokens)
            if i == 0
            or (
                tokens[i - 1] in SEPARATORS
                and not (tokens[i - 1] == "|" and token in PAGERS)
                and not _is_operator(token)
            )
        ]
        if operators := _operators(tokens):
            verb = "is" if len(operators) == 1 else "are"
            causes.append(f"{', '.join(operators)} {verb} not allowed: {SHAPE_RULE}")
    if not causes:
        causes.append(SHAPE_RULE)
    hints = [f"run `echo ${var}` on its own, as a separate Bash call" for var in echoed]
    if "--vars" in command and {"{", "}"} & set(command):
        hints.append("write --vars as YAML without braces: --vars 'k: v'")
    hints.extend(_program_hints(programs))
    return "; ".join([*causes, *hints])


def denial(
    command: str, workspace: Path, scripts: Sequence[Path], variables: Sequence[str]
) -> str | None:
    """Why `command` may not run, or None when it may. `workspace` is already resolved."""
    if _prints_variable(command, variables):
        return None
    split = _split(command)
    if split is None:
        return _shape_denial(command, variables)
    directory, segments = split
    # Paths in the command resolve from the directory entered, which must exist: Bash would stop
    # at a missing one, and only a real directory can be checked past its symlinks.
    cwd = workspace if directory is None else resolve(workspace, directory)
    if not (inside(cwd, workspace) and cwd.is_dir()):
        return f"cd {directory} leaves the workspace or names no directory in it"
    (program, *args), *pagers = segments
    for pager, *pager_args in pagers:
        if pager not in PAGERS or not all(PAGER_ARG.fullmatch(arg) for arg in pager_args):
            return "; ".join(
                [
                    f"only tail or head with a line count may follow a pipe, not {pager!r}",
                    *_program_hints([program]),
                ]
            )
    if program == "dbt":
        return _dbt_denial(args, cwd, workspace)
    if program == "python3" and args and scripts:
        allowed = {script.resolve() for script in scripts}
        if resolve(cwd, args[0]) not in allowed:
            return f"python3 may only run {', '.join(sorted(s.name for s in allowed))}"
        # The scripts take the project directory and, for validate, a timeout in seconds. The
        # project is always checked as a path: `123` may be a symlink out of the workspace.
        project, *timeout = args[1:] or ["."]
        if len(timeout) > 1 or (timeout and not timeout[0].isdigit()):
            return "the dbt-workflow scripts take a project directory and a timeout in seconds"
        if not inside(resolve(cwd, project), workspace):
            return "the dbt-workflow scripts may only read the workspace"
        return None
    if program == "cd":
        return "cd may only precede a dbt command, as `cd <dir> && dbt ...`"
    return "; ".join([f"{program!r} is not allowed", *_program_hints([program])])


def command_guard(
    workspace: Path, scripts: Sequence[Path], variables: Sequence[str]
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
