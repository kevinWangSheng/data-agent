"""`python -m evals livesqlbench <step>`: LiveSQLBench's data, split, images and tasks (ticket 128).

    fetch      the public HF data at its pinned revision (evals.livesqlbench.HF_REVISION) into
               ROOT/data/: the JSONL and each Query database's three public asset files; files
               already there are kept (a pinned revision does not change)
    split      the 60/120 split of the public JSONL with --seed: the manifest's top-level `dev` and
               `holdout` get the `lsb-` ids (replacing earlier ones, data-eng-bench's entries
               untouched) and `livesqlbench_seed` / `livesqlbench_source`; DATABASES gets the id ->
               database table. Prints each database's count on each side, nothing else of a record
               (ADR-0004: holdout questions are not looked at)
    tasks      the Harbor tasks of the public JSONL into --out, the answers from --answers (an empty
               file: no task can be scored); the write credential comes from $LSB_PG_WRITE_PASSWORD
    prepare    on the server (`da-remote benchmarks`): fetch, the dump (downloaded once, its sha256
               recorded then and compared on every later call; unpacked), the 18 database images
               and the `main` image (`docker build`, the write credential as a build secret from
               $LSB_PG_WRITE_PASSWORD), each image's table and column counts summed against
               upstream's, then the tasks; steps already done are skipped
    check      on the server (`da-remote check`): readiness lines `ok`/`FAIL`
    readonly   on the server: the read-only checks of one database image (pass/fail lines only)

Nothing printed names a credential or a server address.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from evals import livesqlbench
from evals.livesqlbench import (
    ASSET_SUFFIXES,
    DATABASES,
    HF_DATASET,
    HF_REVISION,
    PUBLIC_FILE,
    WRITE_PASSWORD_ENV,
)

HF_URL = "https://huggingface.co/datasets/{dataset}/resolve/{revision}/{path}"
# Base-Lite's dumps (upstream LiveSQLBench-CLI/README.md:53): no checksum upstream, so the first
# download's sha256 is recorded and every later call compares against it.
DUMP_FILE_ID = "1QIGQlRKbkqApAOrQXPqFJgUg8rQ7HRRZ"
GDOWN = "gdown==5.2.0"
# Upstream README.md:204-207 (evaluation/check_db_metadata.py's counts): Base-Lite's 18 databases.
EXPECTED_TABLES = 175
EXPECTED_COLUMNS = 2286
# Written by Dockerfile.db's bootstrap into each image: "<tables> <columns>" of its template.
COUNTS_FILE = "/opt/livesqlbench/counts.txt"
NO_ANSWERS = "no gold answers: the tasks can only be smoke-tested, never scored"

type Run = Callable[..., subprocess.CompletedProcess[str]]


def say(line: str) -> None:
    print(line, flush=True)


def download(url: str, path: Path) -> None:
    """`url` into `path`, through a temporary file beside it, so a cut download leaves nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp,
        urllib.request.urlopen(url, timeout=120) as response,
    ):
        shutil.copyfileobj(response, tmp)
    Path(tmp.name).replace(path)


def hf_url(path: str) -> str:
    return HF_URL.format(dataset=HF_DATASET, revision=HF_REVISION, path=path)


def query_databases(public: Path) -> list[str]:
    return sorted(
        {str(r["selected_database"]) for r in livesqlbench.query_records(read_public(public))}
    )


def read_public(public: Path) -> list[dict[str, Any]]:
    return livesqlbench.read_jsonl(public)


def fetch(data: Path, get: Callable[[str, Path], None] = download) -> int:
    """The public JSONL and the Query databases' assets into `data` (`data/<db>/...`, the layout
    `generate_tasks` reads); returns how many files were fetched now."""
    fetched = 0
    public = data / PUBLIC_FILE
    if not public.is_file():
        get(hf_url(PUBLIC_FILE), public)
        fetched += 1
    for db in query_databases(public):
        for suffix in ASSET_SUFFIXES:
            path = data / db / f"{db}{suffix}"
            if not path.is_file():
                get(hf_url(f"{db}/{db}{suffix}"), path)
                fetched += 1
    return fetched


def projected(public: Path) -> list[dict[str, Any]]:
    """The records with the split's three fields only, so nothing else of a record is held."""
    keys = ("instance_id", "selected_database", "category")
    return [{k: r.get(k) for k in keys} for r in read_public(public)]


