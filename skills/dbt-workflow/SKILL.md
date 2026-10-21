---
name: dbt-workflow
description: "Load FIRST before any dbt project work. Covers the full 8-step dbt workflow: project scanning, skill loading, validation, macro discovery, research, technical spec, SQL writing, and verification. Also covers output shape inference, incremental model handling, and what to trust in YML."
disable-model-invocation: false
allowed-tools: Bash(dbt *) Bash(python3 *)
---

<!-- Copied from signalpilot-plugin (see NOTICE). Modified for data-agent: tool calls name this
agent's tools (query_database, list_tables; the verification tools and verifier agents only when
present); SignalPilot-only steps were removed or rewritten: analyze_project_db (now list_tables and
query_database), the knowledge-base check, TaskCreate tracking, loading domain/testing/snapshot/
versioning skills (data-agent does not ship them; dbt-write, dbt-debugging and the database's SQL
skill are loaded with this one), and the knowledge-base skill in Step 6 (its spec fields are inlined). Two
remarks about how results are graded were reworded as plain dbt reasons (ADR-0005). The rules that
limited rolling/period-over-period models to the latest date with NULL comparisons were replaced by
general ones (one date only when the description asks; comparisons from the source history). The
rule for skipping Steps 4-7 also keeps them when the task asks for a new model. Examples carrying
specific macro, column or entity names were made generic; the rule for review tasks no longer
names a task format. Step 8 uses the verifiers' four verdicts (PASS, FAIL, NEEDS_INFO, N/A) and
verify_model_values takes the model's upstream tables and the project directory and compares
the model SQL's own COUNTs exactly; sums and averages match only up to rounding; map_columns adds
no domain prefix. After the first failure attribution: an edit is compared with the original
predicate by predicate; rule-defined columns implement each factor as stated; Step 8 checks
column types for every model the task asks for and adds a key-level check against an
independent recomputation, which also gates the STOP. -->

# dbt Workflow Skill - Full Project Lifecycle

## Overview

This skill orchestrates the complete dbt project workflow. Load it FIRST whenever
working on a dbt project - it contains rules that affect how you interpret everything.

## Tools

### scan_project.py - Project scanner (Step 1)
```bash
python3 "${CLAUDE_SKILL_DIR}/scan_project.py" "<project_directory>"
```
Returns: models to build, stubs to rewrite, dependencies, required columns,
sources, macros (with full definitions), and current_date hazards.

### validate_project.py - dbt parse validator (Step 3)
```bash
python3 "${CLAUDE_SKILL_DIR}/validate_project.py" "<project_directory>"
```
Runs `dbt parse` and returns structured errors, warnings, and orphan patches.

### list_tables and query_database - DB hints (MCP tools, Step 1)
Call `list_tables` for the tables that exist (materialized models included) and
their columns. Use `query_database` (read-only SELECT) for the database-derived
hints: lookup joins (`_id` columns with matching dimension tables), staging-vs-raw
row gaps, and parent-child driving tables. Run alongside scan_project.py in Step 1.

### map_columns - Upstream column mapper (MCP tool, Step 7) (a verification tool: use it only if it is in your tool list)
Call `map_columns(model_name, project_dir)`.
Queries each upstream table's columns and maps them
against the YML contract. Outputs every column as MAPPED, UNMAPPED-INCLUDE,
or UNMAPPED-EXCLUDE with the recommended output alias.
Run this BEFORE writing each model's SQL. Include all UNMAPPED-INCLUDE columns; skip UNMAPPED-EXCLUDE columns (see dbt-write §1).

