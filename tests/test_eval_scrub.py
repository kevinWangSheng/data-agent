"""Edge cases for `python -m evals scrub` beyond the seam C acceptance test (ticket 11)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# Key-shaped values are assembled here so the repository never contains a literal one.
FAKE_KEY = "sk-" + "ab12" * 8


def scrub(directory: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("DATA_AGENT_")}
    return subprocess.run(
        [sys.executable, "-m", "evals", "scrub", str(directory), *args],
        cwd=REPO_ROOT,
        env={**clean, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_scrub_keeps_lookalikes_that_are_not_secrets(tmp_path: Path) -> None:
    text = (
        "dbt 1.10.0, duckdb 1.2.2, sdk 0.2.165; "
        "commit 0123456789abcdef0123456789abcdef01234567; "
        "ran dbt build on db-1 and task-sk-short\n"
    )
    log = tmp_path / "run.log"
    log.write_text(text)
    proc = scrub(tmp_path, "--hostname", "db")
    assert proc.returncode == 0, proc.stderr
    assert log.read_text() == text


def test_scrub_removes_endpoint_with_trailing_slash_and_ip_host(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(
        "POST http://198.51.100.9:8080/v1/messages\n"
        "retry against HTTP://198.51.100.9:8080/v1/messages, then 198.51.100.9.\n"
    )
    proc = scrub(tmp_path, DATA_AGENT_BASE_URL="http://198.51.100.9:8080/")
    assert proc.returncode == 0, proc.stderr
    out = log.read_text()
    assert "198.51.100.9" not in out
    assert out.endswith(".\n")


def test_scrub_matches_hostnames_case_insensitively_including_subdomains(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(
        "ssh BUILD-BOX-7 then curl api.build-box-7:9000 ; build-box-70 is another host\n"
    )
    proc = scrub(tmp_path, "--hostname", "build-box-7")
    assert proc.returncode == 0, proc.stderr
    out = log.read_text()
    assert "BUILD-BOX-7 " not in out and "build-box-7:" not in out
    assert "build-box-70" in out


def test_scrub_keeps_json_valid_and_reports_counts_without_secrets(tmp_path: Path) -> None:
    events = tmp_path / "trial" / "agent" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps({"seq": 0, "type": "error", "message": f"bad key {FAKE_KEY}"}) + "\n"
    )
    proc = scrub(tmp_path, DATA_AGENT_API_KEY=FAKE_KEY)
    assert proc.returncode == 0, proc.stderr
    assert FAKE_KEY not in proc.stdout + proc.stderr
    assert "1 file" in proc.stdout
    event = json.loads(events.read_text())
    assert FAKE_KEY not in event["message"]
    assert event["message"].startswith("bad key ")


def test_scrub_fails_on_files_it_cannot_read_as_text(tmp_path: Path) -> None:
    (tmp_path / "warehouse.duckdb").write_bytes(b"\xff\xfe\x00binary" + FAKE_KEY.encode())
    proc = scrub(tmp_path)
    assert proc.returncode != 0
    assert "warehouse.duckdb" in proc.stderr


def test_scrub_removes_secrets_right_after_json_escapes(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    output = f"ok\n{FAKE_KEY}\nbuild-box-7\tdone ébuild-box-7"
    events.write_text(json.dumps({"seq": 1, "type": "tool_end", "output": output}) + "\n")
    proc = scrub(tmp_path, "--hostname", "build-box-7")
    assert proc.returncode == 0, proc.stderr
    text = events.read_text()
    assert FAKE_KEY not in text and "build-box-7" not in text
    assert json.loads(text)["output"].startswith("ok\n")


def test_scrub_removes_other_common_token_shapes(tmp_path: Path) -> None:
    # Assembled at runtime so the repository never contains a literal token.
    tokens = [
        "Bearer " + "q7Zx" * 8,
        "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxIn0" + "." + "c2lnbmF0dXJl" * 3,
        "ghp_" + "a1B2" * 9,
        "AKIA" + "Q7ZX" * 4,
    ]
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({"output": "; ".join(tokens) + "; café"}) + "\n")
    proc = scrub(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = events.read_text()
    for token in tokens:
        assert token.split()[-1] not in text
    assert "café" in text


def test_scrub_sees_through_json_escapes_without_breaking_them(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"output": "build\\u002dbox-7 up"}\n{"output": "ok\\node-1 up"}\n')
    result = tmp_path / "result.json"
    result.write_text('{\n  "error": "build\\u002dbox-7 down"\n}\n')
    proc = scrub(tmp_path, "--hostname", "build-box-7", "--hostname", "node-1")
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(line)["output"] for line in events.read_text().splitlines()]
    assert lines == ["[redacted-host] up", "ok\node-1 up"]
    assert json.loads(result.read_text()) == {"error": "[redacted-host] down"}


def test_scrub_decodes_json_nested_in_strings(tmp_path: Path) -> None:
    # A tool that prints JSON: after the outer decode, the inner escape still spells the host.
    inner = '{"error": "build\\u002dbox-7 down", "name": "caf\\u00e9"}'
    lines = '{"a": "build\\u002dbox-7"}\n{"b": "node\\u002d1"}'
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({"output": inner}) + "\n" + json.dumps({"output": lines}) + "\n")
    proc = scrub(tmp_path, "--hostname", "build-box-7", "--hostname", "node-1")
    assert proc.returncode == 0, proc.stderr
    first, second = (json.loads(line)["output"] for line in events.read_text().splitlines())
    assert json.loads(first) == {"error": "[redacted-host] down", "name": "café"}
    assert [json.loads(line) for line in second.split("\n")] == [
        {"a": "[redacted-host]"},
        {"b": "[redacted-host]"},
    ]


def test_scrub_fails_on_unicode_escapes_outside_json(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    text = json.dumps({"output": 'got {"error": "build\\u002dbox-7 down" (truncated'}) + "\n"
    events.write_text(text)
    log = tmp_path / "run.log"
    log.write_text("echo build\\u002dbox-7\n")
    proc = scrub(tmp_path, "--hostname", "build-box-7")
    assert proc.returncode != 0
    assert "events.jsonl" in proc.stderr and "run.log" in proc.stderr
    assert events.read_text() == text


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_scrub_fails_when_a_subdirectory_cannot_be_read(tmp_path: Path) -> None:
    locked = tmp_path / "trial" / "agent"
    locked.mkdir(parents=True)
    (locked / "events.jsonl").write_text(FAKE_KEY + "\n")
    locked.chmod(0)
    try:
        proc = scrub(tmp_path)
    finally:
        locked.chmod(0o755)
    assert proc.returncode != 0
    assert "agent" in proc.stderr


def test_scrub_fails_on_missing_directory(tmp_path: Path) -> None:
    proc = scrub(tmp_path / "nope")
    assert proc.returncode != 0
    assert "nope" in proc.stderr
