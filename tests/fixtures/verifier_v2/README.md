# Fixtures for the verifier v2 acceptance tests (ticket 44)

- `project_before/`: the dbt project as the harness snapshots it before the agent starts.
- `project_after/`: the same project after an agent's edits. Each edit is one change type that
  `model_diff` must report (spec "Testing Decisions"): a dropped filter (`stg_orders`), a swapped
  source (`stg_customers`), UNION -> UNION ALL (`all_events`), a new LIMIT (`top_orders`), a
  materialization change in `dbt_project.yml` (`daily_sales`), an edit inside a `{% if %}` model
  (`conditional`, must fall back to a text diff), a new model with an agent-written YML
  (`customer_ltv`), a changed window ORDER BY (`ranked_orders`), and one untouched model
  (`daily_returns`, must not appear). Nine models in all.
- `verifier_v2.sql`: builds the DuckDB file the tools query: the "built" models as `dbt run`
  would have left them from `project_after/`, plus `cmp_model` for `compare_by_key`. Tests build
  it into their own temp dir, so no binary is committed and worktrees never share a file.
- `verifier_transcript.jsonl`: a synthetic subagent transcript in the CLI's format (one JSON
  object per line; a model call is one `api-request` line followed by one `assistant` line per
  content block sharing a `message.id`; tool results are `user` lines). 3 model calls, 4 tool
  calls; the last text block is a v2-format report.
