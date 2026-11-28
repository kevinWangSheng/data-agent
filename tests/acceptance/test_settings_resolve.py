"""The effective settings of a run (.scratch/settings/spec.md "设置的两类键 (S7)", "作用域与权限",
"临时覆盖 (S4)"; "Testing Decisions" 生效设置解析; decisions.md S2, S4, S7, Q8 "`[policy]` 和
命令行参数冲突: 报错拒绝", "建 settings 工单时主会话定的细节" 5, 11, 16).

Written by ticket 157. The tests marked TICKET_158 are xfail(strict) until ticket 158 makes them
pass and removes the marker, changing nothing else.

Contract 1, the store and the resolution (human-reviewed with ticket 157):

- `data_agent.settings.store`:
  - `open_store(path, config, *, version=None) -> Store`: the store in the sqlite file `path`
    (one file, spec S9). A file that does not exist is made and its org layer seeded from the
    settings keys `config`'s files set (deployment keys never); an existing file is never seeded
    again, and one of an older schema version is upgraded (`version`: make or upgrade the file
    only up to that version, for checking a migration). The version is the file's
    `PRAGMA user_version`; `SCHEMA_VERSION` is the current one.
  - `Store.set(scope, key, value, *, by)`: one scope's override of one settings key, typed as
    `config.toml`'s key (ticket 143's schema), recorded in the change history with `by` (a
    platform user id, contract 2). A deployment key, or a key no schema has, is refused with
    `SettingsError` naming it.
  - `Store.get(scope, key)` (None when the scope does not set it), `Store.overrides(scope) ->
    dict[str, Any]` (dotted keys), `Store.history(scope) -> list[Change]`, `Store.version() ->
    int`.
  - `SettingsError(ValueError)`, its `key` the dotted key it is about.
- `data_agent.settings.resolve`:
  - `Where(workspace=None, channel=None, user=None)`, scope ids (contract 3). With a channel the
    chain is org < workspace < channel and the user is left out (S2); without one (a direct chat,
    a run from the web) org < workspace < user.
  - `resolve(config, store, where, overrides=None, pinned=None) -> Settings`, once per run:
    code defaults < the config's files < the store (the chain) < `overrides` (a message's or a
    thread's, dotted keys); `[policy]` over everything. `store` None: the files alone. A value of
    the store or of `overrides` outside `[policy]`, or a source selection naming no
    `[sources.*]`, is refused with `SettingsError` naming the key, never changed to the locked
    value (decisions.md Q8). May be raised by `Store.set` instead, where the store can tell.
  - `Settings.value(key)`; `Settings.source(key)`: `default`, `file`, `org`, `workspace`,
    `channel`, `user`, `override` or `policy` (locked); `Settings.mismatches`: one
    `Mismatch(key, file, db)` per settings key the files and the store give different values
    (test_settings_seed.py); `Settings.pin() -> Pinned`, the thread's Skills and instructions
    (`skills.*`, `instructions.*`) as they are when the thread starts, which a later
    `resolve(..., pinned=)` keeps whatever the store says then (S4).

The keys (S7; ticket 143's schema, plus the new ones named here, which ticket 158 adds unless the
ticket that needs them first does):
- settings keys, access and spend: `roles.*`, `mcp.*`, `skills.*`, `plugins.*`,
  `sandbox.network`, `limits.*` but `limits.effort`, and the new `chat.sources` (the
  `[sources.*]` names a scope may use; unset: all) and `chat.mcp` (the `[mcp.servers.*]` names;
  unset: all);
- settings keys, behaviour: `instructions.*` with the new `instructions.text` (a scope's own
  instructions), `limits.effort`, and the new `chat.require_mention` (bool, default true;
  ticket 161);
- deployment keys, file and environment only: everything else, `[server]` (new: `db_path`,
  `org_admins`, `public_url`), `[sources.*]`, `[models.*]`, `[policy]` (new:
  `max_budget_usd`, the most `limits.max_budget_usd` may be; `behavior_admins_only`, ticket 159),
  `[im.*]`, `[sandbox]` but `network`, `[layers]`, `[prompts]`, `[trace]`.

Contract 2: `[server].db_path` defaults to `<runs_dir>/settings.sqlite` for `data-agent web`; a
`data-agent run` reads the store only when its config writes `[server].db_path` and the file is
there (then its org layer). `[server].org_admins` and every change record name people as
`<platform>:<user id>` (`slack:U0ROOT001`, `telegram:42424242`, `discord:990001`).

Contract 3, the scope ids: `org`; `workspace:<platform>:<id>` (a Slack team id, a Discord server
id, a Telegram bot's id); `channel:<platform>:<workspace id>:<chat id>`;
`user:<platform>:<user id>`. Overrides are dotted keys to values; `Pinned` is opaque.

`config show` prints the store's org layer by default; `--channel <scope id>` and `--user <scope
id>` show that scope's chain, each value's source after it (`# org`, `# channel`, ...). Decision
11 also names `--workspace`, which `config show` already takes as a directory (ticket 143,
tests/acceptance/test_config_cli.py); ticket 157 hands that clash back instead of testing it.
"""

