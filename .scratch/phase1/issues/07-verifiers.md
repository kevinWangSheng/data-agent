# 07 — 验证子 agent 与 6 个验证工具

Status: in-review
Blocked by: 04, 05
Spec: ../spec.md
Claimed by: claude (worktree 07-verifiers)
Branch: worktree-07-verifiers

## Goal

dbt 任务交付前，结构与数值两个只读验证者并行检查，逐项给通过/失败/需更多信息/不适用；主 agent 复现失败、最小修改、重审；验证者开始结束记成事件；关验证子 agent 时不出现。

## Authority

- May edit: `src/data_agent/`、`skills/`、验证者定义、`tests/`（除 `tests/acceptance/`）、`NOTICE`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $3

## Context

ADR-0002（6 个工具清单与要改的方言写法）；plan §3 验证；上游 signalpilot-plugin 的 `agents/verifier.md`、`agents/value-verifier.md` 与 dbt-workflow 第 8 步。

## Acceptance

- [ ] 接缝 B 中 6 个验证工具的 DuckDB 用例转绿
- [ ] 接缝 A 中基线配置无验证者痕迹的测试转绿
- [ ] `make check` 通过

## Evidence required

一次真实 dbt 任务的事件文件里验证者开始/结束与逐项结论；花费。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 5（Codex 4 + eng:code-review 1）
- Model spend: ~$0.29（之前 ~$0.22 + 两次真实运行 $0.034 + $0.030）
- Outcome: 退回 4 条全修，另修 da-review 4 条；第 2 轮 da-review 4 条 P2 记为已知限制
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `984ced4`…`eea96dd`）

#### What was built

- **Four verifier tools** in `src/data_agent/verifier_tools.py`: `map_columns`, `check_model_schema`, `audit_model_sources`, `verify_model_values`.
  - They are ported from SignalPilot's gateway onto our read-only gateway and served only when the config turns verifiers on.
  - The SQL is written to run on Postgres too (no `SHOW TABLES`, no `FILTER`). The Postgres tests themselves are still waiting on ticket 08.
- **Two verifier subagents** in `src/data_agent/agents/`, copied from signalpilot-plugin and adapted. Every check ends PASS / FAIL / NEEDS_INFO / N/A.
  - They are offered only to dbt runs with verifiers on, and they can only read files and use the data tools.
  - The main agent's prompt tells it to run both in parallel, reproduce any failure first, make the smallest fix, and send only the changed models back. It stops when every check is PASS or N/A.
- **Events:** each verifier writes a `verifier_start` event, and a `verifier_end` event with the parsed verdicts and the full report.
- **NOTICE** has entries for the ported tools and copied prompts. The dbt-workflow Skill's Step 8 now uses the four verdicts.

Two choices go beyond a straight port; both are explained at the top of `verifier_tools.py`:
- `verify_model_values` takes an optional `source_tables`. Upstream compared against the three largest tables. On our test fixture that put a broken sibling model's doubled count next to a correct model, which the acceptance test forbids.
- `mcp-server` has a new optional `--workspace` flag, which `data-agent run` always passes, so `map_columns` can't read files outside the workspace.

#### Real runs turned up three bugs, all fixed with tests

- **Verifiers ran in the background.** That split one run into three SDK results, and the result event kept only the last (num_turns 1, $0.0016). Marking the agent definitions as foreground didn't help; setting `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` for the SDK process fixed it. This setting applies to every config, so it also turns off background Bash for the main agent.
- **Cost left out the verifiers.** The token counts covered the main agent only. Cost now comes from the SDK's per-model totals, which include subagents ($0.015 became about $0.035 per run).
- **Verdict parsing missed bold lines** like `**CHECK 1**: PASS`.

#### Evidence

