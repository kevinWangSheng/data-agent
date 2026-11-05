"""Read, Glob, Grep, Write and Edit may only reach files inside the task workspace."""

from pathlib import Path
from typing import Any

import pytest

from data_agent import agent
from data_agent.agent import ModelSettings, Task
from data_agent.run_config import load_config
from data_agent.workspace_guard import GREP_TYPES, workspace_guard


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "models").mkdir(parents=True)
    (ws / "models" / "orders.sql").write_text("select 1")
    (tmp_path / "secret.env").write_text("KEY=x")
    (ws / "escape").symlink_to(tmp_path)
    (ws / "secret_link").symlink_to(tmp_path / "secret.env")
    return ws


async def decide(
    workspace: Path,
    tool: str,
    tool_input: dict[str, Any],
    protected: tuple[Path, ...] = (),
    readable: tuple[Path, ...] = (),
) -> str:
    output = await hook_output(workspace, tool, tool_input, protected, readable)
    return output.get("permissionDecision", "none")


async def hook_output(
    workspace: Path,
    tool: str,
    tool_input: dict[str, Any],
    protected: tuple[Path, ...] = (),
    readable: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """The guard's `hookSpecificOutput` for one call; empty when it lets the call run."""
    guard = workspace_guard(workspace, protected, readable)
    hook_input: Any = {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "transcript_path": "t",
        "cwd": str(workspace),
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_use_id": "u",
    }
    output: Any = await guard(hook_input, "u", {"signal": None})
    return output.get("hookSpecificOutput", {})


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "models/orders.sql"}),
        ("Read", {"file_path": "{ws}/models/orders.sql"}),
        ("Glob", {"pattern": "**/*.sql"}),
        ("Glob", {"pattern": "*.sql", "path": "{ws}/models"}),
        ("Glob", {"pattern": "{ws}/models/*.sql"}),
        ("Grep", {"pattern": "select"}),
        ("Grep", {"pattern": "select", "path": "models", "glob": "*.sql"}),
    ],
)
async def test_paths_inside_the_workspace_are_left_to_the_normal_rules(
    workspace: Path, tool: str, tool_input: dict[str, Any]
) -> None:
    filled = {k: v.format(ws=workspace) for k, v in tool_input.items()}
    assert await decide(workspace, tool, filled) == "none"


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "{home}/.ssh/id_ed25519"}),
        ("Read", {"file_path": "{ws}/../secret.env"}),
        ("Read", {"file_path": "../secret.env"}),
        ("Read", {"file_path": "secret_link"}),
        ("Read", {"file_path": "escape/secret.env"}),
        ("Glob", {"pattern": "*", "path": "{home}/.ssh"}),
        ("Glob", {"pattern": "*", "path": ".."}),
        ("Glob", {"pattern": "../*.env"}),
        ("Glob", {"pattern": "{home}/.ssh/*"}),
        ("Glob", {"pattern": "models/../../*.env"}),
        ("Glob", {"pattern": "*", "path": "escape"}),
        ("Grep", {"pattern": "KEY", "path": "{home}/.config"}),
        ("Grep", {"pattern": "KEY", "path": "../"}),
        ("Grep", {"pattern": "KEY", "path": "secret_link"}),
        ("Grep", {"pattern": "KEY", "glob": "../*.env"}),
        ("Grep", {"pattern": "KEY", "glob": "/etc/*"}),
    ],
)
async def test_paths_outside_the_workspace_are_denied(
    workspace: Path, tool: str, tool_input: dict[str, Any]
) -> None:
    filled = {k: v.format(ws=workspace, home=Path.home()) for k, v in tool_input.items()}
    assert await decide(workspace, tool, filled) == "deny"


def test_query_runs_install_the_guard_for_every_file_tool(tmp_path: Path) -> None:
    task = Task(text="q", type="query", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    settings = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})
    options = agent.build_options(task, load_config("full"), settings, [], lambda *_, **__: None)
    matchers = (options.hooks or {}).get("PreToolUse", [])
    guarded = {name for m in matchers if m.matcher for name in m.matcher.split("|")}
    assert set(agent.QUERY_BUILTIN_TOOLS) <= guarded


