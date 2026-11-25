"""IM entry, conversations (.scratch/im/spec.md "会话映射"; "Testing Decisions" 会话映射).

Written by ticket 130. The tests marked TICKET_131 are xfail(strict) until ticket 131 makes them
pass and removes the marker, changing nothing else.

The contract (data_agent.im.adapter, human-reviewed): the key is platform + chat id + thread id;
a key with no conversation yet calls `create()` once, then `send()` with the id it returned; a
key with one calls only `send()`. Telegram's `/new` (`Incoming.new_conversation`) drops the key's
conversation. The map is `<runs-dir>/im/threads.json`, read back by a new adapter on the same
runs directory, and no run as `RunsDir` lists them.
"""

from pathlib import Path

import pytest
from im_support import (
    FakePlatform,
    Sent,
    finish_turn,
    message,
    start_bot,
)

from data_agent.im.adapter import IM_DIR, THREADS_FILE
from data_agent.im.platform import Incoming


@pytest.mark.parametrize("direct", [True, False], ids=["direct", "group-mention"])
async def test_a_new_message_creates_a_conversation_and_sends_to_it(
    tmp_path: Path, direct: bool
) -> None:
    incoming = message(
        "How many orders?",
        direct=direct,
        mentioned=not direct,
        chat="D1" if direct else "C1",
        thread=None if direct else "T1",
    )
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(incoming)
    assert len(bot.conversations.created) == 1
    # The same run options as the web UI's conversations: `send` with its default network.
    assert bot.conversations.sent == [Sent(bot.conversations.created[0], "How many orders?", True)]


async def test_a_reply_in_the_thread_goes_to_its_conversation_another_thread_gets_another(
    tmp_path: Path,
) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    [first] = bot.conversations.created
    finish_turn(bot, bot.conversations.latest_run(first))
    await bot.adapter.poll()

    await bot.platform.deliver(message("And last year?", thread="T1", mentioned=False))
    assert bot.conversations.created == [first]
    assert bot.conversations.sent[-1] == Sent(first, "And last year?", True)

    await bot.platform.deliver(message("Top customers?", thread="T2"))
    assert len(bot.conversations.created) == 2
    second = bot.conversations.created[1]
    assert second != first
    assert bot.conversations.sent[-1] == Sent(second, "Top customers?", True)


async def test_new_conversation_in_a_chat_without_threads_starts_another(tmp_path: Path) -> None:
    platform = FakePlatform("telegram")

    def say(text: str, new: bool = False) -> Incoming:
        return message(
            text,
            platform="telegram",
            chat="4242",
            thread=None,
            direct=True,
            mentioned=False,
            new_conversation=new,
        )

    bot = await start_bot(tmp_path / "runs", platform=platform)
    conversations = bot.conversations
    await platform.deliver(say("How many orders?"))
    finish_turn(bot, conversations.latest_run(conversations.created[0]))
    await bot.adapter.poll()
    await platform.deliver(say("And last year?"))
    finish_turn(bot, conversations.latest_run(conversations.created[0]))
    await bot.adapter.poll()
    assert len(conversations.created) == 1

    # A bare `/new`: the next message starts the next conversation.
    await platform.deliver(say("", new=True))
    await platform.deliver(say("Top customers?"))
    assert len(conversations.created) == 2
    finish_turn(bot, conversations.latest_run(conversations.created[1]))
    await bot.adapter.poll()

    # `/new` with text: that text is the next conversation's first message.
    await platform.deliver(say("Refunds by month?", new=True))
    assert len(conversations.created) == 3
    first, second, third = conversations.created
    assert conversations.sent == [
        Sent(first, "How many orders?", True),
        Sent(first, "And last year?", True),
        Sent(second, "Top customers?", True),
        Sent(third, "Refunds by month?", True),
    ]


async def test_the_thread_map_survives_a_restart_and_is_no_run(tmp_path: Path) -> None:
    runs_path = tmp_path / "runs"
    bot = await start_bot(runs_path)
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    [conversation] = bot.conversations.created
    finish_turn(bot, bot.conversations.latest_run(conversation))
    await bot.adapter.poll()
    await bot.platform.stop()

    # A new adapter on the same runs directory (the web process restarted); the conversations
    # are the same ones on disk.
    restarted = await start_bot(runs_path, conversations=bot.conversations)
    await restarted.platform.deliver(message("And last year?", thread="T1", mentioned=False))
    assert bot.conversations.created == [conversation]
    assert bot.conversations.sent[-1] == Sent(conversation, "And last year?", True)

    im_dir = runs_path / IM_DIR
    assert (im_dir / THREADS_FILE).is_file()
    assert not list(im_dir.rglob("*.jsonl"))
    assert IM_DIR not in {summary["id"] for summary in restarted.runs.summaries()}