from pathlib import Path
from typing import Any

import pytest
from settings_support import (
    ALICE,
    ORG,
    channel,
    load_config,
    open_store,
    platform_user,
    query_args,
    raised,
    run_cli,
    run_config_leaves,
    server_config_probe,
    settings_attr,
    settings_error,
    shop_config,
    stop_sessions,
    toml_path,
    user,
    workspace,
)

TICKET_158 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 158: the settings store is not built yet"
)

BY = platform_user("U0ROOT001")


def resolve(config: Any, store: Any, where: Any, **kwargs: Any) -> Any:
    return settings_attr("resolve", "resolve")(config, store, where, **kwargs)


def where(**scopes: str) -> Any:
    return settings_attr("resolve", "Where")(**scopes)


def store_and_config(tmp_path: Path, text: str = "", seed: str = "") -> tuple[Any, Any]:
    """A store made from a config with `seed` (its first start), and the config as it is now,
    with `text`: the file edited since, as on a live deployment."""
    settings_attr("resolve", "resolve")
    first = load_config([shop_config(tmp_path / "first", seed)], {})
    store = open_store(tmp_path / "settings.sqlite", first)
    return store, load_config([shop_config(tmp_path / "now", text)], {})


def check(settings: Any, key: str, value: Any, source: str) -> None:
    assert settings.value(key) == value, f"{key}: {settings.value(key)!r}, expected {value!r}"
    assert settings.source(key) == source, f"{key}: from {settings.source(key)!r}, not {source}"


@TICKET_158
def test_the_layers_stack_in_order_and_each_value_names_its_source(tmp_path: Path) -> None:
    store, config = store_and_config(
        tmp_path,
        '[limits]\nmax_budget_usd = 0.9\neffort = "low"\n\n[limits.max_turns]\nlarge = 70\n\n'
        "[policy]\nnetwork = false\n",
    )
    store.set(ORG, "limits.max_budget_usd", 0.8, by=BY)
    store.set(ORG, "limits.effort", "medium", by=BY)
    store.set(workspace(), "limits.effort", "high", by=BY)
    store.set(ORG, "instructions.text", "ORG", by=BY)
    store.set(workspace(), "instructions.text", "WORKSPACE", by=BY)
    store.set(channel(), "instructions.text", "CHANNEL", by=BY)
    store.set(channel(), "roles.main", "deepseek-v4-pro", by=BY)
    settings = resolve(
        config,
        store,
        where(workspace=workspace(), channel=channel()),
        overrides={"roles.main": "deepseek-flash"},
    )
    check(settings, "limits.max_turns.dbt", 200, "default")
    check(settings, "limits.max_turns.large", 70, "file")
    check(settings, "limits.max_budget_usd", 0.8, "org")
    check(settings, "limits.effort", "high", "workspace")
    check(settings, "instructions.text", "CHANNEL", "channel")
    check(settings, "roles.main", "deepseek-flash", "override")
    check(settings, "sandbox.network", False, "policy")


@TICKET_158
def test_in_a_channel_the_personal_layer_is_left_out(tmp_path: Path) -> None:
    store, config = store_and_config(tmp_path)
    store.set(workspace(), "instructions.text", "WORKSPACE", by=BY)
    store.set(user(ALICE), "instructions.text", "ALICE-ONLY", by=platform_user(ALICE))
    store.set(user(ALICE), "limits.effort", "high", by=platform_user(ALICE))

    direct = resolve(config, store, where(workspace=workspace(), user=user(ALICE)))
    check(direct, "instructions.text", "ALICE-ONLY", "user")
    check(direct, "limits.effort", "high", "user")

    in_channel = resolve(
        config, store, where(workspace=workspace(), channel=channel(), user=user(ALICE))
    )
    check(in_channel, "instructions.text", "WORKSPACE", "workspace")
    assert in_channel.value("limits.effort") != "high"
    assert in_channel.source("limits.effort") != "user"


