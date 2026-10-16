---
name: dbt-debugging
description: "Load when dbt run or dbt parse fails. Covers YML duplicate patches, ref errors, passthrough model warnings, current_date fixes, DuckDB error messages, and zero-row diagnosis."
type: skill
---

<!-- Copied from signalpilot-plugin (see NOTICE). Modified for data-agent: SignalPilot-only tools
were replaced (dbt_project_map by the dbt-workflow scan, get_date_boundaries by MIN/MAX queries
and describe_table by list_tables, both through this agent's tools); the remark about a sandbox
was reworded (ADR-0005); the rule about models named like source tables was rewritten as a general
rule without example table names. -->

# dbt Debugging Skill

## 1. Duplicate YML Patches (VERY COMMON)

dbt fails with "Duplicate patch" when the same model appears in multiple YML files.
Fix in ONE pass:
1. Glob `models/**/*.yml` to find all YML files
2. Keep the entry with the full contract (descriptions, refs, columns) - usually in a subdirectory YML
3. Remove the duplicate from `schema.yml` (which typically only has tests)

## 2. Ref Not Found

If `Compilation Error: node not found for ref()`:
- Check if the name is a raw DuckDB table: `SELECT table_name FROM information_schema.tables WHERE table_name = 'name'`
- If yes, create an ephemeral stub:
  ```sql
  {{ config(materialized='ephemeral') }}
  select * from main.<name>
  ```
- If ephemeral causes CTE issues, replace `{{ ref('name') }}` with `main.name` directly

## 3. Model Named Like a Source Table

A model materializes into the target schema under its own name. If a source
table has the same name AND lives in that same schema, `dbt run` replaces the
source data with the model.
1. Before naming a model after a source table, check where the source lives:
   `list_tables`, or `SELECT table_schema, table_name FROM information_schema.tables
   WHERE table_name = '<name>'` with `query_database`.
2. Same schema as the model's target: give the model another name, or, when the
   source definition in YML lacks a `schema:`, add the source's real schema there
   so dbt reads it from the right place.
3. Different schemas: the same name is legal - do NOT refuse or rename it.

## 4. current_date Fix

If the dbt-workflow scan warns about `current_date` usage:
1. Run `SELECT MIN(<col>), MAX(<col>)` with `query_database` on the source's date columns - pick the column whose range ends at the data's real last date
2. Replace `current_date`/`now()` with `(SELECT MAX(<col>) FROM {{ ref('<table>') }})`
3. For package models: create `models/<name>.sql`, paste full SQL, replace current_date

## 5. ROW_NUMBER Non-Determinism

If a model uses ROW_NUMBER/RANK:
1. Check if ORDER BY columns are unique within each partition
2. If not unique, append the primary key to ORDER BY
3. Re-run `dbt run --select <model>`

## 6. DuckDB Error Messages

| Error | Fix |
|-------|-----|
| `invalid date field format` | `STRPTIME(col, '%d/%m/%Y')::DATE` |
| `Table does not exist` | Check actual names with `list_tables` |
| `column not found` | Check exact names - case matters in DuckDB |
| `Cannot mix TIMESTAMP and INTEGER` | Cast both args to same type |
| `No function matches DOUBLE / VARCHAR` | Add explicit `CAST()` |
| `fivetran_utils is undefined` | Run `dbt deps` (only if `packages.yml` exists) |

## 7. Package Model Build Failures

If `dbt run` fails on a model inside `dbt_packages/` with a type error
(e.g., `date_trunc` on an INTEGER, `No function matches`), you MUST fix
the package SQL file directly. The project may have no internet, so you cannot
reinstall the package. Read the failing SQL, find the type mismatch, and
add the appropriate CAST or conversion (e.g., `to_timestamp(epoch_col)`
for epoch integers, `CAST(col AS DATE)` for type mismatches). Broken
upstream models block everything downstream.

## 8. Zero-Row Model

Binary search: comment out WHERE clauses and JOINs one at a time to find which
condition drops all rows. Most common cause: INNER JOIN where LEFT JOIN is needed.

## 8. Fan-Out (Too Many Rows)

1. Diagnose: `SELECT join_key, COUNT(*) FROM right_table GROUP BY 1 HAVING COUNT(*) > 1`
2. Fix A: pre-aggregate right table before joining
3. Fix B: `SELECT DISTINCT` (if valid for the grain)
4. Fix C: `ROW_NUMBER()` dedup pattern
