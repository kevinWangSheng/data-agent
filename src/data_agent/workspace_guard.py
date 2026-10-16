"""Keep the file tools (Read, Glob, Grep, and Write, Edit in dbt runs) inside the task workspace.

The SDK's allow list says which tools may run, not where they may look, so a `PreToolUse` hook
checks every path argument: resolved against the workspace, with symlinks followed, it must stay
inside the workspace, or the call is denied. Write and Edit may also not touch a protected file:
the database, which only `dbt run` may change.
"""

from collections.abc import Sequence
from pathlib import Path

from claude_agent_sdk import HookContext, HookInput, HookJSONOutput

GUARDED_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = ("Write", "Edit")
GLOB_CHARS = frozenset("*?[{")


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


def outside_paths(
    tool: str, tool_input: dict[str, object], workspace: Path, protected: Sequence[Path] = ()
) -> list[str]:
    """The path arguments of one tool call that reach outside `workspace` (already resolved), or
    that Write or Edit would use to change a `protected` file (already resolved)."""
    bad: list[str] = []
    match tool:
        case "Read":
            raw = str(tool_input.get("file_path", ""))
            if not raw or not inside(resolve(workspace, raw), workspace):
                bad.append(raw)
        case "Write" | "Edit":
            raw = str(tool_input.get("file_path", ""))
            path = resolve(workspace, raw)
            if not raw or not inside(path, workspace) or any(_protects(p, path) for p in protected):
                bad.append(raw)
        case "Glob" | "Grep":
            raw_base = tool_input.get("path")
            base = resolve(workspace, str(raw_base)) if raw_base else workspace
            if not inside(base, workspace):
                bad.append(str(raw_base))
            pattern = tool_input.get("pattern" if tool == "Glob" else "glob")
            if pattern and not _glob_stays_inside(base, str(pattern), workspace):
                bad.append(str(pattern))
        case _:
            pass
    return bad


def workspace_guard(workspace: Path, protected: Sequence[Path] = ()):
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
                "permissionDecisionReason": (
                    f"Only files inside the workspace {root} can be used, and only dbt run may "
                    f"change the database; denied: {', '.join(bad)}"
                ),
            }
        }

    return guard
