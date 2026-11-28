"""Loading the layered config (.scratch/product-config/spec.md "Testing Decisions": 配置加载;
"配置文件与分层"; decisions.md Q1, Q8).

Written by ticket 141. Every test here is xfail(strict) until ticket 143 builds the loader and
removes the marker, changing nothing else. `src/data_agent/config.py` is ticket 141's empty shell:
it returns an empty config, so each test fails on an assertion.

The contract fixed here (ticket 141 Context contract item 1; for the human review):

1. `data_agent.config.load_effective_config(paths, environ, cli_overrides, workspace=None)
   -> EffectiveConfig`.
   - `paths`: the explicit config files (`data-agent run --config`, repeatable), in order; later
     files win. Given any, only they are read: no user-level, no project-level file, and
     `DATA_AGENT_CONFIG` is ignored. With none, `environ["DATA_AGENT_CONFIG"]` when set is the one
     file read. Otherwise the user-level `<environ["HOME"]>/.data-agent/config.toml` and the
     project-level `<workspace>/.data-agent/config.toml` (each when it exists), in that order.
   - `environ`: the process environment, read for `HOME`, `DATA_AGENT_CONFIG`, the `${VAR}`
     references and the two model overrides (`DATA_AGENT_MODEL` sets `roles.main`;
     `DATA_AGENT_BASE_URL` sets `models.<main model>.base_url`).
   - `cli_overrides`: dotted keys (`"limits.max_budget_usd"`, `"limits.max_turns.dbt"`) to values,
     from the command line, over every other layer.
   - Precedence: code defaults < user-level < project-level (or the explicit files) <
     environment < `cli_overrides`. Merged by section and key: a key set by a later layer is
     replaced whole; lists are not concatenated.
   - `[policy]` (`network`, `dbt_target`, `disabled_models`) is over all: the first file that sets
     a policy key holds it (the user-level file before the project-level one; the first explicit
     file before the later ones), and no later file changes it. `policy.network = false` locks
     `sandbox.network` to false. A locked key's source is `policy`. A `cli_overrides` value
     against a policy raises (decisions.md Q8: refused, not silently changed).
2. `EffectiveConfig`: `value(key)` (the resolved value, `${VAR}` expanded; None when the key is
   unset), `source(key)` (`"default"`, a file's path as `str(path)`, `"env:<VAR>"`, `"cli"`,
   `"policy"`), `files` (the files merged, in merge order, as given), `text` (the effective
   config as TOML, every `${VAR}` reference as written, never its value) and `sha256` (the hex
   sha256 of `text` as UTF-8).
3. `data_agent.config.ConfigFileError` (a ValueError) for every config file error, with `path`
   (the file) and `key` (the dotted key); its message names both, as `<file>: <key>: <reason>`,
   and never holds a secret's value. Errors: an unknown section or key; a plaintext secret (a key
   ending in `api_key`, `token`, `password` or `secret` whose value is not a `${VAR}` reference,
   or any value a gitleaks rule of the repo's would catch, such as `.gitleaks.toml`'s
   `deepseek-api-key`); its reason tells the user to write `${VAR}`; a `${VAR}` reference to an
   unset variable, whose message names the variable too. Keys that hold a variable's name
   (`[im.<platform>].bot_token_env`, `[sources.<name>].env`) are not secrets.
"""

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from product_config_support import toml_path, write_config, write_file

from data_agent import config as product_config

TICKET_143 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 143: the config loader is not built yet"
)
pytestmark = TICKET_143

# Placeholders, built from parts so no scanner takes them for real keys.
TEST_KEY = "test-" + "Kd83" + "-key"
# What `.gitleaks.toml`'s deepseek-api-key rule matches (`\bsk-[0-9a-f]{32}\b`).
DEEPSEEK_LIKE = "sk-" + "0123456789abcdef" * 2
PLAIN_TOKEN = "ghp" + "_" + "Zq1x" * 9
PLAIN_PASSWORD = "hunter2-" + "pl41n"
PLAIN_SECRET = "plain-" + "s3cr" + "-value"


