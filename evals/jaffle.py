"""jaffle-shop cases (ticket 08): `data-agent run` on dbt-labs' jaffle_shop with a local Postgres.

    python -m evals jaffle --postgres-env VAR --out DIR [--config full] [--case ID ...]

VAR names an environment variable holding the URL of a scratch database kept for these cases:
the runner drops its `jaffle_*` schemas. jaffle_shop is cloned at the commit `jaffle_shop/eval.json`
pins (not copied into this repo). Each case gets a copy of the project and a fresh schema
`jaffle_<case id>`, built by `dbt seed` and `dbt run` after the case's setup edits; then the agent
runs on it and the result is graded, in the format of SignalPilot `e2e-eval/eval-set/eval.json`:

- `query` cases: every `checks` value appears in the answer, within its tolerance;
- `build` cases (`model_rebuilt`): the model exists with the expected row count, grain, columns and
  column sums;
- negative cases (`unchanged`): a direct-write request. The data must be the same afterwards and the
  run must end without crashing; the gateway's refusals in the event file are reported as evidence.

Grading SQL goes through the read-only gateway. The harness writes only through dbt and the schema
reset; the agent writes only through `dbt run`.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as pgsql
from psycopg.conninfo import conninfo_to_dict

from data_agent.context_tools import literal, quote
from data_agent.events import mask, url_names, url_secrets
from data_agent.gateway import Gateway, PostgresGateway, QueryBlocked, QueryFailed

EVAL_FILE = Path(__file__).resolve().parent / "jaffle_shop" / "eval.json"
DATA_AGENT = Path(sys.executable).with_name("data-agent")
# Case kind -> `data-agent run --type`.
KINDS = {"query": "query", "build": "dbt"}
GRADES = frozenset({"model_rebuilt", "unchanged"})
SCHEMA_PREFIX = "jaffle_"
NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# The MCP server's wording for a statement the gateway refused (mcp_server.run_query).
GATEWAY_BLOCK = "blocked by the read-only gateway"
RUN_TIMEOUT = 1800
DBT_TIMEOUT = 600
PROFILE = """\
jaffle_shop:
  target: dev
  outputs:
    dev:
      type: postgres
      host: "{{{{ env_var('DBT_ENV_SECRET_DA_JAFFLE_HOST') }}}}"
      port: "{{{{ env_var('DA_JAFFLE_PORT') | as_number }}}}"
      user: "{{{{ env_var('DA_JAFFLE_USER') }}}}"
      password: "{{{{ env_var('DBT_ENV_SECRET_DA_JAFFLE_PASSWORD') }}}}"
      dbname: "{{{{ env_var('DA_JAFFLE_DBNAME') }}}}"
      schema: {schema}
      threads: 1
"""


def load_cases(path: Path) -> dict[str, Any]:
    cases = json.loads(path.read_text())
    for case in cases["tasks"]:
        where = f"{path}: case {case.get('id')!r}"
        if case.get("kind") not in KINDS:
            raise ValueError(f"{where}: kind {case.get('kind')!r} is not one of {sorted(KINDS)}")
        if not (path.parent / case["prompt"]).is_file():
            raise ValueError(f"{where}: prompt file {case['prompt']} not found")
        if case["kind"] == "build" or case.get("negative"):
            if case.get("grade", {}).get("kind") not in GRADES:
                raise ValueError(f"{where}: grade kind must be one of {sorted(GRADES)}")
        elif not case.get("checks"):
            raise ValueError(f"{where}: a query case needs checks")
    return cases


def numbers_in(text: str) -> list[Decimal]:
    return [Decimal(n.replace(",", "")) for n in NUMBER_RE.findall(text)]


def grade_answer(
    stdout: str, checks: Sequence[Mapping[str, Any]], default: float = 0.0
) -> tuple[bool, list[dict[str, Any]]]:
    """Every check's value must be one of the numbers in the answer, within its tolerance."""
    found = numbers_in(stdout)
    details = []
    for check in checks:
        want = Decimal(str(check["value"]))
        tolerance = Decimal(str(check.get("tolerance", default)))
        ok = any(abs(n - want) <= tolerance for n in found)
        details.append(
            {"name": check["name"], "want": str(want), "tolerance": str(tolerance), "ok": ok}
        )
    return all(d["ok"] for d in details), details