@TICKET_158
def test_another_workspace_or_channel_is_not_in_the_chain(tmp_path: Path) -> None:
    store, config = store_and_config(tmp_path)
    store.set(workspace("T0GLOBEX2"), "instructions.text", "GLOBEX", by=BY)
    store.set(channel("C0ENG0002"), "instructions.text", "ENG", by=BY)
    store.set(channel(), "limits.effort", "high", by=BY)
    settings = resolve(config, store, where(workspace=workspace(), channel=channel()))
    check(settings, "limits.effort", "high", "channel")
    assert settings.value("instructions.text") not in ("GLOBEX", "ENG")


@TICKET_158
def test_without_a_store_the_files_alone_count(tmp_path: Path) -> None:
    settings_attr("resolve", "resolve")
    config = load_config([shop_config(tmp_path, '[limits]\neffort = "low"\n')], {})
    settings = resolve(config, None, where())
    check(settings, "limits.effort", "low", "file")
    check(settings, "limits.max_turns.query", 50, "default")


# --- [policy] ---------------------------------------------------------------------------------


@TICKET_158
def test_a_policy_lock_is_applied_last(tmp_path: Path) -> None:
    store, config = store_and_config(tmp_path, "[policy]\nnetwork = false\n")
    settings = resolve(config, store, where(workspace=workspace(), channel=channel()))
    check(settings, "sandbox.network", False, "policy")


OUT_OF_POLICY = [
    pytest.param(
        "[policy]\nnetwork = false\n", "store", "sandbox.network", True, id="network-in-store"
    ),
    pytest.param(
        "[policy]\nnetwork = false\n", "override", "sandbox.network", True, id="network-override"
    ),
    pytest.param(
        '[policy]\ndisabled_models = ["deepseek-v4-pro"]\n',
        "store",
        "roles.main",
        "deepseek-v4-pro",
        id="disabled-model-in-store",
    ),
    pytest.param(
        '[policy]\ndisabled_models = ["deepseek-v4-pro"]\n',
        "override",
        "roles.main",
        "deepseek-v4-pro",
        id="disabled-model-override",
    ),
    pytest.param(
        "[policy]\nmax_budget_usd = 2.0\n",
        "store",
        "limits.max_budget_usd",
        5.0,
        id="budget-over-policy-in-store",
    ),
    pytest.param(
        "[policy]\nmax_budget_usd = 2.0\n",
        "override",
        "limits.max_budget_usd",
        5.0,
        id="budget-over-policy-override",
    ),
]


@TICKET_158
@pytest.mark.parametrize(("policy", "where_set", "key", "value"), OUT_OF_POLICY)
def test_a_value_outside_the_policy_is_refused_naming_the_key(
    tmp_path: Path, policy: str, where_set: str, key: str, value: Any
) -> None:
    store, config = store_and_config(tmp_path, policy)
    # Within the policy first: the same key at an allowed value resolves.
    allowed = {"sandbox.network": False, "roles.main": "deepseek-flash"}.get(key, 1.0)
    check(
        resolve(config, store, where(workspace=workspace()), overrides={key: allowed}),
        key,
        allowed,
        "policy" if key == "sandbox.network" else "override",
    )

    def attempt() -> Any:
        if where_set == "store":
            store.set(channel(), key, value, by=BY)
            return resolve(config, store, where(workspace=workspace(), channel=channel()))
        return resolve(config, store, where(workspace=workspace()), overrides={key: value})

    settings_error(raised(attempt), key)


@TICKET_158
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("sources.shop.path", "/elsewhere/shop.duckdb"),
        ("models.deepseek-flash.base_url", "https://models.example/v1"),
        ("server.port", 9000),
        ("server.org_admins", [platform_user(ALICE)]),
        ("policy.network", True),
        ("im.slack.allowed_users", [ALICE]),
        ("sandbox.backend", "local"),
    ],
)
def test_a_deployment_key_cannot_be_stored(tmp_path: Path, key: str, value: Any) -> None:
    store, _ = store_and_config(tmp_path)
    # A settings key is stored and read back, so the refusal below is the key's kind.
    store.set(ORG, "limits.effort", "high", by=BY)
    assert store.get(ORG, "limits.effort") == "high"
    settings_error(raised(lambda: store.set(ORG, key, value, by=BY)), key)
    assert store.get(ORG, key) is None


