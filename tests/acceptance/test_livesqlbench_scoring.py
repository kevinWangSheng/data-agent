"""LiveSQLBench soft EX (.scratch/livesqlbench/spec.md "Testing Decisions": 评分; spec :37: scoring
follows the official soft EX).

Written by ticket 127. Every test is `TICKET_128` until ticket 128 implements
`evals.livesqlbench.clean_sql` and `results_match` (copying upstream's functions, spec U9), which
then drops the marks and changes nothing else. Pure functions only: no SQL is executed (`make
check` has no Postgres); comparing against the official evaluation script on real answers is an
integration test for later (spec :74).

Upstream (LiveSQLBench @ e15cd22): a `Query` task is scored with `TEST_CASE_DEFAULT` and its
`conditions` (LiveSQLBench-CLI/adapters/livesqlbench/template/tests/evaluator.py:173-175), i.e.
`test_case_default` (evaluation/src/test_utils.py:394-411): both SQL lists go through
`remove_comments` (:375-392), `remove_distinct` (:193-227, keeping `DISTINCT ON`) and
`remove_round` (:101-116), then `ex_base` (:255-285) compares the executed results after
`preprocess_results` (:136-159). Beyond the rules the spec names (DISTINCT, ROUND, row order,
column names), the other official rules get a case each too: comments, decimals rounded half up
to 2 places, dates as `YYYY-MM-DD`, an empty side, duplicate rows under set comparison, JSON
values (ticket 127's reading of spec :37). `ex_base`'s docstring says it strips ORDER BY; its
code does not, and nothing here pins either.

Every expected value was computed by running upstream's functions, not by hand: a scratch script
(not committed) imported `remove_comments`, `remove_distinct`, `remove_round` and
`preprocess_results` from upstream `evaluation/src/test_utils.py` (with stand-in `db_utils` and
`psycopg2` modules, which the pure functions never call), cleaned each SQL list in
`test_case_default`'s order, and compared each pair of results as `ex_base`'s last lines do. The
command and its output are in ticket 127's Comments.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from evals.livesqlbench import Result, clean_sql, results_match

# (input SQL list, upstream's cleaned list)
CLEANED = {
    "comments": (
        ["SELECT a -- the a column\nFROM t /* block\ncomment */ WHERE b = 1\n-- trailing\n"],
        ["SELECT a FROM t WHERE b = 1"],
    ),
    "distinct": (["SELECT DISTINCT city FROM shops"], ["SELECT city FROM shops"]),
    "distinct lower case": (
        ["select distinct city, count(*) from shops group by city"],
        ["select city, count(*) from shops group by city"],
    ),
    "distinct on kept": (
        ["SELECT DISTINCT ON (city) city, name FROM shops ORDER BY city, name"],
        ["SELECT DISTINCT ON (city) city, name FROM shops ORDER BY city, name"],
    ),
    "round": (
        ["SELECT ROUND(AVG(price), 2) AS avg_price FROM items"],
        ["SELECT AVG(price) AS avg_price FROM items"],
    ),
    "nested round": (
        ["SELECT ROUND(ROUND(price, 2) * 1.1, 1) FROM items"],
        ["SELECT price * 1.1 FROM items"],
    ),
    "round with one argument": (["SELECT round(price) FROM items"], ["SELECT price FROM items"]),
    "each statement of a list": (
        ["SELECT DISTINCT a FROM t", "SELECT ROUND(b, 2) FROM u -- note\n"],
        ["SELECT a FROM t", "SELECT b FROM u"],
    ),
}


@pytest.mark.parametrize("case", CLEANED)
def test_sql_is_cleaned_as_upstream_cleans_it(case: str) -> None:
    sqls, expected = CLEANED[case]
    assert clean_sql(sqls) == expected


def rows(*values: tuple[Any, ...]) -> tuple[tuple[Any, ...], ...]:
    return tuple(values)


# (predicted rows, gold rows, conditions, upstream's verdict)
MATCHED: dict[str, tuple[Any, Any, dict[str, Any] | None, bool]] = {
    "order free, rows permuted": (rows((1, "a"), (2, "b")), rows((2, "b"), (1, "a")), {}, True),
    "no conditions, rows permuted": (
        rows((1, "a"), (2, "b")),
        rows((2, "b"), (1, "a")),
        None,
        True,
    ),
    "order false, rows permuted": (
        rows((1, "a"), (2, "b")),
        rows((2, "b"), (1, "a")),
        {"order": False},
        True,
    ),
    "order true, rows permuted": (
        rows((1, "a"), (2, "b")),
        rows((2, "b"), (1, "a")),
        {"order": True},
        False,
    ),
    "order true, same order": (
        rows((1, "a"), (2, "b")),
        rows((1, "a"), (2, "b")),
        {"order": True},
        True,
    ),
    "duplicate rows merged": (rows((1,), (1,), (2,)), rows((1,), (2,)), {}, True),
    "decimal rounded half up": (rows((Decimal("2.345"),)), rows((2.35,)), {}, True),
    "float rounded half up": (rows((2.345,)), rows((Decimal("2.35"),)), {}, True),
    "decimals differ at 2 places": (rows((Decimal("2.344"),)), rows((2.35,)), {}, False),
    "date and datetime of one day": (
        rows((date(2026, 3, 1),)),
        rows((datetime(2026, 3, 1, 13, 45),)),
        {},
        True,
    ),
    "date and its YYYY-MM-DD text": (rows((date(2026, 3, 1),)), rows(("2026-03-01",)), {}, True),
    "dates differ": (rows((date(2026, 3, 1),)), rows((date(2026, 3, 2),)), {}, False),
    "json keys in another order": (rows(({"b": 1, "a": 2},)), rows(({"a": 2, "b": 1},)), {}, True),
    "values differ": (rows((1, "a")), rows((1, "b")), {}, False),
    "predicted empty": (rows(), rows((1,)), {}, False),
    "gold empty": (rows((1,)), rows(), {}, False),
    "both empty": (rows(), rows(), {}, False),
}


def result(columns: tuple[str, ...], values: tuple[tuple[Any, ...], ...]) -> Result:
    width = len(values[0]) if values else len(columns)
    return Result(columns=columns[:width], rows=values)


@pytest.mark.parametrize("case", MATCHED)
def test_results_compare_as_upstream_compares_them(case: str) -> None:
    predicted, gold, conditions, expected = MATCHED[case]
    columns = ("c1", "c2")
    assert results_match(result(columns, predicted), result(columns, gold), conditions) is expected


@pytest.mark.parametrize("conditions", [{}, {"order": True}])
def test_column_names_are_not_compared(conditions: dict[str, Any]) -> None:
    """`ex_base` compares the rows' value tuples only (test_utils.py:276-285)."""
    predicted = Result(columns=("block", "total_kg"), rows=((1, "a"), (2, "b")))
    gold = Result(columns=("block_code", "sum"), rows=((1, "a"), (2, "b")))
    assert results_match(predicted, gold, conditions) is True
