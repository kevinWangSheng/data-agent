# Coding standards

Read by the reviewer (`/code-review`, Codex). Only judgment rules live here; anything a tool can check is
enforced by `make check` and pre-commit instead. Each rule must be checkable against a diff.

## Python

- Python 3.12+. Full type annotations on every function signature, including return types.
- New behavior comes with a test at the seam the spec agreed on. Bug fixes come with a test that failed
  before the fix.
- Tests do not call a real model or the network unless marked `@pytest.mark.integration`.
- No dead code: no commented-out blocks, unused helpers, or compatibility shims for our own old names
  (ktx `AGENTS.md`). The project has no public users yet; prefer clean breaking changes.
- Prefer plain, explicit code over clever abstractions; keep modules small enough to read in one pass.

## Project-specific

- **Skills contain no benchmark-specific content.** No task ids, expected values, table names lifted from
  a benchmark answer, or hints tuned to one task (ADR-0005). Workflow guidance must hold for any dbt project
  or database.
- **Never bypass the read-only gateway.** All SQL against a data source goes through the gateway's AST
  check; no direct driver calls from tools, skills, or subagents. The only write path is a dbt project
  edit followed by `dbt run` (CONTEXT: dbt write).
- **Holdout stays blind.** No code path prints or stores per-task failure details for the evaluation
  holdout where a coding agent can read them; only aggregate scores (ADR-0004).
- **Files copied from upstream keep their original copyright header** and are listed in `NOTICE`.
- **No secrets or server addresses in code, tests, fixtures, docs, or run artifacts.** Read them from the
  environment; refer to the server only by its SSH host alias.
- Every run writes its version header (git commit, Skill version, config name, model, SDK version) as the
  first event (plan §7).
