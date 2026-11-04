# 42 — dictionary_search 改成预建采样字典：搜索不再现扫全库

Status: ready-for-agent
Blocked by: 39
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

开了上下文层的配置，在 agent 开跑前用有时限的采样建好值字典，`dictionary_search` 只查这份字典，几毫秒内返回，并说明覆盖范围和未命中原因。搜索不再连库，建字典时也不会一直占着库文件。

## 已和用户定下的（2026-11-04，ADR-0003 Update 2026-11-04）

- 类别：**改变行为的工单**（ADR-0008 Update 2026-11-04），要跑初筛。
- **什么时候建**：在 `data-agent run` 里、SDK 的 `query(...)` 开始之前建，把字典写成 JSON 文件，放在本次运行的临时目录里（不放 workspace），再把路径作为参数传给 MCP 服务。服务启动时只读这个文件。
  - 建字典的时间算进 Harbor 的 agent 超时，最多 60 秒；但不算进 agent 的第一次工具调用。
  - 不在 MCP 服务启动时建，因为会拖慢 MCP 的 initialize。
  - `data-agent mcp-server` 单独运行、又没有传字典文件时，在启动时建一次，规则相同。
  - 只有上下文层打开时才建（full、no-verifiers）；baseline 和 no-context 不建，也没有这个工具。
- **建字典的规则**（ktx 默认值，`packages/cli/src/context/scan/data-dictionary.ts:9-34`，ktx@49a4ae6）：
  - 只扫基础表，不扫视图。表按 (schema, name) 排序后依次处理。
  - 只收类别型列：类型匹配 ktx 的 `categoricalCandidateTypes`（varchar、char、text、string、enum、bool）；列名命中 ktx 的 21 条 `excludePatterns` 的不收（`_id$`、`_uuid$`、`_key$`、`_at$`、`_date$`、`email$` 等，照抄）。
  - 每张表一条语句：`SELECT <候选列> FROM <表> LIMIT 10000`，取前 10000 行作为样本。不用 `USING SAMPLE` 或 `TABLESAMPLE`，因为它们可能扫全表。
  - 样本里不同值超过 200 个的列跳过；每列最多存 100 个值，按出现次数从多到少。
- **时限与连接**：
  - 总时限 60 秒，单条语句 10 秒。时限用完就停，已经建好的部分照常使用，没扫到的表记进覆盖范围。
  - 整个建字典过程只用**一个**只读连接，建完立即关闭。网关加一个会话接口：开一个只读连接，每条语句照旧过网关的只读检查和单条超时，结束时关闭。所有 SQL 照旧走网关。
  - 收到取消（KeyboardInterrupt / SIGTERM，或者测试里的取消事件）时，打断正在跑的语句（DuckDB 用 `con.interrupt()`，Postgres 取消查询），关闭连接，再退出。
- **搜索**：
  - 工具签名不变：`dictionary_search(keyword)`。
  - 在字典里做不区分大小写的子串匹配，返回 `schema.table.column: 'value'` 形式的命中。每列最多显示 20 个值，最多显示 50 列，这两个上限沿用现在的。
  - 每次返回都带覆盖范围：扫了几张表 / 共几张基础表、收了几列、采样行数上限、建字典用时、是否因时限中断。
  - 没命中时写明原因，照 ktx `misses`：值不在样本里（`value_not_in_sample`），或者表没扫到（`not_profiled`）；并提示用 `query_database` 在具体的列上确认。
  - 搜索不连库。
- **不做**：嵌入向量、embedding，以及用 `pg_stats` 的高频值，后者 ktx 有，这次不做。

## Authority

- May edit：`src/data_agent/context_tools.py`、`src/data_agent/gateway.py`（只加只读会话接口）、`src/data_agent/agent.py`、`src/data_agent/cli.py`、`src/data_agent/mcp_server.py`（只改 `register_context_tools` 的接线）、`NOTICE`（如果照抄了 ktx 的常量）、`tests/`（`tests/acceptance/` 除外）
- Must not edit：`skills/`、`src/data_agent/configs/`、`src/data_agent/verifier_tools.py`、`evals/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none（初筛和真实运行由主会话做）
- Spend cap：$0.3（单题真实运行）；初筛由主会话跑

## Context

- 根因（ADR-0003 Update 2026-11-04；`runs/accept-2/reading/design-audit/audit-db-tools.md` 第 1 条，`audit-harness.md` 第 1 条）：
  - 现在的实现是调用时现扫全库（`context_tools.py:219-236`），每条语句 150 秒超时，但整次调用没有总时限。
  - 工具是同步函数，mcp 2.3.0 用 `anyio.to_thread.run_sync` 跑，`abandon_on_cancel` 默认是 False，所以取消打断不了正在跑的工具。
  - Claude Code 在 1800 秒放弃后，服务端仍在读库，后面的 `dbt run` 报 "Conflicting lock"。
  - 12 次调用里 6 次挂住，都是搜 ID 类值。
- ktx 的搜索响应结构：`packages/cli/src/context/sl/dictionary-search.ts`（`searched`、`results`、`misses`）。ktx 是 Apache-2.0，照抄常量就登记 `NOTICE`。
- 依赖工单 39 的表目录缓存来拿基础表和列的列表。

## Acceptance

- [ ] 单元测试（DuckDB 夹具）：
  - [ ] 建字典：只收类别型列，跳过 `_id`、`_at` 等列名，跳过不同值超过 200 的列，每列最多 100 个值，跳过视图。
  - [ ] 搜索命中：返回 `schema.table.column: 'value'`，带覆盖范围。
  - [ ] 未命中：分别给出 `value_not_in_sample` 和 `not_profiled` 两种原因。
  - [ ] 时限：把总时限调小（参数化），建到一半停下，覆盖范围写明没扫到的表数和"因时限中断"，搜索照常可用。
  - [ ] **不占着库文件**：建完字典后，另一个**进程**能以读写模式打开同一个 DuckDB 文件并建表（用 subprocess）。
  - [ ] **可取消**：建字典进行中（用一张慢表，或者 sleep 注入）触发取消，2 秒内退出，连接已关闭；之后另一个进程能以读写模式打开库。
  - [ ] 搜索不连库：建好字典后把库文件改名，搜索照样返回结果。
  - [ ] baseline、no-context 配置不建字典，也没有这个工具。
- [ ] `tests/acceptance/` 原样全部通过。
- [ ] `make check` 通过。
- [ ] 本地真实运行：对 DuckDB 夹具跑 `data-agent run --config full` 的一道小题，贴出建字典用时、覆盖范围和一次搜索的输出。
- [ ] **初筛**（主会话，full，每题 3 次，DeepSeek 闲时）：
  - 目标题：dbt-cart-abandonment-recovery、dbt-customer-cross-sell-insights、dbt-multi-warehouse-stock-rebalance、dbt-receivables-aging-buckets。accept-2 里这 4 道题都挂住过；full 是 7/12。
  - 过关：成功次数 ≥8/12（严格高于 7）；12 个 trial 里没有 1800 秒挂住，也没有 "Conflicting lock"；每个 trial 记下建字典用时和覆盖范围。

## Evidence required

单元测试输出；`make check` 输出；本地真实运行的建字典用时、覆盖范围和搜索输出。

## Stop conditions

- 只有绕过只读网关才能做单连接的建字典会话。
- 在 2400 个模型的库上，60 秒内连 10% 的基础表都扫不完（主会话在真实运行里量出来）：停下，交人决定是否收窄到 dbt 的源表 schema。
- 取消做不到"2 秒内退出并释放连接"。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
