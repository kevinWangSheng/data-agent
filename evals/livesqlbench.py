"""LiveSQLBench Base-Lite as Harbor tasks (.scratch/livesqlbench/spec.md): task generation, the
60/120 split by database, soft EX scoring and the evaluation script's entry.

Contract written by ticket 127; ticket 128 implements it and keeps the names, parameters and return
types. The acceptance tests are tests/acceptance/test_livesqlbench_*.py. The scoring names
(`Result`, `ExecutionError`, `Execute`, `clean_sql`, `results_match`, `evaluate`) live in
evals/livesqlbench_verifier.py, the evaluation script each task carries, and are re-exported here.

Task ids (spec U2): `lsb-<instance_id>`, the instance id unchanged; that is the Harbor task's
directory name, so it is the name Harbor gives the trial (`task_name` in `result.json`) and the
name the manifest's top-level `dev` and `holdout` list. The ids' first 32 characters are distinct
(`da-remote pull` judges a trial without `result.json` by `task[:32]`).

Records (upstream LiveSQLBench @ e15cd22; HF `birdsql/livesqlbench-base-lite` at `HF_REVISION`):
a public record is one line of the Base-Lite JSONL with `instance_id`, `selected_database`,
`query`, `preprocess_sql`, `clean_up_sqls`, `category` (`Query` or `Management`), `high_level`,
`conditions`, `difficulty_tier`, and the hidden fields' keys with empty values. The answer file is
a JSONL of the hidden fields keyed by `instance_id` (`sol_sql`, `test_cases`,
`external_knowledge`; upstream `integrate_gt_data.py:47` merges the same fields). A database's
public assets are the directory `<db_assets>/<selected_database>/` holding `<db>_schema.txt`,
`<db>_kb.jsonl` and `<db>_column_meaning_base.json` (upstream
`LiveSQLBench-CLI/adapters/livesqlbench/adapter.py` `_build_instruction` names them).

A generated task (ticket 128 Context 9):
- The agent works in the `main` container, the image `main_image()` that `da-remote benchmarks`
  builds on the server (evals/livesqlbench_images/Dockerfile.main), with `/app` as its working
  directory and the database's public assets mounted read-only at `/app/db_assets/`. Its
  instruction asks for the SQL in `/app/pred.json`, in upstream's format (README_TASK.md:19-27).
- The database is the compose service `postgresql`, the image `db_image(<db>)` built from the dump
  (evals/livesqlbench_images/Dockerfile.db). The agent connects with the read-only role
  `READER_ROLE`, whose URL is `DATABASE_URL_ENV` under `[environment] env`.
- The verifier (`tests/test.sh`, then `tests/livesqlbench_verifier.py` on
  `tests/task_payload.json`) runs in its own environment (`[verifier] environment_mode =
  "separate"`): Harbor collects the artifact `/app/pred.json`, stops the agent's environment,
  starts a fresh copy of the task's compose environment (a new `main` and a new database from the
  image) and uploads `pred.json` to the same path there, so no process the agent left behind is
  running when the write credential arrives. The verifier connects with the write role
  `WRITER_ROLE`, whose password is the Harbor template `${LSB_PG_WRITE_PASSWORD}` under
  `[verifier] env`, resolved from the host when verifying. A task whose record has no answer in
  the answer file still gets a task: its `[metadata] gold_answer` is false and its verifier
  scores 0 with a "no gold answer" diagnostic, a 0 that is no score.
"""

import hashlib
import json
import random
import re
import shutil
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_agent.run_config import REPO_ROOT
from evals.livesqlbench_verifier import (
    PREDICTION,
    Execute,
    ExecutionError,
    Result,
    clean_sql,
    evaluate,
    results_match,
)

__all__ = [
    "Execute",
    "ExecutionError",
    "Result",
    "Split",
    "clean_sql",
    "evaluate",
    "generate_tasks",
    "results_match",
    "split",
    "task_id",
]

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

