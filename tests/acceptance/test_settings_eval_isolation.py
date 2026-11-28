"""Evaluations read no settings database (.scratch/settings/spec.md "Testing Decisions" 评测隔离;
ADR-0012 decision 8; ADR-0011 decision 3).

Written by ticket 157. The guard passes now and must keep passing through tickets 158-162; no
ticket edits it. The test marked TICKET_158 is xfail(strict) until ticket 158 removes the marker.

What a settings database could be found by: `[server].db_path`'s default (contract 2,
`<runs_dir>/settings.sqlite`, with `./runs` the default runs directory) and the fake HOME
(`~/.data-agent/settings.sqlite`, `~/settings.sqlite`). No evaluation config file writes
`[server].db_path`, and `data-agent run` reads a database only where the config names one
(decisions.md "建 settings 工单时主会话定的细节" 16). Each evaluation entry, driven as ticket 141's
snapshot tests drive it (tests/acceptance/settings_support.py `drive_entry`, a copy), still gives
the snapshot ticket 141 recorded (tests/fixtures/product_config/snapshots/, read only).
"""

from pathlib import Path

import pytest
from settings_support import (
    ORG,
    SALES,
    SNAPSHOT_CASES,
    Capture,
    channel,
    compare_snapshot,
    drive_entry,
    fake_home,
    load_config,
    normalized_snapshot,
    open_store,
    plain_sqlite,
    platform_user,
    stop_sessions,
    workspace,
    write_file,
)

TICKET_158 = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="ticket 158: the settings store is not built yet"
)

# Settings that would change every evaluation run were they read: the model, the caps, the
# instructions and the network.
SETTINGS = {
    ORG: {"roles.main": "deepseek-v4-pro", "limits.max_turns.query": 3, "sandbox.network": True},
    workspace(): {"limits.max_budget_usd": 0.05, "instructions.text": "WORKSPACE-SETTING-MARKER"},
    channel(SALES): {"limits.max_turns.dbt": 2, "instructions.text": "CHANNEL-SETTING-MARKER"},
}


def database_places(root: Path, home: Path) -> list[Path]:
    """Where a settings database could be looked for: the default `db_path` under `./runs` of
    the directory the entry runs from, and the fake HOME."""
    return [
        root / "cwd" / "runs" / "settings.sqlite",
        home / ".data-agent" / "settings.sqlite",
        home / "settings.sqlite",
    ]


@pytest.mark.parametrize(("entry", "layer"), SNAPSHOT_CASES)
def test_an_evaluation_entry_ignores_a_sqlite_file_where_settings_would_be(
    entry: str,
    layer: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = fake_home(tmp_path)
    capture: Capture = stop_sessions(monkeypatch, home)
    root = tmp_path / "entry"
    for place in database_places(root, home):
        plain_sqlite(place)
    ran = drive_entry(entry, layer, root, capsys, capture, monkeypatch)
    got = normalized_snapshot(ran.session, ran.version, ran.roots)
    compare_snapshot(f"{entry}-{layer}", got)


@TICKET_158
@pytest.mark.parametrize(("entry", "layer"), SNAPSHOT_CASES)
def test_an_evaluation_entry_ignores_settings_in_a_store(
    entry: str,
    layer: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = fake_home(tmp_path)
    capture: Capture = stop_sessions(monkeypatch, home)
    root = tmp_path / "entry"
    config = load_config([write_file(tmp_path / "empty.toml", "")], {})
    for place in database_places(root, home):
        place.parent.mkdir(parents=True, exist_ok=True)
        store = open_store(place, config)
        for scope, values in SETTINGS.items():
            for key, value in values.items():
                store.set(scope, key, value, by=platform_user("U0ROOT001"))
        reopened = open_store(place, config)
        for scope, values in SETTINGS.items():
            for key, value in values.items():
                assert reopened.get(scope, key) == value, f"{place}: {scope} {key} not stored"
    ran = drive_entry(entry, layer, root, capsys, capture, monkeypatch)
    got = normalized_snapshot(ran.session, ran.version, ran.roots)
    compare_snapshot(f"{entry}-{layer}", got)
