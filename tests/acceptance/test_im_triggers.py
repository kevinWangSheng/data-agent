"""IM entry, who gets an answer (.scratch/im/spec.md "会话映射", "权限"; "Testing Decisions" 触发).

Written by ticket 130. The tests marked TICKET_131 are xfail(strict) until ticket 131 makes them
pass and removes the marker, changing nothing else.

The contract (data_agent.im.adapter, human-reviewed): a direct message always gets an answer; in
a channel or group, a message that mentions the bot, or any message in a thread the bot is in. A
group without threads (Telegram) needs the mention every time. Only users on the allowed list
make the bot do anything, buttons included; anyone else gets, in a direct chat, one reply giving
their user id, and in a group nothing at all.
"""

from pathlib import Path

from im_support import (
    ALICE,
    OUTSIDER,
    FakePlatform,
    Sent,
    expect_status,
    expect_stop_button,
    message,
    start_bot,
)

from data_agent.im.platform import Incoming


async def test_in_a_group_only_a_mention_gets_an_answer(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1", mentioned=True))
    assert len(bot.conversations.created) == 1
    assert bot.platform.in_thread("C1", "T1"), "the mention got no answer"

    shown = len(bot.platform.shown)
    await bot.platform.deliver(message("Lunch at noon?", thread="T2", mentioned=False))
    assert len(bot.conversations.created) == 1
    assert len(bot.conversations.sent) == 1
    assert len(bot.platform.shown) == shown


async def test_in_a_thread_the_bot_is_in_no_mention_is_needed(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1", mentioned=True))
    [conversation] = bot.conversations.created
    await bot.platform.deliver(message("Only paid ones", thread="T1", mentioned=False))
    assert bot.conversations.sent[-1] == Sent(conversation, "Only paid ones", True)


async def test_a_group_without_threads_needs_the_mention_every_time(tmp_path: Path) -> None:
    platform = FakePlatform("telegram")
    bot = await start_bot(tmp_path / "runs", platform=platform)

    def say(text: str, mentioned: bool) -> Incoming:
        return message(text, platform="telegram", chat="-100777", thread=None, mentioned=mentioned)

    await platform.deliver(say("How many orders?", mentioned=True))
    assert len(bot.conversations.sent) == 1

    await platform.deliver(say("Lunch at noon?", mentioned=False))
    assert len(bot.conversations.sent) == 1


async def test_an_outsider_in_a_direct_chat_gets_their_id_and_nothing_runs(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", chat="D1", thread=None, direct=True))
    assert len(bot.conversations.created) == 1

    await bot.platform.deliver(
        message("How many orders?", user=OUTSIDER, chat="D9", thread=None, direct=True)
    )
    replies = bot.platform.in_thread("D9", None)
    assert len(replies) == 1
    assert OUTSIDER in replies[0].text
    assert len(bot.conversations.created) == 1
    assert len(bot.conversations.sent) == 1


async def test_an_outsider_in_a_group_gets_no_answer_at_all(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    assert len(bot.conversations.created) == 1
    shown = len(bot.platform.shown)

    # A mention in a new thread, a message in the bot's thread, and `stop` there.
    await bot.platform.deliver(message("Drop the table", user=OUTSIDER, thread="T2"))
    await bot.platform.deliver(message("Drop it", user=OUTSIDER, thread="T1", mentioned=False))
    await bot.platform.deliver(message("stop", user=OUTSIDER, thread="T1", mentioned=False))
    assert len(bot.platform.shown) == shown
    assert len(bot.conversations.created) == 1
    assert len(bot.conversations.sent) == 1
    assert bot.conversations.cancelled == []


async def test_an_outsider_pressing_a_button_does_nothing(tmp_path: Path) -> None:
    bot = await start_bot(tmp_path / "runs")
    await bot.platform.deliver(message("How many orders?", thread="T1"))
    [conversation] = bot.conversations.created
    status = expect_status(bot)
    stop = expect_stop_button(status)

    await bot.platform.press(status, stop, user_id=OUTSIDER)
    assert bot.conversations.cancelled == []
    await bot.platform.press(status, stop, user_id=ALICE)
    assert bot.conversations.cancelled == [conversation]
