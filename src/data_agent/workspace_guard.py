"""Keep the file tools (Read, Glob, Grep, and Write, Edit in dbt runs) inside the task workspace.

The SDK's allow list says which tools may run, not where they may look, so a `PreToolUse` hook
checks every path argument: resolved against the workspace, with symlinks followed, it must stay
inside the workspace, or the call is denied. Write and Edit may also not touch a protected file:
the database, which only `dbt run` may change. Credential files inside the workspace (`.env*`, keys)
are off limits too: no tool may read or change them, and Grep may not search a directory holding
one unless its `glob` or `type` filter skips every one of them (dbt packages ship sample dotenv
files, which would otherwise close the whole project to Grep). Glob still lists them, as it shows
names, not contents.

A few files outside the workspace may be named `readable` (the run's task file, ticket 32): Read
may open exactly those paths and nothing beside them; Write, Edit, Glob and Grep get no exception.
Read and Grep may also open the files Claude Code saves an over-long tool output in, under
`~/.claude/projects/<project>/tool-results/`: they hold our own tools' output.
"""

import fnmatch
import os
from collections.abc import Sequence
from pathlib import Path

from claude_agent_sdk import HookCallback, HookContext, HookInput, HookJSONOutput

GUARDED_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = ("Write", "Edit")
GLOB_CHARS = frozenset("*?[{")
# File names that usually hold secrets: dotenv and direnv files, login and password files, keys.
CREDENTIAL_NAMES = (
    ".env*",
    ".netrc",
    ".pgpass",
    ".git-credentials",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ecdsa*",
    "id_ed25519*",
)
# The file-name patterns ripgrep's `--type` stands for (`rg --type-list`, ripgrep 14), for the
# types a dbt project is searched with; any other type is not trusted to skip a credential file.
GREP_TYPES = {
    "sql": ("*.psql", "*.sql"),
    "yaml": ("*.yaml", "*.yml"),
    "py": ("*.py", "*.pyi"),
    "md": ("*.markdown", "*.md", "*.mdown", "*.mdwn", "*.mdx", "*.mkd", "*.mkdn"),
    "json": ("*.json", "*.sarif", "composer.lock"),
    "csv": ("*.csv",),
    "txt": ("*.txt",),
}
# Where Claude Code saves a tool output too long to return inline, relative to the home directory.
TOOL_RESULTS = (".claude", "projects", None, "tool-results")


def inside(path: Path, workspace: Path) -> bool:
    return path == workspace or workspace in path.parents


