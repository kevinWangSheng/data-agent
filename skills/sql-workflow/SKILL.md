---
name: sql-workflow
description: "Use this skill before writing any SQL query. Covers: output shape inference (cardinality clues from the question), efficient schema exploration, iterative CTE-based query building, structured verification loop (row count, NULL audit, fan-out check, sample inspection), error recovery protocol, turn budget management, and common SQL mistakes."
type: skill
---

<!-- Copied from signalpilot-plugin (see NOTICE). Modified for data-agent: tool calls use this
agent's `mcp__data__` tools (query_database, list_tables); steps that used SignalPilot-only tools
(describe_table, explore_column, find_join_path, validate_sql, debug_cte_query, schema_overview)
now use query_database or were removed. Content about benchmark scoring was removed (ADR-0005): the
"Saving Output" section (result.sql/result.csv and their CSV format) and "Common Benchmark Traps";
that section's general SQL checks are kept as section 7, with a domain-neutral example. -->

# SQL Workflow Skill

## 1. Schema Exploration - Do This First

Before writing any SQL, understand the data:

0. **Read local schema files first** (if schema/ directory exists in workdir):
 - `schema/DDL.csv` - all CREATE TABLE statements (if it exists)
 - `schema/{table_name}.json` - column names, types, descriptions, sample values
   Reading these files costs zero tool calls and gives you table structure + sample data.
   Only call MCP tools for information not in the local files (e.g., row counts, live data exploration).
1. Call `list_tables` to get all schemas, tables and their columns - only if no local schema files exist.
2. Run `SELECT DISTINCT col, COUNT(*) ... GROUP BY col` with `query_database` on categorical columns to see their values (for filtering/grouping)

Stop exploring after 3-5 tool calls. Write SQL based on what you've found.

## 2. Output Shape Inference - Before Writing SQL

Read the task question carefully for cardinality clues:

- "for each X" → GROUP BY X, one output row per X
- "top N" / "top 5" → LIMIT N or QUALIFY RANK() <= N
- "total / sum / average" → single row aggregate
- "list all" → detail rows, no aggregation
- "how many" → COUNT, result is 1 row 1 column

Write a comment at the top of your SQL:
```sql
-- EXPECTED: <row count estimate> rows because <reason from question>
```

Critical checks:
- If the question asks for a single number, the result MUST be 1 row × 1 column
- If the question says "how many", verify the result has exactly 1 row with a COUNT value
- If "top N" appears in the question, verify the result has at most N rows

## 3. Iterative Query Building - Build Bottom-Up

Do NOT write a 50-line query and run it all at once:

1. Write the innermost subquery or first CTE first
2. Run it standalone with `query_database` - verify row count and sample values
3. Add the next CTE, verify again
4. Continue until the full query is built

Example incremental pattern:
```sql
-- Step 1: verify source
SELECT COUNT(*) FROM orders WHERE status = 'completed';

-- Step 2: verify join partner cardinality
SELECT COUNT(*), COUNT(DISTINCT customer_id) FROM orders;

-- Step 3: build first CTE, verify
WITH order_totals AS (
  SELECT customer_id, SUM(amount) AS total
  FROM orders
  GROUP BY customer_id
)
SELECT COUNT(*), COUNT(DISTINCT customer_id) FROM order_totals;

-- Step 4: add final aggregation
```

## 4. Execution and Structured Verification

```
mcp__data__query_database
  sql="SELECT ..."
  row_limit=1000
```

After executing, run these checks IN ORDER before answering:

1. **Row count sanity**: Does 0 rows make sense? Does 1M rows make sense for a "top 10" question?
2. **Column count**: Does the result have the right number of columns for the question?
3. **NULL audit**: For each key column - unexpected NULLs indicate wrong JOINs:
   ```sql
   SELECT COUNT(*) - COUNT(col) AS nulls FROM (your_query) t
   ```
4. **Sample inspection**: Look at 5 rows - are values in expected ranges? Do string columns have meaningful values (not join keys)?
5. **Fan-out check**: If JOINing, compare `COUNT(*)` vs `COUNT(DISTINCT primary_key)`:
   ```sql
   SELECT COUNT(*) AS total_rows, COUNT(DISTINCT <pk>) AS unique_keys FROM (your_query) t;
   ```
   If they differ, you have duplicate rows from a fan-out JOIN.
6. **Re-read the question**: Does your output actually answer what was asked?

## 5. Error Recovery Protocol

- **Syntax error**: Read the error message, fix the reported part, and re-run
- **Wrong results**: Do NOT just re-run the same query. Diagnose: which JOIN is wrong? Which filter is too aggressive?
- **Zero rows**: Binary-search your WHERE conditions - remove them one at a time to find the culprit:
  ```sql
  SELECT COUNT(*) FROM table WHERE cond_1;             -- still same? keep it
  SELECT COUNT(*) FROM table WHERE cond_1 AND cond_2;  -- drops? cond_2 is culprit
  ```
- **Too many rows**: Check for fan-out (duplicate join keys) or missing GROUP BY
- **CTE debugging**: Run each CTE on its own with `query_database` to find which step breaks

## 6. Turn Budget Management

- **First 3 turns**: Schema exploration only (`list_tables`, distinct values of key categorical columns). STOP exploring.
- **Turns 4 through (N-3)**: Write query iteratively - execute and verify each step.
- **Last 3 turns**: Give the answer. If you have a working query, ANSWER NOW - do not keep iterating.

If your query works and passes all verification checks, ANSWER IMMEDIATELY - do not continue exploring "just in case".

## 7. Common SQL Mistakes

- **Fan-out from JOINs**: Always check `COUNT(*) vs COUNT(DISTINCT key)` after every JOIN
- **Wrong NULL handling**: Use `IS NULL` / `IS NOT NULL`, not `= NULL`
- **Date format mismatch**: Check the actual format stored in the column by sampling its values
- **Case sensitivity**: Use the correct case-insensitive function for your backend
- **Interpretation errors**: Before answering, re-read the original question. Verify:
  * Filter conditions match domain values (check the column's distinct values if unsure)
  * "Excluding X" means the right thing (NOT IN vs EXCEPT vs WHERE NOT)
  * Metrics match the question's definition (e.g., "customers who ordered" means at least one order, not every customer row)