@pytest.mark.parametrize(
    ("tool", "tool_input", "decision"),
    [
        ("Write", {"file_path": "models/marts/new.sql", "content": "select 1"}, "none"),
        (
            "Edit",
            {"file_path": "{ws}/models/orders.sql", "old_string": "1", "new_string": "2"},
            "none",
        ),
        ("Write", {"file_path": "../secret.env", "content": "x"}, "deny"),
        ("Write", {"file_path": "{home}/.bashrc", "content": "x"}, "deny"),
        ("Write", {"file_path": "escape/secret.env", "content": "x"}, "deny"),
        ("Edit", {"file_path": "secret_link", "old_string": "x", "new_string": "y"}, "deny"),
        ("Write", {"file_path": "shop.duckdb", "content": ""}, "deny"),
        ("Write", {"file_path": "{ws}/shop.duckdb.wal", "content": ""}, "deny"),
        ("Edit", {"file_path": "shop.duckdb", "old_string": "a", "new_string": "b"}, "deny"),
    ],
)
async def test_writes_stay_inside_the_workspace_and_off_the_database(
    workspace: Path, tool: str, tool_input: dict[str, Any], decision: str
) -> None:
    filled = {k: v.format(ws=workspace, home=Path.home()) for k, v in tool_input.items()}
    assert await decide(workspace, tool, filled, (workspace / "shop.duckdb",)) == decision


def test_dbt_runs_guard_file_writes_and_bash(tmp_path: Path) -> None:
    task = Task(text="t", type="dbt", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    settings = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})
    options = agent.build_options(task, load_config("full"), settings, [], lambda *_, **__: None)
    matchers = (options.hooks or {}).get("PreToolUse", [])
    guarded = {name for m in matchers if m.matcher for name in m.matcher.split("|")}
    assert {"Read", "Glob", "Grep", "Write", "Edit", "Bash"} <= guarded
    assert {"Write", "Edit", "Bash"} <= set(options.tools or [])
    query = Task(text="q", type="query", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    query_tools = (
        agent.build_options(query, load_config("full"), settings, [], lambda *_, **__: None).tools
        or []
    )
    assert not {"Write", "Edit", "Bash"} & set(query_tools)


async def test_dbt_runs_let_bash_print_db_type_and_wait_for_validate(tmp_path: Path) -> None:
    """The Bash guard gets the harness's variable list, and Bash's default timeout outlasts
    validate_project.py's 300-second cap, so a slow dbt parse is never cut off mid-run."""
    task = Task(text="t", type="dbt", workspace=tmp_path, duckdb=tmp_path / "x.duckdb")
    settings = ModelSettings.from_env({"DATA_AGENT_API_KEY": "k"})
    options = agent.build_options(task, load_config("full"), settings, [], lambda *_, **__: None)
    assert options.env["BASH_DEFAULT_TIMEOUT_MS"] == "360000"
    assert options.env["BASH_MAX_TIMEOUT_MS"] == "600000"
    # Else the CLI carries a `cd dir && ...` into the next Bash call, which the guard cannot see.
    assert options.env["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] == "1"
    matchers = (options.hooks or {}).get("PreToolUse", [])
    bash_hooks = [hook for m in matchers if m.matcher == "Bash" for hook in m.hooks]
    assert len(bash_hooks) == 1
    hook_input: Any = {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "transcript_path": "t",
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "echo $DB_TYPE"},
        "tool_use_id": "u",
    }
    assert await bash_hooks[0](hook_input, "u", {"signal": None}) == {}


async def test_denial_does_not_write_the_workspace_path(workspace: Path) -> None:
    """The reason reaches the event file, which is published; it names no absolute path."""
    guard = workspace_guard(workspace)
    hook_input: Any = {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "transcript_path": "t",
        "cwd": str(workspace),
        "tool_name": "Read",
        "tool_input": {"file_path": "../secret.env"},
        "tool_use_id": "u",
    }
    output: Any = await guard(hook_input, "u", {"signal": None})
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert "../secret.env" in reason
    assert str(workspace.resolve().parent) not in reason


@pytest.fixture
def with_credentials(workspace: Path) -> Path:
    (workspace / ".env").write_text("DATA_AGENT_API_KEY=x")
    (workspace / "config").mkdir()
    (workspace / "config" / ".env.local").write_text("PGPASSWORD=x")
    (workspace / "config" / "server.pem").write_text("-----BEGIN-----")
    (workspace / "models" / "env_link.sql").symlink_to(workspace / ".env")
    return workspace


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": ".env"}),
        ("Read", {"file_path": "{ws}/config/.env.local"}),
        ("Read", {"file_path": "config/server.pem"}),
        ("Read", {"file_path": "models/env_link.sql"}),
        ("Grep", {"pattern": "KEY", "path": ".env"}),
        ("Grep", {"pattern": "KEY"}),
        ("Grep", {"pattern": "KEY", "path": "config", "glob": "*.local"}),
        # `**/` matches zero directories too, so the root `.env` is reached.
        ("Grep", {"pattern": "KEY", "glob": "**/.env*"}),
        ("Write", {"file_path": ".env", "content": "x"}),
        ("Edit", {"file_path": "config/.env.local", "old_string": "x", "new_string": "y"}),
    ],
)
async def test_credential_files_inside_the_workspace_are_denied(
    with_credentials: Path, tool: str, tool_input: dict[str, Any]
) -> None:
    filled = {k: v.format(ws=with_credentials) for k, v in tool_input.items()}
    assert await decide(with_credentials, tool, filled) == "deny"


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "models/orders.sql"}),
        ("Grep", {"pattern": "select", "path": "models", "glob": "*.sql"}),
        # Glob lists names, not contents.
        ("Glob", {"pattern": "**/*"}),
    ],
)
async def test_files_beside_credentials_stay_readable(
    with_credentials: Path, tool: str, tool_input: dict[str, Any]
) -> None:
    (with_credentials / "models" / "env_link.sql").unlink()
    assert await decide(with_credentials, tool, tool_input) == "none"


