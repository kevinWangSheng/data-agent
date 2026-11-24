"""The LiveSQLBench evaluation script: soft EX on one `Query` task (.scratch/livesqlbench/spec.md
U7, U9, U10; ticket 128).

`generate_tasks` (evals.livesqlbench) copies this file into each task's `tests/`, where Harbor runs
it in the task's `main` container after the agent (`tests/test.sh`): that container has Python and
`psycopg` but not this repo, so the file needs nothing beyond the standard library and `psycopg`,
and `evals.livesqlbench` re-exports its names (`Result`, `ExecutionError`, `Execute`, `clean_sql`,
`results_match`, `evaluate`) rather than defining them twice.

The SQL cleaning and the result comparison are upstream's, copied from LiveSQLBench @ e15cd22
`evaluation/src/test_utils.py` (MIT, below; spec U9). Modified: `remove_round` no longer logs the
SQL it found a ROUND in (upstream's `logging.warning`, which would log the gold SQL, spec U7), and
`ex_base` is split into `results_match` (its comparison, here of fetched results) and `evaluate`
(the execution, as upstream `run_evaluation_phase` and `test_case_default` order it). Upstream logs
every query and result (`evaluation/src/db_utils.py:302-304`); nothing here prints or writes a SQL
statement, a result value or a driver message: the output is the reward and `diagnostics.json`,
which records which side failed and how, never what.

    MIT License

    Copyright (c) 2024 bird_sql

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

REWARD_FILE = "reward.txt"
DIAGNOSTICS_FILE = "diagnostics.json"
# Where Harbor puts the task's tests and keeps the verifier's output, and where the agent writes
# its SQL (upstream template/documents/README_TASK.md:19-27).
PAYLOAD = Path("/tests/task_payload.json")
PREDICTION = Path("/app/pred.json")
VERIFIER_DIR = Path("/logs/verifier")
# Upstream db_utils.py: a 60 s statement timeout, at most 10,000 rows fetched per statement.
STATEMENT_TIMEOUT = "60s"
MAX_ROWS = 10000


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


# --- upstream evaluation/src/test_utils.py (MIT, see the module docstring) ---


def remove_round_functions(sql_string: str) -> str:
    """
    Remove all ROUND() function calls from a SQL string, including nested ones.
    This regex properly handles nested functions with commas.
    """

    def find_matching_paren(text: str, start_pos: int) -> int:
        """Find the position of the matching closing parenthesis."""
        paren_count = 0
        for i in range(start_pos, len(text)):
            if text[i] == "(":
                paren_count += 1
            elif text[i] == ")":
                paren_count -= 1
                if paren_count == 0:
                    return i
        return -1

    def find_first_arg_end(text: str, start_pos: int) -> int:
        """Find the end of the first argument, accounting for nested parentheses."""
        paren_count = 0
        for i in range(start_pos, len(text)):
            if text[i] == "(":
                paren_count += 1
            elif text[i] == ")":
                if paren_count == 0:
                    return i  # End of ROUND function
                paren_count -= 1
            elif text[i] == "," and paren_count == 0:
                return i  # End of first argument
        return len(text)

    result = sql_string

    while True:
        # Find ROUND function (case insensitive)
        pattern = re.compile(r"ROUND\s*\(", re.IGNORECASE)
        match = pattern.search(result)

        if not match:
            break

        start_pos = match.start()
        open_paren_pos = match.end() - 1

        # Find the end of the first argument
        first_arg_end = find_first_arg_end(result, open_paren_pos + 1)

        # Find the matching closing parenthesis
        close_paren_pos = find_matching_paren(result, open_paren_pos)

        if close_paren_pos == -1:
            break  # Malformed SQL, can't find closing paren

        # Extract the first argument
        first_arg = result[open_paren_pos + 1 : first_arg_end].strip()

        # Replace ROUND(...) with just the first argument
        result = result[:start_pos] + first_arg + result[close_paren_pos + 1 :]

    return result


def remove_round(sql_list: Sequence[str]) -> list[str]:
    """
    Remove ROUND function calls while preserving the inner expression.
    For example:
    - ROUND(column, 2) -> column
    - ROUND(ROUND(price, 2), 1) -> ROUND(price, 2) -> price (handles nested ROUNDs)
    """
    # Modified: upstream logs each SQL still holding "ROUND" here; that would log the gold SQL.
    return [remove_round_functions(sql) for sql in sql_list]


def process_decimals_recursive(item: Any, decimal_places: int) -> Any:
    """
    Recursively process decimals in any data structure (list, dict, tuple).
    Returns a new structure with all decimals rounded to specified places.
    """
    quantizer = Decimal(1).scaleb(-decimal_places)

    if isinstance(item, Decimal):
        return float(item.quantize(quantizer, rounding=ROUND_HALF_UP))
    elif isinstance(item, float):
        return float(Decimal(str(item)).quantize(quantizer, rounding=ROUND_HALF_UP))
    elif isinstance(item, (list, tuple)):
        return type(item)(process_decimals_recursive(x, decimal_places) for x in item)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    elif isinstance(item, dict):
        return {k: process_decimals_recursive(v, decimal_places) for k, v in item.items()}  # pyright: ignore[reportUnknownVariableType]
    else:
        return item


def preprocess_results(
    results: Sequence[Sequence[Any]], decimal_places: int = 2
) -> list[tuple[Any, ...]]:
    """
    Process the result set:
    - Replace dates with normalized string: YYYY-MM-DD
    - Convert tuples to lists for JSON serializability
    - Convert any unhashable types (dicts, lists) to their string representation for comparison
    - Process decimals recursively in all nested structures
    """
    processed: list[tuple[Any, ...]] = []
    for result in results:
        processed_result: list[Any] = []
        for item in result:
            if isinstance(item, (date, datetime)):
                processed_result.append(item.strftime("%Y-%m-%d"))
            else:
                # Process decimals recursively first
                processed_item = process_decimals_recursive(item, decimal_places)
                if isinstance(processed_item, (dict, list)):
                    # Convert unhashable types to their string representation with sorted keys
                    processed_result.append(json.dumps(processed_item, sort_keys=True))
                else:
                    processed_result.append(processed_item)
        processed.append(tuple(processed_result))
    return processed


def remove_distinct(sql_list: Sequence[str]) -> list[str]:
    """
    Remove all occurrences of the DISTINCT keyword (in any case form)
    from a single list of SQL query strings, but preserve DISTINCT ON clauses.

    This function uses regex to:
    - Remove standalone DISTINCT keywords
    - Preserve DISTINCT ON (column_list) clauses
    - Handle case-insensitive matching
    """
    cleaned_queries: list[str] = []
    for query in sql_list:
        # Pattern to match DISTINCT but not DISTINCT ON
        # \b ensures word boundaries, so "DISTINCT" matches but "DISTINCTON" doesn't
        # (?![^()]*\bON\b) negative lookahead ensures we don't match if ON follows
        # This handles cases like "DISTINCT ON (col1, col2)" - we keep the whole thing
        pattern = r"\bDISTINCT\b(?![^()]*\bON\b)"
        cleaned_query = re.sub(pattern, "", query, flags=re.IGNORECASE)

        # Clean up any extra whitespace that might be left
        cleaned_query = re.sub(r"\s+", " ", cleaned_query).strip()
        cleaned_queries.append(cleaned_query)

    return cleaned_queries


def remove_comments(sql_list: Sequence[str]) -> list[str]:
    """
    Remove all SQL comments from each query string in the list.
    - Block comments: /* ... */
    - Line comments: -- ... (to end of line)
    Also collapses multiple blank lines into one, and strips leading/trailing whitespace.
    """
    cleaned: list[str] = []
    for sql in sql_list:
        # remove block comments
        no_block = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        # remove line comments, keep newline
        no_line = re.sub(r"--.*?(\r\n|\r|\n)", r"\1", no_block)
        # collapse extra blank lines
        no_blank = re.sub(r"\n\s*\n+", "\n", no_line)
        cleaned.append(no_blank.strip())
    return cleaned


# --- end of the upstream code ---


def clean_sql(sqls: Sequence[str]) -> list[str]:
    """Upstream soft EX's cleaning of a SQL list, in its order (`evaluation/src/test_utils.py`
    `test_case_default`, :398-403): remove comments, then `DISTINCT` (keeping `DISTINCT ON`), then
    `ROUND(...)` (keeping its first argument, nested ones too)."""
    return remove_round(remove_distinct(remove_comments(sqls)))


def results_match(predicted: Result, gold: Result, conditions: Mapping[str, Any] | None) -> bool:
    """Upstream `ex_base`'s comparison (test_utils.py:276-285) of two fetched results: both
    go through `preprocess_results` (dates as `YYYY-MM-DD`, decimals and floats rounded half up
    to 2 places, dicts and lists as sorted-key JSON); an empty side does not match; rows compare
    as a list when `conditions["order"]` is true, otherwise as a set; column names are not
    compared."""
    predicted_res = preprocess_results(predicted.rows)
    ground_res = preprocess_results(gold.rows)
    if not predicted_res or not ground_res:
        return False
    if conditions is not None and conditions.get("order", False):
        return predicted_res == ground_res
    return set(predicted_res) == set(ground_res)


def statements(value: Any) -> list[str]:
    """A record's SQL field as a list of statements: upstream `split_field` takes a list or one
    string; an empty or missing field is no statement."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(s) for s in value if str(s).strip()]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def write_output(out: Path, reward: int, diagnostics: Mapping[str, Any]) -> int:
    out.mkdir(parents=True, exist_ok=True)
    (out / REWARD_FILE).write_text(f"{reward}\n")
    (out / DIAGNOSTICS_FILE).write_text(json.dumps({"reward": reward, **diagnostics}, indent=2))
    return reward