# The public data, pinned (ticket 128 Context 5): the HF dataset at this revision.
HF_DATASET = "birdsql/livesqlbench-base-lite"
HF_REVISION = "507d7d98f477b9d6a990628ea021bd2501cfd6e2"
PUBLIC_FILE = "livesqlbench_data.jsonl"
ASSET_SUFFIXES = ("_column_meaning_base.json", "_kb.jsonl", "_schema.txt")
# What the image build files under IMAGES_DIR and the task share.
IMAGES_DIR = REPO_ROOT / "evals" / "livesqlbench_images"
VERIFIER_SCRIPT = Path(__file__).with_name("livesqlbench_verifier.py")
DB_SERVICE = "postgresql"
WRITER_ROLE = "lsb_writer"
# The agent's role can read and do nothing else (Dockerfile.db's grants), so its password is no
# secret: it reaches only the task's own database container.
READER_ROLE = "lsb_reader"
READER_PASSWORD = "lsb_reader"
DATABASE_URL_ENV = "LSB_DATABASE_URL"
# Upstream adapter.py `_write_task_toml`: agent 1800 s, verifier 1200 s, 2 CPUs, 4 GB, 20 GB.
AGENT_TIMEOUT_SEC = 1800.0
VERIFIER_TIMEOUT_SEC = 1200.0
INSTANCE_ID = re.compile(r"[A-Za-z0-9_-]+")
DATABASE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def task_id(instance_id: str) -> str:
    """The Harbor task id of a record: `lsb-<instance_id>`."""
    if not INSTANCE_ID.fullmatch(instance_id):
        raise ValueError(f"instance id {instance_id!r} is not [A-Za-z0-9_-]+")
    return f"{TASK_PREFIX}{instance_id}"


def image_version() -> str:
    """The image tag: a digest of the image build files, so a changed build gets a new tag (the
    upstream `:latest` cannot tell our builds apart, ticket 128 Context 9.8)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in IMAGES_DIR.iterdir() if p.is_file()):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def db_image(database: str) -> str:
    return f"da-lsb-db-{database}:{image_version()}"


def main_image() -> str:
    return f"da-lsb-main:{image_version()}"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{number}: not a JSON object")
        records.append(record)  # pyright: ignore[reportUnknownArgumentType]
    return records


def query_records(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [r for r in records if r.get("category") == "Query"]


def answer_index(answers: Path) -> dict[str, dict[str, Any]]:
    """The answer file's records with a non-empty `sol_sql`, by instance id."""
    return {
        str(r["instance_id"]): r
        for r in read_jsonl(answers)
        if r.get("instance_id") and r.get("sol_sql")
    }


def toml_string(value: str) -> str:
    """A TOML basic string (JSON's escapes are TOML's)."""
    return json.dumps(value, ensure_ascii=False)


def asset_sections(db_assets: Path, db: str) -> str:
    """A database's public assets as upstream's baseline prompt gives them (LiveSQLBench
    baseline/src/prompt.py `assistant_prompt` and prompt_generator.py `load_db_data_if_needed`,
    MIT, written anew): the schema file as it is, the column meanings with their keys lower-cased,
    and the knowledge base as a list of `id`, `knowledge`, `description` and `definition`, one
    entry per knowledge name (upstream keys a dict by name, so a repeated name keeps its first
    place and its last record), each `json.dumps(..., indent=2)` under upstream's headings."""
    base = db_assets / db
    schema = (base / f"{db}_schema.txt").read_text(encoding="utf-8")
    raw = json.loads((base / f"{db}_column_meaning_base.json").read_text(encoding="utf-8"))
    meanings = {str(k).lower(): v for k, v in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType, reportUnknownMemberType]
    by_name = {r["knowledge"]: r for r in read_jsonl(base / f"{db}_kb.jsonl")}
    visible = ("id", "knowledge", "description", "definition")
    knowledge = [{k: r[k] for k in visible if k in r} for r in by_name.values()]
    return (
        f"# Database Schema:\n{schema}\n\n"
        f"# Column Meanings:\n{json.dumps(meanings, indent=2)}\n\n"
        f"# External Knowledge:\n{json.dumps(knowledge, indent=2)}\n\n"
    )