def load(
    paths: Sequence[Path] = (),
    environ: Mapping[str, str] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    workspace: Path | None = None,
) -> product_config.EffectiveConfig:
    return product_config.load_effective_config(
        list(paths), dict(environ or {}), dict(cli_overrides or {}), workspace=workspace
    )


def load_error(
    paths: Sequence[Path] = (),
    environ: Mapping[str, str] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    workspace: Path | None = None,
) -> product_config.ConfigFileError:
    """The loader's error. Not `pytest.raises`: its `DID NOT RAISE` is no AssertionError."""
    try:
        load(paths, environ, cli_overrides, workspace)
    except product_config.ConfigFileError as exc:
        return exc
    raise AssertionError("the config loaded without an error")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def models(*names: str) -> str:
    return "".join(
        f'[models.{name}]\nbase_url = "https://llm.example/{name}"\n\n' for name in names
    )


def test_each_layer_overrides_the_one_before_and_records_its_source(
    home: Path, workspace: Path
) -> None:
    user = write_config(
        home,
        models("user-model")
        + '[roles]\nmain = "user-model"\n\n'
        + '[limits]\nmax_budget_usd = 2.0\neffort = "low"\nagent_timeout_sec = 50\n',
    )
    project = write_config(
        workspace,
        models("project-model")
        + '[roles]\nmain = "project-model"\n\n'
        + '[limits]\neffort = "high"\nagent_timeout_sec = 100\n',
    )
    environ = {"HOME": str(home), "DATA_AGENT_MODEL": "deepseek-v4-pro"}
    config = load(
        environ=environ, cli_overrides={"limits.agent_timeout_sec": 200}, workspace=workspace
    )

    assert config.value("limits.max_turns.dbt") == 200
    assert config.source("limits.max_turns.dbt") == "default"
    assert config.value("limits.max_budget_usd") == 2.0
    assert config.source("limits.max_budget_usd") == str(user)
    assert config.value("limits.effort") == "high"
    assert config.source("limits.effort") == str(project)
    assert config.value("roles.main") == "deepseek-v4-pro"
    assert config.source("roles.main") == "env:DATA_AGENT_MODEL"
    assert config.value("limits.agent_timeout_sec") == 200
    assert config.source("limits.agent_timeout_sec") == "cli"
    assert list(config.files) == [user, project]


def test_a_later_layer_replaces_a_list_whole(home: Path, workspace: Path, tmp_path: Path) -> None:
    a, b = tmp_path / "skills-a", tmp_path / "skills-b"
    a.mkdir()
    b.mkdir()
    write_config(home, f"[skills]\ndirs = [{toml_path(a)}]\n")
    project = write_config(workspace, f"[skills]\ndirs = [{toml_path(b)}]\n")
    config = load(environ={"HOME": str(home)}, workspace=workspace)

    assert [str(path) for path in config.value("skills.dirs") or []] == [str(b)]
    assert config.source("skills.dirs") == str(project)


def test_policy_holds_against_every_later_layer(home: Path, workspace: Path) -> None:
    write_config(
        home,
        '[policy]\nnetwork = false\ndbt_target = "scratch"\n'
        'disabled_models = ["deepseek-v4-pro"]\n',
    )
    write_config(
        workspace,
        '[policy]\nnetwork = true\ndbt_target = "task"\ndisabled_models = []\n\n'
        "[sandbox]\nnetwork = true\n",
    )
    config = load(environ={"HOME": str(home)}, workspace=workspace)

    assert config.value("policy.network") is False
    assert config.value("policy.dbt_target") == "scratch"
    assert config.value("policy.disabled_models") == ["deepseek-v4-pro"]
    assert config.value("sandbox.network") is False
    for key in ("policy.network", "policy.dbt_target", "policy.disabled_models", "sandbox.network"):
        assert config.source(key) == "policy", key


def test_a_command_line_value_against_the_policy_is_refused(home: Path) -> None:
    user = write_config(home, "[policy]\nnetwork = false\n")
    error = load_error(environ={"HOME": str(home)}, cli_overrides={"sandbox.network": True})

    assert "policy.network" in str(error)
    assert str(user) in str(error)