def write_split(public: Path, seed: int, manifest: Path, databases: Path) -> livesqlbench.Split:
    """The split into the manifest (top-level `dev`/`holdout` after data-eng-bench's entries, and
    the seed and source keys) and the database table; the same input writes the same bytes."""
    result = livesqlbench.split(projected(public), seed)
    data = json.loads(manifest.read_text())
    prefix = livesqlbench.TASK_PREFIX
    data["dev"] = [t for t in data["dev"] if not t.startswith(prefix)] + result.dev
    data["holdout"] = [t for t in data["holdout"] if not t.startswith(prefix)] + result.holdout
    data["livesqlbench_seed"] = seed
    data["livesqlbench_source"] = f"{HF_DATASET}@{HF_REVISION[:7]}"
    manifest.write_text(json.dumps(data, indent=2) + "\n")
    databases.write_text(json.dumps(result.databases, indent=2) + "\n")
    return result


def split_counts(result: livesqlbench.Split) -> list[str]:
    dev = Counter(result.databases[t] for t in result.dev)
    holdout = Counter(result.databases[t] for t in result.holdout)
    lines = [f"{db}: dev {dev[db]}, holdout {holdout[db]}" for db in sorted(dev | holdout)]
    return [*lines, f"total: dev {len(result.dev)}, holdout {len(result.holdout)}"]


def write_password() -> str:
    password = os.environ.get(WRITE_PASSWORD_ENV, "")
    if not password:
        raise SystemExit(f"livesqlbench: ${WRITE_PASSWORD_ENV} is not set")
    return password


def generate(public: Path, answers: Path, db_assets: Path, out: Path) -> list[str]:
    tasks = livesqlbench.generate_tasks(
        public, answers, db_assets, out, write_password=write_password()
    )
    gold = sum(livesqlbench.has_gold_answer(t) for t in tasks)
    lines = [f"tasks: {len(tasks)} in {out}, {gold} with a gold answer"]
    if gold == 0:
        lines.append(f"tasks: {NO_ANSWERS}")
    return lines


# --- the server steps ---


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_dump(root: Path, run: Run) -> Path:
    """The dump archive at ROOT/dump/archive, downloaded once with gdown (pinned) and checked
    against the sha256 recorded at the first download; stops on a mismatch."""
    archive = root / "dump" / "archive"
    recorded = root / "dump" / "archive.sha256"
    if not archive.is_file():
        archive.parent.mkdir(parents=True, exist_ok=True)
        partial = archive.with_suffix(".partial")
        cmd = ["uv", "run", "--no-project", "--with", GDOWN, "gdown", DUMP_FILE_ID]
        done = run([*cmd, "-O", str(partial)], capture_output=True, text=True)
        if done.returncode != 0 or not partial.is_file():
            tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
            raise SystemExit(f"livesqlbench: the dump download failed: {' / '.join(tail)}")
        partial.replace(archive)
        say("dump: downloaded")
    digest = sha256(archive)
    if not recorded.is_file():
        recorded.write_text(digest + "\n")
        say(f"dump: sha256 {digest}, recorded (first download)")
    elif recorded.read_text().strip() != digest:
        raise SystemExit(
            f"livesqlbench: the dump's sha256 {digest} is not the recorded "
            f"{recorded.read_text().strip()}; stop for a human"
        )
    else:
        say(f"dump: sha256 {digest}, matches the recorded one")
    return archive