### verify_model_values - Aggregate cross-validator (MCP tool, Step 8) (a verification tool: use it only if it is in your tool list)
Call `verify_model_values(model_name, source_tables, project_dir)`, with the
tables in the model's FROM / ref() / source() as `source_tables`.
Queries the model output, picks the top row's slice (the model SQL's GROUP BY
columns), and reports COUNT(*) and COUNT(DISTINCT <key>) for that slice in each
upstream. Marks each count metric (a COUNT(...) in the model's SQL) as matching
the same COUNT over the upstream, exactly, or a MISMATCH. Used by the
value-verifier subagent in Step 8.

## The 8-Step Workflow

ALWAYS run Steps 1, 2, 3, and 8. Steps 4-7 are ONLY for projects
with stubs to rewrite or models to build (from the Step 1 scan).
If the scan shows 0 stubs and 0 missing models AND the task does NOT
ask to create a model that does not exist yet or to edit, fix, remove,
or modify existing files, skip Steps 4-7 entirely - go from Step 3
straight to Step 8. The scan only knows models declared in YAML, so a
new model the task asks for counts as a model to build.

If the task edits existing models (edit, fix, remove a variable, refactor),
do NOT skip Steps 4-7 even if the scan shows nothing to build. Edit the
files in Step 7, then run `dbt run --select <edited_model1> <edited_model2>`
to rebuild them - edited SQL only takes effect after materialization.

Even if the task says "create model X" - if X.sql already exists in
the scan's "EXISTING COMPLETE" list, it is already created. Verify it
in Step 8, do not recreate it.

### Step 1 - Map the project
Run BOTH scans IN PARALLEL — a single turn with both tool calls. They are independent
(one reads the project files, the other queries the database), so do NOT wait for one
before calling the other:
1. `python3 "${CLAUDE_SKILL_DIR}/scan_project.py" "<project_directory>"` — filesystem scan.
2. `list_tables` — what is materialized, with columns; then `query_database` for the
   database hints (lookup joins, staging-vs-raw gaps, parent-child driving tables).

Read the ENTIRE output of BOTH. Record:
- STUBS TO REWRITE
- MODELS TO BUILD
- DEPENDENCIES
- REQUIRED COLUMNS
- AVAILABLE MACROS (with definitions)

If the task describes a runtime bug (type mismatch, wrong values, broken output), run `dbt run --select <pre_existing_model> 2>&1 | tail -50` on the affected pre-existing model - `dbt parse` passing does NOT mean the model produces correct output.

### Step 2 - Read the supporting skills
The dbt-write skill, the dbt-debugging skill and the SQL skill for your
database are loaded below this one. Read dbt-write and the SQL
skill now - they contain rules needed for writing AND verifying models.

Do NOT skip this step. These skills contain rules that apply to
verification (Step 8), not just writing.

### Step 3 - Validate and fix stale upstreams
Run `python3 "${CLAUDE_SKILL_DIR}/validate_project.py" "<project_directory>"`.
If errors, fix them before proceeding.

MANDATORY: if the Step 1 scan flagged `current_date` or `now()` hazards in
PRE-EXISTING models, rebuild those models NOW:
`dbt run --select <flagged_model1> <flagged_model2>`
These models contain stale data from a prior run. Without rebuilding, all
downstream models inherit wrong row counts.

Do NOT use `+`. If no hazards flagged, skip the rebuild.

### Step 4 - Discover project macros
Read the AVAILABLE MACROS section from Step 1 output. For each macro NOT
referenced by any existing complete model:
1. Read its definition - it is printed in the scan output.
2. Identify what column it produces - the macro's body shows the output
   expression and the name it is meant to carry (often the input column with
   a prefix or suffix).
3. Record which models MUST use it - any model whose source table has the
   macro's input column.

These macro-derived columns are ADDITIONAL columns beyond the YML list.
Include them in your SQL in Step 7.

### Step 5 - Research (data exploration)
For EACH model that needs SQL, gather the facts to write it correctly:

1. **Driving table** - the Step 1 scan's AGGREGATION DRIVING TABLE hint flags a
   parent whose rows have no matching children. Classify by the model's METRICS, then
   FOLLOW the result - this is binding; do NOT override based on the task phrase
   "aggregate X by Y" (that names what to summarize, not the FROM clause):
 - If every child metric is a COUNT or SUM (e.g. `total_<children>`) -
     meaningful as 0 for a childless parent - KEEP childless rows: drive FROM the
     parent, LEFT JOIN children.
 - If any metric is a RATIO, AVERAGE, or SCORE that divides by the child count
     (a share of the children, an average) - undefined (division by
     zero) for a childless parent - DROP childless rows: drive FROM the child
     aggregation, INNER JOIN the entity.
   LEFT JOIN all other upstreams.
2. **Cardinalities** - run `query_database` with `SELECT COUNT(*)` and
   `SELECT COUNT(DISTINCT <key>)` on each upstream to confirm grain.
3. **Contract** - read the model's YML entry for column names, tests, and
   descriptions.
4. **Sibling patterns** - read sibling SQL and the YML in `dbt_packages/`
   for JOIN types, CASE WHEN predicates, and categorical vocabulary. The test
   data is often too sparse to reveal every value; the package YML descriptions
   are not.
5. **Categorical values** - run `query_database` with `SELECT DISTINCT <col>`
   on status/flag/type columns before writing CASE WHEN. The values determine
   which rows are purchases vs returns vs cancelled.

**CTE extraction override:** If you are extracting CTEs from a parent model,
read the parent model's outermost final SELECT - the table in its FROM clause
is the true spine. Run `SELECT COUNT(DISTINCT <key>)` on both the spine and the
aggregation source. If counts differ, use the spine.

### Step 6 - Write technical spec
Write `<project_dir>/technical_spec.md` - a structured plan that distills
your Step 5 research into decisions for every model: build order, sources,
joins (type and keys), filters, column expressions, grain, and expected row
count with the query that measured it (with the rows each added filter removes, dbt-write §5).

If `technical_spec.md` already exists (retry), read it and skip re-research.

STOP this step when the spec file is written and every model has all seven
fields above.

### Step 7 - Write and Build ALL Models
Read `<project_dir>/technical_spec.md` from Step 6. Write SQL for each
model following the spec's build order, sources, joins, and expressions.

For each model in dependency order:
1. Match YML column names EXACTLY where they exist.
2. Copy JOIN types and aggregation patterns from sibling analysis. Before
   finalizing this model, re-check the Step 1 scan's LOOKUP JOINS section and the
   sibling's date/timestamp handling for this model's columns - these two are the
   easiest to skip.
3. Add macro-derived columns from Step 4.
4. Read the YML description for date boundaries. If it says "to the
   current date" or "to today", add `WHERE date_col <= current_date`.
5. Read the YML description for transformation rules. If it states
   explicit logic ("categorized as X if Y"), implement that logic. For a score
   or weighted column, write one expression per factor and apply each cap and
   threshold to the measure it names, not one clamp on the final value.
6. Write the SQL file.

Do NOT rewrite pre-existing SQL files from scratch. For bug-fix tasks, EDIT the existing SQL minimally - change only the broken expression (e.g., add a CAST, fix an aggregation function). Keep all existing JOINs, CTEs, column aliases, and WHERE clauses intact. Rewriting from scratch drops logic the original author put there (lookup JOINs, filters, aliases) that you may not notice is missing. After any edit, compare the new SQL with the original predicate by predicate (every WHERE, HAVING and JOIN condition): each stays unless the task names it for removal.

After ALL SQL files are written, build ONLY the models you wrote:
`dbt run --select <model1> <model2> <model3>` (NO `+` prefix).

The `+` prefix rebuilds upstream models you did NOT write, destroying
surrogate key assignments and FK relationships in pre-existing models.
Try without `+` first. If a model fails because an upstream dependency
is not yet materialized, THEN add `+` for that specific model only.

If `dbt run` fails on ANY model - including package models in
`dbt_packages/` - load the dbt-debugging skill and fix the error.
Broken upstream models block every downstream model.

Do NOT run a bare `dbt run` - it rebuilds ALL models including pre-existing ones.

### Step 8 - Verify and Fix
1. Run `query_database` with `SELECT 1`. If it errors, wait and retry.
2. If the Agent tool offers the `verifier` and `value-verifier` agents,
   dispatch BOTH in parallel:
 - `subagent_type="verifier"` - structure checks
 - `subagent_type="value-verifier"` - value checks
   Both are READ-ONLY. They return reports. They fix nothing.
   Pass: project directory and the names of every model the task asks for,
   not only the ones you last edited.
   Also pass the path to `technical_spec.md` so verifiers can reference it.
   Do NOT include column definitions, SQL logic, or your interpretation
   of what the columns mean. The verifiers must discover this themselves.
   If those agents are not offered, run the same checks yourself, read-only,
   with `list_tables` and `query_database`, for every model the task asks for:
   CHECK 1 it exists as a table; CHECK 2 its column names and column types
   match the YML contract and the task (a timestamp the source holds is not a
   DATE); CHECK 3 its row count matches the grain (`COUNT(*)` vs
   `COUNT(DISTINCT <key>)`, against the driving table); value CHECK 2 its
   metric totals match the same aggregates computed from the sources, with
   the model's filters: counts, mins and maxes exactly, sums and averages up to
   rounding (equal at the column's DECIMAL places; a float column within
   1e-6 relative error). Anything more is a mismatch.
   Then, whether or not the verifiers ran, run the key-level check yourself:
   totals can match while single keys are wrong, and a check that reuses the
   model's expression repeats its error. For each built model, pick 3-5 keys
   at the output grain (include edge cases: NULLs, zeros, the largest group)
   and recompute every metric for those keys straight from the sources with a
   query you write from the task's and YML's written rules - never copy the
   model's expression or CTEs. For a column defined by rules (weights,
   thresholds, caps, CASE branches), pick rows that exercise each branch.
   Compare with the model row by row; a difference is a FAIL like a
   verifier's, and the fix follows step 3.