def instruction(record: Mapping[str, Any], db_assets: Path | None = None) -> str:
    """The task text: the database, the question and the public assets, and upstream's answer
    format (README_TASK.md); nothing about how to solve it (spec U10), and no `conditions`,
    `external_knowledge` or credential. With `db_assets` (ticket 139, spec D3) the assets' content
    from there (`asset_sections`) also follows the files' list; without it the text is the one
    before that ticket, byte for byte."""
    db, iid = record["selected_database"], record["instance_id"]
    example = json.dumps({"instance_id": iid, "predicted_sql": ["<SQL1>", "<SQL2>", "..."]})
    return (
        "# Write PostgreSQL to answer a question\n\n"
        f"Database: `{db}` (PostgreSQL). Answer this question about it:\n\n"
        f"{record['query']}\n\n"
        "Files describing the database:\n"
        f"- `/app/db_assets/{db}_schema.txt`: its schema and sample rows\n"
        f"- `/app/db_assets/{db}_column_meaning_base.json`: what its columns mean\n"
        f"- `/app/db_assets/{db}_kb.jsonl`: the knowledge base about the database\n\n"
        + ("" if db_assets is None else asset_sections(db_assets, db))
        + "## Output format (mandatory)\n\n"
        "Write the SQL that answers the question to the file `/app/pred.json`, with exactly "
        "this JSON:\n\n"
        f"```json\n{example}\n```\n\n"
        "- `predicted_sql` is always a JSON list, even with one statement; the result of the "
        "last statement is the answer.\n"
        "- SQL strings only: no markdown fences or comments.\n"
    )


def task_toml(record: Mapping[str, Any], gold: bool) -> str:
    db = record["selected_database"]
    url = f"postgresql://{READER_ROLE}:{READER_PASSWORD}@{DB_SERVICE}:5432/{db}"
    lines = [
        "# Generated by evals.livesqlbench (ticket 128); regenerate, do not edit.",
        'version = "1.0"',
        "# Collected from the agent's environment and uploaded at the same path into the",
        "# verifier's (separate mode, [verifier] below).",
        f"artifacts = [{toml_string(str(PREDICTION))}]",
        "",
        "[metadata]",
        f"benchmark = {toml_string(BENCHMARK)}",
        'task_type = "query"',
        f"instance_id = {toml_string(record['instance_id'])}",
        f"selected_database = {toml_string(db)}",
        f"source = {toml_string(f'{HF_DATASET}@{HF_REVISION}')}",
        # False: the answers are not in hand, the verifier scores 0 and says so; no score.
        f"gold_answer = {'true' if gold else 'false'}",
        "",
        "[verifier]",
        f"timeout_sec = {VERIFIER_TIMEOUT_SEC}",
        "# A fresh copy of [environment] (main and database), started after the agent's is",
        "# stopped: nothing the agent started can read the write credential below.",
        'environment_mode = "separate"',
        "",
        "# The verification phase only (Harbor 0.24 merges it when verifying); the password is",
        "# resolved from the host then, never written here.",
        "[verifier.env]",
        f"PGHOST = {toml_string(DB_SERVICE)}",
        'PGPORT = "5432"',
        f"PGUSER = {toml_string(WRITER_ROLE)}",
        f'PGPASSWORD = "${{{WRITE_PASSWORD_ENV}}}"',
        "",
        "[agent]",
        f"timeout_sec = {AGENT_TIMEOUT_SEC}",
        "",
        "[environment]",
        "build_timeout_sec = 1200.0",
        f"docker_image = {toml_string(main_image())}",
        "cpus = 2",
        "memory_mb = 4096",
        "storage_mb = 20480",
        "",
        "# The agent's read-only connection (the adapter's --postgres-env).",
        "[environment.env]",
        f"{DATABASE_URL_ENV} = {toml_string(url)}",
        "",
    ]
    return "\n".join(lines)


def compose(db: str) -> str:
    """The task's compose services: Harbor's `main` (its image is `docker_image`) gets the assets
    read-only and waits for the database; neither service is pulled (both are local images) and
    no credential is set here (spec U6)."""
    return (
        "# Generated by evals.livesqlbench (ticket 128), after upstream LiveSQLBench-CLI\n"
        "# adapters/livesqlbench/template/environment/docker-compose.yaml without its\n"
        "# credentials.\n"
        "services:\n"
        "  main:\n"
        "    pull_policy: never\n"
        "    depends_on:\n"
        f"      {DB_SERVICE}:\n"
        "        condition: service_healthy\n"
        "    volumes:\n"
        "      - ./db_assets:/app/db_assets:ro\n"
        f"  {DB_SERVICE}:\n"
        f"    image: {db_image(db)}\n"
        "    pull_policy: never\n"
        "    volumes:\n"
        "      - ./db_init:/docker-entrypoint-initdb.d/db_assets:ro\n"
        "    healthcheck:\n"
        # Over TCP: the entrypoint's own socket-only server answers on the socket while it
        # still clones the template.
        '      test: ["CMD-SHELL", "pg_isready -U postgres -h 127.0.0.1"]\n'
        "      interval: 5s\n"
        "      timeout: 5s\n"
        "      retries: 60\n"
    )