@pytest.fixture
def with_sample_env(workspace: Path) -> Path:
    """A dbt project whose package ships sample dotenv files, as dbt_utils does."""
    samples = workspace / "dbt_packages" / "dbt_utils" / "integration_tests" / ".env"
    samples.mkdir(parents=True)
    (samples / "postgres.env").write_text("PGPASSWORD=sample")
    (samples / "duckdb.env").write_text("DUCKDB_PATH=sample")
    (workspace / "models" / "schema.yml").write_text("version: 2")
    return workspace


@pytest.mark.parametrize(
    "tool_input",
    [
        {"pattern": "select", "glob": "*.sql"},
        {"pattern": "select", "glob": "*.{sql,yml}"},
        {"pattern": "select", "glob": "models/**/*.sql"},
        # A path glob whose last segment cannot match the file name keeps nothing of it.
        {"pattern": "select", "glob": "dbt_packages/*.sql"},
        {"pattern": "select", "glob": "!*.env"},
        {"pattern": "select", "type": "sql"},
        {"pattern": "select", "type": "yaml"},
        {"pattern": "select", "path": "dbt_packages", "type": "sql"},
        {"pattern": "select", "type": "env", "glob": "*.sql"},
        {"pattern": "select", "type": "sql", "glob": "!*.env"},
        # Grep passes each whitespace- or comma-separated piece as its own glob.
        {"pattern": "select", "glob": "*.sql *.yml"},
        {"pattern": "select", "glob": "*.sql,*.yml"},
        # ripgrep lets the last matching glob decide.
        {"pattern": "select", "glob": "*.env !*.env"},
        {"pattern": "select", "glob": "!**/*.env"},
        *({"pattern": "select", "type": name} for name in GREP_TYPES),
    ],
)
async def test_grep_runs_when_its_filter_skips_every_credential_file(
    with_sample_env: Path, tool_input: dict[str, Any]
) -> None:
    assert await decide(with_sample_env, "Grep", tool_input) == "none"


