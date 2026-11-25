"""The Slack platform (data_agent.im.slack): Slack's payloads <-> the platform interface, the
mrkdwn conversion, the limits, and no token in the logs. No Slack: a client that records its
calls, or a real client pointed at a closed port on the loopback address."""

import asyncio
import json
import logging
import random
import socket
from pathlib import Path
from typing import Any, cast

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.slack_response import SlackResponse

from data_agent.im.config import PlatformConfig
from data_agent.im.platform import Button, Incoming, Outgoing, Press
from data_agent.im.slack import (
    LIMITS,
    SECTION_CHARS,
    SlackPlatform,
    message_payload,
    to_incoming,
    to_mrkdwn,
    to_press,
)

PAYLOADS = json.loads(
    (Path(__file__).parent / "fixtures" / "im" / "slack_payloads.json").read_text(encoding="utf-8")
)
BOT = "U0BOT0001"
# Made up, in no real token's shape.
BOT_TOKEN = "sb-" + "Kd8w" + "-made-up-bot"
APP_TOKEN = "sa-" + "Pq2n" + "-made-up-app"
CONFIG = PlatformConfig(
    "slack",
    frozenset({"U0ALICE01"}),
    {"bot_token_env": "DATA_AGENT_SLACK_BOT_TOKEN", "app_token_env": "DATA_AGENT_SLACK_APP_TOKEN"},
    {"bot_token_env": BOT_TOKEN, "app_token_env": APP_TOKEN},
)


class FakeClient:
    """The calls of `AsyncWebClient` the platform makes, recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.token = BOT_TOKEN

    async def auth_test(self) -> dict[str, Any]:
        return {"ok": True, "user_id": BOT}

    async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("chat.postMessage", kwargs))
        return {"ok": True, "ts": f"1764000009.{len(self.calls):06d}"}

    async def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("chat.update", kwargs))
        return {"ok": True}


def platform(client: object | None = None) -> SlackPlatform:
    return SlackPlatform(CONFIG, cast(AsyncWebClient, client or FakeClient()))


# --- Slack's payloads -> the interface -----------------------------------------------------


def test_a_top_level_mention_in_a_channel() -> None:
    incoming = to_incoming(PAYLOADS["channel_mention"], BOT)
    assert incoming == Incoming(
        platform="slack",
        chat_id="C0DATA001",
        # A top-level message: the bot answers in its thread, named by its own ts.
        thread_id="1764000000.000100",
        user_id="U0ALICE01",
        direct=False,
        mentioned=True,
        text="How many orders & refunds?",
    )


def test_a_message_without_the_mention_is_not_mentioned() -> None:
    incoming = to_incoming(PAYLOADS["channel_plain"], BOT)
    assert incoming is not None
    assert not incoming.mentioned
    assert incoming.text == "Lunch at noon?"


def test_a_reply_in_a_thread_is_in_that_thread() -> None:
    incoming = to_incoming(PAYLOADS["thread_reply"], BOT)
    assert incoming is not None
    assert incoming.thread_id == "1764000000.000100"
    assert not incoming.mentioned


def test_a_direct_message_is_direct() -> None:
    incoming = to_incoming(PAYLOADS["direct"], BOT)
    assert incoming is not None
    assert incoming.direct
    assert incoming.thread_id == "1764000000.000300"


@pytest.mark.parametrize("name", ["bots_own", "other_bot", "edited", "deleted"])
def test_bot_messages_edits_and_deletions_are_no_message(name: str) -> None:
    assert to_incoming(PAYLOADS[name], BOT) is None


def test_a_button_press_on_a_status_message() -> None:
    press = to_press(PAYLOADS["stop_press"])
    assert press == Press(
        platform="slack",
        chat_id="C0DATA001",
        thread_id="1764000000.000100",
        user_id="U0ALICE01",
        direct=False,
        message_id="1764000000.000400",
        value="stop",
    )


def test_a_press_is_in_the_thread_of_the_message_that_started_it() -> None:
    incoming = to_incoming(PAYLOADS["channel_mention"], BOT)
    press = to_press(PAYLOADS["stop_press"])
    assert incoming is not None and press is not None
    key = (incoming.platform, incoming.chat_id, incoming.thread_id)
    assert (press.platform, press.chat_id, press.thread_id) == key


def test_a_press_in_a_direct_message_is_direct() -> None:
    body = json.loads(json.dumps(PAYLOADS["stop_press"]))
    body["channel"]["id"] = "D0ALICE01"
    press = to_press(body)
    assert press is not None and press.direct


async def test_the_same_message_is_handed_on_once() -> None:
    slack = platform()
    got: list[Incoming] = []

    async def on_message(message: Incoming) -> None:
        got.append(message)

    async def on_press(_: Press) -> None:
        pass

    await slack_handlers(slack, on_message, on_press)
    for _ in range(2):
        await slack.handle_event(PAYLOADS["channel_mention"])
    await slack.handle_event(PAYLOADS["edited"])
    assert len(got) == 1


async def test_messages_and_presses_are_handed_on_one_at_a_time() -> None:
    slack = platform()
    running = 0
    overlapped = False
    order: list[str] = []

    async def handler(name: str) -> None:
        nonlocal running, overlapped
        running += 1
        overlapped = overlapped or running > 1
        await asyncio.sleep(0.01)
        order.append(name)
        running -= 1

    async def on_message(message: Incoming) -> None:
        await handler(message.text)

    async def on_press(press: Press) -> None:
        await handler(press.value)

    await slack_handlers(slack, on_message, on_press)
    await asyncio.gather(
        slack.handle_event(PAYLOADS["channel_mention"]),
        slack.handle_action(PAYLOADS["stop_press"]),
        slack.handle_event(PAYLOADS["thread_reply"]),
    )
    assert not overlapped
    assert order == ["How many orders & refunds?", "stop", "Only paid ones"]


async def slack_handlers(slack: SlackPlatform, on_message: Any, on_press: Any) -> None:
    """The handlers and the bot's user id as `start` sets them, without connecting."""
    slack.bot_user_id = BOT
    slack.on_message = on_message
    slack.on_press = on_press


