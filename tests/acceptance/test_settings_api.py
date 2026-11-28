"""The settings page's backend (.scratch/settings/spec.md "设置的两类键 (S7)", "作用域与权限",
"存储与审计 (S9)"; decisions.md S3, S7, S9, Q8 人定 4, "建 settings 工单时主会话定的细节" 2).

Written by ticket 157. The tests marked TICKET_160 are xfail(strict) until ticket 160 makes them
pass and removes the marker, changing nothing else.

Contract 6, the settings API (ticket 160 builds it, with ticket 159's sign-in and permission
checks; human-reviewed with ticket 157). Scope ids are contract 3's, given as the `scope` query
parameter:

- `GET /api/settings?scope=<id>` -> `{"scope": id, "keys": [{"key", "value", "source", "locked",
  "editable", "kind"}], "sources": [{"name", "kind", "read_only"}], "mismatches": [{"key",
  "file", "db"}]}`. `keys` holds every key of the effective settings in that scope's chain
  (contract 1's `resolve`), deployment keys too, but `[sources.*]` (listed apart in `sources`)
  and secrets (`api_key` and the like): `source` as contract 1 names it, `locked` for a
  key `[policy]` holds, `kind` one of `access`, `behavior`, `deployment`, `editable` whether the
  signed-in person may change it there (contract 1's `can_edit`; never a deployment or locked
  key). `sources` names the deployment's data sources by name, kind and whether a run may only
  read them, never their connection (no path, no URL, no password; S3).
- `PUT /api/settings?scope=<id>` with `{"key", "value"}`: 200 and the store changed, the change
  recorded with the person's platform user id; 403 for a person who may not (a deployment key
  too), the store unchanged; 422 for a value outside `[policy]`, its `detail` naming the key.
- `GET /api/settings/history?scope=<id>` -> `[{"user", "at", "scope", "key", "before", "after",
  "secret"}]`, oldest first.
- `POST /api/settings/use-file?scope=<id>` (test_settings_seed.py).
- `GET /api/options` gains `network_default: bool`, the effective `sandbox.network` the run form
  starts from: false on a cloud server whose config does not turn the network on, true on a local
  one by default (moved here from cloud-deploy ticket 151, its contract item 4).
"""

from pathlib import Path
from typing import Any

import pytest
from settings_support import (
    ADMIN,
    ALICE,
    EVE,
    ORG,
    PG_PASSWORD,
    PG_URL,
    ROOT,
    SALES,
    FakeSlack,
    channel,
    cloud_config_text,
    cloud_env,
    https_client,
    key_of,
    load_config,
    open_store,
    patch_async_web_client,
    platform_user,
    served,
    shop_config,
    signed_in,
    stop_sessions,
)

TICKET_160 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 160: the settings API is not built yet"
)

WAREHOUSE_ENV = "DATA_AGENT_TEST_WAREHOUSE_URL"
EXTRA = (
    '\n[sources.warehouse]\nkind = "postgres"\nenv = "' + WAREHOUSE_ENV + '"\n\n'
    "[policy]\nnetwork = false\n"
)


@pytest.fixture
def slack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSlack:
    stop_sessions(monkeypatch, tmp_path / "home")
    cloud_env(monkeypatch)
    monkeypatch.setenv(WAREHOUSE_ENV, PG_URL)
    fake = FakeSlack()
    patch_async_web_client(monkeypatch, fake)
    return fake


def cloud(tmp_path: Path) -> Path:
    return shop_config(tmp_path, cloud_config_text(extra=EXTRA))


def keys_of(body: Any) -> dict[str, dict[str, Any]]:
    rows = key_of(body, "keys")
    assert isinstance(rows, list), rows
    return {row["key"]: row for row in rows}


def read(client: Any, scope: str) -> dict[str, Any]:
    response = client.get("/api/settings", params={"scope": scope})
    assert response.status_code == 200, f"{scope}: {response.status_code} {response.text}"
    return response.json()


def write(client: Any, scope: str, key: str, value: Any) -> Any:
    return client.put("/api/settings", params={"scope": scope}, json={"key": key, "value": value})


def store_of(tmp_path: Path, config: Path) -> Any:
    return open_store(tmp_path / "srv" / "runs" / "settings.sqlite", load_config([config], {}))