def unpack(archive: Path, into: Path) -> None:
    """The archive (zip or tar) into `into`, once; refuses members outside it."""
    if into.is_dir():
        return
    partial = into.with_name(into.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if not (partial / name).resolve().is_relative_to(partial.resolve()):
                    raise SystemExit(f"livesqlbench: the dump holds a path outside it: {name}")
            z.extractall(partial)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            t.extractall(partial, filter="data")
    else:
        raise SystemExit("livesqlbench: the dump is neither a zip nor a tar archive")
    partial.replace(into)


def dump_dirs(dumps: Path, databases: Sequence[str]) -> dict[str, Path]:
    """Each database's dump directory under `dumps`, named `<db>` or `<db>_template` at any depth
    (upstream build_db_images.sh takes either); stops when one is missing or found twice."""
    found: dict[str, list[Path]] = {db: [] for db in databases}
    for path in sorted(p for p in dumps.rglob("*") if p.is_dir()):
        # An archive made on macOS mirrors every file under __MACOSX/ as `._<name>`.
        if any(
            part == "__MACOSX" or part.startswith(".") for part in path.relative_to(dumps).parts
        ):
            continue
        name = path.name.removesuffix("_template")
        if name in found and any(not p.name.startswith(".") for p in path.glob("*.sql")):
            found[name].append(path)
    missing = [db for db, paths in found.items() if not paths]
    twice = [db for db, paths in found.items() if len(paths) > 1]
    if missing or twice:
        raise SystemExit(
            f"livesqlbench: dump directories missing for {missing}, found twice for {twice}"
        )
    return {db: paths[0] for db, paths in found.items()}


def image_exists(run: Run, image: str) -> bool:
    return (
        run(["docker", "image", "inspect", image], capture_output=True, text=True).returncode == 0
    )


def build(run: Run, args: list[str], image: str) -> None:
    # `--secret` and `RUN --mount` need BuildKit.
    env = os.environ | {"DOCKER_BUILDKIT": "1"}
    done = run(["docker", "build", "-q", *args], capture_output=True, text=True, env=env)
    if done.returncode != 0:
        tail = (done.stdout + done.stderr).strip().splitlines()[-5:]
        raise SystemExit(f"livesqlbench: docker build of {image} failed:\n" + "\n".join(tail))


def table_order(dump: Path, db: str) -> str | None:
    """The dump's table restore order, as upstream build_db_images.sh `write_table_order_file`
    finds it beside the database's directory: a `<db>_table_order(s).txt`, else the line of an
    `init-databases*.sh` naming it; None leaves init-db.sh's DATABASE_MAPPING to decide."""
    root, names = dump.parent, (db, dump.name)
    for name in names:
        for suffix in ("_table_orders.txt", "_table_order.txt"):
            candidate = root / f"{name}{suffix}"
            if candidate.is_file() and candidate.stat().st_size:
                return candidate.read_text()
    for script in sorted(root.glob("init-databases*.sh")):
        for line in script.read_text(errors="replace").splitlines():
            found = re.fullmatch(rf'\s*\["{re.escape(dump.name)}"\]="(.*)"\s*', line)
            if found:
                return found.group(1) + "\n"
    return None


def build_db_image(run: Run, db: str, dump: Path, work: Path) -> None:
    """One database's image (upstream build_db_images.sh, rewritten): the build context holds the
    image files and the dump, the write credential is a build secret from the environment
    (never a build argument, ENV or file in the image)."""
    image = livesqlbench.db_image(db)
    context = work / f"build-{db}"
    shutil.rmtree(context, ignore_errors=True)
    shutil.copytree(livesqlbench.IMAGES_DIR, context / "environment")
    shutil.copytree(dump, context / "db_seed" / "db_dump")
    (context / "db_seed" / "db_name.txt").write_text(f"{db}\n")
    if (order := table_order(dump, db)) is not None:
        (context / "db_seed" / "table_order.txt").write_text(order)
    secret = f"id=lsb_write_password,env={WRITE_PASSWORD_ENV}"
    file = str(context / "environment" / "Dockerfile.db")
    try:
        build(run, ["--no-cache", "--secret", secret, "-t", image, "-f", file, str(context)], image)
    finally:
        shutil.rmtree(context, ignore_errors=True)


def image_counts(run: Run, image: str) -> tuple[int, int]:
    done = run(
        ["docker", "run", "--rm", "--entrypoint", "cat", image, COUNTS_FILE],
        capture_output=True,
        text=True,
    )
    try:
        tables, columns = (int(n) for n in done.stdout.split())
    except ValueError as exc:
        raise SystemExit(f"livesqlbench: cannot read {COUNTS_FILE} in {image}") from exc
    return tables, columns


def images(run: Run, root: Path, databases: Sequence[str]) -> None:
    """The database images missing, from the dump, and the `main` image; then the counts."""
    need = [db for db in databases if not image_exists(run, livesqlbench.db_image(db))]
    archive = root / "dump" / "archive"
    if need or archive.is_file():
        fetch_dump(root, run)  # downloads when missing; the recorded sha256 is compared each call
    if need:
        unpack(archive, root / "dumps")
        dirs = dump_dirs(root / "dumps", databases)
        for db in need:
            say(f"image {livesqlbench.db_image(db)}: building")
            build_db_image(run, db, dirs[db], root)
    else:
        say(f"images: all {len(databases)} database images present")
    main = livesqlbench.main_image()
    if image_exists(run, main):
        say(f"image {main}: present")
    else:
        say(f"image {main}: building")
        file = str(livesqlbench.IMAGES_DIR / "Dockerfile.main")
        build(run, ["-t", main, "-f", file, str(livesqlbench.IMAGES_DIR)], main)
    tables = columns = 0
    for db in databases:
        t, c = image_counts(run, livesqlbench.db_image(db))
        tables, columns = tables + t, columns + c
    line = f"{tables} tables, {columns} columns (upstream: {EXPECTED_TABLES}, {EXPECTED_COLUMNS})"
    if (tables, columns) != (EXPECTED_TABLES, EXPECTED_COLUMNS):
        raise SystemExit(f"livesqlbench: the databases hold {line}; stop for a human")
    say(f"databases: {line}")


def prepare(root: Path, answers: Path, run: Run = subprocess.run) -> None:
    write_password()  # before any step: the images and the tasks need it
    data = root / "data"
    say(f"data: {fetch(data)} file(s) fetched, {HF_DATASET}@{HF_REVISION[:7]}")
    databases = sorted(set(json.loads(DATABASES.read_text()).values()))
    if databases != query_databases(data / PUBLIC_FILE):
        raise SystemExit(f"livesqlbench: {DATABASES} and the public data name other databases")
    images(run, root, databases)
    if not answers.is_file():
        answers = root / "no-answers.jsonl"
        answers.write_text("")
    for line in generate(data / PUBLIC_FILE, answers, data, root / "tasks"):
        say(line)


def check(root: Path, run: Run = subprocess.run) -> int:
    """Readiness lines in `da-remote check`'s format; returns the failures."""
    fails = 0

    def line(ok: bool, text: str) -> None:
        nonlocal fails
        fails += not ok
        say(f"  {'ok  ' if ok else 'FAIL'}  {text}")

    public = root / "data" / PUBLIC_FILE
    databases = sorted(set(json.loads(DATABASES.read_text()).values()))
    assets = [root / "data" / db / f"{db}{s}" for db in databases for s in ASSET_SUFFIXES]
    present = public.is_file() and all(p.is_file() for p in assets)
    line(
        present,
        f"LiveSQLBench data at {HF_REVISION[:7]}: {sum(p.is_file() for p in assets)} of "
        f"{len(assets)} asset files" + ("" if public.is_file() else ", no public JSONL"),
    )
    images_found = [
        livesqlbench.db_image(db)
        for db in databases
        if image_exists(run, livesqlbench.db_image(db))
    ]
    line(
        len(images_found) == len(databases),
        f"LiveSQLBench database images: {len(images_found)} of {len(databases)}",
    )
    line(
        image_exists(run, livesqlbench.main_image()),
        f"LiveSQLBench image {livesqlbench.main_image()}",
    )
    tasks = sorted(p for p in (root / "tasks").glob(f"{livesqlbench.TASK_PREFIX}*") if p.is_dir())
    gold = sum(livesqlbench.has_gold_answer(t) for t in tasks)
    line(
        len(tasks) == livesqlbench.DEV_SIZE + livesqlbench.HOLDOUT_SIZE,
        f"LiveSQLBench tasks: {len(tasks)} in {root / 'tasks'}, {gold} with a gold answer"
        + ("" if gold else f" ({NO_ANSWERS})"),
    )
    return fails


# --- the read-only check (ticket 128 Acceptance "只读实测") ---

PROBE = r"""
import os
import psycopg

url = os.environ["LSB_DATABASE_URL"]
fails = 0


def result(ok, text):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + text, flush=True)


def refused(conn, statement):
    try:
        conn.execute(statement)
    except psycopg.Error:
        conn.rollback()
        return True
    conn.rollback()
    return False


with psycopg.connect(url, connect_timeout=10) as conn:
    # Past the role's read-only default: the privileges alone must refuse each write.
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
    conn.commit()
    table, column = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position LIMIT 1"
    ).fetchone()
    conn.rollback()
    t, c = f'"{table}"', f'"{column}"'
    result(conn.execute(f"SELECT count(*) FROM {t}").fetchone() is not None, "read-only role reads")
    conn.rollback()
    for label, statement in [
        ("CREATE TABLE", "CREATE TABLE lsb_probe (x int)"),
        ("CREATE TEMP TABLE", "CREATE TEMP TABLE lsb_probe (x int)"),
        ("INSERT", f"INSERT INTO {t} DEFAULT VALUES"),
        ("UPDATE", f"UPDATE {t} SET {c} = {c}"),
        ("DELETE", f"DELETE FROM {t}"),
        ("DROP", f"DROP TABLE {t}"),
    ]:
        result(refused(conn, statement), f"read-only role: {label} refused")

host = psycopg.conninfo.conninfo_to_dict(url)
for user in ("postgres", "root", "lsb_writer", "lsb_reader"):
    for label, password in (("no password", None), ("123123", "123123")):
        try:
            psycopg.connect(
                host=host["host"], port=host["port"], dbname=host["dbname"], user=user,
                password=password, connect_timeout=10,
            ).close()
            ok = False
        except psycopg.Error:
            ok = True
        result(ok, f"{user} with {label} cannot connect")
raise SystemExit(1 if fails else 0)
"""


WRITER_PROBE = r"""
import os
import sys

sys.path.insert(0, "/tests")
from livesqlbench_verifier import PostgresExecute

text = "the write role clones, queries and drops the process database"
execute = PostgresExecute(os.environ["LSB_DATABASE"], os.environ)
try:
    ok = execute(["SELECT 1"]).rows == ((1,),)
    execute.close()
    print(("PASS " if ok else "FAIL ") + text)
except Exception as exc:
    print(f"FAIL {text} ({type(exc).__name__})")
"""


def task_environment(task_dir: Path) -> tuple[str, dict[str, str], Path]:
    """A generated task's database, the variables its `main` container gets (`[environment]
    env`, as Harbor passes them; the compose file sets none), and its `db_init` directory."""
    config = tomllib.loads((task_dir / "task.toml").read_text())
    database = str(config["metadata"]["selected_database"])
    env = {str(k): str(v) for k, v in config["environment"]["env"].items()}
    return database, env, task_dir / "environment" / "db_init"


def readonly(task_dir: Path, run: Run = subprocess.run) -> int:
    """Start the task's database image and a `main` container beside it on a network of their
    own, the database reachable by its compose name and `main` given the task's own variables;
    from `main` check the read-only role and the credentials that must not work, then that
    `main`'s environment holds no write credential. Prints PASS/FAIL lines only; returns the
    failures."""
    password = write_password()
    database, task_env, init = task_environment(task_dir)
    tag = hashlib.sha256(os.urandom(8)).hexdigest()[:8]
    network, db_name, main_name = f"da-lsb-ro-{tag}", f"da-lsb-ro-db-{tag}", f"da-lsb-ro-main-{tag}"
    env_args = [a for k, v in task_env.items() for a in ("-e", f"{k}={v}")]
    fails = 0
    try:
        run(["docker", "network", "create", network], capture_output=True, text=True, check=True)
        run(
            [
                *("docker", "run", "-d", "--name", db_name, "--network", network),
                *("--network-alias", livesqlbench.DB_SERVICE),
                *("-v", f"{init.resolve()}:/docker-entrypoint-initdb.d/db_assets:ro"),
                livesqlbench.db_image(database),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        for _ in range(120):
            ready = [
                "docker",
                "exec",
                db_name,
                "pg_isready",
                "-U",
                "postgres",
                "-h",
                "127.0.0.1",
            ]
            if run(ready, capture_output=True, text=True).returncode == 0:
                break
            run(["sleep", "2"], capture_output=True, text=True)
        probe = run(
            [
                *("docker", "run", "-i", "--name", main_name, "--network", network),
                *env_args,
                *(livesqlbench.main_image(), "python3", "-"),
            ],
            input=PROBE,
            capture_output=True,
            text=True,
        )
        lines = [line for line in probe.stdout.splitlines() if line.startswith(("PASS", "FAIL"))]
        for line in lines:
            say(line)
        fails += sum(line.startswith("FAIL") for line in lines)
        if probe.returncode != 0 and not fails:
            fails += 1
            say(f"FAIL the probe did not finish (exit {probe.returncode})")
        env = run(
            ["docker", "inspect", "-f", "{{json .Config.Env}}", main_name],
            capture_output=True,
            text=True,
        )
        clean = env.returncode == 0 and password not in env.stdout
        fails += not clean
        say(
            f"{'PASS' if clean else 'FAIL'} the main container's environment holds no "
            "write credential"
        )
        # The verifier's side, in a container of its own: the task's tests/ and the write role,
        # the credential passed by name from this process's environment (never in argv).
        writer = run(
            [
                *("docker", "run", "-i", "--rm", "--network", network),
                *("-e", f"PGHOST={livesqlbench.DB_SERVICE}", "-e", "PGPORT=5432"),
                *("-e", f"PGUSER={livesqlbench.WRITER_ROLE}", "-e", "PGPASSWORD"),
                *("-e", f"LSB_DATABASE={database}"),
                *("-v", f"{(task_dir / 'tests').resolve()}:/tests:ro"),
                *(livesqlbench.main_image(), "python3", "-"),
            ],
            input=WRITER_PROBE,
            capture_output=True,
            text=True,
            env=os.environ | {"PGPASSWORD": password},
        )
        line = next(
            (x for x in writer.stdout.splitlines() if x.startswith(("PASS", "FAIL"))),
            f"FAIL the write role's check did not finish (exit {writer.returncode})",
        )
        fails += not line.startswith("PASS")
        say(line)
    finally:
        for name in (main_name, db_name):
            run(["docker", "rm", "-f", name], capture_output=True, text=True)
        run(["docker", "network", "rm", network], capture_output=True, text=True)
    return fails


def count_credential(root: Path) -> int:
    """How often the write credential ($LSB_PG_WRITE_PASSWORD) occurs in the files under `root`
    (a run directory, ticket 128 "口令不进运行产出"); only the count is ever printed."""
    needle = write_password().encode()
    return sum(p.read_bytes().count(needle) for p in sorted(root.rglob("*")) if p.is_file())


# --- the command line ---


def run_step(args: argparse.Namespace) -> int:
    if args.step == "fetch":
        say(f"data: {fetch(args.data)} file(s) fetched into {args.data}")
    elif args.step == "split":
        for line in split_counts(
            write_split(args.public, args.seed, args.manifest, args.databases)
        ):
            say(line)
    elif args.step == "tasks":
        for line in generate(args.public, args.answers, args.db_assets, args.out):
            say(line)
    elif args.step == "prepare":
        prepare(args.root, args.answers)
    elif args.step == "check":
        return 1 if check(args.root) else 0
    elif args.step == "readonly":
        return 1 if readonly(args.task) else 0
    elif args.step == "leaks":
        found = count_credential(args.dir)
        say(f"write credential: {found} occurrence(s) under {args.dir}")
        return 1 if found else 0
    return 0


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser("livesqlbench", help="LiveSQLBench data, split, images, tasks")
    steps = parser.add_subparsers(dest="step", required=True)
    p = steps.add_parser("fetch", help="the public HF data at its pinned revision")
    p.add_argument("--data", type=Path, required=True)
    p = steps.add_parser("split", help="the 60/120 split into the manifest and DATABASES")
    p.add_argument("--public", type=Path, required=True, help=PUBLIC_FILE)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument(
        "--manifest", type=Path, default=livesqlbench.REPO_ROOT / "evals" / "manifest.json"
    )
    p.add_argument("--databases", type=Path, default=DATABASES)
    p = steps.add_parser("tasks", help=f"Harbor tasks; the credential from ${WRITE_PASSWORD_ENV}")
    p.add_argument("--public", type=Path, required=True, help=PUBLIC_FILE)
    p.add_argument("--answers", type=Path, required=True, help="JSONL of the hidden fields")
    p.add_argument("--db-assets", type=Path, required=True, help="<db>/<db>_*.{json,jsonl,txt}")
    p.add_argument("--out", type=Path, required=True)
    p = steps.add_parser("prepare", help="on the server: data, dump, images, tasks")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--answers", type=Path, required=True, help="used when it exists")
    p = steps.add_parser("check", help="on the server: readiness lines")
    p.add_argument("--root", type=Path, required=True)
    p = steps.add_parser("readonly", help="on the server: the read-only checks of one task")
    p.add_argument("--task", type=Path, required=True, help="a generated task's directory")
    p = steps.add_parser("leaks", help=f"how often ${WRITE_PASSWORD_ENV} occurs under a directory")
    p.add_argument("--dir", type=Path, required=True)
    parser.set_defaults(run=run_step)
