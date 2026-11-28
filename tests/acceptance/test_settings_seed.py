"""Seeding, file and store disagreeing, the change history, migrations (.scratch/settings/spec.md
"设置的两类键 (S7)", "存储与审计 (S9)"; "Testing Decisions" 播种与不一致; decisions.md S7, S9).

Written by ticket 157. The tests marked TICKET_158 and TICKET_160 are xfail(strict) until that
ticket makes them pass and removes the marker, changing nothing else.

The store and its functions are contract 1 (test_settings_resolve.py). Here:

- First start: `data-agent web` opens the store at `[server].db_path`, by default
  `<runs_dir>/settings.sqlite` (contract 2). A file that is not there is made and its org layer
  seeded with the settings keys the config files set; no deployment key goes in. A second start
  seeds nothing: what the file says since is not written to the store.
- A settings key the files and the store's org layer give different values: the store's value
  holds, its source is `org`, and `Settings.mismatches` lists `Mismatch(key, file, db)`.
- `Store.history(scope)`: one `Change(user, at, scope, key, before, after, secret)` per write, in
  order; `at` a timezone-aware datetime; a secret key (ticket 143's rule: a name ending in
  `api_key`, `token`, `password`, `secret`, or a credential header) has `secret` true and neither
  value (`before`, `after` None), so no value of it is anywhere in the history.
- `open_store(path, config, version=1)` makes the file at schema version 1; opened again without
  `version`, it is upgraded to `SCHEMA_VERSION`, its data kept. `SCHEMA_VERSION` is more than 1
  (the store's first release already has a migration past version 1, so the upgrade is tested).
- Contract 6 (ticket 160), "use the file's value": `POST /api/settings/use-file?scope=<scope id>`
  with `{"keys": [...]}` writes the files' values of those keys into that scope (recorded as a
  change); organisation admins only, anyone else 403 and the store unchanged.
  `GET /api/settings?scope=<scope id>` lists the scope's keys (test_settings_api.py).
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from settings_support import (
    ALICE,
    MCP_TOKEN,
    MCP_TOKEN_ENV,
    ORG,
    ROOT,
    FakeSlack,
    channel,
    cloud_config_text,
    cloud_env,
    https_client,
    load_config,
    open_store,
    patch_async_web_client,
    platform_user,
    served,
    settings_attr,
    shop_config,
    sqlite_version,
    stop_sessions,
    workspace,
    write_file,
)

TICKET_158 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 158: the settings store is not built yet"
)
TICKET_160 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 160: the settings API is not built yet"
)

SEEDED = '[roles]\nmain = "deepseek-v4-pro"\n\n[limits]\neffort = "high"\n'
EDITED = '[roles]\nmain = "deepseek-v4-pro"\n\n[limits]\neffort = "low"\nmax_budget_usd = 0.5\n'


def resolve(config: Any, store: Any, **where: str) -> Any:
    place = settings_attr("resolve", "Where")(**where)
    return settings_attr("resolve", "resolve")(config, store, place)


def serve_once(
    root: Path,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with served(root, monkeypatch, capsys, ["--config", str(config)]) as server:
        assert server.web is not None, f"did not start (exit {server.code!r}): {server.stderr}"


@TICKET_158
def test_the_first_start_seeds_the_org_layer_and_the_second_does_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_sessions(monkeypatch, tmp_path / "home")
    settings_attr("store", "open_store")
    config = shop_config(tmp_path, SEEDED)
    root = tmp_path / "srv"
    db = root / "runs" / "settings.sqlite"
    assert not db.exists()
    serve_once(root, config, monkeypatch, capsys)
    assert db.is_file(), "the first start made no store at <runs_dir>/settings.sqlite"

    store = open_store(db, load_config([config], {}))
    org = store.overrides(ORG)
    assert org.get("roles.main") == "deepseek-v4-pro"
    assert org.get("limits.effort") == "high"
    assert not [key for key in org if key.startswith(("sources.", "server.", "models."))], org

    # The file edited, the server started again: the store keeps what it had.
    write_file(config, config.read_text().replace(SEEDED, EDITED))
    serve_once(root, config, monkeypatch, capsys)
    again = open_store(db, load_config([config], {}))
    assert again.overrides(ORG) == org


@TICKET_158
def test_a_store_named_by_db_path_is_seeded_there(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_sessions(monkeypatch, tmp_path / "home")
    db = tmp_path / "volume" / "settings.sqlite"
    db.parent.mkdir()
    config = shop_config(tmp_path, f'[server]\ndb_path = "{db}"\n\n{SEEDED}')
    root = tmp_path / "srv"
    serve_once(root, config, monkeypatch, capsys)
    assert db.is_file(), "no store at [server].db_path"
    assert not (root / "runs" / "settings.sqlite").exists()
    store = open_store(db, load_config([config], {}))
    assert store.get(ORG, "limits.effort") == "high"


@TICKET_158
def test_a_file_edited_after_seeding_leaves_the_store_and_is_flagged(tmp_path: Path) -> None:
    settings_attr("resolve", "resolve")
    config = shop_config(tmp_path, SEEDED)
    store = open_store(tmp_path / "settings.sqlite", load_config([config], {}))
    write_file(config, config.read_text().replace(SEEDED, EDITED))
    now = load_config([config], {})
    store = open_store(tmp_path / "settings.sqlite", now)
    assert store.get(ORG, "limits.effort") == "high"

    settings = resolve(now, store)
    assert settings.value("limits.effort") == "high"
    assert settings.source("limits.effort") == "org"
    # A key the store does not set is the file's, with nothing to flag.
    assert settings.value("limits.max_budget_usd") == 0.5
    assert settings.source("limits.max_budget_usd") == "file"
    mismatches = {m.key: (m.file, m.db) for m in settings.mismatches}
    assert mismatches == {"limits.effort": ("low", "high")}, mismatches


@TICKET_158
def test_every_write_is_recorded_and_a_secret_only_as_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MCP_TOKEN_ENV, MCP_TOKEN)
    config = load_config([shop_config(tmp_path)], {MCP_TOKEN_ENV: MCP_TOKEN})
    store = open_store(tmp_path / "settings.sqlite", config)
    before = datetime.now(UTC)
    store.set(channel(), "limits.effort", "high", by=platform_user(ALICE))
    store.set(channel(), "limits.effort", "low", by=platform_user("U0BOB0002"))
    header = "Bearer ${" + MCP_TOKEN_ENV + "}"
    store.set(ORG, "mcp.servers.acme.headers.Authorization", header, by=platform_user(ROOT))
    after = datetime.now(UTC)

    changes = store.history(channel())
    assert [(c.user, c.scope, c.key, c.before, c.after, c.secret) for c in changes] == [
        (platform_user(ALICE), channel(), "limits.effort", None, "high", False),
        (platform_user("U0BOB0002"), channel(), "limits.effort", "high", "low", False),
    ]
    for change in changes:
        assert change.at.tzinfo is not None
        assert before <= change.at <= after
    assert store.history(workspace()) == []

    [secret] = store.history(ORG)
    assert (secret.user, secret.key, secret.secret) == (
        platform_user(ROOT),
        "mcp.servers.acme.headers.Authorization",
        True,
    )
    assert secret.before is None and secret.after is None
    assert MCP_TOKEN_ENV not in repr(store.history(ORG))


@TICKET_158
def test_an_older_store_is_upgraded_and_keeps_its_data(tmp_path: Path) -> None:
    current = settings_attr("store", "SCHEMA_VERSION")
    assert current > 1, f"SCHEMA_VERSION {current}: no older version to upgrade from"
    config = load_config([shop_config(tmp_path)], {})
    db = tmp_path / "settings.sqlite"
    old = open_store(db, config, version=1)
    assert old.version() == 1 and sqlite_version(db) == 1
    old.set(ORG, "limits.effort", "high", by=platform_user(ROOT))

    store = open_store(db, config)
    assert store.version() == current and sqlite_version(db) == current
    assert store.get(ORG, "limits.effort") == "high"


@TICKET_160
def test_an_org_admin_takes_the_files_value_and_no_one_else_can(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from settings_support import signed_in

    stop_sessions(monkeypatch, tmp_path / "home")
    cloud_env(monkeypatch)
    slack = FakeSlack()
    patch_async_web_client(monkeypatch, slack)
    config = shop_config(tmp_path, cloud_config_text(extra="\n" + SEEDED))
    root = tmp_path / "srv"
    serve_once(root, config, monkeypatch, capsys)
    write_file(config, config.read_text().replace(SEEDED, EDITED))
    db = root / "runs" / "settings.sqlite"

    with served(root, monkeypatch, capsys, ["--config", str(config)]) as server:
        with https_client(server.app()) as client:
            signed_in(client, slack, ALICE)
            response = client.post(
                "/api/settings/use-file?scope=org", json={"keys": ["limits.effort"]}
            )
            assert response.status_code == 403, response.text
        assert open_store(db, load_config([config], {})).get(ORG, "limits.effort") == "high"

        with https_client(server.app()) as client:
            signed_in(client, slack, ROOT)
            response = client.get("/api/settings?scope=org")
            assert response.status_code == 200, response.text
            assert [m["key"] for m in response.json()["mismatches"]] == ["limits.effort"]
            response = client.post(
                "/api/settings/use-file?scope=org", json={"keys": ["limits.effort"]}
            )
            assert response.status_code == 200, response.text
            response = client.get("/api/settings?scope=org")
            assert response.status_code == 200, response.text
            body = response.json()
            effort = next(k for k in body["keys"] if k["key"] == "limits.effort")
            assert (effort["value"], effort["source"]) == ("low", "org")
            assert body["mismatches"] == []
    store = open_store(db, load_config([config], {}))
    assert store.get(ORG, "limits.effort") == "low"
    assert store.history(ORG)[-1].user == platform_user(ROOT)
