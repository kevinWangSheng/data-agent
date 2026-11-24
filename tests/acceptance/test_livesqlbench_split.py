"""The LiveSQLBench split (.scratch/livesqlbench/spec.md "Testing Decisions": 划分; Q2, U2, U4).

Written by ticket 127. The `TICKET_128` tests pass once ticket 128 implements
`evals.livesqlbench.split` and commits the 60/120 split, which then drops their marks and changes
nothing else. The `da-remote pull` tests are guards (no mark): the LiveSQLBench ids sit in the
manifest's top-level `dev` and `holdout` (spec U2), so today's `pull` already treats them like
data-eng-bench's, and must keep doing so.

- The split function is tested on 180 generated `Query` records over 18 databases of different
  sizes, one of them with a single record (generated here, not fixture files). "Every database on
  both sides when its size allows" is read as: every database with at least 2 records (ticket
  127's reading).
- The committed split: `evals/manifest.json` holds 60 dev and 120 holdout `lsb-` ids, and
  `evals/livesqlbench_databases.json` (`livesqlbench.DATABASES`) names each one's database. The
  ids neither equal nor share their first 32 characters with data-eng-bench's (the registry's
  tasks and the manifest's other entries): `da-remote pull` judges a trial without `result.json`
  by Harbor's trial name, `task[:32]` (scripts/da-remote:687-693).
- `evals board` is not tested (spec U3: it runs registry data-eng-bench only).
"""

import json
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from livesqlbench_support import REPO_ROOT, Env, ablate_run, lsb_server_manifest, pulled_whole

from evals import livesqlbench

TICKET_128 = pytest.mark.xfail(
    strict=True,
    raises=(NotImplementedError, AssertionError),
    reason="ticket 128: the LiveSQLBench split is not implemented or not committed",
)

MANIFEST = REPO_ROOT / "evals" / "manifest.json"
REGISTRY = REPO_ROOT / "evals" / "registry.json"
SEED = 20261124
# 18 databases, 180 records; one database has a single record. Each id is over 32 characters
# with `lsb-`, distinct only in its first 10, so an id scheme that lengthens the prefix much
# makes ids that share their first 32 characters.
LONG_TAIL = "generated_question_identifier"
SIZES = [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 12, 13, 14, 15, 16, 18, 19]


def generated_records() -> list[dict[str, Any]]:
    return [
        {
            "instance_id": f"s{d:02d}q{n:02d}_{LONG_TAIL}",
            "selected_database": f"synth_db_{d:02d}",
            "query": f"question {n} on database {d}",
            "category": "Query",
            "conditions": {},
        }
        for d, size in enumerate(SIZES)
        for n in range(1, size + 1)
    ]


def by_database(ids: list[str], databases: dict[str, str]) -> Counter[str]:
    return Counter(databases[i] for i in ids)


def test_the_generated_records_are_what_the_split_tests_assume() -> None:
    records = generated_records()
    assert len(records) == livesqlbench.DEV_SIZE + livesqlbench.HOLDOUT_SIZE == 180
    assert len({r["instance_id"] for r in records}) == 180
    sizes = Counter(r["selected_database"] for r in records)
    assert len(sizes) == 18 and min(sizes.values()) == 1
    assert all(len(f"lsb-{r['instance_id']}") > 32 for r in records)


@TICKET_128
def test_the_same_seed_gives_the_same_split() -> None:
    first = livesqlbench.split(generated_records(), SEED)
    second = livesqlbench.split(generated_records(), SEED)

    assert first.dev
    assert first == second


@TICKET_128
def test_the_split_is_60_dev_and_120_holdout_of_all_records() -> None:
    result = livesqlbench.split(generated_records(), SEED)
    every = {livesqlbench.task_id(r["instance_id"]) for r in generated_records()}

    assert len(result.dev) == len(set(result.dev)) == 60
    assert len(result.holdout) == len(set(result.holdout)) == 120
    assert not set(result.dev) & set(result.holdout)
    assert set(result.dev) | set(result.holdout) == every


@TICKET_128
def test_every_database_with_two_or_more_records_is_on_both_sides() -> None:
    result = livesqlbench.split(generated_records(), SEED)
    sizes = Counter(r["selected_database"] for r in generated_records())

    assert set(result.databases) == set(result.dev) | set(result.holdout)
    dev = by_database(result.dev, result.databases)
    holdout = by_database(result.holdout, result.databases)
    for database, size in sizes.items():
        assert dev[database] + holdout[database] == size
        if size >= 2:
            assert dev[database] >= 1 and holdout[database] >= 1, database