TEST_SH = """\
#!/bin/bash
# Generated by evals.livesqlbench (ticket 128). Harbor's verifier: wait for the database, then
# score with livesqlbench_verifier.py, which writes /logs/verifier/reward.txt and diagnostics.json
# and prints no SQL or value (spec U7).
mkdir -p /logs/verifier
up='import os, socket; socket.create_connection((os.environ["PGHOST"], int(os.environ["PGPORT"])))'
for _ in $(seq 300); do
  python3 -c "$up" 2>/dev/null && break
  sleep 2
done
exec python3 /tests/livesqlbench_verifier.py
"""


def write_task(
    out: Path,
    record: Mapping[str, Any],
    answer: Mapping[str, Any] | None,
    db_assets: Path,
    *,
    inline_assets: bool = False,
) -> Path:
    iid, db = str(record["instance_id"]), str(record["selected_database"])
    if not DATABASE_NAME.fullmatch(db):
        raise ValueError(f"database name {db!r} is not an identifier")
    task_dir = out / task_id(iid)
    if task_dir.exists():
        shutil.rmtree(task_dir)
    environment = task_dir / "environment"
    (environment / "db_assets").mkdir(parents=True)
    for suffix in ASSET_SUFFIXES:
        shutil.copyfile(
            db_assets / db / f"{db}{suffix}", environment / "db_assets" / f"{db}{suffix}"
        )
    (environment / "db_init").mkdir()
    # Upstream adapter.py `_copy_db_assets`: the database's name, and the record's public
    # preprocess SQL, which the database container runs on the agent's database at start.
    (environment / "db_init" / "db_name.txt").write_text(f"{db}\n")
    preprocess = record.get("preprocess_sql") or []
    if isinstance(preprocess, str):
        preprocess = [preprocess]
    blob = "\n\n".join(str(s) for s in preprocess if str(s).strip())  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    (environment / "db_init" / "preprocess.sql").write_text(blob + "\n")
    (environment / "docker-compose.yaml").write_text(compose(db))
    (task_dir / "instruction.md").write_text(
        instruction(record, db_assets if inline_assets else None)
    )
    (task_dir / "task.toml").write_text(task_toml(record, answer is not None))
    tests = task_dir / "tests"
    tests.mkdir()
    given = answer or {}
    hidden = {k: given[k] for k in ("sol_sql", "test_cases", "external_knowledge") if k in given}
    payload = dict(record) | hidden
    (tests / "task_payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    shutil.copyfile(VERIFIER_SCRIPT, tests / VERIFIER_SCRIPT.name)
    (tests / "test.sh").write_text(TEST_SH)
    (tests / "test.sh").chmod(0o755)
    return task_dir


def generate_tasks(
    public: Path,
    answers: Path,
    db_assets: Path,
    out: Path,
    *,
    write_password: str,
    inline_assets: bool = False,
) -> list[Path]:
    """Write one Harbor task directory `out/<task_id>` per `Query` record of the public JSONL
    `public` and return their paths; a `Management` record gets none (spec Q2: no CRUD tasks).

    The task is a `query` task on its database: `task.toml` has `task_type = "query"` under
    `[metadata]`, the instruction has the record's `query` and its `selected_database`, and the
    agent sees the database's public assets from `db_assets` (spec U8) unchanged, as files under
    `environment/` (mounted into its compose `main` service). The hidden fields from `answers` go
    only where the verifier reads them (`tests/task_payload.json`): none of `sol_sql`,
    `test_cases` or `external_knowledge`, nor the answer file's path, is in the instruction or
    under `environment/` (spec U7, U8; unlike upstream adapter.py:270, which writes
    `external_knowledge` into the instruction). A record the answer file has no `sol_sql` for
    still gets its task, marked `gold_answer = false` (module docstring).

    `write_password` is the database's write credential, which the verifier needs (upstream
    `template/tests/evaluator.py` creates and drops `<db>_process_1`). It is given to the
    verification phase only, as the `${VAR}` template of `WRITE_PASSWORD_ENV` under
    `[verifier] env`, which Harbor resolves from the host when verifying; no task file holds it
    (checked here). The agent's container (`environment/`, `[environment] env`) gets neither it,
    nor that variable's name, nor upstream's default `123123` (spec U6).

    `inline_assets` (ticket 139, spec D3; off by default) also writes the assets' content into
    each instruction (`asset_sections`), the files staying where they are; every other file of
    the task is the same as with it off.
    """
    if not write_password:
        raise ValueError("write_password is empty")
    hidden = answer_index(answers)
    out.mkdir(parents=True, exist_ok=True)
    tasks = [
        write_task(
            out,
            record,
            hidden.get(str(record["instance_id"])),
            db_assets,
            inline_assets=inline_assets,
        )
        for record in query_records(read_jsonl(public))
    ]
    for task_dir in tasks:
        for path in task_dir.rglob("*"):
            if path.is_file() and write_password.encode() in path.read_bytes():
                raise ValueError(f"{path} holds the write credential")
    return tasks


def has_gold_answer(task_dir: Path) -> bool:
    """Whether a generated task's verifier has the gold answer (`[metadata] gold_answer`); a
    directory that is not a generated task has none."""
    try:
        config = tomllib.loads((task_dir / "task.toml").read_text())
    except (OSError, ValueError):
        return False
    return config.get("metadata", {}).get("gold_answer") is True


@dataclass(frozen=True)
class Split:
    """The dev and holdout task ids, and each id's database (the `DATABASES` table)."""

    dev: list[str]
    holdout: list[str]
    databases: dict[str, str]


def dev_counts(sizes: Mapping[str, int], dev_size: int, rng: random.Random) -> dict[str, int]:
    """How many dev tasks each database gets: its share of `dev_size`, rounded by largest
    remainder (ties in an order drawn from `rng`), and at least one on each side for a database
    with 2 or more tasks."""
    total = sum(sizes.values())
    if not 0 <= dev_size <= total:
        raise ValueError(f"dev_size {dev_size} is not between 0 and the {total} records")
    order = sorted(sizes)
    rng.shuffle(order)
    low = {d: 1 if sizes[d] >= 2 else 0 for d in order}
    high = {d: sizes[d] - 1 if sizes[d] >= 2 else sizes[d] for d in order}
    if not sum(low.values()) <= dev_size <= sum(high.values()):
        raise ValueError(f"no split of {dev_size} dev tasks puts every database on both sides")
    share = {d: sizes[d] * dev_size / total for d in order}
    counts = {d: min(max(int(share[d]), low[d]), high[d]) for d in order}
    by_remainder = sorted(order, key=lambda d: share[d] - int(share[d]), reverse=True)
    # Each pass moves each database by at most one, largest remainders first when adding.
    while (left := dev_size - sum(counts.values())) != 0:
        step = 1 if left > 0 else -1
        for d in by_remainder if step > 0 else list(reversed(by_remainder)):
            if left != 0 and low[d] <= counts[d] + step <= high[d]:
                counts[d] += step
                left -= step
    return counts


def split(
    records: Sequence[Mapping[str, Any]],
    seed: int,
    dev_size: int = DEV_SIZE,
) -> Split:
    """Split `Query` records into `dev_size` dev and the rest holdout tasks, at random with
    `seed` and stratified by `selected_database` (spec U4): the same seed gives the same split,
    and every database with at least 2 records has tasks on both sides (ticket 127's reading of
    the spec's "库的题数允许时"). Reads `instance_id`, `selected_database` and `category` only."""
    by_db: dict[str, list[str]] = {}
    for record in query_records(records):
        by_db.setdefault(str(record["selected_database"]), []).append(
            task_id(str(record["instance_id"]))
        )
    ids = [t for tasks in by_db.values() for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("instance ids repeat")
    rng = random.Random(seed)
    counts = dev_counts({d: len(t) for d, t in by_db.items()}, dev_size, rng)
    dev: list[str] = []
    for db in sorted(by_db):
        dev.extend(rng.sample(sorted(by_db[db]), counts[db]))
    chosen = set(dev)
    databases = {t: db for db, tasks in by_db.items() for t in tasks}
    return Split(
        dev=sorted(dev),
        holdout=sorted(t for t in ids if t not in chosen),
        databases=dict(sorted(databases.items())),
    )