3. Read BOTH reports. Every check is PASS, FAIL, NEEDS_INFO or N/A. Only act on
   checks the verifiers marked FAIL or NEEDS_INFO (give the verifier what it asks for).
   If a check is PASS or N/A, accept it - do NOT override the verifier
   or "fix" something it approved, unless your key-level check shows a
   difference there. If the model's row count differs from
   your technical spec but the verifier says CHECK 3 PASS, update your
   spec to match reality - the verifier has already diagnosed whether
   the row count is correct.

   For each FAIL:
 - Structure CHECK 1 FAIL (missing table): run `dbt run --select +<model>`.
 - Structure CHECK 2 FAIL (missing columns): a column that appears ONLY in the
     YML - with no source counterpart, no sibling that outputs it, and not named by
     the task - is aspirational; do NOT add it (it makes the output one column too
     wide than the contract). Otherwise - it is a real source column the
     model should carry - add it and rebuild.
 - Structure CHECK 3 FAIL (row count): investigate SQL logic,
     pre-aggregate or add GROUP BY, rebuild.
 - Value CHECK 2 FAIL (aggregate mismatch): apply the verifier's
     prescribed fix exactly as stated. Do NOT rationalize the mismatch
     as intentional. The verifier's numbers are measured from source data.
   For ANY fix, update `technical_spec.md` FIRST, then rewrite the SQL
   from the updated spec. Rebuild.