**Real run** at `eea96dd`, `sh runs/manual07/run.sh full runs/manual07/final` (seam A's DBT_TASK, model settings loaded the way `make test-integration` loads them):
```
exit 0
customer_ltv rows 12 [('C0001', 6, 285.25), ('C0002', 4, 232.50), ('C0003', 2, 98.75)]
{"seq": 55, "type": "verifier_start", "verifier": "verifier", "agent_id": "ae56c775ce6deacf6"}
{"seq": 57, "type": "verifier_start", "verifier": "value-verifier", "agent_id": "ae8045cb2aff45976"}
{"seq": 121, "type": "verifier_end", "verifier": "value-verifier", "checks": [CHECK 1 PASS, CHECK 2 PASS, CHECK 3 PASS]}
{"seq": 123, "type": "verifier_end", "verifier": "verifier", "checks": [CHECK 1-4 PASS, CHECK 5 N/A]}
{"seq": 127, "type": "result", "num_turns": 21, "uncached_input_tokens": 45169, "cached_input_tokens": 320640, "output_tokens": 15323, "cost_usd": 0.0339, "sdk_cost_usd": 0.769}
```
One SDK turn, no REDACTED strings in the event file.

**Seam A** (`runs/manual07/seam-a.sh`, at `984ced4`): `test_baseline_leaves_no_verifier_or_context_trace` and `test_dbt_model_built_with_correct_values` → `2 passed in 135.21s`. This was before the later fixes and was not re-run at `eea96dd`; the later full-config runs passed.

**Codex review** (`da-review main`), two rounds:
- Round 1 (1 P1, 5 P2), all fixed in `a47e0ae`:
  - `map_columns` could read outside the workspace.
  - `verify_model_values` sliced on only the first dimension.
  - Its joins to reach the slice could go through unrelated tables.
  - Sampled column stats were divided by the full row count.
  - The value-verifier prompt forbade the sum baselines it needs.
  - The YAML column parser assumed fixed indentation.
- Round 2 (3 P2), all fixed in `f6e511b`:
  - Slices spanning several tables were partly dropped; they are now joined or marked inconclusive.
  - The model's own filters aren't applied; the tool output now says so, and the value-verifier re-counts with them.
  - YAML lists starting at column 0 weren't parsed.

**eng:code-review main:**
- *Standards*: no hard breaches.
  - Fixed in `eea96dd`:
    - `verifier.md` used `standings`/`results`, which look like one benchmark's table names; it now uses generic names.
    - A stale tool description.
    - Two small tidy-ups.
  - Not fixed:
    - Splitting the 715-line `verifier_tools.py`, and turning the tuple-heavy code into small dataclasses: judgement calls, and fix rounds were used up.
    - Typing the event callback strictly: it matches the existing `EventLog.emit`.
    - The fallback to main-thread token counts: kept for SDK results that carry no per-model totals.
- *Spec*:
  - Only 4 of the 6 tools switch with the config. `list_tables` and `query_database` are base tools, as the acceptance tests require; the spec's wording differs.
  - The background-tasks setting, the cost change and `--workspace` are beyond the ticket's goal, but each fixes a real defect found while doing it.
  - It asked whether the workspace guard also covers the verifiers' file reads. It does: the guard hooks apply to the whole session, and the SDK documents subagent tool calls going through them.
  - Not fixed: if a report drifts from the format, the event gets an empty verdict list with no warning (the full report is still stored).

**Known limits:**
- The model label in parsed verdicts is the report's heading text as written; in the last run it included the file path.
- Postgres variants of the verifier tests stay xfail for ticket 08.

### 2026-10-16 第 7 步：PR #12 退回第 1 轮（主会话）

CI 两次全绿。退回：原因如下，在原分支 `worktree-07-verifiers` 上修，修完提交，不 push、不 rebase（需要同步 main 时用 `git merge origin/main`）。四条都在 `src/data_agent/verifier_tools.py`，主会话已对照代码核实；各配一个修复前会失败的测试。

1. **Codex P1（`verify_values`，约 :555）**：只按数值/非数值区分维度和指标，`customer_id`（整数）、`count(*) AS n_orders` 这类只按数值键分组的模型，所有列都被当成指标，`slice_values` 为空，直接跳过、不做任何值核对。把 `id`、`*_id` 这类标识/粒度列当切片维度，不当指标。
2. **Codex P1（同处）**：顶行某个文本/日期维度为 NULL 时被丢掉，切片变宽，可能误报不一致；唯一维度为 NULL 时整个跳过。保留 NULL 维度，生成 `IS NULL` 谓词。
3. **Codex P2（`model_sql`，约 :169）**：全仓库按文件名找模型，`analyses/customer_orders.sql` 可能被当成 `models/customer_orders.sql`。只在 `dbt_project.yml` 的 `model-paths`（缺省 `models`）下找（工单 04 的 `scan_project.py` 已有同样的规则，可参照）。
4. **Codex P2（grain key，约 :442）**：只在前 5 个扫描列里找唯一列，唯一键在第 6 列以后就误报"composite key or fan-out"。在全部已扫描列里找。

修完 `make check`、full 配置真实 dbt 运行一次（看 verifier 事件），再跑一轮 `da-review main`；最终回复给出要追加的 Comments 与 Ledger（Attempts 2）。

### 2026-10-16 PR #12 return round 1 handled (commits `8577810`, `733319e`)

**The four returned items** (`8577810`), each with a test that failed before the fix:
1. **Integer keys:** `id` and `*_id` columns now slice the comparison instead of counting as metrics, so a model grouped only by an integer `customer_id` is checked (`test_verify_model_values_slices_on_an_integer_key`). A numeric slice value is written unquoted.
2. **NULL dimensions:** a NULL dimension stays in the slice as `IS NULL`. That also covers a model whose only dimension is NULL (`test_verify_model_values_keeps_a_null_dimension_in_the_slice`).
3. **`model_sql`:** it now looks only under the project's `model-paths`. It falls back to `source-paths`, then to `models`, following the same rule as `scan_project.py`. An analysis with the same name is no longer taken for the model (`test_model_sql_looks_only_under_the_model_paths`).
4. **Grain key:** it is now searched across all scanned columns, not just the first 5 (`test_audit_finds_a_grain_key_past_the_fifth_column`).

**`da-review main` round 1** (on `8577810`) found 4 P2s. All are fixed with tests in `733319e`:
- **Joins:** tables are joined only on a shared `*_id` column that is unique in the table joined to. A shared bare `id` no longer links two tables.
- **Timestamp/time/boolean/UUID columns** are now part of the slice. A grouping column the slice can't match, such as a BLOB, marks the candidate inconclusive.
- **Scalar aggregates:** a one-row model with no dimension columns, given `source_tables`, is compared against each whole upstream table.
- **Source schema:** a source's `schema:` is read even when it comes after `tables:`.

**`da-review main` round 2** (on `733319e`) found 4 P2s. None are fixed; all predate this return:
- **A numeric grouping column (e.g. `year INT`) counts as a metric.** Telling a numeric dimension from a metric needs the model's GROUP BY, which the tool doesn't parse. The tool output already says the baselines skip the model's filters, and the value-verifier re-counts.
- **An integer `SUM` is checked as a row count.** The same limit: telling the two apart needs the SQL. The MISMATCH line is something the value-verifier re-checks, not a final verdict.
- **The ±10% match tolerance hides small count errors.** This is the original port's threshold (whether upstream uses it too is 未确认). Exact equality is a small change but changes what "match" means; left for the main session to decide.
- **`agents/verifier.md` lines 62–66 call a fan-out PASS when the lookup rows' labels differ.** This text was copied from the signalpilot-plugin prompt and is a change to verifier judgement, beyond this return.

**`make check`:** `743 passed, 15 deselected` (`runs/retry1/check2.txt`).

**Real runs** with `sh runs/manual07/run.sh full <dir>`:
- **At `8577810`** (`runs/retry1/real`): exit 0. `verifier_start`/`verifier_end` for both verifiers. verifier: CHECK 1–4 PASS, CHECK 5 N/A. value-verifier: CHECK 1–2 PASS, CHECK 3 N/A. `num_turns` 22, `cost_usd` 0.0340, no REDACTED strings in the event file. `verify_model_values` sliced on `customer_id = 'C0001'`.
- **At `733319e`** (`runs/retry1/real2`): exit 0. `customer_ltv rows 12 [('C0001', 6, 285.25), ('C0002', 4, 232.50), ('C0003', 2, 98.75)]`. verifier: CHECK 1–4 PASS. value-verifier: CHECK 1–3 PASS. `num_turns` 22, `cost_usd` 0.0303, no REDACTED strings.

**Not run this round:** `eng:code-review main` (the return note asked only for `da-review`).

**主会话**：第 2 轮 da-review 的 4 条 P2 都早于本轮，记为已知限制。数值分组列与整数 SUM 的区分要解析模型 SQL，属于启发式工具的边界；±10% 容差与 `agents/verifier.md` 的 fan-out 判定改的是验证口径，留给失败归因（工单 14）出数据后再定。
