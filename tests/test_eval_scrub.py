"""Edge cases for `python -m evals scrub` beyond the seam C acceptance test (ticket 11)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from harbor.models.trajectories import Trajectory

from evals.atif import convert

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


def test_scrub_fails_on_duplicate_json_keys(tmp_path: Path) -> None:
    # json.loads keeps only the last value, so the first one would pass unchecked and unscrubbed.
    events = tmp_path / "events.jsonl"
    text = '{"output": "build-box-7", "output": "ok"}\n'
    events.write_text(text)
    nested = tmp_path / "nested.jsonl"
    nested_text = json.dumps({"output": '{"h": "build\\u002dbox-7", "h": "ok"}'}) + "\n"
    nested.write_text(nested_text)
    proc = scrub(tmp_path, "--hostname", "build-box-7")
    assert proc.returncode != 0
    assert "events.jsonl" in proc.stderr and "nested.jsonl" in proc.stderr
    assert events.read_text() == text
    assert nested.read_text() == nested_text


def test_scrub_treats_object_keys_like_string_values(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({'{"h": "build\\u002dbox-7"}': 1, "build-box-7": 2, "ok": 3}) + "\n"
    )
    stray = tmp_path / "stray.jsonl"
    stray_text = json.dumps({"got build\\u002dbox-7 (truncated": 1}) + "\n"
    stray.write_text(stray_text)
    proc = scrub(tmp_path, "--hostname", "build-box-7")
    assert proc.returncode != 0
    assert "stray.jsonl" in proc.stderr and "events.jsonl" not in proc.stderr
    assert stray.read_text() == stray_text
    keys = list(json.loads(events.read_text()))
    assert keys == ['{"h": "[redacted-host]"}', "[redacted-host]", "ok"]


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


def test_scrub_removes_the_local_home_directory_and_user_name(tmp_path: Path) -> None:
    """Harbor results record host paths (the job and task directories) under the user's home."""
    result = tmp_path / "trial" / "result.json"
    result.parent.mkdir()
    result.write_text(
        json.dumps(
            {
                "trial_uri": "file:///home/alice-dev/jobs/2026-10-16/trial",
                "task": {"path": "/home/alice-dev/data-eng-bench/tasks/dbt-x"},
                "mount": "/Volumes/alice-dev/jobs; /Volumes/alice-developer is someone else",
            },
            indent=2,
        )
    )
    proc = scrub(tmp_path, HOME="/home/alice-dev", USER="alice-dev", LOGNAME="alice-dev")
    assert proc.returncode == 0, proc.stderr
    text = result.read_text()
    assert "/home/alice-dev" not in text
    data = json.loads(text)
    assert "/alice-dev/" not in data["mount"]
    assert "/alice-developer " in data["mount"]
    assert data["task"]["path"].endswith("/data-eng-bench/tasks/dbt-x")


def test_scrub_keeps_json_keys_that_spell_the_user_name(tmp_path: Path) -> None:
    """A user named like a Harbor field (`agent`) must not rename that field; values that are the
    name are still removed."""
    config = tmp_path / "trial" / "config.json"
    config.parent.mkdir()
    original = {"agent": {"import_path": "evals.harbor_agent:DataAgent", "name": "agent"}}
    config.write_text(json.dumps(original, indent=2))
    proc = scrub(tmp_path, HOME="/home/agent", USER="agent", LOGNAME="agent")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(config.read_text()) == {
        "agent": {"import_path": "evals.harbor_agent:DataAgent", "name": "[redacted-user]"}
    }


def test_scrub_fails_on_missing_directory(tmp_path: Path) -> None:
    proc = scrub(tmp_path / "nope")
    assert proc.returncode != 0
    assert "nope" in proc.stderr


def test_scrub_removes_the_homes_and_users_of_the_machine_that_ran_the_job(tmp_path: Path) -> None:
    """A job run on a server and scrubbed elsewhere holds the server's paths, not this machine's."""
    result = tmp_path / "trial" / "result.json"
    result.parent.mkdir()
    result.write_text(
        json.dumps(
            {
                "trial_uri": "file:///home/ci-runner/jobs/2026-10-16/trial",
                "task": {"path": "/srv/bench/tasks/dbt-x", "owner": "/data/bob/tasks"},
            },
            indent=2,
        )
    )
    proc = scrub(tmp_path, "--home", "/home/ci-runner", "--user", "ci-runner", "--user", "bob")
    assert proc.returncode == 0, proc.stderr
    text = result.read_text()
    assert "ci-runner" not in text and "/bob/" not in text
    assert json.loads(text)["task"]["path"] == "/srv/bench/tasks/dbt-x"


def test_scrub_fails_on_host_paths_left_after_scrubbing(tmp_path: Path) -> None:
    """Paths under another machine's home that no rule removed are listed, and the command fails.
    /root is the task container's home (the agent runs as root there), so it is not listed."""
    log = tmp_path / "trial" / "trial.log"
    log.parent.mkdir()
    log.write_text(
        "job dir /home/ci-runner/jobs/x\n"
        "mounted /Users/carol/data-eng-bench\n"
        "cache /root/.cache/uv\n"
        "container /app/home/x/models and /logs/agent; /homeless/x/ and /usr/home/y/ are fine\n"
    )
    proc = scrub(tmp_path)
    assert proc.returncode != 0
    assert "trial.log:1" in proc.stderr and "/home/ci-runner" in proc.stderr
    assert "trial.log:2" in proc.stderr and "/Users/carol" in proc.stderr
    assert "trial.log:3" not in proc.stderr and "trial.log:4" not in proc.stderr


