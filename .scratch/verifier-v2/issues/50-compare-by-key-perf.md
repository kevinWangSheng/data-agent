# 50 — compare_by_key 的两个根因：参考 SQL 的执行计划、每次调用重复扫项目

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (main session, 2026-11-05)
Branch: worktree-50-compare-by-key-perf

## Goal

compare_by_key 一次调用只执行一遍参考 SQL 和模型，而且参考 SQL 不被嵌进会让优化器改坏它的语句；一次调用只遍历一遍 dbt 项目，和已改模型数无关。工具接口、输出格式和比较语义不变。

## 已和用户定下的（2026-11-05）

- 主会话直接修（取证和原型都在主会话里），审查和验收按第 6 节流程做，审查用新上下文。
- 不做 27 处调用统一用项目索引的重构；`grain_facts` 服务器上中位数 92 s 的原因没有查清，等新 main 的数据再定。

## 取证（主会话，2026-11-05，本地真实 `retail.duckdb`，sha256 和 LFS 指针一致，2 线程）

- **根因 B（cross-sell 超时）**：t47-screen 中超时的参考 SQL 单独执行 0.5 s；嵌进现有语句后，哨兵语句 28.9 s，计数语句 300 s 仍超时（还溢写了几 GB 临时文件）。不是重复执行 4–5 遍，是嵌进去以后优化器选了极差的执行计划。原型（模型和参考各用 `AS MATERIALIZED` 物化，单条主语句）整次调用 2.4 s，结果正确。
- **根因 A（pos-operations、web-session 慢）**：pos-operations 工作区（2377 个模型文件，21 个已改模型），一次调用 31.6 s，其中 SQL 只占 0.5 s，`_forbidden` 占 30.3 s：41 次 `workspace_alias`，每次都完整遍历项目（`files_under` 对每个文件 `resolve()`，共 19.6 万次）并重新解析 YAML。两次调用并行时抢 GIL，每次 58 s。
- 哨兵只需要从模型取：模型的键全是 NULL 时，只用 NULL 标志配对就是精确的。

## Authority

- May edit: `src/data_agent/verifier_v2/compare.py`、`src/data_agent/verifier_v2/db.py`、`src/data_agent/verifier_v2/project.py`（`files_under`；新增 `model_aliases` 取代 `model_alias`，2026-11-05 审查后补）、`src/data_agent/verifier_tools.py`（只改 compare_by_key 包装）、`tests/test_verifier_v2_tools.py`
- Must not edit: `tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Acceptance

- [x] 现有 compare_by_key 测试（DuckDB 和 Postgres）不改一字全部通过。
- [x] 新测试：一次调用里，含参考 SQL 的语句只有 1 条。
- [x] 新测试：一次调用遍历项目的次数和已改模型数无关。
- [x] `make check` 通过。
- [x] 本地真实样例复测（pos-operations 工作区、cross-sell 超时的那条参考 SQL）：每次调用 ≤5 s，结果和现有实现一致（现有实现能跑完时）。

## Stop conditions

- 必须改比较语义或者现有测试才能做下去。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2（eng:code-review 新上下文；da-review 和 Codex app 都是 at capacity / unknown error，由 fable 只读审查代替）
- Model spend: $0
- Outcome: accepted，PR #49 squash 合入 595ebc5
- Human review minutes: —

## Comments

### 2026-11-05 主会话

**改动**（82f8d87、9263211）
- `compare.py`：比较改成一条语句，模型和参考各是一个 `AS MATERIALIZED` CTE；三个计数、取整损失检查、各类样例都在这一条里算。哨兵只从模型取（`_model_has_key_values`）。键在比较结果里按位置命名（`__k_<i>`）。`_forbidden` 读一次工作区，拿到全部已改模型的别名。
- `db.py`：新增 `workspace_aliases`；`find_model` 可以接收调用方预先算好的别名。
- `project.py`：`files_under` 只对符号链接文件做 resolve；`model_aliases` 一次读完项目文件和 YML，取代 `model_alias`。
- 测试：参考 SQL 只在 1 条执行的语句里出现；项目遍历次数和已改模型数无关；`__kind` 这样的键名不和内部列混淆。

**验证**
- 修复前两个新测试失败：参考 SQL 出现在 5 条语句里；遍历次数 12 对 72。
- `make check`：1698 passed，1 skipped，1 xfailed（工单 47 的配置测试，本来就在），ruff 和 basedpyright 都是 0 错误。修复轮之后 v2 测试 89 passed。
- Postgres 集成测试：本地临时 PG16 上 2 passed（两轮都跑过）。
- 真实样例：本地 `retail.duckdb`，pos-operations 工作区，21 个已改模型，`scratchpad/measure50.py`。

| 场景 | 修复前 | 修复后 |
|---|---|---|
| pos 一次完整调用（含 model_diff） | 20.9 s | 1.8 s |
| 两次并行 | 每次 58 s | 每次 1.4 s |
| cross-sell 超时的那条参考 SQL | 超过 300 s | 1.6 s |
| web-session | — | 0.9 s |

  结果都是 0/0/0，和修复前一致（cross-sell 修复前跑不完）。

**审查（eng:code-review，新上下文，第 1 轮）**
- 已修：
  - `model_aliases` 越出 Authority：这是 Authority 写漏了，已补上。
  - `model_alias` 成了死代码，并且规则写了两份：已删除，测试改用 `model_aliases`。
  - 键名和内部列冲突（`__kind`）：这是回归，已修并补测试。
- 不修：
  - 取整损失检查现在要等整条语句跑完才报错。语句跑完时报错内容和 main 一致。要提前检查，参考 SQL 就得再执行一遍，正是本工单要去掉的。
  - 模型被读两次：探测只是对模型的 `LIMIT 1`。要并进主语句，得为任意类型造一个非空常量当哨兵，更脆弱。
- 没发现问题：计数和样例、NULL 键、重复键、Postgres 兼容、符号链接、别名优先级都和 main 一致（审查用 main 对照分支实际跑过）。

**审查第 2 轮（fable，代替 Codex）**
- da-review 报 "model is at capacity" 中断；PR 上 Codex app 两次失败（第二次是 "An unknown error occurred"）。
- fable 只读审查：main 和分支差分对照，DuckDB、Postgres 各 29 个用例，计数、样例集合、报错都一致，没有正确性问题。
- 一条性能小意见：没有已改模型时仍会遍历一次工作区。已修（37eb9f6）。
- 已知且和 main 相同、不在本工单范围：比较列名叫 `present` 时，`__m_present` / `__r_present` 会重名（Postgres 报 ambiguous）。

**合并**：PR #49，CI 绿，squash 合入 595ebc5。下一步：在新 main 上重跑工单 47 的初筛。