def test_explicit_files_are_the_only_ones_read(home: Path, workspace: Path, tmp_path: Path) -> None:
    write_config(home, '[limits]\nmax_budget_usd = 2.0\neffort = "low"\n')
    write_config(workspace, "[limits.max_turns]\ndbt = 3\n")
    a = write_file(tmp_path / "a.toml", '[limits]\nmax_budget_usd = 3.0\neffort = "high"\n')
    b = write_file(tmp_path / "b.toml", "[limits]\nmax_budget_usd = 4.0\n")
    config = load([a, b], environ={"HOME": str(home)}, workspace=workspace)

    assert list(config.files) == [a, b]
    assert config.value("limits.max_budget_usd") == 4.0
    assert config.source("limits.max_budget_usd") == str(b)
    assert config.value("limits.effort") == "high"
    assert config.source("limits.effort") == str(a)
    # Neither the user-level nor the project-level value.
    assert config.value("limits.max_turns.dbt") == 200
    assert config.source("limits.max_turns.dbt") == "default"


def test_data_agent_config_is_read_alone_and_loses_to_explicit_files(
    home: Path, workspace: Path, tmp_path: Path
) -> None:
    write_config(home, "[limits.max_turns]\nquery = 9\n")
    named = write_file(tmp_path / "named.toml", "[limits]\nmax_budget_usd = 5.0\n")
    given = write_file(tmp_path / "given.toml", "[limits]\nmax_budget_usd = 6.0\n")
    environ = {"HOME": str(home), "DATA_AGENT_CONFIG": str(named)}

    by_variable = load(environ=environ, workspace=workspace)
    assert list(by_variable.files) == [named]
    assert by_variable.value("limits.max_budget_usd") == 5.0
    assert by_variable.value("limits.max_turns.query") == 50
    assert by_variable.source("limits.max_turns.query") == "default"

    by_argument = load([given], environ=environ, workspace=workspace)
    assert list(by_argument.files) == [given]
    assert by_argument.value("limits.max_budget_usd") == 6.0


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ('[colours]\nsky = "blue"\n', "colours"),
        ("[limits]\nmax_budget = 2.0\n", "limits.max_budget"),
        (
            '[sources.shop]\nkind = "duckdb"\npath = "shop.duckdb"\nflavour = "x"\n',
            "sources.shop.flavour",
        ),
    ],
)
def test_an_unknown_section_or_key_is_refused_by_file_and_key(
    text: str, key: str, home: Path, tmp_path: Path
) -> None:
    path = write_file(tmp_path / "bad.toml", text)
    error = load_error([path], environ={"HOME": str(home)})

    assert str(path) in str(error)
    assert key in str(error)
    assert error.path == path
    assert error.key == key


def secret_cases() -> list[tuple[str, str, str]]:
    """(file text, the key, the plaintext value)."""
    return [
        (
            f'[models.m]\nbase_url = "https://llm.example/v1"\napi_key = "{TEST_KEY}"\n',
            "models.m.api_key",
            TEST_KEY,
        ),
        (
            '[mcp.servers.gh]\ncommand = "gh-mcp"\nallowedTools = ["*"]\n\n'
            f'[mcp.servers.gh.env]\nDATA_AGENT_MCP_GH_TOKEN = "{PLAIN_TOKEN}"\n',
            "mcp.servers.gh.env.DATA_AGENT_MCP_GH_TOKEN",
            PLAIN_TOKEN,
        ),
        (
            '[mcp.servers.db]\ncommand = "db-mcp"\nallowedTools = ["*"]\n\n'
            f'[mcp.servers.db.env]\nDATA_AGENT_MCP_DB_PASSWORD = "{PLAIN_PASSWORD}"\n',
            "mcp.servers.db.env.DATA_AGENT_MCP_DB_PASSWORD",
            PLAIN_PASSWORD,
        ),
        (
            '[mcp.servers.api]\ncommand = "api-mcp"\nallowedTools = ["*"]\n\n'
            f'[mcp.servers.api.env]\nDATA_AGENT_MCP_API_SECRET = "{PLAIN_SECRET}"\n',
            "mcp.servers.api.env.DATA_AGENT_MCP_API_SECRET",
            PLAIN_SECRET,
        ),
        # Under a key with no secret's name, a value a gitleaks rule catches.
        (
            f'[prompts]\nanswer_rule = "Use the key {DEEPSEEK_LIKE}."\n',
            "prompts.answer_rule",
            DEEPSEEK_LIKE,
        ),
    ]


