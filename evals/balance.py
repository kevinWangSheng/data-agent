"""`python -m evals.balance [--need USD]`: the model account's balance (ADR-0008 preflight).

Asks DeepSeek's "Get User Balance" (GET /user/balance, api-docs.deepseek.com/api/get-user-balance)
with DATA_AGENT_API_KEY, which goes into the request header only and is never printed. Prints the
balance and, with --need, whether it covers that many USD. Exit 0 when it is enough; 1 when
DeepSeek says it cannot pay for API calls or it is below --need; 2 when it cannot be read.

A CNY balance is converted at CNY_PER_USD, a fixed rate on the high side, so the check errs
toward "not enough".
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

BALANCE_URL = "https://api.deepseek.com/user/balance"
CNY_PER_USD = Decimal("7.3")
TIMEOUT_SECONDS = 15


class BalanceUnknown(Exception):
    pass


@dataclass(frozen=True)
class Balance:
    available: bool  # DeepSeek's is_available: whether the balance can pay for API calls
    amounts: tuple[tuple[Decimal, str], ...]  # total balance per currency

    @property
    def usd(self) -> Decimal:
        return sum((a if c == "USD" else a / CNY_PER_USD for a, c in self.amounts), Decimal(0))

    def describe(self) -> str:
        parts = [
            f"{a} {c}" if c == "USD" else f"{a} {c} (about ${a / CNY_PER_USD:.2f})"
            for a, c in self.amounts
        ]
        return " + ".join(parts)


def fetch() -> Any:
    """The parsed answer of the balance endpoint."""
    key = os.environ.get("DATA_AGENT_API_KEY", "").strip()
    if not key:
        raise BalanceUnknown("DATA_AGENT_API_KEY is not set")
    request = urllib.request.Request(
        BALANCE_URL, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise BalanceUnknown(f"HTTP {exc.code} from {BALANCE_URL}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise BalanceUnknown(f"cannot reach {BALANCE_URL}: {reason}") from exc
    except ValueError as exc:
        raise BalanceUnknown(f"{BALANCE_URL} did not answer JSON") from exc


def read() -> Balance:
    found = fetch()
    if not isinstance(found, dict) or not isinstance(found.get("is_available"), bool):
        raise BalanceUnknown("the answer has no is_available")
    infos = found.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        raise BalanceUnknown("the answer has no balance_infos")
    amounts: list[tuple[Decimal, str]] = []
    for info in infos:
        currency = info.get("currency") if isinstance(info, dict) else None
        if currency not in ("USD", "CNY"):
            raise BalanceUnknown(f"a balance in an unexpected currency: {currency!r}")
        try:
            amount = Decimal(str(info.get("total_balance")))
        except InvalidOperation as exc:
            raise BalanceUnknown(f"a {currency} total_balance that is not a number") from exc
        if not amount.is_finite():
            raise BalanceUnknown(f"a {currency} total_balance that is not a number")
        amounts.append((amount, currency))
    return Balance(available=found["is_available"], amounts=tuple(amounts))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.balance", description=__doc__)
    parser.add_argument("--need", type=Decimal, help="USD the balance must cover")
    args = parser.parse_args(argv)
    try:
        found = read()
    except BalanceUnknown as exc:
        print(f"balance unknown: {exc}")
        return 2
    line = f"balance: {found.describe()}"
    if not found.available:
        print(f"{line}; DeepSeek says it is not available for API calls")
        return 1
    if args.need is not None and found.usd < args.need:
        print(f"{line}, below the ${args.need} needed")
        return 1
    print(f"{line}, covers ${args.need}" if args.need is not None else line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
