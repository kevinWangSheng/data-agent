"""LiveSQLBench Base-Lite as Harbor tasks (.scratch/livesqlbench/spec.md): task generation, the
60/120 split by database, soft EX scoring and the evaluation script's entry.

Contract written by ticket 127; ticket 128 implements it (every body below raises
`NotImplementedError("ticket 128")` until then) and keeps the names, parameters and return types.
The acceptance tests are tests/acceptance/test_livesqlbench_*.py.

Task ids (spec U2): `lsb-<instance_id>`, the instance id unchanged; that is the Harbor task's
directory name, so it is the name Harbor gives the trial (`task_name` in `result.json`) and the
name the manifest's top-level `dev` and `holdout` list. The ids' first 32 characters are distinct
(`da-remote pull` judges a trial without `result.json` by `task[:32]`).

Records (upstream LiveSQLBench @ e15cd22): a public record is one line of the Base-Lite JSONL
with `instance_id`, `selected_database`, `query`, `preprocess_sql`, `clean_up_sql`, `category`
(`Query` or `Management`), `high_level`, `conditions`, `difficulty_tier`. The answer file is a
JSONL of the hidden fields keyed by `instance_id` (`sol_sql`, `test_cases`, `external_knowledge`;
upstream `integrate_gt_data.py:47` merges the same fields). A database's public assets are the
directory `<db_assets>/<selected_database>/` holding `<db>_schema.txt`, `<db>_kb.jsonl` and
`<db>_column_meaning_base.json` (upstream `LiveSQLBench-CLI/adapters/livesqlbench/adapter.py`
`_build_instruction` names them).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_agent.run_config import REPO_ROOT

# The Harbor adapter's `benchmark` kwarg for these tasks (`--ak benchmark=livesqlbench`, spec U5).
BENCHMARK = "livesqlbench"
TASK_PREFIX = "lsb-"
DEV_SIZE = 60
HOLDOUT_SIZE = 120
# The task id -> database table, committed beside the manifest: a JSON object
# `{"lsb-<instance_id>": "<selected_database>", ...}` covering exactly the manifest's LiveSQLBench
# ids (spec U2).
DATABASES = REPO_ROOT / "evals" / "livesqlbench_databases.json"
# The host variable holding the write credential at verification time, for a generated task that
# names it as a Harbor template (`PGPASSWORD = "${LSB_PG_WRITE_PASSWORD}"` under `[verifier.env]`)
# instead of writing `write_password` itself.
WRITE_PASSWORD_ENV = "LSB_PG_WRITE_PASSWORD"


def task_id(instance_id: str) -> str:
    """The Harbor task id of a record: `lsb-<instance_id>`."""
    raise NotImplementedError("ticket 128")


def generate_tasks(
    public: Path, answers: Path, db_assets: Path, out: Path, *, write_password: str
) -> list[Path]:
    """Write one Harbor task directory `out/<task_id>` per `Query` record of the public JSONL
    `public` and return their paths; a `Management` record gets none (spec Q2: no CRUD tasks).

    The task is a `query` task on its database: `task.toml` has `task_type = "query"` under
    `[metadata]`, the instruction has the record's `query` and its `selected_database`, and the
    agent sees the database's public assets from `db_assets` (spec U8) unchanged, as files under
    `environment/` (in its image or mounted into its compose `main` service). The hidden fields
    from `answers` go only where the verifier reads them: none of `sol_sql`, `test_cases` or
    `external_knowledge`, nor the answer file's path, is in the instruction or under
    `environment/` (spec U7, U8; unlike upstream adapter.py:270, which writes
    `external_knowledge` into the instruction).

    `write_password` is the database's write credential, which the verifier needs (upstream
    `template/tests/evaluator.py` creates and drops `<db>_process_1`). It is given to the
    verification phase only: Harbor's `[verifier] env`, as the value or as a `${VAR}` template of
    `WRITE_PASSWORD_ENV`, which Harbor resolves from the host when verifying. The agent's
    container (`environment/`, `[environment] env`) gets neither it, nor that variable's name, nor
    upstream's default `123123` (spec U6).
    """
    raise NotImplementedError("ticket 128")


@dataclass(frozen=True)
class Split:
    """The dev and holdout task ids, and each id's database (the `DATABASES` table)."""

    dev: list[str]
    holdout: list[str]
    databases: dict[str, str]


def split(
    records: Sequence[Mapping[str, Any]],
    seed: int,
    dev_size: int = DEV_SIZE,
) -> Split:
    """Split `Query` records into `dev_size` dev and the rest holdout tasks, at random with
    `seed` and stratified by `selected_database` (spec U4): the same seed gives the same split,
    and every database with at least 2 records has tasks on both sides (ticket 127's reading of
    the spec's "库的题数允许时")."""
    raise NotImplementedError("ticket 128")


@dataclass(frozen=True)
class Result:
    """A query's result: its column names and rows, as the database driver returned them."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


class ExecutionError(Exception):
    """A SQL statement failed or timed out; the message is the driver's."""


# Runs a list of statements on the task's database and returns the last one's result; raises
# ExecutionError. The evaluation script's real one connects with the verifier's credential.
Execute = Callable[[Sequence[str]], Result]


def clean_sql(sqls: Sequence[str]) -> list[str]:
    """Upstream soft EX's cleaning of a SQL list, in its order (`evaluation/src/test_utils.py`
    `test_case_default`, :398-403): remove comments, then `DISTINCT` (keeping `DISTINCT ON`), then
    `ROUND(...)` (keeping its first argument, nested ones too). Ticket 128 copies the upstream
    functions (MIT, spec U9) rather than rewriting them."""
    raise NotImplementedError("ticket 128")


def results_match(predicted: Result, gold: Result, conditions: Mapping[str, Any] | None) -> bool:
    """Upstream `ex_base`'s comparison (test_utils.py:276-285) of two fetched results: both
    go through `preprocess_results` (dates as `YYYY-MM-DD`, decimals and floats rounded half up
    to 2 places, dicts and lists as sorted-key JSON); an empty side does not match; rows compare
    as a list when `conditions["order"]` is true, otherwise as a set; column names are not
    compared."""
    raise NotImplementedError("ticket 128")


def evaluate(
    payload: Mapping[str, Any], predicted_sql: Sequence[str], execute: Execute, out: Path
) -> int:
    """Score one `Query` task (the evaluation script's entry): run `payload`'s `preprocess_sql`,
    then the predicted and the gold SQL (`payload["sol_sql"]`), both through `clean_sql`, and
    compare with `results_match` under `payload["conditions"]`. An `ExecutionError` on either side
    scores 0. Writes the reward (`1` or `0`) to `out/reward.txt` and returns it.

    `payload` is the public record merged with its hidden fields. How the evaluation script reads
    it and the agent's SQL (upstream's `/app/pred.json`) is ticket 128's (spec U10).

    Whatever it writes under `out`, prints or logs is the reward and diagnostics without the
    answer: no gold SQL and no gold result value (spec U7; unlike upstream
    `evaluation/src/db_utils.py:302`, which logs every query).
    """
    raise NotImplementedError("ticket 128")
