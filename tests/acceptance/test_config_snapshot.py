"""The evaluation entries' run parameters stay as they were (.scratch/product-config/spec.md
"Testing Decisions": 运行参数快照; decisions.md Q5, Q8), across the product-config migration.

Written by ticket 141. The guard tests (`test_guard_...`) pass now and must keep passing through
tickets 143-148: no ticket edits them or re-records their snapshots. What they prove
(decisions.md "调研后定" 4): the first step of the migration leaves the run parameters as they
were, the old and the new mechanism on the same Skill files. Once ticket 146 is merged that job
is done; after that the main session records the snapshots again when the second step of the
migration or a deliberate Skill change moves them, reviews the diff and notes it in that ticket
(how: product_config_support.py's docstring). The tests marked TICKET_146 are xfail(strict) until
ticket 146 moves the evaluation entries to config files and removes the marker, changing nothing
else.

Each entry is driven the way the evaluation drives it, its session stopped at the SDK's client
(tests/acceptance/product_config_support.py, "截停接缝"):

- data-eng-bench: the command `evals/harbor_agent.py` `_run_command` builds, its `data-agent run`
  words split by shell rules, the trial container's workspace, database and events dir swapped
  for the test's by flag name; a `--config` path in the container maps to the checkout's
  `evals/profiles/` by its part from `profiles/` on (`product_config_support.profile_path`: ticket
  146 installs the files wherever it likes, under a `profiles/` directory). Run in-process.
- LiveSQLBench: `_query_command`, the same way; its Postgres is a placeholder URL (below).
- DAB: `python -m evals dab ... --config <layer>` in-process, on a stand-in DAB checkout.
- jaffle: `python -m evals jaffle ... --config <layer> --case b1_monthly_revenue` in-process; the
  `data-agent run` command it would start is read and run in-process in its environment.

What a snapshot holds (Q5): the system prompt and the task prompt, in full; `tools`,
`allowed_tools`, `disallowed_tools`, `permission_mode`; `mcp_servers` (type, command, arguments,
environment); `skills`, `plugins`, `setting_sources`, `strict_mcp_config`; `max_turns`,
`max_budget_usd`; the CLI's `env`, keys and values; the `version` event's `skill_version`,
`skills`, `config` and `layers`. Normalized only where every run or machine differs
(`product_config_support.text_rules`, paths on path boundaries only): never prompt text, tool
lists, variable names or caps. `skill_version` is recorded as the Skill directory it hashes and
that directory's content hash, ignored files left out (`product_config_support.skill_fingerprint`):
a change to any Skill file, a script too, changes the snapshot.

The recorded entries and layers (`SNAPSHOT_CASES`): each benchmark at the layer it runs (DAB at
`no-context`: its tool server has no context tools, so `full` is refused there), and
data-eng-bench also at `baseline` and `full-old-skill`, the two extremes (every layer off; the
Skill directory swapped). data-eng-bench is the one benchmark where every layer acts (verifiers
run on dbt tasks only), so its extremes cover what the other entries' would.

Ticket 141's readings, for the human review (ticket 141 "spec 里的解读"):

1. The data server's `--config` argument: today an ablation name, after the migration one or more
   config file paths. A snapshot keeps the arguments in order with each `--config VALUE` as one
   `--config <config>` (`config_arg_rule`). Which layers the server runs is pinned by the
   `version` event's `config` and `layers` and by `--no-verifier-tools`, compared as they are.
   Where the values point is asserted apart (`assert_server_configs`): each is the ablation name
   the `version` event's `config` names, or a path under `evals/profiles/` or the temp dir, and
   none is under the fake home or the workspace.
2. LiveSQLBench and jaffle run on Postgres, and `make check` has none. Nothing connects before
   the session except the value dictionary build (a Postgres gateway connects on first use),
   so the build reads the shop DuckDB instead (`dictionary_from_duckdb`); the dictionary's
   content is in no snapshot, only its file's path. The URL is a placeholder.

The TICKET_146 tests: driven the same way, the command an entry gives `data-agent run` has two
`--config`, `.../profiles/<benchmark>.toml` then `.../profiles/layers/<layer>.toml`, the
`version` event's `config` is the layer's name, and the snapshot still equals the recorded one.
DAB runs no `data-agent run`: its event file's `config` event lists the two files instead. And
`data-agent run --config full` (an ablation name) is refused once ticket 146 ends the transition.
lsb-single-shot runs no `data-agent run` and reads only `[models]`/`[roles]` (Q8): ticket 146's
unit tests cover it.
"""

from pathlib import Path