def evaluate(
    payload: Mapping[str, Any], predicted_sql: Sequence[str], execute: Execute, out: Path
) -> int:
    """Score one `Query` task (the evaluation script's entry): run `payload`'s `preprocess_sql`,
    then the predicted and the gold SQL (`payload["sol_sql"]`), both through `clean_sql`, and
    compare with `results_match` under `payload["conditions"]`. An `ExecutionError` on either side
    scores 0. Writes the reward (`1` or `0`) to `out/reward.txt` and returns it.

    As upstream orders it (`evaluation/src/evaluation.py` `run_evaluation_phase`, then
    `test_case_default`): the predicted SQL first runs as given, and an error there scores 0
    before the cleaned lists run. A payload without `sol_sql` (the answers are not in hand, ticket
    128) scores 0 without running anything, and its diagnostics say there is no gold answer: that
    0 is no score. No predicted SQL also scores 0 without running anything (upstream: a
    `pred.json` without a non-empty `predicted_sql` list).

    Whatever it writes under `out`, prints or logs is the reward and diagnostics without the
    answer: `out/diagnostics.json` names the outcome and, on an error, which side failed and the
    error's class, never a SQL statement, a result value or the driver's message (spec U7).
    """
    gold_sql = statements(payload.get("sol_sql"))
    if not gold_sql:
        return write_output(out, 0, {"gold_answer": "missing", "outcome": "no_gold_answer"})
    found: dict[str, Any] = {"gold_answer": "present"}
    if not predicted_sql:
        return write_output(out, 0, found | {"outcome": "no_prediction"})
    # Upstream `run_preprocessing` ignores the flags its `execute_queries` returns: a failed
    # preprocess statement is noted and the evaluation goes on.
    if preprocess := statements(payload.get("preprocess_sql")):
        try:
            execute(preprocess)
        except ExecutionError as exc:
            found["preprocess_error"] = kind(exc)
    try:
        execute(list(predicted_sql))
    except ExecutionError as exc:
        return write_output(out, 0, found | {"outcome": "predicted_error", "error": kind(exc)})
    results: dict[str, Result] = {}
    for side, sqls in (("predicted", clean_sql(predicted_sql)), ("gold", clean_sql(gold_sql))):
        try:
            results[side] = execute(sqls)
        except ExecutionError as exc:
            return write_output(out, 0, found | {"outcome": f"{side}_error", "error": kind(exc)})
    matched = results_match(results["predicted"], results["gold"], payload.get("conditions"))
    found["predicted_rows"] = len(results["predicted"].rows)
    return write_output(out, int(matched), found | {"outcome": "match" if matched else "mismatch"})