@pytest.mark.parametrize(("text", "key", "value"), secret_cases())
def test_a_plaintext_secret_is_refused_without_its_value(
    text: str, key: str, value: str, home: Path, tmp_path: Path
) -> None:
    path = write_file(tmp_path / "secret.toml", text)
    error = load_error([path], environ={"HOME": str(home)})

    assert str(path) in str(error)
    assert key in str(error)
    assert "${" in str(error), "the error does not tell the user to write a ${VAR} reference"
    assert value not in str(error)
    assert value not in repr(error.args)


def test_a_variable_name_is_no_secret(home: Path, tmp_path: Path) -> None:
    path = write_file(
        tmp_path / "names.toml",
        '[im.slack]\nbot_token_env = "DATA_AGENT_SLACK_BOT_TOKEN"\n'
        'app_token_env = "DATA_AGENT_SLACK_APP_TOKEN"\nallowed_users = ["U012ABCDEF"]\n\n'
        '[sources.warehouse]\nkind = "postgres"\nenv = "WAREHOUSE_URL"\n',
    )
    config = load([path], environ={"HOME": str(home)})

    assert config.value("im.slack.bot_token_env") == "DATA_AGENT_SLACK_BOT_TOKEN"
    assert config.value("sources.warehouse.env") == "WAREHOUSE_URL"


def test_a_reference_is_expanded_in_the_value_and_kept_in_the_text(
    home: Path, tmp_path: Path
) -> None:
    path = write_file(
        tmp_path / "ref.toml",
        '[models.m]\nbase_url = "https://llm.example/v1"\napi_key = "${DATA_AGENT_TEST_KEY}"\n\n'
        '[roles]\nmain = "m"\n',
    )
    config = load([path], environ={"HOME": str(home), "DATA_AGENT_TEST_KEY": TEST_KEY})

    assert config.value("models.m.api_key") == TEST_KEY
    assert config.source("models.m.api_key") == str(path)
    assert "${DATA_AGENT_TEST_KEY}" in config.text
    assert TEST_KEY not in config.text
    assert config.sha256 == hashlib.sha256(config.text.encode()).hexdigest()
    assert list(config.files) == [path]


def test_a_reference_to_an_unset_variable_is_refused(home: Path, tmp_path: Path) -> None:
    path = write_file(
        tmp_path / "ref.toml",
        '[models.m]\nbase_url = "https://llm.example/v1"\napi_key = "${DATA_AGENT_TEST_KEY}"\n',
    )
    error = load_error([path], environ={"HOME": str(home)})

    assert "DATA_AGENT_TEST_KEY" in str(error)
    assert "models.m.api_key" in str(error)
    assert str(path) in str(error)


def test_the_model_variables_override_the_main_model(home: Path) -> None:
    write_config(home, models("user-model") + '[roles]\nmain = "user-model"\n')
    url = "https://llm.example/override"
    config = load(environ={"HOME": str(home), "DATA_AGENT_MODEL": "deepseek-v4-pro"})
    assert config.value("roles.main") == "deepseek-v4-pro"
    assert config.source("roles.main") == "env:DATA_AGENT_MODEL"

    config = load(environ={"HOME": str(home), "DATA_AGENT_BASE_URL": url})
    assert config.value("roles.main") == "user-model"
    assert config.value("models.user-model.base_url") == url
    assert config.source("models.user-model.base_url") == "env:DATA_AGENT_BASE_URL"


def test_no_file_gives_the_code_defaults(home: Path, workspace: Path) -> None:
    config = load(environ={"HOME": str(home)}, workspace=workspace)

    assert list(config.files) == []
    assert config.value("layers.skill") is True
    assert config.value("layers.verifiers") is True
    assert config.value("layers.context") is True
    assert config.value("skills.tool") is True
    assert config.value("roles.main") == "deepseek-flash"
    assert config.value("limits.max_budget_usd") == 1.0
    assert config.value("limits.max_turns.query") == 50
    assert config.source("layers.skill") == "default"