@TICKET_160
def test_a_layer_reads_with_values_sources_locks_and_kinds(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = cloud(tmp_path)
    with served(tmp_path / "srv", monkeypatch, capsys, ["--config", str(config)]) as server:
        store_of(tmp_path, config).set(ORG, "limits.effort", "high", by=platform_user(ROOT))
        client = https_client(server.app())
        signed_in(client, slack, ROOT)
        body = read(client, ORG)
        keys = keys_of(body)
        effort = keys["limits.effort"]
        assert (effort["value"], effort["source"], effort["kind"]) == ("high", "org", "behavior")
        assert effort["editable"] is True and effort["locked"] is False
        network = keys["sandbox.network"]
        assert (network["value"], network["locked"], network["editable"]) == (False, True, False)
        port = keys["server.port"]
        assert (port["kind"], port["editable"]) == ("deployment", False)
        assert keys["roles.main"]["kind"] == "access"

        sources = {row["name"]: row for row in key_of(body, "sources")}
        assert set(sources) == {"shop", "warehouse"}
        assert sources["shop"]["kind"] == "duckdb" and sources["warehouse"]["kind"] == "postgres"
        assert all(isinstance(row["read_only"], bool) for row in sources.values())
        for secret in (PG_URL, PG_PASSWORD, str(tmp_path / "shop.duckdb"), "shop.duckdb"):
            assert secret not in response_text(client, ORG)


def response_text(client: Any, scope: str) -> str:
    return client.get("/api/settings", params={"scope": scope}).text


@TICKET_160
def test_what_a_person_may_edit_follows_who_they_are(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = cloud(tmp_path)
    with served(tmp_path / "srv", monkeypatch, capsys, ["--config", str(config)]) as server:
        alice = https_client(server.app())
        signed_in(alice, slack, ALICE)
        keys = keys_of(read(alice, channel(SALES)))
        assert keys["instructions.text"]["editable"] is True
        assert keys["limits.effort"]["editable"] is True
        assert keys["roles.main"]["editable"] is False
        assert keys["server.port"]["editable"] is False
        assert not [key for key in keys if key.startswith("sources.")]

        admin = https_client(server.app())
        signed_in(admin, slack, ADMIN)
        assert keys_of(read(admin, channel(SALES)))["roles.main"]["editable"] is True


@TICKET_160
def test_a_write_by_someone_allowed_is_stored_and_recorded_and_others_are_refused(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = cloud(tmp_path)
    with served(tmp_path / "srv", monkeypatch, capsys, ["--config", str(config)]) as server:
        app = server.app()
        alice = https_client(app)
        signed_in(alice, slack, ALICE)
        response = write(alice, channel(SALES), "instructions.text", "Answer in euros.")
        assert response.status_code == 200, response.text
        store = store_of(tmp_path, config)
        assert store.get(channel(SALES), "instructions.text") == "Answer in euros."
        assert store.history(channel(SALES))[-1].user == platform_user(ALICE)

        for key, value in (("roles.main", "deepseek-v4-pro"), ("server.port", 9000)):
            response = write(alice, channel(SALES), key, value)
            assert response.status_code == 403, (key, response.text)
            assert store.get(channel(SALES), key) is None
        eve = https_client(app)
        signed_in(eve, slack, EVE)
        response = write(eve, channel(SALES), "instructions.text", "EVE")
        assert response.status_code == 403, response.text

        root = https_client(app)
        signed_in(root, slack, ROOT)
        response = write(root, channel(SALES), "sandbox.network", True)
        assert response.status_code == 422, response.text
        assert "sandbox.network" in response.text
        assert store.get(channel(SALES), "sandbox.network") is None
        assert store.get(channel(SALES), "instructions.text") == "Answer in euros."


@TICKET_160
def test_a_layer_s_history_lists_its_changes(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = cloud(tmp_path)
    with served(tmp_path / "srv", monkeypatch, capsys, ["--config", str(config)]) as server:
        alice = https_client(server.app())
        signed_in(alice, slack, ALICE)
        for text in ("first", "second"):
            assert write(alice, channel(SALES), "instructions.text", text).status_code == 200
        response = alice.get("/api/settings/history", params={"scope": channel(SALES)})
        assert response.status_code == 200, response.text
        rows = response.json()
        assert [(r["user"], r["key"], r["before"], r["after"]) for r in rows] == [
            (platform_user(ALICE), "instructions.text", None, "first"),
            (platform_user(ALICE), "instructions.text", "first", "second"),
        ]
        assert all(r["scope"] == channel(SALES) and r["secret"] is False and r["at"] for r in rows)


@TICKET_160
def test_the_run_form_s_network_default_follows_the_server(
    tmp_path: Path,
    slack: FakeSlack,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    config = shop_config(tmp_path / "c", cloud_config_text())
    with served(tmp_path / "c" / "srv", monkeypatch, capsys, ["--config", str(config)]) as server:
        client = https_client(server.app())
        signed_in(client, slack, ALICE)
        response = client.get("/api/options")
        assert response.status_code == 200, response.text
        assert key_of(response.json(), "network_default") is False

    local = shop_config(tmp_path / "l")
    with served(tmp_path / "l" / "srv", monkeypatch, capsys, ["--config", str(local)]) as server:
        response = TestClient(server.app()).get("/api/options")
        assert response.status_code == 200, response.text
        assert key_of(response.json(), "network_default") is True