# --- the interface -> Slack's calls --------------------------------------------------------


async def test_send_posts_into_the_thread_with_numbered_buttons() -> None:
    client = FakeClient()
    slack = platform(client)
    buttons = (Button("1", "answer:1"), Button("2", "answer:2"))
    ts = await slack.send("C0DATA001", "1764000000.000100", Outgoing("Net or **gross**?", buttons))
    [(method, kwargs)] = client.calls
    assert method == "chat.postMessage"
    assert ts == "1764000009.000001"
    assert kwargs["channel"] == "C0DATA001"
    assert kwargs["thread_ts"] == "1764000000.000100"
    assert kwargs["text"] == "Net or *gross*?"
    section, actions = kwargs["blocks"]
    assert section["text"] == {"type": "mrkdwn", "text": "Net or *gross*?"}
    elements = actions["elements"]
    assert [element["text"]["text"] for element in elements] == ["1", "2"]
    assert [element["value"] for element in elements] == ["answer:1", "answer:2"]
    assert len({element["action_id"] for element in elements}) == 2


async def test_an_edit_without_buttons_takes_them_off() -> None:
    client = FakeClient()
    slack = platform(client)
    await slack.edit("C0DATA001", "1764000000.000400", Outgoing("Done · 37s · $0.25"))
    [(method, kwargs)] = client.calls
    assert method == "chat.update"
    assert kwargs["channel"] == "C0DATA001"
    assert kwargs["ts"] == "1764000000.000400"
    # The blocks are sent again, without an actions block, so the old ones (the buttons) go
    # (chat.update: "To remove previous `blocks`, include an empty array"; docs.slack.dev).
    assert [block["type"] for block in kwargs["blocks"]] == ["section"]


async def test_a_rate_limited_edit_raises_for_the_adapter_to_skip() -> None:
    """The adapter skips an edit that raises and catches up later
    (tests/test_im_adapter.py::test_a_failed_edit_is_skipped_and_caught_up_later)."""

    class Limited(FakeClient):
        async def chat_update(self, **kwargs: Any) -> dict[str, Any]:
            response = SlackResponse(
                client=None,
                http_verb="POST",
                api_url="https://slack.com/api/chat.update",
                req_args={},
                data={"ok": False, "error": "ratelimited"},
                headers={"Retry-After": "1"},
                status_code=429,
            )
            raise SlackApiError("ratelimited", response)

    with pytest.raises(SlackApiError) as raised:
        await platform(Limited()).edit("C0DATA001", "1764000000.000400", Outgoing("Thinking…"))
    assert raised.value.response.status_code == 429


