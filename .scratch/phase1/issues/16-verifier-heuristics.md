# 16 — 验证工具与验证提示的口径修正

Status: done
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

- Attempts: 3
- Review rounds: 7
- Model spend: ~$0.45
- Outcome: 退回第 2 轮已修（sum/avg 只容舍入误差）；最后一轮 da-review 无新问题，3 条已知限制保留
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

### 2026-10-16 第 7 步：PR #15 退回第 1 轮（主会话）

`39338b6` CI 两次全绿。在原分支上修，修完提交，不 push、不 rebase；各配修复前会失败的测试。

1. **Codex P2（`verifier_tools.py:712`）**：`ref()`/`source()` 替换成逻辑名，source 有 `identifier` 覆盖或模型配了 `alias` 时与 catalog 的物理表名对不上，正确的计数被报成缺失/inconclusive。按项目的 dbt 元数据解析成物理关系名（source YAML 的 `identifier`、模型 `config(alias=...)`/YAML `alias`；有 `target/manifest.json` 时优先用它）。
2. **Codex P2（`verifier_tools.py:1185`）**：嵌套类型只取外层名，`INTEGER[]` 与 `VARCHAR[]` 都归一成 `ARRAY`；`MAP`/`STRUCT` 同理。递归归一嵌套类型参数。
3. **工单 agent 自己建议的提示修正（`agents/value-verifier.md`）**：名称规则（`total_<行名词>` 必须等于 COUNT(*)、"total" 列等于 COUNT(DISTINCT) 判 FAIL）会推翻工具已基于 SQL 判为匹配的 `total_customers = COUNT(DISTINCT customer_id)`。改为：工具给出基于 SQL 的结论时以它为准，名称规则只用于"Model SQL could not be parsed"的情况。顺带把 prompt 测试从"出现某些词"改成断言这条优先级。

修完 `make check`、full 配置真实 dbt 运行一次，再跑一轮 `da-review main`。最终回复给出 Comments、Ledger（Attempts 2）和 `Status: in-review`。

### 2026-10-16 Ticket agent, send-back round 1 (commits `b8f89d0`, `dc978ed`, `af8d190`)

**The three send-back items (`b8f89d0`).** Each has a test that failed before the fix.

1. **`ref()` / `source()` now resolve to the physical table names.**
   - The new `dbt_relations(project)` reads `target/manifest.json` first.
   - Without a manifest, it falls back to:
     - the model YAML's `alias:`, then the model SQL's `{{ config(alias=...) }}`, which takes precedence as in dbt;
     - the source YAML's `schema` and `identifier`.
   - `parse_model`, `verify_values` and `map_columns` all use it.
   - When the model itself has an alias, its SQL file is still found under the model name.
   - Tests:
     - `test_verify_model_values_reads_a_ref_through_the_model_alias`
     - `test_verify_model_values_reads_a_source_through_its_identifier`
     - `test_verify_model_values_prefers_the_manifests_relation_names`
2. **Nested types are normalized recursively.**
   - `INTEGER[]` becomes `ARRAY(INT)` and `VARCHAR[]` becomes `ARRAY(VARCHAR)`; `MAP` and `STRUCT` are normalized field by field; `LIST` is the same as `ARRAY`.
   - A bare `ARRAY`, which is how Postgres' information_schema reports any array, matches any array.
   - Tests: `test_check_model_schema_compares_nested_types_by_their_element_types`, `test_an_array_without_element_types_matches_any_array`.
3. **`value-verifier.md`: the tool's verdict now wins over the name rules.**
   - When the output starts with "Model SQL read", the tool's count verdict decides. The name rules, including "total_<row noun>" and the under-counting FAIL, apply only when the output says "Model SQL could not be parsed" or "not read".
   - The prompt test now asserts this precedence: `test_value_verifier_lets_the_tools_sql_verdict_override_the_name_rules`.
   - The exact/±10% test now checks the actual rule sentences rather than single words.