def kind(exc: BaseException) -> str:
    """An error's class, and the driver's class beneath an ExecutionError: never its message."""
    cause = exc.__cause__
    return type(cause).__name__ if cause is not None else type(exc).__name__


# --- the script: Harbor's verifier runs it in the task's `main` container ---


def read_prediction(path: Path) -> tuple[str, list[str]]:
    """The agent's SQL from `pred.json` (`{"instance_id", "predicted_sql": [...]}`) and its state:
    `missing`, `unparsable` (not JSON, or no non-empty `predicted_sql` list of strings) or `ok`."""
    if not path.is_file():
        return "missing", []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return "unparsable", []
    sqls = data.get("predicted_sql") if isinstance(data, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(sqls, list) or not sqls or not all(isinstance(s, str) for s in sqls):  # pyright: ignore[reportUnknownVariableType]
        return "unparsable", []
    return "ok", [str(s) for s in sqls]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def last_result(cur: Any) -> Result:
    """The rows of the last statement a cursor ran, at most MAX_ROWS. An entry of a SQL list may
    hold several statements; psycopg 3 starts at the first one's result, where upstream's psycopg2
    keeps the last, so the cursor moves on to its last result set first."""
    while cur.nextset():
        pass
    if cur.description is None:
        return Result(columns=(), rows=())
    columns = tuple(str(c.name) for c in cur.description)
    return Result(columns=columns, rows=tuple(cur.fetchmany(MAX_ROWS)))


class PostgresExecute:
    """The real `Execute`: on first use, a fresh `<db>_process_1` cloned from the image's
    `<db>_template` with the verifier's (write) role, as upstream `template/tests/evaluator.py`
    makes one; each statement runs there under a 60 s timeout and is committed, and the last
    statement's rows are returned, at most 10,000 (upstream db_utils.py). `close` drops it."""

    def __init__(self, database: str, env: Mapping[str, str]) -> None:
        self.base = database
        self.process = f"{database}_process_1"
        self.settings = {
            "host": env.get("PGHOST", "postgresql"),
            "port": env.get("PGPORT", "5432"),
            "user": env.get("PGUSER", ""),
            "password": env.get("PGPASSWORD", ""),
            "connect_timeout": 10,
        }
        self.conn: Any = None

    def _admin(self, *statements: tuple[str, str]) -> None:
        import psycopg
        from psycopg import sql

        with psycopg.connect(dbname="postgres", autocommit=True, **self.settings) as admin:  # pyright: ignore[reportArgumentType]
            for template, name in statements:
                admin.execute(sql.SQL(template).format(sql.Identifier(name)))  # pyright: ignore[reportArgumentType]

    def _connect(self) -> Any:
        import psycopg
        from psycopg import sql

        terminate = (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = {} AND pid <> pg_backend_pid()"
        )
        with psycopg.connect(dbname="postgres", autocommit=True, **self.settings) as admin:  # pyright: ignore[reportArgumentType]
            admin.execute(sql.SQL(terminate).format(sql.Literal(self.process)))
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(self.process))
            )
            admin.execute(
                sql.SQL("CREATE DATABASE {} WITH TEMPLATE {}").format(
                    sql.Identifier(self.process), sql.Identifier(f"{self.base}_template")
                )
            )
        return psycopg.connect(dbname=self.process, **self.settings)  # pyright: ignore[reportArgumentType]

    def __call__(self, sqls: Sequence[str]) -> Result:
        import psycopg

        try:
            if self.conn is None:
                self.conn = self._connect()
            result = Result(columns=(), rows=())
            for query in sqls:
                with self.conn.cursor() as cur:
                    cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
                    try:
                        cur.execute(query)  # pyright: ignore[reportArgumentType]
                    except psycopg.Error:
                        self.conn.rollback()
                        raise
                    self.conn.commit()
                    result = last_result(cur)
            return result
        except psycopg.Error as exc:
            raise ExecutionError(str(exc)) from exc

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
            self._admin(("DROP DATABASE IF EXISTS {}", self.process))


def main(
    payload_path: Path = PAYLOAD, prediction: Path = PREDICTION, out: Path = VERIFIER_DIR
) -> int:
    """Score the task and write the reward and diagnostics to `out`; the diagnostics add the
    state of `pred.json`. Any failure of the script itself scores 0 and is recorded by its class
    only, so neither its output nor Harbor's copy of it carries a SQL statement or a value."""
    state = "missing"
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        state, predicted = read_prediction(prediction)
        execute = PostgresExecute(str(payload["selected_database"]), os.environ)
        try:
            reward = evaluate(payload, predicted, execute, out)
        finally:
            execute.close()
    except Exception as exc:
        reward = write_output(out, 0, {"outcome": "verifier_error", "error": type(exc).__name__})
    path = out / DIAGNOSTICS_FILE
    diagnostics = json.loads(path.read_text())
    path.write_text(json.dumps(diagnostics | {"pred_json": state}, indent=2))
    print(f"reward {reward} ({diagnostics.get('outcome')}, pred.json {state})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
