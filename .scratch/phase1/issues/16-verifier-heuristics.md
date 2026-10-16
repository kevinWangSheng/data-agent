# 16 — 验证工具与验证提示的口径修正

Status: in-review
Blocked by: 07
Spec: ../spec.md
Claimed by: claude-code (opus-5-5) 2026-10-16
Branch: worktree-16-verifier-heuristics

## Goal

`verify_model_values`、`audit_model_sources`、`check_model_schema` 与两个验证子 agent 提示不再把正确的 SQL 判成 MISMATCH/FAIL，也不放过真实的 fan-out 与计数错误。指标与粒度的语义从模型 SQL 读出（sqlglot），不再靠结果类型和列名猜。

## Authority

- May edit: `src/data_agent/verifier_tools.py`、`src/data_agent/agents/`（verifier.md、value-verifier.md）、`skills/dbt-workflow/SKILL.md`（只限 Step 8 与验证工具的调用说明）、`tests/`（除 `tests/acceptance/`）、`NOTICE`（若改动上游复制文件的修改说明）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $1

## Context

来源：工单 07 Comments 里 PR #12 各轮 Codex 与 da-review 记为已知限制的意见（人决定 2026-10-16：在工单 12 消融前修）。ADR-0005（Skill 与提示不含榜单专属内容）。上游 signalpilot-plugin 的 `agents/verifier.md`、`value-verifier.md`（改动要在文件头修改说明里写明）。

## Acceptance

每条配一个修复前会失败的测试（构造的小 DuckDB 模型）：

- [ ] 用 sqlglot 解析模型 SQL（最外层 SELECT，跟进 CTE）：切片维度取 GROUP BY 表达式对应的输出列，不取聚合输出（`MAX(ordered_at) AS ordered_at` 不是维度）；数值分组列（如 `year INT`）若在 GROUP BY 里则是维度。
- [ ] 计数判定：只有最外层聚合是 `COUNT`（允许 CAST、COALESCE 这类透明包装）才是计数；`CAST(SUM(a)/COUNT(*) AS BIGINT)` 不是。
- [ ] `COUNT(DISTINCT x)` 与上游 `COUNT(DISTINCT x)` 比，不与 `COUNT(*)` 比。
- [ ] 条件计数 `COUNT(CASE WHEN p THEN 1 END)` / `COUNT(*) FILTER (WHERE p)`：基线带上谓词 p；带不上就标 inconclusive，不报 MISMATCH。
- [ ] 计数类指标精确比较；±10% 容差只用于非计数的加和/均值。
- [ ] 解析不了 SQL 时退回现有的列名规则，并在输出里说明"未解析 SQL"。
- [ ] `value-verifier.md` 的 `total_X` 规则只适用于行数名词（如 `total_orders`），与同文件的加和指标说明一致；`total_amount`、`total_revenue` 按加和。
- [ ] `verifier.md` CHECK 3：lookup 表同键多行一律报可能的 fan-out，除非确认模型预期粒度就是这样；非键字段不同只用来解释原因，不能据此判 PASS。
- [ ] `verifier.md` CHECK 1：读 `dbt_project.yml` 的 `model-paths`（缺省 `models`），在其下递归找 SQL 与 YAML（含 ephemeral/disabled 配置）。
- [ ] `check_model_schema` 接受"列名:类型"对并与 catalog 的类型比较（类型名做 DuckDB/PG 归一化）；`verifier.md` CHECK 2 的说明与工具能力一致。
- [ ] `make check` 通过

## Evidence required

`make check` 输出；full 配置真实 dbt 运行一次（接缝 A 的 DBT_TASK），贴两个 `verifier_end` 事件；另用一个含 `COUNT(DISTINCT)`、条件计数、`MAX(date)` 输出列的小模型不经模型直接调 `verify_model_values`，贴输出。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 3（da-review 2 + eng:code-review 1，后者未修）
- Model spend: ~$0.3（估算；两次 `make test-integration` 各 5 次 agent 运行，dbt 运行 $0.032、$0.029）
- Outcome: 验收达成；value-verifier 名称规则与工具结论冲突一处，建议合并前修
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `670f1e8`、`9a95b83`、`39338b6`）

#### What changed
- **`verify_model_values`** now reads the model's SQL with sqlglot (`qualify` plus scopes against the catalog, through CTEs and subqueries):
  - The slice is the GROUP BY output columns, including numeric ones like `year`. `MAX(ordered_at)` is never a slice column.
  - A metric counts as a count only when its outermost aggregate is COUNT (CAST and COALESCE wrappers allowed).
  - The model's own COUNT, including DISTINCT, CASE WHEN or FILTER, is re-run over each upstream and compared exactly.
  - It reports "inconclusive" instead of MISMATCH when that count can't be rebuilt over an upstream: a column the SQL computes, renames or takes from another table, rows of an aggregated CTE, or a computed GROUP BY expression.
  - If the SQL doesn't parse, it falls back to the old column-name rules and says "Model SQL could not be parsed".
