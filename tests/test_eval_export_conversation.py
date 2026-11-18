"""`python -m evals export --conversation DIR` (ticket 96): a recorded conversation as the online
site's static data. Its index goes into `index.json`, each turn's run into `runs/<run_id>/`, all
scrubbed; a conversation is let through only from local data or a dev task, as a featured run is.

The conversation is the hand-made one of tests/fixtures/chat/conversation/, laid out here as
`data-agent web` keeps it: `<runs>/conversations/<id>/conversation.json` and each turn's run in
`<runs>/<run_id>/`. Its fourth turn was cancelled: the server's `web-run.json` says so."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CHAT = REPO_ROOT / "tests" / "fixtures" / "chat" / "conversation"
INDEX = json.loads((CHAT / "conversation.json").read_text())
CONVERSATION = INDEX["id"]
TURNS = [t["run_id"] for t in INDEX["turns"]]
CANCELLED = TURNS[3]
NOTES = {"title": "Revenue, then customers", "local_data": True}


def export(*args: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("DATA_AGENT_")}
    return subprocess.run(
        [sys.executable, "-m", "evals", "export", *args],
        cwd=REPO_ROOT,
        env=clean,
        capture_output=True,
        text=True,
        timeout=300,
    )


def manifest(tmp_path: Path, dev: list[str], holdout: list[str] | None = None) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"seed": 0, "dev": dev, "holdout": holdout or []}))
    return path


def lay_out(tmp_path: Path, notes: Any = NOTES) -> Path:
    """The fixture conversation in a runs directory; returns the conversation's directory."""
    runs = tmp_path / "runs"
    conversation = runs / "conversations" / CONVERSATION
    conversation.mkdir(parents=True)
    shutil.copyfile(CHAT / "conversation.json", conversation / "conversation.json")
    (conversation / "workspace").mkdir()
    (conversation / "claude-config").mkdir()
    (conversation / "claude-config" / "session.jsonl").write_text('{"secret": "transcript"}\n')
    if notes is not None:
        (conversation / "notes.json").write_text(json.dumps(notes))
    for run_id in TURNS:
        (runs / run_id).mkdir()
        shutil.copyfile(CHAT / run_id / "events.jsonl", runs / run_id / f"run-{run_id}.jsonl")
        (runs / run_id / "data-sources.toml").write_text("[orders]\nkind = 'duckdb'\n")
    (runs / CANCELLED / "web-run.json").write_text(json.dumps({"status": "cancelled"}))
    return conversation


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_the_index_and_every_turns_events_are_exported(tmp_path: Path) -> None:
    out = tmp_path / "site"
    proc = export(
        "--manifest",
        str(manifest(tmp_path, ["a"])),
        "--out",
        str(out),
        "--conversation",
        str(lay_out(tmp_path)),
    )
    assert proc.returncode == 0, proc.stderr

    index = read(out / "index.json")
    assert index["runs"] == []  # the turns are the conversation's, not featured runs
    [entry] = index["conversations"]
    assert entry == {
        "id": CONVERSATION,
        "title": NOTES["title"],
        "created_at": INDEX["created_at"],
        "turns": INDEX["turns"],
    }
    for turn in INDEX["turns"]:
        run_dir = out / "runs" / turn["run_id"]
        assert sorted(p.name for p in run_dir.iterdir()) == ["events.jsonl", "meta.json"]
        source = CHAT / turn["run_id"] / "events.jsonl"
        assert read_events(run_dir / "events.jsonl") == read_events(source)
        meta = read(run_dir / "meta.json")
        assert meta["id"] == turn["run_id"]
        assert meta["title"] == turn["message"]
        assert meta["task_type"] == "query"
        assert meta["featured"] is False
        assert meta["grade"] is None
        assert meta["cancelled"] is (turn["run_id"] == CANCELLED)
        assert meta["failed"] is False
    # Nothing else of the conversation: not its workspace, not the SDK's session record.
    assert sorted(p.name for p in out.iterdir()) == ["eval.json", "index.json", "runs"]
    assert sorted(p.name for p in (out / "runs").iterdir()) == sorted(TURNS)


def test_the_conversation_is_scrubbed(tmp_path: Path) -> None:
    conversation = lay_out(tmp_path)
    home = "/home/alice-ci"
    leaky_message = f"Use {home}/retail.duckdb on build-box-7"
    index = read(conversation / "conversation.json")
    index["turns"][0]["message"] = leaky_message
    (conversation / "conversation.json").write_text(json.dumps(index))
    run_file = tmp_path / "runs" / TURNS[1] / f"run-{TURNS[1]}.jsonl"
    events = read_events(run_file)
    events[1]["task"] = "connect to 203.0.113.7 from build-box-7"
    run_file.write_text("".join(json.dumps(e) + "\n" for e in events))

    out = tmp_path / "site"
    proc = export(
        "--manifest",
        str(manifest(tmp_path, ["a"])),
        "--out",
        str(out),
        "--conversation",
        str(conversation),
        "--hostname",
        "build-box-7",
        "--home",
        home,
    )
    assert proc.returncode == 0, proc.stderr
    written = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
    for secret in (home, "build-box-7", "203.0.113.7"):
        assert secret not in written
    assert read(out / "index.json")["conversations"][0]["turns"][0]["message"] != leaky_message

    # A scrub of the export finds nothing more to change.
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    rescrub = subprocess.run(
        [sys.executable, "-m", "evals", "scrub", str(out), "--hostname", "build-box-7"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert rescrub.returncode == 0, rescrub.stderr
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before


def test_a_turn_cut_off_mid_line_is_exported_without_the_half_line(tmp_path: Path) -> None:
    """A cancel kills the run mid-write: the export writes only the lines that parse, so the site
    (`parseEventLines` runs JSON.parse on every line) can open the conversation, and says how many
    lines it dropped."""
    conversation = lay_out(tmp_path)
    run_file = tmp_path / "runs" / CANCELLED / f"run-{CANCELLED}.jsonl"
    whole = run_file.read_text()
    run_file.write_text(whole + '{"seq": 99, "type": "text", "te')

    out = tmp_path / "site"
    proc = export(
        "--manifest",
        str(manifest(tmp_path, ["a"])),
        "--out",
        str(out),
        "--conversation",
        str(conversation),
    )
    assert proc.returncode == 0, proc.stderr
    lines = (out / "runs" / CANCELLED / "events.jsonl").read_text().split("\n")
    assert lines[-1] == ""  # each line ends with a newline
    source = read_events(CHAT / CANCELLED / "events.jsonl")
    assert [json.loads(line) for line in lines[:-1]] == source
    assert f"{CANCELLED}: dropped 1 event line(s)" in proc.stdout


def refused(tmp_path: Path, conversation: Path, dev: list[str], holdout: list[str]) -> str:
    out = tmp_path / "site"
    proc = export(
        "--manifest",
        str(manifest(tmp_path, dev, holdout)),
        "--out",
        str(out),
        "--conversation",
        str(conversation),
    )
    assert proc.returncode == 2
    assert not out.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".site.export-")]
    return proc.stdout + proc.stderr


