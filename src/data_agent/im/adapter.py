"""The common part of the IM entry (.scratch/im/spec.md "会话映射", "澄清、进度、取消", "权限",
"出错"; contract items 2, 3 and 9 of ticket 130): one `Adapter` per platform.

It drives the chat backend only through `Conversations.create`, `send` and `cancel`, and reads a
turn's progress from its event file through `RunsDir`, as the web UI does. It writes no event and
no `conversation.json`; its own state is the thread map. Nothing it logs, writes or sends carries
a token: it is never given one (`config.PlatformConfig` goes to the platform implementation).

The adapter never sleeps and starts no task of its own: a handler (`on_message`, `on_press`)
returns once every call it makes is done, and `poll` makes one pass over the turns it follows.
`run` is the loop that calls `poll` every `POLL_SECONDS`; the clock it is given (seconds, only
ever read) times the edits.

Conversations (contract item 3):

- The key is platform + chat id + thread id. A message under a key with no conversation yet
  calls `create()` once, then `send()` with that id; under a key with one, only `send()`.
- The map is `<runs-dir>/im/threads.json` (`IM_DIR`, `THREADS_FILE`), replaced whole on every
  change and read back when an adapter starts, so a restart keeps it. It is no `*.jsonl` file,
  so `RunsDir` lists no run for `im/`.
- `Incoming.new_conversation` (Telegram's `/new`) drops the key's conversation; the next message,
  or the command's own text when not blank, starts another.

Who gets an answer:

- A direct message, always. In a channel or group: a message that mentions the bot, or any
  message in a thread the bot is in (a mapped key whose thread id is not None). A group without
  threads (Telegram) needs the mention every time; otherwise the bot would answer every message
  of the group after its first.
- Only the users of the allowed list (`allowed_users`) make the bot do anything: a turn, a steer,
  `stop`, `/new`, a button press. Anyone else, in a direct chat, gets one reply that gives their
  user id, and nothing else happens; in a channel or group, nothing at all (no reply, no call).

A turn:

- `send()` answering `{"kind": "turn", ...}`: on a platform that edits, a status message goes
  into the thread (with a Stop button, when the platform has buttons) and is edited as the turn's
  events come (`progress.status_text`), at most once per `Limits.edit_interval`. When the turn
  ends, its status shows how it ended, with duration and cost, and the Stop button is gone. Not
  fixed: whether that last edit waits out the interval.
- `{"kind": "steer", ...}`: no new status message; the running turn's carries on. Not fixed:
  whether the bot acknowledges the steer.
- A `clarification` event: one message with the question and its options numbered (`1. ...`
  on a line each); with buttons, one per option showing only its number. Pressing button k is
  `send()` of option k's text; a typed reply in the thread is `send()` of what was typed.
- The answer (`answer.text`): sent after the turn ends, as `format.format_answer` splits it.
- A turn that ended without an answer:
  - after a clarification (`result` is `success`, no `answer`): the status shows it waits for
    the answer, and nothing more is sent;
  - a failed `result` (the model call failed, the budget or the turn limit ran out, or another
    error): one reply saying which, taken from `result` (`terminal_reason`, `subtype`,
    `api_error`), with the run's id, where the web UI shows it;
  - no `result`, and `RunsDir.ended` (the record says `cancelled` or `failure`): one reply
    saying it was stopped, or that it failed, with the run's id.
- Without edits there is no status message: the clarification, the answer or the failure reply
  only.

Stopping:

- The Stop button, or a message whose text is `stop` (`STOP`: after taking out the mention and
  the surrounding blanks, in any case; "stop the query" is an ordinary message), calls
  `cancel()` of the key's conversation and never `send()`. With no turn running (`cancel()`
  refused, or no conversation under the key) the bot replies that nothing is running.

Refusals: `send()` refused (`Refused`: 409 while another run goes, one run at a time across the
web UI and every conversation; 400 when no data source is configured, `--im` without
`--data-sources`): one reply in the thread with the refusal's `detail`. A conversation just
created for it stays mapped, so the next message in the thread goes to it and no empty
conversation is left for each try.

The bot writes in English, as the web UI does.
"""

import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from data_agent.im.platform import Incoming, Platform, Press
from data_agent.web.runs import RunsDir

# Under the runs directory: the adapter's own files, beside the runs and `conversations/`.
IM_DIR = "im"
THREADS_FILE = "threads.json"
STOP = "stop"
# How often `run` calls `poll`.
POLL_SECONDS = 0.5

Clock = Callable[[], float]
"""Seconds, from any start; only differences count (`time.monotonic`)."""


class ConversationsLike(Protocol):
    """What the adapter uses of `data_agent.web.conversations.Conversations`."""

    def create(self) -> str: ...

    def send(self, conversation: str, text: str, network: bool = True) -> dict[str, Any]: ...

    def cancel(self, conversation: str) -> dict[str, Any]: ...


class Adapter:
    def __init__(
        self,
        platform: Platform,
        conversations: ConversationsLike,
        runs: RunsDir,
        allowed_users: Iterable[str],
        clock: Clock = time.monotonic,
    ) -> None:
        """Reads the thread map back from `runs`. The conversation calls are synchronous and
        hold the launcher's lock, so the adapter makes them in a worker thread."""
        raise NotImplementedError("ticket 131")

    async def start(self) -> None:
        """`platform.start` with this adapter's handlers."""
        raise NotImplementedError("ticket 131")

    async def on_message(self, message: Incoming) -> None:
        raise NotImplementedError("ticket 131")

    async def on_press(self, press: Press) -> None:
        raise NotImplementedError("ticket 131")

    async def poll(self) -> None:
        """One pass over the turns the adapter follows: reads each one's new events
        (`RunsDir.read` from where it stopped) and sends or edits what they call for; a turn
        that has ended (a `result`, or `RunsDir.ended`) is followed no more."""
        raise NotImplementedError("ticket 131")

    async def run(self) -> None:
        """`poll` every `POLL_SECONDS`, until cancelled; an error is logged, not raised."""
        raise NotImplementedError("ticket 131")
