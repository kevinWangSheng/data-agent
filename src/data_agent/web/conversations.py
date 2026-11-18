"""Conversations in the chat backend (.scratch/chat-ui/spec.md decisions 6, 9, 13, 17; the
contract is the docstring of tests/acceptance/test_chat_api.py).

A conversation is the directory `<runs_dir>/conversations/<id>/`, holding `conversation.json` (its
index, replaced whole on every change), `workspace/` (the working directory every turn shares) and
`claude-config/` (where `data-agent run --conversation-dir` keeps the SDK's session record). Each
turn is an ordinary run in `<runs_dir>/<run_id>/`, started by the launcher (one run at a time,
across the run form and every conversation) with `--chat`; from the second turn on it resumes the
session of the latest earlier turn that has one. A message sent while a turn runs goes to that
run's stdin as one JSON line, which the run turns into a steer. A run that is no conversation's
turn (the command line's, the run form's) lists as a conversation of one turn that takes no
follow-up.

Every operation holds the launcher's lock, so a row, a new turn and a steer agree on which run is
going.
"""

import json
import logging
import os
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from data_agent.web.launcher import (
    WORKSPACE,
    Launcher,
    Refused,
    Running,
    check_source,
    new_run_id,
)
from data_agent.web.runs import CONVERSATIONS, RunsDir, is_plain_name
from data_agent.web.sources import DataSource, dump_data_sources

INDEX = "conversation.json"
CLAUDE_CONFIG = "claude-config"
# Every configured source, written into each turn's run directory for `--data-sources`.
SOURCES_FILE = "data-sources.toml"
# A conversation only queries (spec decision 3), with the full configuration.
TASK_TYPE = "query"
CONFIG = "full"

log = logging.getLogger(__name__)