@TICKET_158
def test_a_source_selection_names_only_registered_sources(tmp_path: Path) -> None:
    store, config = store_and_config(tmp_path)
    store.set(channel(), "chat.sources", ["shop"], by=BY)
    settings = resolve(config, store, where(workspace=workspace(), channel=channel()))
    check(settings, "chat.sources", ["shop"], "channel")

    def attempt() -> Any:
        store.set(channel(), "chat.sources", ["warehouse"], by=BY)
        return resolve(config, store, where(workspace=workspace(), channel=channel()))

    settings_error(raised(attempt), "chat.sources")


# --- Pinned at the thread's start (S4) ----------------------------------------------------------


@TICKET_158
def test_skills_and_instructions_are_pinned_when_the_thread_starts(tmp_path: Path) -> None:
    store, config = store_and_config(tmp_path)
    store.set(channel(), "instructions.text", "OLD", by=BY)
    store.set(channel(), "skills.disabled", ["data-agent:dbt-write"], by=BY)
    place = where(workspace=workspace(), channel=channel())
    pinned = resolve(config, store, place).pin()

    store.set(channel(), "instructions.text", "NEW", by=BY)
    store.set(channel(), "skills.disabled", [], by=BY)
    store.set(channel(), "limits.effort", "high", by=BY)
    in_thread = resolve(config, store, place, pinned=pinned)
    assert in_thread.value("instructions.text") == "OLD"
    assert in_thread.value("skills.disabled") == ["data-agent:dbt-write"]
    # Only Skills and instructions are pinned: the rest is read again.
    assert in_thread.value("limits.effort") == "high"

    new_thread = resolve(config, store, place)
    assert new_thread.value("instructions.text") == "NEW"
    assert new_thread.value("skills.disabled") == []


# --- data-agent run and config show (details 11, 16) ----------------------------------------------


def run_config_with_store(tmp_path: Path, db_path: bool, make_db: bool) -> tuple[Path, Path]:
    """A config file whose `[limits.max_turns]` gives 40, with `[server].db_path` written when
    `db_path`; a store there (when `make_db`) whose org layer gives 7."""
    db = tmp_path / "data" / "settings.sqlite"
    server = f"[server]\ndb_path = {toml_path(db)}\n\n" if db_path else ""
    path = shop_config(tmp_path, f"{server}[limits.max_turns]\nquery = 40\n")
    if make_db:
        settings_attr("store", "open_store")
        db.parent.mkdir(parents=True, exist_ok=True)
        store = open_store(db, load_config([path], {}))
        store.set(ORG, "limits.max_turns.query", 7, by=BY)
        store.set(channel(), "limits.max_turns.query", 3, by=BY)
    return path, db


@TICKET_158
def test_run_reads_the_org_layer_of_the_store_its_config_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = stop_sessions(monkeypatch, tmp_path / "home")
    capture.probe = server_config_probe
    config, _ = run_config_with_store(tmp_path, db_path=True, make_db=True)
    run = run_cli(capsys, query_args(tmp_path, "--config", str(config)))
    assert capture.sessions, f"no session (exit {run.code!r}): {run.stderr[-2000:]}"
    leaves = capture.only().probed
    assert isinstance(leaves, dict), leaves
    # The org layer's 7, not the file's 40 nor the channel's 3.
    assert leaves.get("limits.max_turns.query") == 7, leaves.get("limits.max_turns.query")


