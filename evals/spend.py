"""`spend RUNS_DIR --budget USD`: the cumulative spend guard for batch runs (plan §7).

Sums the billed spend over the result events of every `*.jsonl` event file under RUNS_DIR. Exit 0
below 80% of the budget; exit 1 at or above it, so a batch runner stops and a human decides; exit 2
when the spend cannot be known (unreadable directory or event file, a symlink, a result without a
cost or with a negative one, bad arguments).
Amounts are Decimal so "exactly 80%" is exact.

Billed spend (ticket 34) is what the model account is charged. A result's `cost_usd` prices the
tokens at DeepSeek's peak prices (`data_agent.agent.PRICES`), which also cap a single run; DeepSeek
bills every price at half off peak. So a run of a DeepSeek model priced from the price table
(`cost_basis` "price_table") that started off peak, by the `ts` of its file's first event, is
billed at `OFF_PEAK_RATE` of `cost_usd`; any other run (the SDK's figure, another model, no
readable start) at the full `cost_usd`. China's public holidays are off peak too; they are not
tracked here, so a holiday run counts at peak, never understating spend.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from evals.walk import walk_files

STOP_FRACTION = Decimal("0.8")
STOP_PERCENT = f"{STOP_FRACTION:.0%}"

# DeepSeek's peak hours, Monday to Friday in UTC, as [start, end) hours; the rest is off peak
# (api-docs.deepseek.com/quick_start/pricing, checked 2026-11-01; docs/plan.md §7).
PEAK_HOURS_UTC = ((1, 4), (6, 10))
OFF_PEAK_RATE = Decimal("0.5")

Period = Literal["peak", "off-peak"]


class SpendUnknown(Exception):
    pass


@dataclass(frozen=True)
class FileSpend:
    listed: Decimal  # the results' `cost_usd`, at peak prices
    billed: Decimal
    period: Period | None  # when the run started; None when its start cannot be read


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    stop = STOP_PERCENT.replace("%", "%%")  # argparse formats help with %
    parser = commands.add_parser(
        "spend", help=f"exit non-zero once billed spend reaches {stop} of budget"
    )
    parser.add_argument("runs_dir", type=Path)
    parser.add_argument("--budget", required=True, help="total budget in USD")
    parser.set_defaults(run=run)


def period(first_event: dict[str, Any] | None) -> Period | None:
    """The DeepSeek pricing period a run started in, from the `ts` of its first event."""
    ts = (first_event or {}).get("ts")
    if not isinstance(ts, str):
        return None
    try:
        started = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if started.utcoffset() is None:  # no zone: the UTC hour is unknown
        return None
    utc = started.astimezone(UTC)
    weekday = utc.weekday() < 5
    peak = weekday and any(start <= utc.hour < end for start, end in PEAK_HOURS_UTC)
    return "peak" if peak else "off-peak"


def run_rate(first_event: dict[str, Any] | None, version: dict[str, Any] | None) -> Decimal:
    """What share of a price-table `cost_usd` the run is billed: half for a DeepSeek model's run
    that started off peak, all of it otherwise."""
    model = (version or {}).get("model")
    deepseek = isinstance(model, str) and model.startswith("deepseek")
    return OFF_PEAK_RATE if deepseek and period(first_event) == "off-peak" else Decimal(1)


def billed(cost: Decimal, result: dict[str, Any], rate: Decimal) -> Decimal:
    """A result's billed cost: the run's rate applies to price-table costs only."""
    return cost * rate if result.get("cost_basis") == "price_table" else cost


def file_spend(path: Path) -> FileSpend:
    listed = total = Decimal(0)
    first: dict[str, Any] | None = None
    version: dict[str, Any] | None = None
    # Only "\n" ends a JSONL line; splitlines() would also split at U+2028 inside a string.
    for number, line in enumerate(path.read_text().split("\n"), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line, parse_float=Decimal)
        except json.JSONDecodeError as e:
            raise SpendUnknown(f"{path} line {number} is not JSON: {e}") from e
        if not isinstance(event, dict):
            continue
        if first is None:
            first = event
        if version is None and event.get("type") == "version":
            version = event
        if event.get("type") != "result":
            continue
        cost = event.get("cost_usd")
        if isinstance(cost, bool) or not isinstance(cost, Decimal | int):
            raise SpendUnknown(f"{path} line {number}: result event has no numeric cost_usd")
        if cost < 0:  # would hide real spend from the guard
            raise SpendUnknown(f"{path} line {number}: result event has a negative cost_usd")
        listed += cost
        total += billed(Decimal(cost), event, run_rate(first, version))
    return FileSpend(listed=listed, billed=total, period=period(first))


def run(args: argparse.Namespace) -> int:
    runs_dir: Path = args.runs_dir
    try:
        budget = Decimal(args.budget)
    except InvalidOperation:
        budget = None
    if budget is None or not budget.is_finite() or budget <= 0:
        print(f"spend: budget must be a positive number, got {args.budget!r}", file=sys.stderr)
        return 2
    if not runs_dir.is_dir():
        print(f"spend: {runs_dir} is not a directory", file=sys.stderr)
        return 2
    found, unchecked = walk_files(runs_dir)
    if unchecked:
        print("spend: cannot see every event file, stopping:", file=sys.stderr)
        for problem in unchecked:
            print(f"  {problem}", file=sys.stderr)
        return 2
    files = [p for p in found if p.suffix == ".jsonl"]
    try:
        spends = [file_spend(p) for p in files]
    except (SpendUnknown, OSError, UnicodeDecodeError) as e:
        print(f"spend: cannot total spend, stopping: {e}", file=sys.stderr)
        return 2
    spent = sum((s.billed for s in spends), Decimal(0))
    listed = sum((s.listed for s in spends), Decimal(0))
    periods = [s.period for s in spends]
    limit = budget * STOP_FRACTION
    print(
        f"spend: billed ${spent:.4f} (at peak prices ${listed:.4f}) over {len(files)} event "
        f"file(s) ({periods.count('off-peak')} off-peak, {periods.count('peak')} peak, "
        f"{periods.count(None)} unknown); budget ${budget}, stop at {STOP_PERCENT} = ${limit:.4f}"
    )
    if spent >= limit:
        print(
            f"spend: billed spend reached {STOP_PERCENT} of the budget; "
            "stop the batch and ask a human",
            file=sys.stderr,
        )
        return 1
    return 0
