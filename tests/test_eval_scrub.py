"""Edge cases for `python -m evals scrub` beyond the seam C acceptance test (ticket 11)."""

import json
import os
import subprocess
import sys
from pathlib import Path

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


def test_scrub_fails_on_missing_directory(tmp_path: Path) -> None:
    proc = scrub(tmp_path / "nope")
    assert proc.returncode != 0
    assert "nope" in proc.stderr