@pytest.mark.parametrize(
    "tool_input",
    [
        {"pattern": "PASSWORD"},
        {"pattern": "PASSWORD", "path": "dbt_packages"},
        {"pattern": "PASSWORD", "glob": "*.env"},
        {"pattern": "PASSWORD", "glob": "*"},
        {"pattern": "PASSWORD", "glob": "**/*"},
        {"pattern": "PASSWORD", "glob": "!*.sql"},
        {"pattern": "PASSWORD", "glob": "dbt_packages/**"},
        {"pattern": "PASSWORD", "glob": "**/*.env"},
        {"pattern": "PASSWORD", "type": "env"},
        # An including glob keeps a file whatever `type` says (ripgrep checks globs first).
        {"pattern": "PASSWORD", "type": "sql", "glob": "*.env"},
        {"pattern": "PASSWORD", "glob": "*.sql *.env"},
        {"pattern": "PASSWORD", "glob": "*.sql,*.env"},
        {"pattern": "PASSWORD", "glob": "!*.env *.env"},
        # In a glob with a slash, `*` does not cross a slash, so this skips nothing nested.
        {"pattern": "PASSWORD", "glob": "!dbt_packages/*.env"},
        {"pattern": "PASSWORD", "glob": "!dbt_packages/*/*.env"},
        # Where ripgrep anchors a path glob depends on how the search path was given, so an
        # including one may keep the credential file.
        {"pattern": "PASSWORD", "glob": "*/dbt_packages/**"},
        {"pattern": "PASSWORD", "glob": "dbt_packages/*.env"},
    ],
)
async def test_grep_that_would_read_a_credential_file_is_denied_naming_it(
    with_sample_env: Path, tool_input: dict[str, Any]
) -> None:
    guard = workspace_guard(with_sample_env)
    hook_input: Any = {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "transcript_path": "t",
        "cwd": str(with_sample_env),
        "tool_name": "Grep",
        "tool_input": tool_input,
        "tool_use_id": "u",
    }
    output: Any = await guard(hook_input, "u", {"signal": None})
    specific = output["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    reason = specific["permissionDecisionReason"]
    assert "dbt_packages/dbt_utils/integration_tests/.env/postgres.env" in reason
    assert "glob" in reason and "type" in reason
    assert str(with_sample_env.resolve()) not in reason


@pytest.mark.parametrize(
    ("tool_input", "decision"),
    [
        # Globs are case-sensitive (Grep passes `--glob`), so `!*.env` leaves SNOWFLAKE.ENV in.
        ({"pattern": "KEY", "glob": "!*.env"}, "deny"),
        ({"pattern": "KEY", "glob": "*.env"}, "deny"),
        ({"pattern": "KEY", "glob": "*.sql"}, "none"),
        ({"pattern": "KEY", "type": "sql"}, "none"),
    ],
)
async def test_grep_filters_are_matched_case_sensitively(
    with_sample_env: Path, tool_input: dict[str, Any], decision: str
) -> None:
    upper = with_sample_env / "dbt_packages" / "dbt_utils" / "integration_tests" / ".env"
    (upper / "SNOWFLAKE.ENV").write_text("SNOWFLAKE_PASSWORD=sample")
    assert await decide(with_sample_env, "Grep", tool_input) == decision


@pytest.fixture
def tool_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where Claude Code stores a tool's over-long output, under the home directory."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    saved = tmp_path / "home" / ".claude" / "projects" / "-tmp-ws" / "tool-results"
    saved.mkdir(parents=True)
    (saved / "r.txt").write_text("dbt run output")
    (saved / "escape.txt").symlink_to(tmp_path / "secret.env")
    return saved


@pytest.mark.parametrize(
    ("tool", "tool_input", "decision"),
    [
        ("Read", {"file_path": "~/.claude/projects/-tmp-ws/tool-results/r.txt"}, "none"),
        ("Read", {"file_path": "{saved}/r.txt"}, "none"),
        ("Grep", {"pattern": "dbt", "path": "{saved}/r.txt"}, "none"),
        ("Grep", {"pattern": "dbt", "path": "{saved}"}, "none"),
        ("Grep", {"pattern": "dbt", "path": "{saved}", "glob": "*.txt"}, "none"),
        ("Write", {"file_path": "{saved}/r.txt", "content": "x"}, "deny"),
        ("Edit", {"file_path": "{saved}/r.txt", "old_string": "a", "new_string": "b"}, "deny"),
        ("Read", {"file_path": "{saved}/escape.txt"}, "deny"),
        ("Read", {"file_path": "{saved}/../other.txt"}, "deny"),
        ("Read", {"file_path": "~/.claude/projects/-tmp-ws/memory.md"}, "deny"),
        ("Glob", {"pattern": "*", "path": "{saved}"}, "deny"),
        ("Grep", {"pattern": "dbt", "path": "{saved}", "glob": "../*"}, "deny"),
    ],
)
async def test_saved_tool_results_may_be_read_and_searched_only(
    workspace: Path, tool_results: Path, tool: str, tool_input: dict[str, Any], decision: str
) -> None:
    filled = {k: v.format(saved=tool_results) for k, v in tool_input.items()}
    assert await decide(workspace, tool, filled) == decision


@pytest.fixture
def task_file(tmp_path: Path) -> Path:
    """A task file outside the workspace (`agent.saved_task`), with a file beside it."""
    directory = tmp_path / "task-dir"
    directory.mkdir()
    (directory / "task.md").write_text("Build the model.")
    (directory / "other.md").write_text("not for the agent")
    return directory / "task.md"


@pytest.mark.parametrize(
    ("tool", "tool_input", "decision"),
    [
        ("Read", {"file_path": "{task}"}, "none"),
        ("Read", {"file_path": "{task_dir}/../task-dir/task.md"}, "none"),
        ("Write", {"file_path": "{task}", "content": "x"}, "deny"),
        ("Edit", {"file_path": "{task}", "old_string": "B", "new_string": "R"}, "deny"),
        ("Read", {"file_path": "{task_dir}/other.md"}, "deny"),
        ("Read", {"file_path": "{task_dir}"}, "deny"),
        ("Glob", {"pattern": "*", "path": "{task_dir}"}, "deny"),
        ("Glob", {"pattern": "{task_dir}/*"}, "deny"),
        ("Grep", {"pattern": "B", "path": "{task_dir}"}, "deny"),
        ("Grep", {"pattern": "B", "path": "{task}"}, "deny"),
        ("Grep", {"pattern": "B", "glob": "{task_dir}/*"}, "deny"),
        # The workspace rules are unchanged.
        ("Read", {"file_path": "../secret.env"}, "deny"),
        ("Read", {"file_path": "models/orders.sql"}, "none"),
    ],
)
async def test_the_task_file_may_only_be_read(
    workspace: Path, task_file: Path, tool: str, tool_input: dict[str, Any], decision: str
) -> None:
    filled = {k: v.format(task=task_file, task_dir=task_file.parent) for k, v in tool_input.items()}
    assert await decide(workspace, tool, filled, readable=(task_file,)) == decision


async def test_the_task_file_is_denied_without_being_named_readable(
    workspace: Path, task_file: Path
) -> None:
    assert await decide(workspace, "Read", {"file_path": str(task_file)}) == "deny"


# Ticket 55: in t47-screen the model added `glob: "!**/dbt_packages/**"` and was still denied. A
# glob `**/<dir>/**` matches the directory wherever the search starts, and ripgrep never enters an
# excluded directory, so the credential files in it are not read.
@pytest.mark.parametrize(
    ("tool_input", "decision"),
    [
        ({"pattern": "select", "glob": "!**/dbt_packages/**"}, "none"),
        ({"pattern": "select", "glob": "!**/integration_tests/**"}, "none"),
        ({"pattern": "select", "glob": "!**/dbt_utils/integration_tests/**"}, "none"),
        ({"pattern": "select", "glob": "!**/dbt_*/**"}, "none"),
        ({"pattern": "select", "path": "dbt_packages", "glob": "!**/dbt_utils/**"}, "none"),
        ({"pattern": "select", "glob": "*.sql !**/dbt_packages/**"}, "none"),
        # Another directory excluded: the samples are still searched.
        ({"pattern": "select", "glob": "!**/models/**"}, "deny"),
        ({"pattern": "select", "glob": "!**/dbt_utils/models/**"}, "deny"),
        # Only the directories below the search path count: how Grep hands ripgrep the path
        # itself (as given, absolute, symlinks followed) is not known to the guard.
        ({"pattern": "select", "path": "dbt_packages", "glob": "!**/dbt_packages/**"}, "deny"),
        # A later including glob that matches the directory brings it back (last match wins).
        ({"pattern": "select", "glob": "!**/dbt_packages/** **/dbt_packages/**"}, "deny"),
        ({"pattern": "select", "glob": "!**/dbt_packages/** dbt_packages/**"}, "deny"),
        ({"pattern": "select", "glob": "!**/dbt_packages/**,**/*.env"}, "deny"),
        # Without the leading `**/`, where the glob is anchored depends on the search path.
        ({"pattern": "select", "glob": "!dbt_packages/**"}, "deny"),
        ({"pattern": "select", "glob": "!**/**"}, "deny"),
    ],
)
async def test_grep_excluding_a_whole_directory_skips_the_credentials_in_it(
    with_sample_env: Path, tool_input: dict[str, Any], decision: str
) -> None:
    assert await decide(with_sample_env, "Grep", tool_input) == decision


# Review of ticket 55: ripgrep reads `[^s]` as "not s" and `\.` as an escaped dot, where Python's
# fnmatch reads a class holding `^` and a literal backslash. Such a glob decides nothing: an
# excluding one skips no file, an including one keeps every file.
@pytest.mark.parametrize(
    "glob",
    [
        # fnmatch: `d` is in the class, so the directory is skipped; ripgrep: `d` is excluded.
        "!**/[^d]bt_packages/**",
        # Same for a file name: fnmatch skips postgres.env and duckdb.env, ripgrep neither.
        "!*[^sb].env",
        # ripgrep reads `\e` as `e`, so this keeps both samples; fnmatch matches neither.
        r"*.\env",
        r"!**/dbt\_packages/**",
    ],
)
async def test_grep_globs_ripgrep_reads_differently_skip_nothing(
    with_sample_env: Path, glob: str
) -> None:
    assert await decide(with_sample_env, "Grep", {"pattern": "select", "glob": glob}) == "deny"


async def test_grep_excluding_a_directory_still_denies_a_credential_elsewhere(
    with_sample_env: Path,
) -> None:
    (with_sample_env / "config").mkdir()
    (with_sample_env / "config" / ".env").write_text("PGPASSWORD=x")
    tool_input = {"pattern": "select", "glob": "!**/dbt_packages/**"}
    assert await decide(with_sample_env, "Grep", tool_input) == "deny"


async def test_grep_denial_shows_a_filter_that_would_run(with_sample_env: Path) -> None:
    """Ticket 55: the denial names the files and gives filters the model can copy, and each of them
    does pass the guard."""
    reason = (await hook_output(with_sample_env, "Grep", {"pattern": "select"}))[
        "permissionDecisionReason"
    ]
    assert 'type: "sql"' in reason
    assert 'glob: "!**/dbt_packages/**"' in reason
    for retry in ({"type": "sql"}, {"glob": "!**/dbt_packages/**"}):
        assert await decide(with_sample_env, "Grep", {"pattern": "select", **retry}) == "none"


async def test_grep_denial_offers_no_directory_glob_for_a_top_level_credential(
    with_credentials: Path,
) -> None:
    """A `.env` right in the search path lies in no directory to exclude; `type` still helps."""
    reason = (await hook_output(with_credentials, "Grep", {"pattern": "KEY"}))[
        "permissionDecisionReason"
    ]
    assert 'type: "sql"' in reason
    assert "!**/" not in reason
