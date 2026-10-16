---
name: postgres-sql
description: "Load when hitting PostgreSQL syntax errors or writing PostgreSQL-specific SQL. Covers gotchas that differ from DuckDB/MySQL."
type: skill
---

<!-- Written for data-agent in the format of signalpilot-plugin's duckdb-sql Skill (see NOTICE for
that Skill); every rule below was checked against PostgreSQL 16. -->

# PostgreSQL SQL - Key Differences from DuckDB/MySQL

## Gotchas

- **`/` on integers is integer division**: `5 / 2 = 2`. Make one side numeric for a fraction: `5 / 2.0 = 2.5`, `count_col::numeric / total`
- **ROUND needs NUMERIC**: `round(double precision, int)` does not exist. Cast first: `round(avg(col)::numeric, 2)`
- **DATE_TRUNC returns a timestamp** (with time zone when given a DATE): cast if a DATE is needed: `date_trunc('month', col)::date`
- **Date arithmetic**: `date - date` is an integer number of days; `col + interval '1 day'` gives a timestamp, cast with `::date` to keep a DATE. There is no `DATEADD`/`DATEDIFF`/`DATE_DIFF`
- **No QUALIFY**: filter window results in an outer query (see below)
- **SUM(NULL) = NULL**: Not 0. Use `COALESCE(SUM(col), 0)` if 0 is needed
- **`||` with NULL gives NULL**: `'a' || NULL` is NULL; `concat('a', NULL)` is `'a'`
- **Identifiers fold to lower case**: `SELECT Amount` reads column `amount`; a column created as `"Amount"` must be quoted exactly every time
- **GROUP BY is strict**: every selected column that is not aggregated must be in GROUP BY
- **NULL ordering**: NULLs sort last ascending and FIRST descending. Add `NULLS LAST` to `ORDER BY col DESC` when NULLs must not lead
- **Aggregate types**: `count(*)` and `sum(integer)` return BIGINT, `avg(integer)` returns NUMERIC, `extract(...)` returns NUMERIC
- **ROUND precision**: If the YML specifies a decimal type like `numeric(6,2)`,
  cast the FINAL output to match: `round(avg(col)::numeric, 2)::numeric(6,2)`.
  Do NOT cast the input - cast the result after rounding.
- **Avoid CURRENT_DATE** in models with historical data - use `(SELECT MAX(date_col) FROM source)` to anchor to the data's actual date range

## Schemas

Tables live in schemas (`schema.table`). Unqualified names resolve through `search_path`
(usually `public`). dbt builds models in the target's schema, and seeds and sources may sit in
another one: find the real schema with `list_tables` before writing a query.

## Date Parsing

- Non-ISO strings: `to_date(col, 'DD/MM/YYYY')`, `to_timestamp(col, 'DD/MM/YYYY HH24:MI')`
- There is no `TRY_CAST`: guard the cast, e.g. `CASE WHEN col ~ '^\d{4}-\d{2}-\d{2}$' THEN col::date END`
- Never `col::date` on non-ISO strings

## Window Filters and First Row per Group

```sql
SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY grp ORDER BY col DESC) AS rn
    FROM table
) ranked
WHERE rn <= 10
```
For exactly one row per group: `SELECT DISTINCT ON (grp) * FROM table ORDER BY grp, col DESC`

## Conditional Aggregates

`count(*) FILTER (WHERE status = 'completed')`, `sum(amount) FILTER (WHERE ...)`

## Date Spines

```sql
SELECT generate_series(min_date, max_date, interval '1 day')::date AS date_day
```
Always use the primary fact table's max date as endpoint - query its `MIN`/`MAX` first.

## Type Casting

`col::integer`, `col::numeric`, `col::text`, `col::date` (or `CAST(col AS ...)`)