4. STOP when both reports show every check PASS or N/A and your key-level check matches. Once verification passes, do NOT modify any model files - the task is complete. No further investigation, no "what if" queries, no source table changes. The verifiers and your key-level check are the final authority.

---

## Output Shape - Read YML Description BEFORE Writing SQL

Extract from `description:` field:
- **ENTITY**: "for each <entity>" → one row per qualifying entity
- **QUALIFIER**: "due to <reason>" / "with at least one <child>" → filter or INNER JOIN
- **RANK CONSTRAINT**: "top N" / "ranks the top N" → exactly N output rows. Filter
  with `ROW_NUMBER() ... <= N` using a deterministic tiebreaker (add primary key to
  ORDER BY). Do NOT use DENSE_RANK for filtering - it can return more than N rows.
- **TEMPORAL SCOPE**: "latest", "current", "as of the last date" in the
  description → ONE output date. Filter with
  `WHERE date_col = (SELECT MAX(date_col) FROM source)`. A rolling window or a
  period-over-period column alone does not limit the output to one date.
- **DATE BOUNDARY**: "from X to the current date" or "to today" in the description
  → cap the output with `WHERE date_col <= current_date`. When crossing a calendar
  spine with entities (one row per entity per day), add the cap to the cross
  join: `ON spine.date_day >= entity.<start_date> AND spine.date_day <= current_date`.
- **PERIOD-OVER-PERIOD**: If the description mentions MoM, WoW, YoY comparisons,
  compute them from the periods the source holds - see rule below.

**How to read YML descriptions:** Descriptions tell you what the data MEANS, not
what code to write. Use them to:
- Identify which source columns to use
- Understand the business meaning of each column
- Pick the right aggregation logic

But do NOT treat descriptions as literal computation instructions. After reading
the description, always verify your logic against the actual source data.