class Conversations:
    def __init__(self, runs: RunsDir, launcher: Launcher, sources: Sequence[DataSource]) -> None:
        self.runs = runs
        self.launcher = launcher
        self.sources = list(sources)
        self.root = runs.path / CONVERSATIONS

    def create(self) -> str:
        """A new conversation with no turns; returns its id."""
        created_at = datetime.now(UTC)
        conversation = new_run_id(created_at)
        directory = self.root / conversation
        (directory / WORKSPACE).mkdir(parents=True)
        (directory / CLAUDE_CONFIG).mkdir()
        write_index(
            directory, {"id": conversation, "created_at": created_at.isoformat(), "turns": []}
        )
        return conversation

    def rows(self) -> list[dict[str, Any]]:
        """Every conversation and every run that is in none, newest first."""
        with self.launcher.lock:
            live = self.launcher.active()
            indexes = self.indexes()
            turns = {turn["run_id"] for index in indexes for turn in index["turns"]}
            rows = [self.conversation_row(index, live) for index in indexes]
            rows += [
                single_row(summary, live)
                for summary in self.runs.summaries()
                if summary["id"] not in turns
            ]
        return sorted(rows, key=lambda row: row["created_at"] or "", reverse=True)

    def row(self, conversation: str) -> dict[str, Any] | None:
        """The conversation's row, or the row of a run that is in no conversation; None for
        anything else."""
        with self.launcher.lock:
            live = self.launcher.active()
            index = self.index(conversation)
            if index is not None:
                return self.conversation_row(index, live)
            if self.is_single_run(conversation):
                summary = self.runs.summary(conversation)
                assert summary is not None
                return single_row(summary, live)
            return None

    def send(self, conversation: str, text: str, network: bool = True) -> dict[str, Any]:
        """A message: a new turn when the conversation has none running, else a steer into the
        running one. Refused (409) while a run of another conversation or of the run form is
        going. A new turn has the web tools (`data-agent run --network`, ticket 98) unless
        `network` is off; a steer goes into the turn as it was started."""
        if not text.strip():
            raise Refused(400, "the message is empty")
        with self.launcher.lock:
            index = self.existing(conversation)
            live = self.launcher.active()
            if live is not None and live.conversation == conversation:
                return self.steer(index, live, text)
            return self.start_turn(index, text, network)

    def cancel(self, conversation: str) -> dict[str, Any]:
        """Stops the conversation's running turn (as the run form's cancel stops a run); returns
        the conversation's row."""
        with self.launcher.lock:
            index = self.existing(conversation)
            live = self.launcher.active()
            if live is None or live.conversation != conversation:
                raise Refused(409, f"conversation {conversation} has no turn running")
            self.launcher.cancel_locked(live.run_id)
            return self.conversation_row(index, self.launcher.active())

    def start_turn(self, index: dict[str, Any], text: str, network: bool) -> dict[str, Any]:
        self.launcher.check_configured()
        for source in self.sources:
            check_source(source)
        directory = self.root / index["id"]
        workspace = directory / WORKSPACE
        number = len(index["turns"]) + 1
        resume = self.resume(index)

        def prepare(run_dir: Path) -> tuple[list[str], Path]:
            sources_file = run_dir / SOURCES_FILE
            sources_file.write_text(dump_data_sources(self.sources), encoding="utf-8")
            workspace.mkdir(parents=True, exist_ok=True)
            args = [
                f"--task={text}",
                *("--type", TASK_TYPE, "--config", CONFIG),
                *("--events-dir", str(run_dir), "--workspace", str(workspace)),
                *("--data-sources", str(sources_file)),
                *("--chat", "--conversation-dir", str(directory), "--turn", str(number)),
                *(("--network",) if network else ()),
            ]
            if resume is not None:
                args += ["--resume", resume]
            return args, workspace

        running = self.launcher.launch(text, TASK_TYPE, CONFIG, prepare, index["id"])
        index["turns"].append(
            {"turn": number, "run_id": running.run_id, "message": text, "steers": []}
        )
        write_index(directory, index)
        return {"kind": "turn", "turn": number, "run_id": running.run_id}

    def steer(self, index: dict[str, Any], live: Running, text: str) -> dict[str, Any]:
        """Adds the message to the running turn's steers and writes it to the run's stdin. A run
        that has just stopped reading (it wrote its `result` and is exiting) never gets it; its
        event file then shows no `steer_delivered` for it."""
        [turn] = [turn for turn in index["turns"] if turn["run_id"] == live.run_id]
        message = {"id": str(uuid.uuid4()), "text": text}
        turn["steers"].append(message)
        write_index(self.root / index["id"], index)
        if live.stdin is not None:
            live.stdin.send((json.dumps(message) + "\n").encode())
        return {"kind": "steer", "run_id": live.run_id, "id": message["id"]}

    def resume(self, index: dict[str, Any]) -> str | None:
        """The session of the latest turn whose event file has one (a cancelled turn counts)."""
        for turn in reversed(index["turns"]):
            session = self.runs.session_id(turn["run_id"])
            if session is not None:
                return session
        return None

    def existing(self, conversation: str) -> dict[str, Any]:
        index = self.index(conversation)
        if index is not None:
            return index
        if self.is_single_run(conversation):
            raise Refused(400, f"{conversation} is a single run; it takes no follow-up")
        raise Refused(404, f"no conversation {conversation}")

    def is_single_run(self, run_id: str) -> bool:
        if self.runs.summary(run_id) is None:
            return False
        return all(turn["run_id"] != run_id for index in self.indexes() for turn in index["turns"])

    def index(self, conversation: str) -> dict[str, Any] | None:
        """The conversation's index; None when there is no such conversation, ValueError when the
        file does not parse or lacks what a row needs."""
        if not is_plain_name(conversation):
            return None
        path = self.root / conversation / INDEX
        if not path.is_file():
            return None
        index = json.loads(path.read_text(encoding="utf-8"))
        if not (
            isinstance(index, dict)
            and {"id", "created_at", "turns"} <= index.keys()
            and isinstance(index["turns"], list)
        ):
            raise ValueError(f"{path}: not a conversation index")
        return cast(dict[str, Any], index)

    def indexes(self) -> list[dict[str, Any]]:
        """Every conversation's index. One that does not parse is left out (and the log says
        so), not the whole list."""
        if not self.root.is_dir():
            return []
        indexes: list[dict[str, Any]] = []
        for entry in self.root.iterdir():
            try:
                index = self.index(entry.name)
            except ValueError as exc:
                log.warning("conversation %s: unreadable index: %s", entry.name, exc)
                continue
            if index is not None:
                indexes.append(index)
        return indexes

    def conversation_row(self, index: dict[str, Any], live: Running | None) -> dict[str, Any]:
        return {
            "id": index["id"],
            "single_run": False,
            "created_at": index["created_at"],
            "running": live is not None and live.conversation == index["id"],
            "turns": [{**turn, "status": self.status(turn["run_id"])} for turn in index["turns"]],
        }

    def status(self, run_id: str) -> str:
        summary = self.runs.summary(run_id)
        # A turn's directory is made before its process starts; one removed since has failed.
        return summary["status"] if summary is not None else "failure"


def single_row(summary: dict[str, Any], live: Running | None) -> dict[str, Any]:
    run_id = summary["id"]
    return {
        "id": run_id,
        "single_run": True,
        "created_at": summary["started_at"],
        "running": live is not None and live.run_id == run_id,
        "turns": [
            {
                "turn": 1,
                "run_id": run_id,
                "message": summary["task"],
                "steers": [],
                "status": summary["status"],
            }
        ],
    }


def write_index(directory: Path, index: dict[str, Any]) -> None:
    """Replaces the index whole, so a reader never sees half of it."""
    path = directory / INDEX
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