**`da-review main`, first run → fixed in `dc978ed`:**
- **P2:** a column the SQL reads through a UNION fell back to the name rule. A correct `SUM` named `n_units` was reported as a MISMATCH. Now, once the SQL is parsed, such a column is inconclusive.
- **P2:** `declared_columns` split nested types such as `STRUCT(id INT, amount DECIMAL(10,2))`. It now splits by bracket depth.

**`da-review main`, second run → fixed in `af8d190`:**
- **P2:** when two schemas had a table of the same name, a count's column lost its schema. It now keeps `(schema, table, column)`.
- **P2:** a constant output such as `'all' AS region` was used as a slice dimension. It no longer is.
- **P2:** the subtotal row of `ROLLUP` / `CUBE` / `GROUPING SETS` was sliced with `IS NULL`. Those keys are now marked unsliceable, which makes the comparison inconclusive.

**`da-review main`, third run: not fixed (fix-round limit reached).** These are worth recording as known limitations or putting in a follow-up ticket's acceptance criteria:
- **P2 (`verifier_tools.py:1083`):** a slice dimension loses the table it belongs to. Grouping by `customers.year` while counting orders filters on `orders.year` instead.
- **P2 (`verifier_tools.py:917`):** a `COALESCE` or `CAST` around a column that a CTE grouped on is dropped when tracing. `COALESCE(year, 0)` is sliced as `year = 0`.
- **P2 (`verifier_tools.py:1091`):** a positional key such as `ROLLUP(1)` is not recognized as a subtotal key.

**Evidence**
- `make check`: ruff and format clean; basedpyright `0 errors, 0 warnings, 0 notes`; `838 passed, 15 deselected`.
- **Real dbt run** at the final commit `af8d190`: `make test-integration` with full config and seam A's DBT_TASK gave `5 passed, 838 deselected, 10 xfailed`. The run took 22 turns and `cost_usd` 0.0304.
  - The project had a `target/manifest.json`, so the manifest path was exercised.
  - `verify_model_values` output:
    - `Model SQL read: …`
    - `stg_orders: COUNT(CASE WHEN LOWER(TRIM(status)) IN ('complete', 'completed') THEN order_id END): 6 → Model matches …`
    - `stg_customers: uses stg_orders.status, stg_orders.order_id, not in main.stg_customers; no baseline, inconclusive`
  - The two `verifier_end` events (full lines in `runs/16/real4_events.txt`):
    ```
    seq 120 value-verifier: CHECK 1 PASS, CHECK 2 PASS, CHECK 3 PASS
    seq 122 verifier: CHECK 1 PASS, CHECK 2 PASS, CHECK 3 PASS, CHECK 4 PASS, CHECK 5 N/A
    ```
- The direct-call evidence script `runs/16/evidence.py` gives output identical to the previous round's (`diff` is empty).

**主会话**：工单 agent 因自审修复轮次用满停在 `ready-for-human`；剩下 3 条都是更少见的 SQL 形状（切片维度丢所属表、CTE 分组列外的 COALESCE/CAST、`ROLLUP(1)` 位置键），不需要人决定，记为已知限制。推 PR 让 Codex 复审；PR 还剩 1 轮退回。

### 2026-10-16 第 7 步：PR #15 退回第 2 轮（主会话；最后一轮）

`af8d190` CI 两次全绿。Codex 1 条 P1，属实，且是主会话写验收标准时的错误（"±10% 容差只用于非计数的加和/均值"）：

1. **`src/data_agent/agents/value-verifier.md:116`**：验证子 agent 自己用同一切片、同样的过滤重算 sum/avg，结果只应差浮点或小数舍入；±10% 会放过误差 10% 以内的错误结果，也与 `CODING_STANDARDS.md` 去掉评分式容差的规则不符。改为舍入级容差：按模型列的小数位（DECIMAL 的 scale）舍入后相等，浮点列相对误差不超过 1e-6；超出即 MISMATCH。同步改 `verifier_tools.py` 里非计数指标若有相关文字、dbt-workflow Step 8 的说明和 prompt 测试（断言新规则、断言 "10%" 不再出现）。本工单 Acceptance 里那句以此为准。