def test_scrub_does_not_report_host_paths_it_removed(tmp_path: Path) -> None:
    log = tmp_path / "trial.log"
    log.write_text("job dir /home/ci-runner/jobs/x and /home/ci-runner\n")
    proc = scrub(tmp_path, "--user", "ci-runner")
    assert proc.returncode == 0, proc.stderr
    assert "/home/[redacted-user]/jobs/x" in log.read_text()


def test_scrub_fails_on_host_paths_hidden_by_json_escapes(tmp_path: Path) -> None:
    """A JSON escape (`\\/`, `\\u002f`) must not hide a host path from the check."""
    result = tmp_path / "result.json"
    result.write_text(
        '{"a": "\\/home\\/remote-user\\/job", "b": "\\u002fUsers\\u002fdave\\u002fx"}'
    )
    nested = json.dumps({"output": '{"path": "\\/home\\/erin\\/jobs"}'})
    (tmp_path / "events.jsonl").write_text(nested + "\n")
    proc = scrub(tmp_path)
    assert proc.returncode != 0
    assert "/home/remote-user" in proc.stderr
    assert "/Users/dave" in proc.stderr
    assert "/home/erin" in proc.stderr


def test_scrub_removes_the_user_name_as_a_word_in_values_and_text(tmp_path: Path) -> None:
    """Not only as a path segment: a JSON value or a log line can name the user outright."""
    events = tmp_path / "trial" / "events.jsonl"
    events.parent.mkdir()
    events.write_text(
        json.dumps({"worker": "ci-runner", "ci-runner": 1, "note": "running as CI-Runner."}) + "\n"
    )
    log = tmp_path / "trial" / "trial.log"
    log.write_text("running as ci-runner on ci-runners and xci-runner\n")
    proc = scrub(tmp_path, "--user", "ci-runner")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(events.read_text()) == {
        "worker": "[redacted-user]",
        "ci-runner": 1,
        "note": "running as [redacted-user].",
    }
    assert log.read_text() == "running as [redacted-user] on ci-runners and xci-runner\n"


def test_scrub_fails_on_user_names_left_after_scrubbing(tmp_path: Path) -> None:
    """Keys are kept, so a tool's JSON output keyed by the name keeps it: listed, and the command
    fails. A key of the file's own JSON that spells the name is not listed."""
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({"ci-runner": 1}) + "\n")
    output = tmp_path / "output.jsonl"
    output.write_text(json.dumps({"output": '{"ci-runner": "caf\\u00e9"}'}) + "\n")
    proc = scrub(tmp_path, "--user", "ci-runner")
    assert proc.returncode != 0
    assert "output.jsonl" in proc.stderr and "ci-runner" in proc.stderr
    assert "events.jsonl" not in proc.stderr


def test_scrub_cleans_every_field_of_an_atif_trajectory_and_keeps_it_valid(
    tmp_path: Path,
) -> None:
    endpoint = "https://llm.build-box-7.example/v1"
    events = [
        {"type": "version", "git_commit": "abc", "model": "m", "config": "/home/ci-runner/cfg"},
        {"type": "thinking", "text": "the database is at 198.51.100.9"},
        {"type": "text", "text": "Connecting from build-box-7."},
        {
            "type": "tool_start",
            "tool": "Bash",
            "tool_use_id": "c1",
            "input": {"command": f"curl -H 'Authorization: Bearer {FAKE_KEY}' {endpoint}"},
        },
        {
            "type": "tool_end",
            "tool_use_id": "c1",
            "content": [{"type": "text", "text": f"POST {endpoint} failed for key {FAKE_KEY}"}],
        },
        {"type": "verifier_start", "verifier": "verifier", "agent_id": "a1"},
        {
            "type": "verifier_end",
            "verifier": "verifier",
            "agent_id": "a1",
            "report": "Source: /home/ci-runner/job/models/x.sql",
        },
        {"type": "error", "source": "sdk", "error": "lost 198.51.100.9 via build-box-7"},
    ]
    trajectory = convert(events, instruction="fix it on build-box-7", session_id="run-1")
    assert trajectory is not None
    path = tmp_path / "trial" / "agent" / "trajectory.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(trajectory.to_json_dict(), indent=2))
    proc = scrub(
        tmp_path,
        "--hostname",
        "build-box-7",
        "--home",
        "/home/ci-runner",
        "--user",
        "ci-runner",
        DATA_AGENT_API_KEY=FAKE_KEY,
        DATA_AGENT_BASE_URL=endpoint,
    )
    assert proc.returncode == 0, proc.stderr
    out = path.read_text()
    for leak in (FAKE_KEY, endpoint, "build-box-7", "198.51.100.9", "/home/ci-runner"):
        assert leak not in out
    scrubbed = Trajectory.model_validate_json(out)
    assert len(scrubbed.steps) == len(trajectory.steps)
    assert scrubbed.steps[0].message == "fix it on [redacted-host]"