@TICKET_128
def test_ids_are_lsb_instance_ids_distinct_in_their_first_32_characters() -> None:
    result = livesqlbench.split(generated_records(), SEED)
    ids = result.dev + result.holdout

    assert {f"lsb-{r['instance_id']}" for r in generated_records()} == set(ids)
    assert all(livesqlbench.task_id(i.removeprefix("lsb-")) == i for i in ids)
    assert len({i[:32] for i in ids}) == len(ids)
    records = {f"lsb-{r['instance_id']}": r["selected_database"] for r in generated_records()}
    assert result.databases == records


def committed() -> tuple[list[str], list[str], dict[str, str]]:
    manifest = json.loads(MANIFEST.read_text())
    dev = [t for t in manifest["dev"] if t.startswith(livesqlbench.TASK_PREFIX)]
    holdout = [t for t in manifest["holdout"] if t.startswith(livesqlbench.TASK_PREFIX)]
    assert len(dev) == 60, f"{len(dev)} LiveSQLBench dev ids in the manifest"
    assert len(holdout) == 120, f"{len(holdout)} LiveSQLBench holdout ids in the manifest"
    assert livesqlbench.DATABASES.exists(), f"{livesqlbench.DATABASES} is not committed"
    databases = json.loads(livesqlbench.DATABASES.read_text())
    return dev, holdout, databases


@TICKET_128
def test_the_committed_manifest_holds_the_60_120_split_and_its_database_table() -> None:
    dev, holdout, databases = committed()

    assert len(set(dev)) == 60 and len(set(holdout)) == 120
    assert not set(dev) & set(holdout)
    assert set(databases) == set(dev) | set(holdout)
    sizes = Counter(databases.values())
    sides: dict[str, set[str]] = defaultdict(set)
    for side, ids in (("dev", dev), ("holdout", holdout)):
        for i in ids:
            sides[databases[i]].add(side)
    for database, size in sizes.items():
        if size >= 2:
            assert sides[database] == {"dev", "holdout"}, database


@TICKET_128
def test_the_committed_ids_do_not_collide_with_data_eng_bench() -> None:
    dev, holdout, _ = committed()
    manifest = json.loads(MANIFEST.read_text())
    lsb = dev + holdout
    others = set(json.loads(REGISTRY.read_text())["tasks"]) | {
        t for t in manifest["dev"] + manifest["holdout"] if not t.startswith("lsb-")
    }

    assert others, "no data-eng-bench entries to compare with"
    assert not set(lsb) & others
    assert len({t[:32] for t in lsb}) == len(lsb)
    assert not {t[:32] for t in lsb} & {t[:32] for t in others}


# da-remote pull (guards): the server manifest holds LiveSQLBench ids next to data-eng-bench's.
LSB_DEV = ["lsb-orchard_q1", "lsb-harbour_crane_maintenance_log_12"]  # the second is over 32
LSB_HOLDOUT = ["lsb-tide_q1", "lsb-harbour_crane_inspection_records_31"]
SERVER_MANIFEST = lsb_server_manifest(LSB_DEV, LSB_HOLDOUT)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    e = Env(tmp_path)
    yield e
    (e.server / "release").write_text("0")  # never leave a fake run waiting


def test_pull_of_an_ablate_run_on_lsb_dev_tasks_only_takes_the_whole_dir(env: Env) -> None:
    run = ablate_run(env, SERVER_MANIFEST, {"no-verifiers": LSB_DEV, "baseline": LSB_DEV[:1]})
    # A trial still running (no result.json yet) is judged by Harbor's trial name.
    running = f"{LSB_DEV[1][:32].rstrip('-_')}__run1234"
    (run / "jobs" / "no-verifiers" / running / "agent").mkdir(parents=True)

    assert pulled_whole(env)


def test_pull_of_an_ablate_run_with_an_lsb_holdout_trial_takes_the_summary_only(env: Env) -> None:
    run = ablate_run(env, SERVER_MANIFEST, {"no-verifiers": LSB_DEV})
    trial = run / "jobs" / "no-verifiers" / f"{LSB_HOLDOUT[0]}__x"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({"task_name": LSB_HOLDOUT[0]}))

    assert not pulled_whole(env)


def test_pull_of_an_ablate_run_with_an_unfinished_lsb_holdout_trial_takes_the_summary_only(
    env: Env,
) -> None:
    """No result.json: the trial is judged by its Harbor name, `task[:32]` without a trailing
    `-` or `_`; the holdout id here is over 32 characters and shares its first 18 with a dev id."""
    run = ablate_run(env, SERVER_MANIFEST, {"no-verifiers": LSB_DEV})
    name = f"{LSB_HOLDOUT[1][:32].rstrip('-_')}__run1234"
    (run / "jobs" / "no-verifiers" / name / "agent").mkdir(parents=True)

    assert not pulled_whole(env)