- **`check_model_schema`** accepts `name:data_type` pairs and compares types with DuckDB/Postgres aliases folded (int4 = INTEGER, text = VARCHAR); precision and length are not compared.
- **Prompts:**
  - `verifier.md` CHECK 1 finds models under `model-paths`.
  - CHECK 2 passes `name:data_type` pairs to `check_model_schema`.
  - CHECK 3 reports a repeated lookup key as possible fan-out, and differing non-key columns are never a reason to PASS.
  - `value-verifier.md`: `total_X` means COUNT(*) only for row nouns (`total_amount` and `total_revenue` are sums), counts must match exactly, and sums/averages match within ±10%.
  - The SKILL.md tool description is updated.
- 22 new tests; each failed before its fix.

#### Evidence
- `make check`: ruff clean, basedpyright 0 errors, `827 passed, 15 deselected`.
- **Real dbt run** (`make test-integration`, full config, seam A DBT_TASK) at clean commit `39338b6`: `5 passed, 10 xfailed`. Run result: 21 turns, `cost_usd` 0.0289. The agent's model counts `COUNT(CASE WHEN lower(trim(o.status)) IN (...) THEN o.order_id END)` over `stg_customers LEFT JOIN stg_orders`. The tool output:
  - `stg_orders`: `COUNT(CASE WHEN LOWER(TRIM(status)) IN ('completed', 'complete') THEN order_id END): 6 → Model matches …`
  - `stg_customers`: `uses stg_orders.status, stg_orders.order_id, not in main.stg_customers; no baseline, inconclusive`
- The two `verifier_end` events:
  ```
  {"seq": 127, "type": "verifier_end", "verifier": "verifier", "checks": [CHECK 1 PASS, CHECK 2 PASS, CHECK 3 PASS, CHECK 4 PASS, CHECK 5 N/A] (model customer_ltv)}
  {"seq": 129, "type": "verifier_end", "verifier": "value-verifier", "checks": [CHECK 1 PASS, CHECK 2 PASS, CHECK 3 PASS] (model customer_ltv)}
  ```
  The full lines are in `runs/16/real2_events.txt`.
- **Direct call** (`runs/16/evidence.py`, real MCP server over stdio, no model). The model uses `COUNT(DISTINCT)`, a CASE count, a FILTER count and a `MAX(ordered_at)` column, inside dbt Jinja with `with orders as (select * from {{ ref('orders') }})`. Output:
  ```
  Top row (by n_orders) slice: region = 'north'
  n_orders   → Model matches COUNT(*)
  customers  → Model matches COUNT(DISTINCT customer_id)
  returns    COUNT(CASE WHEN status = 'returned' THEN 1 END): 1 → Model matches …
  placed     COUNT(*) FILTER(WHERE status = 'placed'): 2 → Model matches …
  ```
  `last_ordered_at` is not in the slice. Full output is in `runs/16/evidence.txt`.

**Review: `da-review main` (Codex), 2 fix rounds, all findings fixed**
- **Round 1, three P2s, fixed in `9a95b83`:**
  - A count over a column the CTE transformed produced a false MISMATCH.
  - `COALESCE(placed, 0)` around a CTE's count wasn't followed.
  - A window `SUM` named `n_*` was compared as a count.
- **Round 2, three P2s, fixed in `39338b6`:**
  - Table qualifiers were dropped in joins.
  - CTE renames weren't followed.
  - A computed GROUP BY expression was sliced as the raw column.
- My own evidence run also caught a regression from round 2: a CTE named after its table (the dbt idiom) resolved to itself. Fixed in the same commit with a test.

**Review: `eng:code-review main`.** That was my last allowed fix round, so I left everything below unfixed:
- **Spec, implemented but possibly wrong (judgement call):** the `value-verifier.md` name rules ("`total_<row noun>` MUST match COUNT(*)", and FAIL when a "total" column matches COUNT(DISTINCT)) can still fail a correct `total_customers = COUNT(DISTINCT customer_id)` that the tool marked as matching. Not fixed only because of the round limit. I'd fix it before merge: let the tool's SQL-based verdict override the name rules, and keep the name rules only for the "could not be parsed" case. It's a prompt-only change.
- **Spec, not asked for:** "a min or max must match exactly" in `value-verifier.md`. It's consistent with the ticket's wording (±10% only for sums and averages); the header note doesn't mention it.
- **Spec, weak test:** the prompt test only checks that "exactly" and "10%" appear. Not fixed because of the round limit.
- **Standards:** no hard violations. Judgement calls I didn't act on:
  - `verifier_tools.py` is now about 1300 lines; the sqlglot lineage code could move to its own module.
  - `DIALECT = "duckdb"` duplicates `DuckDBGateway.dialect`. It's a known limitation for ticket 08: the Postgres gateway must supply its dialect.
  - Duplicated depth guards and the `trace` loop.
  - `Roles.counts` and `Roles.opaque` are parallel dicts, and string error returns stand in for a type.
  - Names: `derived`, `portable`, `origin`.

  These are refactor-level points with no behaviour impact, out of scope for this ticket.

**主会话**：名称规则与工具结论冲突那一处，并进 PR 第 1 轮退回一起修。`DIALECT = "duckdb"` 写死，Postgres 运行时 sqlglot 方言不对——等工单 08 合并后在集成时处理。
