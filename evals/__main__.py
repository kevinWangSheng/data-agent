"""Evaluation CLI: `python -m evals <command>` from the repo root (spec seam C)."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from evals import attribute, dab, scrub, spend
from evals.compare import paired_bootstrap
from evals.harbor import by_task, load_trials
from evals.report import TRIALS_PER_TASK, check_complete, score, split_trials, write_report
from evals.sample import read_task_list, sample, task_difficulty


def _print(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result))
    else:
        print("\n".join(f"{key}: {value}" for key, value in result.items()))


def cmd_sample(args: argparse.Namespace) -> None:
    manifest = sample(
        task_difficulty(args.tasks_dir), read_task_list(args.dev_list), args.size, args.seed
    )
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")


def cmd_score(args: argparse.Namespace) -> None:
    _print(score(load_trials(args.job_dir)), args.json)


def cmd_compare(args: argparse.Namespace) -> None:
    baseline, candidate = load_trials(args.baseline), load_trials(args.candidate)
    if args.split:
        manifest = json.loads(args.manifest.read_text())
        baseline = split_trials(baseline, manifest, args.split, "the baseline")
        candidate = split_trials(candidate, manifest, args.split, "the candidate")
    check_complete(baseline, "baseline", args.trials)
    check_complete(candidate, "candidate", args.trials)
    result = paired_bootstrap(
        by_task(baseline), by_task(candidate), seed=args.seed, n_boot=args.n_boot
    )
    _print(result, args.json)


def cmd_report(args: argparse.Namespace) -> None:
    manifest = json.loads(args.manifest.read_text())
    report = write_report(load_trials(args.job_dir), manifest, args.split, args.out, args.trials)
    print(
        f"{args.split}: accuracy {report['accuracy']:.1f}% ± {report['accuracy_stderr']:.1f}% "
        f"over {report['n_trials']} trials -> {args.out}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("sample", help="split tasks into dev set and stratified holdout")
    p.add_argument("--tasks-dir", type=Path, required=True)
    p.add_argument("--dev-list", type=Path, required=True)
    p.add_argument("--size", type=int, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_sample)

    p = commands.add_parser("score", help="leaderboard metrics for a Harbor job")
    p.add_argument("job_dir", type=Path)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_score)

    p = commands.add_parser("compare", help="paired per-task bootstrap of two jobs")
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--n-boot", type=int, default=10000)
    p.add_argument("--trials", type=int, default=TRIALS_PER_TASK, help="required trials per task")
    p.add_argument("--manifest", type=Path, help="with --split: both jobs must cover the split")
    p.add_argument("--split", choices=("dev", "holdout"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = commands.add_parser("report", help="report one split; holdout gets aggregates only")
    p.add_argument("job_dir", type=Path)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--split", choices=("dev", "holdout"), required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--trials", type=int, default=TRIALS_PER_TASK, help="required trials per task")
    p.set_defaults(func=cmd_report)

    for module in (scrub, spend, attribute, dab):
        module.add_parser(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "compare" and (args.manifest is None) != (args.split is None):
        parser.error("compare: --manifest and --split go together")
    if hasattr(args, "run"):
        return args.run(args)
    try:
        args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