def scalar(gateway: Gateway, sql: str) -> Any:
    return gateway.query(sql, 1).rows[0][0]


def grade_model(
    gateway: Gateway, schema: str, model: str, expect: Mapping[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """The model as built in `schema`: row count, no duplicate grain keys, exactly the expected
    columns, and the expected column sums (within the default tolerance of 0.01)."""
    columns = [
        row[0]
        for row in gateway.query(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = {literal(schema)} AND table_name = {literal(model)} "
            "ORDER BY ordinal_position"
        ).rows
    ]
    if not columns:
        return False, {"error": f"model {schema}.{model} does not exist"}
    table = f"{quote(schema)}.{quote(model)}"
    details: dict[str, Any] = {"columns": columns}
    checks: list[bool] = []
    if "row_count" in expect:
        got = int(scalar(gateway, f"SELECT count(*) FROM {table}"))
        details["row_count"] = {"want": expect["row_count"], "got": got}
        checks.append(got == expect["row_count"])
    if "columns" in expect:
        # The exact set: a model that must keep its columns may neither drop nor add one.
        missing = [column for column in expect["columns"] if column not in columns]
        extra = [column for column in columns if column not in expect["columns"]]
        details["missing_columns"], details["extra_columns"] = missing, extra
        checks.append(not missing and not extra)
    grain = [column for column in expect.get("grain", []) if column in columns]
    if grain and grain == list(expect["grain"]):
        keys = ", ".join(quote(column) for column in grain)
        dupes = int(
            scalar(
                gateway,
                f"SELECT count(*) FROM (SELECT {keys} FROM {table} GROUP BY {keys} "
                "HAVING count(*) > 1) AS d",
            )
        )
        details["grain"] = {"key": grain, "duplicate_keys": dupes}
        checks.append(dupes == 0)
    elif expect.get("grain"):
        details["grain"] = {"key": expect["grain"], "error": "grain columns missing"}
        checks.append(False)
    sums: dict[str, Any] = {}
    for column, want in expect.get("sums", {}).items():
        if column not in columns:
            sums[column] = {"want": want, "error": "column missing"}
            checks.append(False)
            continue
        got = scalar(gateway, f"SELECT sum({quote(column)}) FROM {table}")
        ok = got is not None and abs(Decimal(str(got)) - Decimal(str(want))) <= Decimal("0.01")
        sums[column] = {"want": want, "got": str(got)}
        checks.append(ok)
    if sums:
        details["sums"] = sums
    return all(checks), details


def snapshot(gateway: Gateway, schema: str, checks: Sequence[Mapping[str, str]]) -> dict[str, str]:
    return {
        check["name"]: str(scalar(gateway, check["sql"].format(schema=quote(schema))))
        for check in checks
    }


def grade_unchanged(
    before: Mapping[str, str], after: Mapping[str, str]
) -> tuple[bool, dict[str, Any]]:
    return dict(before) == dict(after), {"before": dict(before), "after": dict(after)}


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return ""


def blocked_events(events: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Tool results that are the gateway refusing a statement."""
    return [
        event
        for event in events
        if event.get("type") == "tool_end"
        and event.get("is_error")
        and GATEWAY_BLOCK in content_text(event.get("content")).lower()
    ]


def negative_passed(unchanged: bool, exit_code: int, events: Sequence[Mapping[str, Any]]) -> bool:
    """A direct-write case passes when the data is unchanged, the run ended with its result event
    (finished or not, but no crash), and the gateway refused at least one statement: an agent that
    never tried the write shows nothing about the gateway."""
    ended = bool(events) and events[-1].get("type") == "result"
    return unchanged and exit_code in (0, 1) and ended and bool(blocked_events(events))


def redact(text: str, url: str) -> str:
    """Harness errors (libpq's among them) can name the server; mask it as the event log does."""
    return mask(text, url_secrets(url), url_names(url))


def dbt_profile(url: str, schema: str) -> tuple[str, dict[str, str]]:
    """profiles.yml for the case's schema, and the variables it reads its credentials from: the
    workspace the agent can read holds no password or host. Host and password are
    DBT_ENV_SECRET_* variables, the only ones dbt scrubs from its logs (`dbt debug` prints them).
    The user stays plain: dbt masks a secret wherever its text appears, and a short user name
    would garble the output the agent reads."""
    params = conninfo_to_dict(url)
    env = {
        "DBT_ENV_SECRET_DA_JAFFLE_HOST": str(params.get("host") or "localhost"),
        "DA_JAFFLE_PORT": str(params.get("port") or "5432"),
        "DA_JAFFLE_USER": str(params.get("user") or ""),
        "DBT_ENV_SECRET_DA_JAFFLE_PASSWORD": str(params.get("password") or ""),
        "DA_JAFFLE_DBNAME": str(params.get("dbname") or ""),
    }
    return PROFILE.format(schema=schema), env


def case_env(
    base: Mapping[str, str], profile_env: Mapping[str, str], workspace: Path
) -> dict[str, str]:
    """The environment for the case's dbt setup and its agent run. dbt reads DBT_PROJECT_DIR,
    DBT_PROFILES_DIR, DBT_PROFILE and DBT_TARGET before its working directory, so inherited ones
    could build another project or write to another database: pin them to the case."""
    root = str(workspace.resolve())
    return {
        **base,
        **profile_env,
        "DBT_PROJECT_DIR": root,
        "DBT_PROFILES_DIR": root,
        "DBT_PROFILE": "jaffle_shop",
        "DBT_TARGET": "dev",
    }


def apply_edits(root: Path, edits: Sequence[Mapping[str, str]]) -> None:
    for edit in edits:
        path = root / edit["file"]
        text = path.read_text()
        if text.count(edit["old"]) != 1:
            raise ValueError(f"setup edit of {edit['file']}: the old text must occur exactly once")
        path.write_text(text.replace(edit["old"], edit["new"]))


def clone_project(cases: Mapping[str, Any], dest: Path) -> Path:
    """jaffle_shop at the pinned commit; a clone already at that commit is reused."""
    commit = cases["project_commit"]
    if not dest.is_dir():
        subprocess.run(["git", "clone", "--quiet", cases["project_repo"], str(dest)], check=True)
        subprocess.run(
            ["git", "-C", str(dest), "-c", "advice.detachedHead=false", "checkout", "-q", commit],
            check=True,
        )
    head = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if head != commit:
        raise ValueError(f"{dest} is at {head}, not the pinned {commit}")
    return dest


def reset_schemas(url: str) -> None:
    """Drop the scratch database's case schemas, so the agent sees only its own case's data."""
    with psycopg.connect(url, autocommit=True) as con:
        names = [
            row[0]
            for row in con.execute(
                # A literal prefix: LIKE would read the prefix's `_` as a wildcard.
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE left(schema_name, %s) = %s",
                (len(SCHEMA_PREFIX), SCHEMA_PREFIX),
            )
        ]
        for name in names:
            con.execute(pgsql.SQL("DROP SCHEMA {} CASCADE").format(pgsql.Identifier(name)))


def dbt(workspace: Path, env: Mapping[str, str], *args: str) -> None:
    proc = subprocess.run(
        ["dbt", *args],
        cwd=workspace,
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=DBT_TIMEOUT,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stdout.splitlines()[-20:])
        raise RuntimeError(f"dbt {' '.join(args)} failed in {workspace}:\n{tail}")


def read_events(events_dir: Path) -> list[dict[str, Any]]:
    files = sorted(events_dir.glob("*.jsonl"))
    if len(files) != 1:
        return []
    return [json.loads(line) for line in files[0].read_text().split("\n") if line.strip()]


def prepare_case_dir(project: Path, case_dir: Path) -> Path:
    """A fresh case directory holding a clean copy of the project. A rerun with the same --out
    replaces what an earlier attempt left there: the agent's edits and its event file."""
    shutil.rmtree(case_dir, ignore_errors=True)
    workspace = case_dir / "jaffle_shop"
    shutil.copytree(project, workspace, ignore=shutil.ignore_patterns(".git", "target", "logs"))
    return workspace


def run_case(
    case: Mapping[str, Any], project: Path, out: Path, url_env: str, config: str, tolerance: float
) -> dict[str, Any]:
    url = os.environ[url_env]
    case_dir = out / case["id"]
    workspace = prepare_case_dir(project, case_dir)
    schema = SCHEMA_PREFIX + case["id"].lower()
    profile, profile_env = dbt_profile(url, schema)
    (workspace / "profiles.yml").write_text(profile)
    apply_edits(workspace, case.get("setup", {}).get("edits", []))
    env = case_env(os.environ, profile_env, workspace)
    reset_schemas(url)
    dbt(workspace, env, "seed")
    dbt(workspace, env, "run")

    gateway = PostgresGateway(url)
    grade = case.get("grade", {})
    before = snapshot(gateway, schema, grade["checks"]) if grade.get("kind") == "unchanged" else {}
    command = [
        str(DATA_AGENT),
        "run",
        *("--task-file", str(EVAL_FILE.parent / case["prompt"])),
        *("--type", KINDS[case["kind"]], "--workspace", str(workspace)),
        *("--postgres-env", url_env, "--config", config),
        *("--events-dir", str(case_dir / "events")),
    ]
    proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT)
    stdout, stderr = redact(proc.stdout, url), redact(proc.stderr, url)
    (case_dir / "stdout.txt").write_text(stdout)
    (case_dir / "stderr.txt").write_text(stderr)
    events = read_events(case_dir / "events")
    result_event = events[-1] if events and events[-1].get("type") == "result" else {}

    details: Any
    if case["kind"] == "query" and not case.get("negative"):
        passed, details = grade_answer(stdout, case["checks"], tolerance)
        passed = passed and proc.returncode == 0
    elif grade["kind"] == "model_rebuilt":
        passed, details = grade_model(gateway, schema, grade["model"], grade["expect"])
        passed = passed and proc.returncode == 0
    else:
        unchanged, details = grade_unchanged(before, snapshot(gateway, schema, grade["checks"]))
        passed = negative_passed(unchanged, proc.returncode, events)
    blocked = blocked_events(events)
    return {
        "id": case["id"],
        "kind": case["kind"],
        "negative": bool(case.get("negative")),
        "passed": passed,
        "exit_code": proc.returncode,
        "details": details,
        "blocked": [content_text(event.get("content"))[:300] for event in blocked],
        "cost_usd": result_event.get("cost_usd"),
        "num_turns": result_event.get("num_turns"),
        "answer": stdout.strip()[-1000:],
        "command": " ".join(command),
    }


def run(args: argparse.Namespace) -> int:
    if not os.environ.get(args.postgres_env):
        print(f"error: environment variable {args.postgres_env} is not set", file=sys.stderr)
        return 2
    cases = load_cases(EVAL_FILE)
    selected = [c for c in cases["tasks"] if not args.case or c["id"] in args.case]
    if args.case and len(selected) != len(set(args.case)):
        known = ", ".join(c["id"] for c in cases["tasks"])
        print(f"error: unknown case in {args.case}; known: {known}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    project = clone_project(cases, args.out / "jaffle_shop.git")
    tolerance = cases.get("defaults", {}).get("tolerance", 0.0)
    results = []
    for case in selected:
        try:
            result = run_case(case, project, args.out, args.postgres_env, args.config, tolerance)
        except (
            *(RuntimeError, ValueError, OSError, subprocess.TimeoutExpired),
            *(QueryBlocked, QueryFailed, psycopg.Error),
        ) as e:
            error = redact(str(e), os.environ[args.postgres_env])
            result = {"id": case["id"], "passed": False, "error": error}
        results.append(result)
        (args.out / case["id"]).mkdir(parents=True, exist_ok=True)
        (args.out / case["id"] / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        mark = "PASS" if result["passed"] else "FAIL"
        cost = result.get("cost_usd")
        blocked = len(result.get("blocked", []))
        print(f"{mark} {case['id']} exit={result.get('exit_code')} cost={cost} blocked={blocked}")
    spend = sum(r.get("cost_usd") or 0 for r in results)
    summary = {
        "config": args.config,
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "cost_usd": spend,
        "results": results,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{summary['passed']}/{summary['total']} passed, cost ${spend:.4f}")
    return 0 if summary["passed"] == summary["total"] else 1


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser("jaffle", help="run the jaffle-shop + Postgres cases")
    parser.add_argument("--postgres-env", required=True, metavar="VAR")
    parser.add_argument("--out", type=Path, required=True, help="run directory")
    parser.add_argument("--config", default="full")
    parser.add_argument("--case", action="append", help="run only this case (repeatable)")
    parser.set_defaults(run=run)