Write at top of SQL: `-- EXPECTED SHAPE: <row count or formula> - REASON: <quote>`

## Incremental Models and Period-Over-Period Columns

When a dbt project uses `materialized="incremental"` models, the project is
designed to accumulate state over multiple runs. On a **first run** (full refresh,
no prior state), incremental models build from scratch.

**Period-over-period metrics (MoM, WoW, YoY)** come from the source history, not
from earlier runs of the model: aggregate to the period grain, then compare with
`LAG`/`LEAD` over the periods. Check which periods the source holds
(`SELECT MIN(date_col), MAX(date_col)` with `query_database`). A period with no
earlier period in the source gets NULL for the comparison - do NOT fill it with 0.
If a sibling incremental model reads its prior periods from `{{ this }}` instead,
do not copy that into a table model: a table model has no `{{ this }}` history.

**Debugging incremental models with missing rows**: inspect the boundary predicate first. If the model uses `WHERE date_col > (SELECT MAX(date_col) FROM {{ this }})`, rows sharing the MAX date are silently dropped - change `>` to `>=` and add a `unique_key` to handle deduplication. Do NOT use `--full-refresh` as a fix - it bypasses the incremental logic that every later run depends on.

## What to Trust in YML

**Trust YML for**: column names (exact match required), column descriptions (what
each column represents), ref dependencies (what tables to join).

`not_null` YML tests are output assertions, not input filters. They describe
what the output SHOULD contain - they do NOT constrain which rows appear.

Apply `WHERE IS NOT NULL` ONLY on `_id` join-key columns - NULL keys cause
cross-joins. Do NOT filter descriptive columns (name, title, date) based on
`not_null` - NULL descriptive fields are valid data (a date-bounded metric
still leaves out NULL-date rows, dbt-write §5).

not_null tests are NOT a reason to change JOIN type or driving table. If a
metric column has a not_null test but would produce NULL from a LEFT JOIN
(e.g., no matching spend data for some dimension values), do NOT switch to
INNER JOIN to force non-NULL values. The domain skill's driving table rule
determines the output population - not_null tests cannot override it.

## Google Sheets and CSV Sources

Google Sheets and CSV connectors produce trailing empty rows - entire rows where every column is NULL. When UNION ALL combines multiple sheets, identical empty rows multiply across sources.

Do NOT filter these with `WHERE col IS NOT NULL` - that removes valid rows with partial NULLs. Use `SELECT DISTINCT` after the UNION ALL to collapse identical empty rows to one.

**Do NOT trust YML for**: grain/row count. YML `unique` and `not_null` tests are
assertions that may be aspirational or wrong.

Derive the grain from these signals (in priority order):
1. **Unique key structure**: If the YML defines a unique/surrogate key, examine its composition
2. **Column list**: The columns themselves reveal the grain
3. **Upstream model grain**: Check existing upstream models
4. **Source cardinality**: Query source tables to check expected row count
5. **Sibling model row counts**: Check complete models at the same level

Do NOT pre-deduplicate lookup tables before joining - fan-out from duplicate
keys may be valid data. See dbt-write Section 3 for when fan-out is correct
vs when to deduplicate.

## Validate Project (Standalone)

Run: `python3 "${CLAUDE_SKILL_DIR}/validate_project.py" "<project_dir>"`

This runs `dbt parse` and produces a structured report of errors, warnings,
and orphan patches (yml-defined models with no .sql file). Use this as the
Step 3 validation when the MCP server is not available. Accepts an optional
second argument for timeout in seconds (default 60).

## Rules

- For tasks that ask you to review or explain existing SQL: trace every output column back through all CTEs to its source. A computed-but-unused column (calculated but never SELECTed) is a defect. Do not assert a data quality problem without querying the source to confirm it exists.
- NEVER run `dbt` commands with `run_in_background` or `&` - dbt holds a database
  write lock while running
- Do NOT modify `.yml` files unless fixing a missing `schema:` in a source definition
- Do NOT guess column names - use the YML contract as source of truth. Exception: if a pre-existing table or sibling model shows the column contains a display value (name, label) rather than a raw FK, follow that pattern (see dbt-write §3).
- Do NOT install external packages - all dbt packages are pre-bundled in dbt_packages/