def rate_limited(method: str, retry_after: str) -> SlackApiError:
    response = SlackResponse(
        client=None,
        http_verb="POST",
        api_url=f"https://slack.com/api/{method}",
        req_args={},
        data={"ok": False, "error": "ratelimited"},
        headers={"Retry-After": retry_after},
        status_code=429,
    )
    return SlackApiError("ratelimited", response)


class LimitedOnce(FakeClient):
    """Its first `chat.postMessage` is rate-limited."""

    def __init__(self, retry_after: str) -> None:
        super().__init__()
        self.retry_after = retry_after
        self.limited = False

    async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        if not self.limited:
            self.limited = True
            raise rate_limited("chat.postMessage", self.retry_after)
        return await super().chat_postMessage(**kwargs)


@pytest.mark.parametrize(("retry_after", "waited"), [("3", 3.0), ("3600", 30.0)])
async def test_a_rate_limited_send_waits_as_slack_asks_and_is_tried_again(
    retry_after: str, waited: float
) -> None:
    client = LimitedOnce(retry_after)
    slack = platform(client)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    slack.sleep = sleep
    ts = await slack.send("C0DATA001", None, Outgoing("The answer."))
    assert ts == "1764000009.000001"
    # Waited as long as Retry-After says, at most a bound.
    assert waits == [waited]
    assert [method for method, _ in client.calls] == ["chat.postMessage"]


async def test_a_send_failing_otherwise_raises_at_once() -> None:
    class Broken(FakeClient):
        async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
            raise SlackApiError("channel_not_found", {"ok": False, "error": "channel_not_found"})

    slack = platform(Broken())
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    slack.sleep = sleep
    with pytest.raises(SlackApiError):
        await slack.send("C0DATA001", None, Outgoing("The answer."))
    assert waits == []


def test_a_section_never_passes_slacks_limit() -> None:
    text = "&" * LIMITS.max_chars
    payload = message_payload(Outgoing(text, (Button("Stop", "stop"),)))
    assert len(payload["blocks"][0]["text"]["text"]) <= SECTION_CHARS


def test_escapes_past_a_sections_limit_go_into_more_sections() -> None:
    text = "a & b, " * (LIMITS.max_chars // 7 - 3) + "the conclusion."
    assert len(text) <= LIMITS.max_chars
    payload = message_payload(Outgoing(text, (Button("Stop", "stop"),)))
    sections = [block for block in payload["blocks"] if block["type"] == "section"]
    assert len(sections) > 1
    assert all(len(block["text"]["text"]) <= SECTION_CHARS for block in sections)
    assert "".join(block["text"]["text"] for block in sections) == to_mrkdwn(text)
    assert payload["blocks"][-1]["type"] == "actions"


def section_texts(markdown: str) -> list[str]:
    payload = message_payload(Outgoing(markdown, (Button("Stop", "stop"),)))
    return [block["text"]["text"] for block in payload["blocks"] if block["type"] == "section"]


def test_a_cut_never_falls_inside_an_escape() -> None:
    texts = section_texts("a" + "&" * 600)
    assert len(texts) > 1
    assert all(len(text) <= SECTION_CHARS for text in texts)
    for text in texts:
        assert "&" not in text.replace("&amp;", "")
    assert "".join(texts) == to_mrkdwn("a" + "&" * 600)


def test_a_cut_never_falls_inside_a_link() -> None:
    # 590 escapes come to 2950 characters, so the link runs across a section's limit.
    link = "[the dashboard](https://example.com/" + "x" * 60 + ")"
    texts = section_texts("&" * 590 + link + " and more")
    assert len(texts) > 1
    assert all(len(text) <= SECTION_CHARS for text in texts)
    [holder] = [text for text in texts if "<https://" in text]
    assert "<https://example.com/" + "x" * 60 + "|the dashboard>" in holder


def test_a_code_block_cut_in_two_is_fenced_in_each_section() -> None:
    rows = "\n".join(f"{index} & {index}" for index in range(500))
    texts = section_texts(f"The rows:\n```\n{rows}\n```\nThat is all.")
    assert len(texts) > 1
    assert all(len(text) <= SECTION_CHARS for text in texts)
    for text in texts:
        fences = [line for line in text.split("\n") if line == "```"]
        assert len(fences) % 2 == 0
    assert texts[0].startswith("The rows:\n```\n")
    assert texts[-1].endswith("```\nThat is all.")


def test_the_limits() -> None:
    assert LIMITS.max_chars < SECTION_CHARS
    assert LIMITS.edit_interval == 2.0
    assert not SlackPlatform.capabilities.typing


# --- Markdown -> mrkdwn --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("markdown", "mrkdwn"),
    [
        ("**Revenue** grew", "*Revenue* grew"),
        ("__Revenue__ grew", "*Revenue* grew"),
        ("an *aside* here", "an _aside_ here"),
        ("~~old~~ new", "~old~ new"),
        ("see [the docs](https://example.com/a?b=1)", "see <https://example.com/a?b=1|the docs>"),
        ("## Totals\nby month", "*Totals*\nby month"),
        ("- one\n* two", "• one\n• two"),
        ("a < b & c > d", "a &lt; b &amp; c &gt; d"),
        ("use `a**b**` here", "use `a**b**` here"),
        (
            "```sql\nSELECT **x** FROM t WHERE a < 1\n```",
            "```\nSELECT **x** FROM t WHERE a &lt; 1\n```",
        ),
        (
            "before\n\n```\n| a | b |\n```\n\nafter **x**",
            "before\n\n```\n| a | b |\n```\n\nafter *x*",
        ),
        (
            "Total:\n```SELECT SUM(revenue) FROM orders```\ndone",
            "Total:\n```SELECT SUM(revenue) FROM orders```\ndone",
        ),
        ("````sql\na < 1\n````\nafter", "```\na &lt; 1\n```\nafter"),
        ("```\na\n``` b\n```\n**x**", "```\na\n``` b\n```\n*x*"),
    ],
    ids=[
        "bold",
        "bold-underscores",
        "italic",
        "strike",
        "link",
        "heading",
        "bullets",
        "escape",
        "inline-code",
        "code-block",
        "around-code",
        "one-line-code",
        "four-backticks",
        "text-after-a-run",
    ],
)
def test_markdown_to_mrkdwn(markdown: str, mrkdwn: str) -> None:
    assert to_mrkdwn(markdown) == mrkdwn


