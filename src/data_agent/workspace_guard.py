"""Keep the file tools (Read, Glob, Grep, and Write, Edit in dbt runs) inside the task workspace.

The SDK's allow list says which tools may run, not where they may look, so a `PreToolUse` hook
checks every path argument: resolved against the workspace, with symlinks followed, it must stay
inside the workspace, or the call is denied. Write and Edit may also not touch a protected file:
the database, which only `dbt run` may change. Credential files inside the workspace (`.env*`, keys)
are off limits too: no tool may read or change them, and Grep may not search a directory holding
one. Glob still lists them, as it shows names, not contents.
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


def _holds_credentials(directory: Path) -> bool:
    """Any credential file below `directory`; symlinked directories are not followed."""
    for _, dirs, files in os.walk(directory):
        if any(is_credential(name) for name in [*dirs, *files]):
            return True
    return False


def outside_paths(
    tool: str, tool_input: dict[str, object], workspace: Path, protected: Sequence[Path] = ()
) -> list[str]:
    """The path arguments of one tool call that reach outside `workspace` (already resolved), that
    reach a credential file, or that Write or Edit would use to change a `protected` file (already
    resolved)."""
    bad: list[str] = []
    match tool:
        case "Read":
            raw = str(tool_input.get("file_path", ""))
            if (
                not raw
                or not inside(resolve(workspace, raw), workspace)
                or _names_credential(workspace, raw)
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
            if not inside(base, workspace):
                bad.append(str(raw_base))
            elif tool == "Grep" and (
                _names_credential(workspace, str(raw_base or "."))
                or (base.is_dir() and _holds_credentials(base))
            ):
                bad.append(str(raw_base or "."))
            pattern = tool_input.get("pattern" if tool == "Glob" else "glob")
            if pattern and not _glob_stays_inside(base, str(pattern), workspace):
                bad.append(str(pattern))
        case _:
            pass
    return bad


def workspace_guard(workspace: Path, protected: Sequence[Path] = ()) -> HookCallback:
    root = workspace.resolve()
    kept = [path.resolve() for path in protected]

    async def guard(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if hook_input["hook_event_name"] != "PreToolUse":
            return {}
        bad = outside_paths(hook_input["tool_name"], hook_input["tool_input"], root, kept)
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
                    "credential files such as .env (Grep: search a directory without them), and "
                    f"only dbt run may change the database; denied: {', '.join(bad)}"
                ),
            }
        }

    return guard
