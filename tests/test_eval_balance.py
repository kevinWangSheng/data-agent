"""The model account's balance (ticket 27): DeepSeek's "Get User Balance", faked at the HTTP call
(`balance.fetch`) for the reading and at `urlopen` for the request itself."""

import io
import json
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any

import pytest

from evals import balance

FAKE_KEY = "sk-" + "z9Y8x7W6v5U4t3S2r1Q0p9O8"


def answer(*infos: tuple[str, str], available: bool = True) -> dict[str, Any]:
    """A response shaped like the documented example."""
    return {
        "is_available": available,
        "balance_infos": [
            {
                "currency": currency,
                "total_balance": total,
                "granted_balance": "0.00",
                "topped_up_balance": total,
            }
            for currency, total in infos
        ],
    }


@pytest.fixture
def respond(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    reply: dict[str, Any] = {"value": answer(("USD", "25.00"))}

    def fetch() -> Any:
        if isinstance(reply["value"], Exception):
            raise reply["value"]
        return reply["value"]

    monkeypatch.setattr(balance, "fetch", fetch)
    return reply


def test_a_usd_balance_reads_as_is(respond: dict[str, Any]) -> None:
    found = balance.read()
    assert found.available
    assert found.usd == Decimal("25.00")
    assert found.describe() == "25.00 USD"


def test_a_cny_balance_is_converted_at_the_fixed_rate(respond: dict[str, Any]) -> None:
    respond["value"] = answer(("CNY", "73.00"))
    found = balance.read()
    assert found.usd == Decimal("73.00") / balance.CNY_PER_USD
    assert found.describe() == f"73.00 CNY (about ${Decimal('73.00') / balance.CNY_PER_USD:.2f})"


@pytest.mark.parametrize(
    "reply",
    [
        {"balance_infos": []},
        {"is_available": True},
        answer(("EUR", "5.00")),
        answer(("USD", "lots")),
        [],
    ],
)
def test_an_answer_off_the_documented_shape_is_unknown(respond: dict[str, Any], reply: Any) -> None:
    respond["value"] = reply
    with pytest.raises(balance.BalanceUnknown):
        balance.read()


@pytest.mark.parametrize(
    ("reply", "need", "code", "says"),
    [
        (answer(("USD", "25.00")), "20", 0, "balance: 25.00 USD, covers $20"),
        (answer(("USD", "25.00")), None, 0, "balance: 25.00 USD"),
        (answer(("USD", "5.00")), "20", 1, "balance: 5.00 USD, below the $20 needed"),
        (answer(("USD", "25.00"), available=False), None, 1, "not available"),
        (balance.BalanceUnknown("HTTP 401"), None, 2, "balance unknown: HTTP 401"),
    ],
)
def test_the_command_says_whether_the_balance_is_enough(
    respond: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    reply: Any,
    need: str | None,
    code: int,
    says: str,
) -> None:
    respond["value"] = reply
    assert balance.main(["--need", need] if need else []) == code
    assert says in capsys.readouterr().out


class Reply(io.BytesIO):
    def __enter__(self) -> "Reply":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_the_request_carries_the_key_and_no_output_shows_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sent: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> Reply:
        sent.append(request)
        return Reply(json.dumps(answer(("USD", "3.50"))).encode())

    monkeypatch.setenv("DATA_AGENT_API_KEY", FAKE_KEY)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert balance.main([]) == 0
    (request,) = sent
    assert request.full_url == "https://api.deepseek.com/user/balance"
    assert request.get_header("Authorization") == f"Bearer {FAKE_KEY}"
    printed = capsys.readouterr()
    assert "3.50 USD" in printed.out
    assert FAKE_KEY not in printed.out + printed.err


@pytest.mark.parametrize(
    ("failure", "says"),
    [
        (urllib.error.HTTPError("u", 401, "Unauthorized", {}, None), "HTTP 401"),  # type: ignore[arg-type]
        (urllib.error.URLError("timed out"), "cannot reach"),
    ],
)
def test_a_failed_request_is_unknown(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, says: str
) -> None:
    def urlopen(request: urllib.request.Request, timeout: float) -> Reply:
        raise failure

    monkeypatch.setenv("DATA_AGENT_API_KEY", FAKE_KEY)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(balance.BalanceUnknown, match=says):
        balance.fetch()


def test_no_key_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_AGENT_API_KEY", raising=False)
    with pytest.raises(balance.BalanceUnknown, match="DATA_AGENT_API_KEY"):
        balance.fetch()