# --- the tokens ----------------------------------------------------------------------------


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_no_token_in_the_libraries_debug_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """slack_sdk and slack_bolt log their requests at DEBUG; a failed start logs and raises
    without either token. The client goes to a closed port on the loopback address, through no
    proxy (slack_sdk takes one from the environment)."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    caplog.set_level(logging.DEBUG)
    client = AsyncWebClient(token=BOT_TOKEN, base_url=f"http://127.0.0.1:{closed_port()}/api/")
    slack = SlackPlatform(CONFIG, client)
    with pytest.raises(Exception) as failed_auth:
        await slack.start(no_message, no_press)

    # Past `auth.test`, to the Socket Mode connection with the app token.
    async def auth_test() -> dict[str, Any]:
        return {"ok": True, "user_id": BOT}

    client.auth_test = auth_test  # type: ignore[method-assign]
    # slack_sdk's Socket Mode client tries again and again until it connects.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(slack.start(no_message, no_press), timeout=2)
    await slack.stop()
    assert "auth.test" in caplog.text
    assert "apps.connections.open" in caplog.text
    for text in [caplog.text, str(failed_auth.value), repr(slack)]:
        assert BOT_TOKEN not in text
        assert APP_TOKEN not in text


async def no_message(_: Incoming) -> None:
    pass


async def no_press(_: Press) -> None:
    pass


def test_a_code_line_longer_than_a_section_is_cut_in_the_line() -> None:
    """Not cut only after the opening fence, again and again (which would never end)."""
    texts = section_texts("```\n" + "&" * 600 + "\n```")
    assert len(texts) == 2
    assert all(len(text) <= SECTION_CHARS for text in texts)
    for text in texts:
        assert text.startswith("```\n")
        assert text.endswith("\n```")
        assert "&" not in text.replace("&amp;", "")


@pytest.mark.parametrize("seed", range(40))
def test_sections_end_and_hold_the_limit_on_mixed_text(seed: int) -> None:
    rng = random.Random(seed)
    words = ["```", "&", "<b>", "[a link](https://example.com/x)", "word", "\n", "&" * 300]
    text = "".join(rng.choice(words) + rng.choice(["", " ", "\n"]) for _ in range(400))
    texts = section_texts(text[: LIMITS.max_chars])
    assert all(len(text) <= SECTION_CHARS for text in texts)
    for text in texts:
        assert sum(line == "```" for line in text.split("\n")) % 2 == 0 or text is texts[-1]
