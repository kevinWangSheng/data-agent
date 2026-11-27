"""The effective config is recorded (.scratch/product-config/spec.md "生效配置的记录"; decisions.md
Q6, Q8): the `config` event, and `evals ablate`'s request check.

Written by ticket 141. Every test here is xfail(strict) until ticket 146 makes it pass and removes
the marker, changing nothing else. The seam: `data-agent run --config FILE` in-process, the
session stopped at the SDK's client (tests/acceptance/product_config_support.py); and
`evals.ablate.check_request` called directly.

The contract fixed here (ticket 141 Context contract item 6; for the human review):

1. The event file's second event (after `version`, which keeps its `config` name) is `config`:
   - `config`: the effective config's TOML text (`EffectiveConfig.text`), each `${VAR}` reference
     as written, never its value;
   - `sha256`: the hex sha256 of `config` as UTF-8;
   - `files`: the config files merged, in merge order, as strings;
   - `file_hashes`: `{path: sha256 hex}` for every file under the run's Skill directories, its
     instructions files and its plugin directories, each path as a string of the file's absolute
     path.
   No resolved secret is anywhere in the event file.
2. `evals.ablate.check_request(out, model, split, tasks, agent_kwargs, benchmark=...,
   config_sha256=None)`: `config_sha256` (the evaluation's effective config hash) is part of the
   request kept in `OUT/request.json`; the same `--out` with another hash is refused (`Stop`),
   with the same one it goes on as before.
"""

import hashlib
import inspect
import json
from pathlib import Path

import pytest
from product_config_support import (
    INSTRUCTIONS,
    Capture,
    assert_session,
    event_at,
    events_text,
    fake_home,
    key_of,
    query_args,
    read_events,
    run_cli,
    stop_sessions,
    toml_path,
    write_file,
)
from seam_support import REPO_ROOT

TICKET_146 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ticket 146: the evaluation entries do not read config files yet",
)
pytestmark = TICKET_146

KEY_ENV = "DATA_AGENT_TEST_KEY"
TEST_KEY = "test-" + "Gw59" + "-key"
SKILLS = REPO_ROOT / "skills"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Capture:
    capture = stop_sessions(monkeypatch, fake_home(tmp_path))
    monkeypatch.setenv(KEY_ENV, TEST_KEY)
    return capture


def test_the_second_event_records_the_effective_config(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_file(
        tmp_path / "product.toml",
        '[models.deepseek-flash]\nbase_url = "https://api.deepseek.com/anthropic"\n'
        f'api_key = "${{{KEY_ENV}}}"\n\n'
        f"[instructions]\nfiles = [{toml_path(INSTRUCTIONS)}]\n",
    )
    argv, events_dir = query_args(tmp_path / "run", "--config", str(config))
    assert_session(run_cli(capsys, argv), capture)
    events = read_events(events_dir)

    event_at(events, 0, "version")
    recorded = event_at(events, 1, "config")
    text = key_of(recorded, "config")
    assert f"${{{KEY_ENV}}}" in text
    assert key_of(recorded, "sha256") == hashlib.sha256(text.encode()).hexdigest()
    assert key_of(recorded, "files") == [str(config)]
    hashes = key_of(recorded, "file_hashes")
    skill_files = sorted(path for path in SKILLS.rglob("*") if path.is_file())
    assert skill_files
    for path in skill_files:
        assert hashes.get(str(path)) == sha256(path), f"no or another hash for {path}"
    assert hashes.get(str(INSTRUCTIONS)) == sha256(INSTRUCTIONS)
    assert TEST_KEY not in events_text(events_dir)


def test_a_run_without_a_file_records_the_defaults(
    tmp_path: Path, capture: Capture, capsys: pytest.CaptureFixture[str]
) -> None:
    argv, events_dir = query_args(tmp_path / "run")
    assert_session(run_cli(capsys, argv), capture)
    recorded = event_at(read_events(events_dir), 1, "config")

    assert key_of(recorded, "files") == []
    text = key_of(recorded, "config")
    assert key_of(recorded, "sha256") == hashlib.sha256(text.encode()).hexdigest()


def test_check_request_refuses_another_config_hash_on_the_same_out(tmp_path: Path) -> None:
    from evals import ablate

    assert "config_sha256" in inspect.signature(ablate.check_request).parameters
    out = tmp_path / "out"
    out.mkdir()
    first, other = "a" * 64, "b" * 64

    def check(config_sha256: str) -> Exception | None:
        try:
            ablate.check_request(out, None, "dev", ["task-1"], [], config_sha256=config_sha256)  # pyright: ignore[reportCallIssue]
        except ablate.Stop as exc:
            return exc
        return None

    assert check(first) is None
    assert first in json.dumps(json.loads((out / "request.json").read_text()))
    assert check(first) is None
    refused = check(other)
    assert refused is not None, "another config hash went on with the same --out"
