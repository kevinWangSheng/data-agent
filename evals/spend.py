"""`spend RUNS_DIR --budget USD`: the cumulative spend guard for batch runs (plan §7).

Sums `cost_usd` over the result events of every `*.jsonl` event file under RUNS_DIR. Exit 0 below
80% of the budget; exit 1 at or above it, so a batch runner stops and a human decides; exit 2 when
the spend cannot be known (unreadable directory or event file, a symlink, a result without a
cost, bad arguments).
Amounts are Decimal so "exactly 80%" is exact.
"""

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from evals.walk import walk_files

STOP_FRACTION = Decimal("0.8")


class SpendUnknown(Exception):
    pass


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser("spend", help="exit non-zero once spend reaches 80%% of budget")
    parser.add_argument("runs_dir", type=Path)
    parser.add_argument("--budget", required=True, help="total budget in USD")
    parser.set_defaults(run=run)


def file_spend(path: Path) -> Decimal:
    total = Decimal(0)
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line, parse_float=Decimal)
        except json.JSONDecodeError as e:
            raise SpendUnknown(f"{path} line {number} is not JSON: {e}") from e
        if not isinstance(event, dict) or event.get("type") != "result":
            continue
        cost = event.get("cost_usd")
        if isinstance(cost, bool) or not isinstance(cost, Decimal | int):
            raise SpendUnknown(f"{path} line {number}: result event has no numeric cost_usd")
        total += cost
    return total


def run(args: argparse.Namespace) -> int:
    runs_dir: Path = args.runs_dir
    try:
        budget = Decimal(args.budget)
    except InvalidOperation:
        budget = Decimal(0)
    if not budget.is_finite() or budget <= 0:
        print(f"spend: budget must be a positive number, got {args.budget!r}", file=sys.stderr)
        return 2
    if not runs_dir.is_dir():
        print(f"spend: {runs_dir} is not a directory", file=sys.stderr)
        return 2
    found, missed = walk_files(runs_dir)
    if missed:
        print("spend: cannot see every event file, stopping:", file=sys.stderr)
        for problem in missed:
            print(f"  {problem}", file=sys.stderr)
        return 2
    files = [p for p in found if p.suffix == ".jsonl"]
    try:
        spent = sum((file_spend(p) for p in files), Decimal(0))
    except (SpendUnknown, OSError, UnicodeDecodeError) as e:
        print(f"spend: cannot total spend, stopping: {e}", file=sys.stderr)
        return 2
    limit = budget * STOP_FRACTION
    print(
        f"spend: ${spent:.4f} over {len(files)} event file(s); "
        f"budget ${budget}, stop at 80% = ${limit:.4f}"
    )
    if spent >= limit:
        print("spend: reached 80% of the budget; stop the batch and ask a human", file=sys.stderr)
        return 1
    return 0