import pytest
from product_config_support import (
    SNAPSHOT_CASES,
    Capture,
    assert_refused_before_session,
    compare_or_record,
    drive_entry,
    event_at,
    fake_home,
    flag_values,
    key_of,
    normalize_text,
    normalized_snapshot,
    query_args,
    run_cli,
    stop_sessions,
)

TICKET_146 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ticket 146: the evaluation entries do not read config files yet",
)
# What each benchmark's base config file is called (spec Q4).
PROFILES = {
    "data-eng-bench": "data-eng-bench",
    "livesqlbench": "livesqlbench",
    "dab": "dab",
}


@pytest.fixture
def capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Capture:
    return stop_sessions(monkeypatch, fake_home(tmp_path))


@pytest.mark.parametrize(("entry", "layer"), SNAPSHOT_CASES)
def test_guard_an_evaluation_entry_gives_the_recorded_parameters(
    entry: str,
    layer: str,
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = drive_entry(entry, layer, tmp_path / "entry", capsys, capture, monkeypatch)
    got = normalized_snapshot(ran.session, ran.version, ran.roots)
    compare_or_record(f"{entry}-{layer}", got)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Each path is replaced once: a placeholder is not searched again for another path.
        ("/work/tmp/x", "<repo>/tmp/x"),
        ("/tmp/work/x", "<tempdir>/work/x"),
        # Only a whole path or a path's first part.
        ("/work/skills/x", "<repo>/skills/x"),
        ("at /work.", "at <repo>."),
        ("/workspace/a", "/workspace/a"),
        ("/work-tree", "/work-tree"),
        ("/work.d", "/work.d"),
        ("/private/tmp/y", "/private/tmp/y"),
        ("sk-secret in /tmp", "<api-key> in <tempdir>"),
        # The longest rule wins wherever it stands in the list (rules added after `text_rules`).
        ("/work/run-1/x", "<run-id>/x"),
    ],
)
def test_guard_normalize_text_replaces_each_path_once(text: str, expected: str) -> None:
    rules = [
        ("/work", "<repo>"),
        ("/tmp", "<tempdir>"),
        ("sk-secret", "<api-key>"),
        ("/work/run-1", "<run-id>"),
    ]
    assert normalize_text(text, rules) == expected


RUN_ENTRIES = [case for case in SNAPSHOT_CASES if case[0] in ("data-eng-bench", "livesqlbench")]


@TICKET_146
@pytest.mark.parametrize(("entry", "layer"), RUN_ENTRIES)
def test_a_harbor_trial_passes_the_base_config_then_the_layer_file(
    entry: str,
    layer: str,
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = drive_entry(entry, layer, tmp_path / "entry", capsys, capture, monkeypatch)
    assert ran.run_args is not None
    configs = flag_values(ran.run_args, "--config")
    assert len(configs) == 2, f"expected two --config, got {configs}"
    assert configs[0].endswith(f"profiles/{PROFILES[entry]}.toml"), configs
    assert configs[1].endswith(f"profiles/layers/{layer}.toml"), configs
    assert key_of(ran.version, "config") == layer
    compare_or_record(f"{entry}-{layer}", normalized_snapshot(ran.session, ran.version, ran.roots))


@TICKET_146
def test_a_jaffle_case_passes_a_config_file_then_the_layer_file(
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = drive_entry("jaffle", "full", tmp_path / "entry", capsys, capture, monkeypatch)
    assert ran.run_args is not None
    configs = flag_values(ran.run_args, "--config")
    assert len(configs) == 2, f"expected two --config, got {configs}"
    assert "/profiles/" in configs[0] and configs[0].endswith(".toml"), configs
    assert configs[1].endswith("profiles/layers/full.toml"), configs
    assert key_of(ran.version, "config") == "full"
    compare_or_record("jaffle-full", normalized_snapshot(ran.session, ran.version, ran.roots))


@TICKET_146
def test_a_dab_session_records_the_base_config_and_the_layer_file(
    tmp_path: Path,
    capture: Capture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = drive_entry("dab", "no-context", tmp_path / "entry", capsys, capture, monkeypatch)
    config = event_at(ran.events, 1, "config")
    files = [str(path) for path in key_of(config, "files")]
    assert len(files) == 2, files
    assert files[0].endswith("profiles/dab.toml"), files
    assert files[1].endswith("profiles/layers/no-context.toml"), files
    assert key_of(ran.version, "config") == "no-context"
    got = normalized_snapshot(ran.session, ran.version, ran.roots)
    compare_or_record("dab-no-context", got)


@TICKET_146
def test_run_refuses_an_ablation_name_as_config(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, _ = query_args(tmp_path, "--config", "full")
    run = run_cli(capsys, argv)
    assert_refused_before_session(run, capture, "--config", "full")
