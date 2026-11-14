"""The channel between a run and the model calls its tool servers make (ticket 70).

A tool server is a subprocess of the CLI, so the run's spend cap (`agent.Spend`) cannot see the
model calls it makes. The run gives the server a directory of its own (`LEDGER_ENV`, outside the
workspace) holding two files:

    run.json     written by the run: its budget, its price per million tokens, and what its own
                 model calls have spent so far; replaced whole on every update
    tools.jsonl  appended by the tool servers: one line per model call, with its tokens

The run adds the tools' tokens to its spend (`cost_usd`, the budget check); a tool stops calling the
model once the run's spend and the tools' together reach the budget.
"""

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

LEDGER_ENV = "DATA_AGENT_SPEND_DIR"
RUN_FILE = "run.json"
TOOLS_FILE = "tools.jsonl"


class ToolTokens(NamedTuple):
    """Tokens of tool model calls, summed. Uncached input includes tokens written to the cache."""

    uncached: int
    cached: int
    output: int


@dataclass(frozen=True)
class RunState:
    """What the run last wrote: its budget and price (US dollars per million tokens), and the spend
    of its own model calls (the tools' not included)."""

    budget_usd: float
    spent_usd: float
    input_price: float
    cached_input_price: float
    output_price: float

    def priced_usd(self, tokens: ToolTokens) -> float:
        return (
            tokens.uncached * self.input_price
            + tokens.cached * self.cached_input_price
            + tokens.output * self.output_price
        ) / 1e6


class Ledger:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = threading.Lock()

    def publish(self, state: RunState) -> None:
        """The run's side: replace run.json whole, so a reader never sees half a file."""
        tmp = self.directory / f".{RUN_FILE}.tmp"
        tmp.write_text(json.dumps(state.__dict__))
        os.replace(tmp, self.directory / RUN_FILE)

    def run_state(self) -> RunState | None:
        """None until the run has written run.json."""
        try:
            return RunState(**json.loads((self.directory / RUN_FILE).read_text()))
        except FileNotFoundError:
            return None

    def record(self, tool: str, tokens: ToolTokens) -> None:
        """The tool's side: one line per model call, appended in a single write."""
        line = json.dumps({"tool": tool, **tokens._asdict()}) + "\n"
        with self._lock:
            fd = os.open(self.directory / TOOLS_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode())
            finally:
                os.close(fd)

    def tool_tokens(self) -> ToolTokens:
        """Every tool model call recorded so far, summed. A last line still being written (no
        newline yet) is left for the next read."""
        try:
            text = (self.directory / TOOLS_FILE).read_text()
        except FileNotFoundError:
            return ToolTokens(0, 0, 0)
        totals = [0, 0, 0]
        for line in text.split("\n")[:-1]:
            entry = json.loads(line)
            for i, name in enumerate(ToolTokens._fields):
                totals[i] += int(entry[name])
        return ToolTokens(*totals)

    def spent_usd(self, state: RunState) -> float:
        """The run's spend with every tool model call so far."""
        return state.spent_usd + state.priced_usd(self.tool_tokens())