@TICKET_158
def test_run_reads_only_the_file_when_the_config_names_no_store_or_it_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = stop_sessions(monkeypatch, tmp_path / "home")
    capture.probe = server_config_probe
    # A store at the default place of a web server's runs directory (contract 2) is not read.
    cwd = tmp_path / "cwd"
    (cwd / "runs").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    config, _ = run_config_with_store(tmp_path / "a", db_path=False, make_db=False)
    first = load_config([config], {})
    store = open_store(cwd / "runs" / "settings.sqlite", first)
    store.set(ORG, "limits.max_turns.query", 7, by=BY)
    run = run_cli(capsys, query_args(tmp_path / "a", "--config", str(config)))
    assert capture.sessions, f"no session (exit {run.code!r}): {run.stderr[-2000:]}"

    # `[server].db_path` written, the file not there: the file alone, and no file made.
    missing, db = run_config_with_store(tmp_path / "b", db_path=True, make_db=False)
    run = run_cli(capsys, query_args(tmp_path / "b", "--config", str(missing)))
    assert len(capture.sessions) == 2, f"no session (exit {run.code!r}): {run.stderr[-2000:]}"
    for session in capture.sessions:
        assert isinstance(session.probed, dict), session.probed
        assert session.probed.get("limits.max_turns.query") == 40
    assert not db.exists()


def shown(stdout: str) -> dict[str, tuple[str, str]]:
    """`config show`'s lines as key -> (value, source)."""
    lines: dict[str, tuple[str, str]] = {}
    for line in stdout.splitlines():
        if " = " in line and "  # " in line:
            key, rest = line.split(" = ", 1)
            value, source = rest.rsplit("  # ", 1)
            lines[key.strip()] = (value.strip(), source.strip())
    return lines


@TICKET_158
def test_config_show_prints_the_org_layer_and_a_chosen_scopes_chain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_sessions(monkeypatch, tmp_path / "home")
    config, db = run_config_with_store(tmp_path, db_path=True, make_db=True)
    store = open_store(db, load_config([config], {}))
    store.set(user(ALICE), "limits.effort", "high", by=platform_user(ALICE))

    run = run_cli(capsys, ["config", "show", "--config", str(config)])
    assert run.code == 0, run.stderr
    lines = shown(run.stdout)
    assert lines.get("limits.max_turns.query") == ("7", "org"), lines.get("limits.max_turns.query")

    run = run_cli(capsys, ["config", "show", "--config", str(config), "--channel", channel()])
    assert run.code == 0, run.stderr
    assert shown(run.stdout).get("limits.max_turns.query") == ("3", "channel")

    run = run_cli(capsys, ["config", "show", "--config", str(config), "--user", user(ALICE)])
    assert run.code == 0, run.stderr
    lines = shown(run.stdout)
    assert lines.get("limits.effort") == ('"high"', "user"), lines.get("limits.effort")
    assert lines.get("limits.max_turns.query") == ("7", "org")


# --- Runs from the web (detail 5) -----------------------------------------------------------------


@TICKET_158
def test_a_web_run_is_given_the_store_s_settings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local mode (no sign-in): the person is the org admin, of no workspace, so the org layer.
    The chain of a signed-in person (org < their workspace < their own layer) is
    test_settings_login.py's, once ticket 159 knows who they are."""
    from fastapi.testclient import TestClient
    from settings_support import FORM, served

    stop_sessions(monkeypatch, tmp_path / "home")
    runs = tmp_path / "srv" / "runs"
    db = runs / "settings.sqlite"
    config = shop_config(tmp_path, f"[server]\ndb_path = {toml_path(db)}\n")
    runs.mkdir(parents=True)
    store = open_store(db, load_config([config], {}))
    store.set(ORG, "limits.max_turns.query", 7, by=BY)
    store.set(ORG, "instructions.text", "ORG-INSTRUCTIONS", by=BY)
    with served(tmp_path / "srv", monkeypatch, capsys, ["--config", str(config)]) as server:
        web = server.web
        assert web is not None, f"the server did not start (exit {server.code!r}): {server.stderr}"
        with TestClient(web.app) as client:
            # The run form (without `config`, which ticket 144 takes out of it) and a
            # conversation's turn.
            response = client.post("/api/runs", json=FORM)
            assert response.status_code == 201, response.text
            web.finish()
            response = client.post("/api/conversations", json={})
            assert response.status_code == 201, response.text
            conversation = response.json()["id"]
            response = client.post(
                f"/api/conversations/{conversation}/messages", json={"text": "How many orders?"}
            )
            assert response.status_code == 201, response.text
        assert len(web.started) == 2, f"expected two runs, got {len(web.started)}"
        for started in web.started:
            leaves = run_config_leaves(started.args)
            assert leaves.get("limits.max_turns.query") == 7, leaves.get("limits.max_turns.query")
            assert leaves.get("instructions.text") == "ORG-INSTRUCTIONS"