def test_a_conversation_of_a_holdout_task_is_refused_by_count(tmp_path: Path) -> None:
    conversation = lay_out(tmp_path, {"title": "t", "task_id": "held-task"})
    said = refused(tmp_path, conversation, ["a"], ["held-task"])
    assert "1 conversation(s) refused" in said
    assert "held-task" not in said
    assert CONVERSATION not in said


def test_a_conversation_without_its_provenance_is_refused(tmp_path: Path) -> None:
    for notes in (None, {"title": "t"}, {"title": "t", "local_data": False}):
        case = tmp_path / str(len(list(tmp_path.iterdir())))
        case.mkdir()
        assert "1 conversation(s) refused" in refused(case, lay_out(case, notes), ["a"], [])


def test_a_conversation_of_a_dev_task_is_exported(tmp_path: Path) -> None:
    conversation = lay_out(tmp_path, {"title": "t", "task_id": "a"})
    out = tmp_path / "site"
    proc = export(
        "--manifest",
        str(manifest(tmp_path, ["a"])),
        "--out",
        str(out),
        "--conversation",
        str(conversation),
    )
    assert proc.returncode == 0, proc.stderr
    assert read(out / "index.json")["conversations"][0]["title"] == "t"


def test_a_turn_still_running_is_refused(tmp_path: Path) -> None:
    conversation = lay_out(tmp_path)
    (tmp_path / "runs" / CANCELLED / "web-run.json").write_text(json.dumps({"status": "running"}))
    said = refused(tmp_path, conversation, ["a"], [])
    assert "turn 4" in said
    assert "not ended" in said


def test_a_failed_turn_is_marked_failed(tmp_path: Path) -> None:
    conversation = lay_out(tmp_path)
    (tmp_path / "runs" / CANCELLED / "web-run.json").write_text(json.dumps({"status": "failure"}))
    out = tmp_path / "site"
    proc = export(
        "--manifest",
        str(manifest(tmp_path, ["a"])),
        "--out",
        str(out),
        "--conversation",
        str(conversation),
    )
    assert proc.returncode == 0, proc.stderr
    meta = read(out / "runs" / CANCELLED / "meta.json")
    assert (meta["cancelled"], meta["failed"]) == (False, True)


def test_a_turn_without_its_run_is_refused(tmp_path: Path) -> None:
    conversation = lay_out(tmp_path)
    shutil.rmtree(tmp_path / "runs" / TURNS[2])
    said = refused(tmp_path, conversation, ["a"], [])
    assert "turn 3" in said


def test_a_turn_that_is_also_a_featured_run_is_refused_as_a_repeated_id(tmp_path: Path) -> None:
    conversation = lay_out(tmp_path)
    featured = tmp_path / "featured" / TURNS[0]
    featured.mkdir(parents=True)
    shutil.copyfile(CHAT / TURNS[0] / "events.jsonl", featured / "events.jsonl")
    (featured / "notes.json").write_text(json.dumps({"title": "t", "local_data": True}))
    out = tmp_path / "site"
    proc = export(
        "--manifest",
        str(manifest(tmp_path, ["a"])),
        "--out",
        str(out),
        "--run",
        str(featured),
        "--conversation",
        str(conversation),
    )
    assert proc.returncode == 2
    assert "repeat" in proc.stderr
    assert not out.exists()


def test_a_turn_run_id_the_scrub_would_change_is_refused(tmp_path: Path) -> None:
    # The scrub rewrites the index's run id but not the directory `runs/<run_id>/`.
    conversation = lay_out(tmp_path)
    leaky = "run.build-box-7"
    (tmp_path / "runs" / TURNS[0]).rename(tmp_path / "runs" / leaky)
    index = read(conversation / "conversation.json")
    index["turns"][0]["run_id"] = leaky
    (conversation / "conversation.json").write_text(json.dumps(index))
    out = tmp_path / "site"
    proc = export(
        "--manifest",
        str(manifest(tmp_path, ["a"])),
        "--out",
        str(out),
        "--conversation",
        str(conversation),
        "--hostname",
        "build-box-7",
    )
    assert proc.returncode == 2
    assert "1 run id(s) hold a value scrubbing removes" in proc.stderr
    assert "build-box-7" not in proc.stdout + proc.stderr
    assert not out.exists()