def resolve(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _glob_stays_inside(base: Path, pattern: str, workspace: Path) -> bool:
    """A glob stays inside when its fixed leading part does and nothing after it climbs out."""
    parts = Path(pattern).parts
    fixed = next((i for i, part in enumerate(parts) if GLOB_CHARS & set(part)), len(parts))
    if ".." in parts[fixed:]:
        return False
    return inside(resolve(base, str(Path(*parts[:fixed]))), workspace)


def _protects(protected: Path, path: Path) -> bool:
    """`protected` itself or a file beside it named after it, such as DuckDB's `.wal`."""
    return path.parent == protected.parent and path.name.startswith(protected.name)


def is_credential(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in CREDENTIAL_NAMES)


def _names_credential(base: Path, raw: str) -> bool:
    """`raw` names a credential file, either as given or once symlinks are followed."""
    return is_credential(Path(raw).name) or is_credential(resolve(base, raw).name)


def _credentials_below(directory: Path) -> list[Path]:
    """Every credential file below `directory`, relative to it; symlinked directories are not
    followed, and a credential-named directory counts with the files it holds."""
    found: list[Path] = []
    for root, _, files in os.walk(directory):
        relative = Path(root).relative_to(directory)
        found.extend(
            relative / name
            for name in sorted(files)
            if is_credential(name) or any(is_credential(part) for part in relative.parts)
        )
    return found


def _expand_braces(pattern: str) -> list[str]:
    """`*.{sql,yml}` as the globs it stands for (one level, as Grep's `glob` is written)."""
    start, end = pattern.find("{"), pattern.find("}")
    if start < 0 or end < start:
        return [pattern]
    head, tail = pattern[:start], pattern[end + 1 :]
    return [
        expanded
        for option in pattern[start + 1 : end].split(",")
        for expanded in _expand_braces(f"{head}{option}{tail}")
    ]


def _glob_matches(pattern: str, path: Path) -> bool:
    """Whether ripgrep's `--glob` would include `path` (relative to the search root). A pattern
    without a slash matches the file name at any depth; with one it matches the whole relative
    path. `*` is let match across slashes, so a doubtful pattern counts as matching."""
    target = str(path) if "/" in pattern else path.name
    return any(
        fnmatch.fnmatchcase(target.lower(), expanded.lower())
        for expanded in _expand_braces(pattern)
    )


def _filter_skips(path: Path, glob: str | None, file_type: str | None) -> bool:
    """Grep's `glob` or `type` filter leaves `path` out of the search."""
    if glob:
        negated = glob.startswith("!")
        if _glob_matches(glob.removeprefix("!"), path) == negated:
            return True
    patterns = GREP_TYPES.get(file_type or "")
    return patterns is not None and not any(_glob_matches(p, path) for p in patterns)


def _is_saved_tool_result(path: Path) -> bool:
    """`path` (already resolved) is a file Claude Code saved a tool output in, or that folder."""
    try:
        parts = path.relative_to(Path.home().resolve()).parts
    except ValueError:
        return False
    return len(parts) >= len(TOOL_RESULTS) and all(
        expected in (None, part) for expected, part in zip(TOOL_RESULTS, parts, strict=False)
    )


def _saved_results_root(path: Path) -> Path:
    return Path(*path.parts[: path.parts.index("tool-results") + 1])


def outside_paths(
    tool: str,
    tool_input: dict[str, object],
    workspace: Path,
    protected: Sequence[Path] = (),
    readable: Sequence[Path] = (),
) -> list[str]:
    """The path arguments of one tool call that reach outside `workspace` (already resolved), that
    reach a credential file, or that Write or Edit would use to change a `protected` file (already
    resolved). Read may also open a `readable` file (already resolved); Read and Grep a saved tool
    result."""
    bad: list[str] = []
    match tool:
        case "Read":
            raw = str(tool_input.get("file_path", ""))
            path = resolve(workspace, raw)
            if not raw or (
                path not in readable
                and not _is_saved_tool_result(path)
                and (not inside(path, workspace) or _names_credential(workspace, raw))
            ):
                bad.append(raw)
        case "Write" | "Edit":
            raw = str(tool_input.get("file_path", ""))
            path = resolve(workspace, raw)
            if (
                not raw
                or not inside(path, workspace)
                or any(_protects(p, path) for p in protected)
                or _names_credential(workspace, raw)
            ):
                bad.append(raw)
        case "Glob" | "Grep":
            raw_base = tool_input.get("path")
            base = resolve(workspace, str(raw_base)) if raw_base else workspace
            # A saved tool result holds no credentials, so only the boundary changes.
            if tool == "Grep" and _is_saved_tool_result(base):
                boundary = _saved_results_root(base)
            else:
                boundary = workspace
                if not inside(base, workspace):
                    bad.append(str(raw_base))
                elif tool == "Grep":
                    bad.extend(_grep_credentials(workspace, base, raw_base, tool_input))
            pattern = tool_input.get("pattern" if tool == "Glob" else "glob")
            if pattern and not _glob_stays_inside(base, str(pattern), boundary):
                bad.append(str(pattern))
        case _:
            pass
    return bad


def _grep_credentials(
    workspace: Path, base: Path, raw_base: object, tool_input: dict[str, object]
) -> list[str]:
    """What a Grep of `base` (inside the workspace) would read of a credential file."""
    shown = str(raw_base or ".")
    if _names_credential(workspace, shown):
        return [shown]
    if not base.is_dir():
        return []
    glob = tool_input.get("glob")
    file_type = tool_input.get("type")
    reached = [
        path
        for path in _credentials_below(base)
        if not _filter_skips(
            path, str(glob) if glob else None, str(file_type) if file_type else None
        )
    ]
    if not reached:
        return []
    names = ", ".join(str(Path(shown) / path) for path in reached)
    return [f"{shown} (holds credential files {names}; add a glob or type that skips them)"]


def workspace_guard(
    workspace: Path, protected: Sequence[Path] = (), readable: Sequence[Path] = ()
) -> HookCallback:
    root = workspace.resolve()
    kept = [path.resolve() for path in protected]
    readable_paths = [path.resolve() for path in readable]

    async def guard(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if hook_input["hook_event_name"] != "PreToolUse":
            return {}
        bad = outside_paths(
            hook_input["tool_name"], hook_input["tool_input"], root, kept, readable_paths
        )
        if not bad:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                # The workspace is the working directory, so the reason need not name its
                # absolute path, which would carry the local user name into the event file.
                "permissionDecisionReason": (
                    "Only files inside the workspace (the working directory) can be used, not "
                    "credential files such as .env (Grep: search a directory without them, or "
                    "pass a glob or type that skips them), and only dbt run may change the "
                    f"database; denied: {', '.join(bad)}"
                ),
            }
        }

    return guard