这是最后一轮；之后的新意见记为已知限制。在原分支上修，修完提交，不 push、不 rebase。修完 `make check`、full 配置真实 dbt 运行一次（看 value-verifier 的 `verifier_end`），再跑一轮 `da-review main`（新意见只记录）。最终回复给出 Comments、Ledger（Attempts 3）和 `Status: in-review`。

### 2026-10-16 Ticket agent, send-back round 2 (commit `64ee041`)

**The send-back item.** Its tests failed before the fix.
- **`agents/value-verifier.md` CHECK 2:** the verifier recomputes the same aggregate over the same slice with the model's filters. If the model's SQL rounds, it applies the same `ROUND`. The model value then has to match the recomputed one:
  - a DECIMAL column must equal it rounded to the column's scale;
  - a float column (DOUBLE, REAL, FLOAT) may differ by a relative error of at most 1e-6;
  - an integer column must match exactly.

  Anything beyond that is a MISMATCH (CHECK 2 = FAIL). Mins and maxes, like counts, still match exactly. The header note is updated to match.
- **`skills/dbt-workflow/SKILL.md`:** the Step 8 fallback (the checks the main agent runs itself when there are no verifier sub-agents) states the same rule. The header note says it too. The Skill avoids the word "tolerance", which ADR-0005's test forbids.
- **`verifier_tools.py`:** unchanged. It never had any ±10% wording, and it still gives no verdict for sums and averages.
- **Tests:**
  - `test_value_verifier_compares_sums_and_averages_only_up_to_rounding` replaces the ±10% test. It checks the new rule's sentences and that "10%" no longer appears anywhere in the prompt.
  - New `test_workflow_compares_sums_and_averages_only_up_to_rounding` checks the Step 8 wording and that "10%" is absent from the Skill.
- The Acceptance line "±10% tolerance only for non-count sums/averages" is replaced by this rule, as the send-back says.

**Evidence**
- `make check`: ruff and format clean; basedpyright `0 errors, 0 warnings, 0 notes`; `839 passed, 15 deselected`.
- **Real dbt run** at `64ee041`: `make test-integration` with full config and seam A's DBT_TASK gave `5 passed, 839 deselected, 10 xfailed`. It took 19 turns and `cost_usd` 0.0280.
  - The value-verifier recomputed `lifetime_value` with the model's own CASE/status filter: `SUM(CASE WHEN lower(trim(status)) IN ('completed','complete') THEN amount END)` = 285.25, against a model value of 285.25, so PASS on an exact match. `completed_orders` = 6 matched the tool's verdict, which comes from the model's SQL.
  - The two `verifier_end` events (full lines in `runs/16/real5_events.txt`):
    ```
    seq 109 verifier: CHECK 1 PASS, CHECK 2 PASS, CHECK 3 PASS, CHECK 4 PASS, CHECK 5 N/A
    seq 111 value-verifier: CHECK 1 PASS, CHECK 2 PASS, CHECK 3 N/A
    ```
  - CHECK 3 is N/A because no rule about excluding returns was given.

**`da-review main` (Codex), recorded only, last round:** no findings on this change. Codex repeated the 3 P2s that are already recorded as known limitations:
- `verifier_tools.py:1083`: a slice dimension loses the table it belongs to.
- `verifier_tools.py:917`: a COALESCE or CAST around a column that a CTE grouped on is dropped when tracing.
- `verifier_tools.py:1091`: a positional key such as `ROLLUP(1)` is not recognised as a subtotal key.

Full output is in `runs/16/da-review-6.txt`.

### 2026-10-16 第 7 步：PR #15 合并（主会话）

`64ee041` CI 两次全绿（PR 的合并检查已含工单 08）；Codex 复审没有针对 `64ee041` 的新意见（未解决线程是已修的旧意见）。合并。已知限制 3 条见上（切片维度丢所属表、CTE 分组列外的 COALESCE/CAST、`ROLLUP(1)` 位置键）。worktree 保留（`runs/16/` 有实测证据），人看过后删。
