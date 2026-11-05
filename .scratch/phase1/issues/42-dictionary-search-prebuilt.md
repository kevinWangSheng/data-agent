# 42 — dictionary_search 改成预建采样字典：搜索不再现扫全库

Status: done
Blocked by: 39
Spec: ../spec.md
Claimed by: claude (fable-5.1)
Branch: worktree-42-dictionary-search-prebuilt

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

- Attempts: 1
- Review rounds: 2
- Model spend: 本地真实运行 $0.0041 + 初筛实际价 $1.1954（cost_usd $2.3907）
- Outcome: done（PR #43）；初筛 11/12，无挂住、无锁冲突，建字典 13–18 s
- Human review minutes: —

## Comments

### 2026-11-04 工单会话（worktree 42-dictionary-search-prebuilt，commits e2c9710、0ed8073、eba56ef）

改动：
- `gateway.py`: a `Session` protocol (one read-only connection, `query` / `interrupt` / `close`) with DuckDB and Postgres implementations. Every session statement still passes `check()` and the per-statement timeout. The per-statement helpers were extracted so the session shares them instead of copying them.
- `context_tools.py`: ktx's sampling constants (registered in `NOTICE`), `DictionaryBuild` (base tables sorted by schema and name, one `LIMIT 10000` statement per table, 60 s total with a timer that interrupts mid-statement, 10 s per statement, cancel from any thread), `Dictionary` with JSON save/load and `search()` that appends coverage and ktx-style miss reasons. `dictionary_search` reads only the dictionary.
- `agent.py`: the build runs before the SDK `query()`, writes `dictionary.json` to a temp dir outside the workspace, emits a `dictionary` event with the coverage, and passes `--dictionary` to the MCP server. SIGINT and SIGTERM cancel the build and end the run.
- `cli.py` / `mcp_server.py`: `--dictionary` flag; a server started without it and with the context layer on builds at startup. baseline and no-context build nothing.

**Step 3, real behaviour (model-free; the model run is left to the main session, see below).** `uv run python runs/t42-real/demo.py runs/t42-real/shop.duckdb` on the shop fixture:

```
build wall time: 0.036 s
coverage: tables_total=9, tables_profiled=9, columns_profiled=34, sample_rows=10000, timed_out=False, not_profiled=[], columns_excluded=14, columns_high_cardinality=0

$ dictionary_search('c0001')  [24 ms round trip, via the real stdio server with --dictionary]
raw.orders.customer_ref: 'c0001' (3), ' C0001' (2), 'C0001' (2)
Coverage: 9 of 9 base tables sampled (up to 10000 rows each), 34 columns kept, 14 skipped by name (ID-like), 0 over 200 distinct values; built in 0.0 s

$ dictionary_search('zebra')  [2 ms]
No sampled value contains 'zebra' (value_not_in_sample). The dictionary holds the 100 most frequent values of each categorical column in the first 10000 rows of a table, and never ID-like columns (_id, _key, _uuid, email, …): confirm with query_database on the column you expect, e.g. SELECT DISTINCT col FROM t WHERE lower(col) LIKE '%…%'.
Coverage: 9 of 9 base tables sampled ...
```

**Unit tests:** `tests/test_dictionary.py`, 26 tests, all passing: build rules, high cardinality, 10,000-row sample, time limit (fake clock, mid-statement, during connect, during catalog read), failed connection, file released to another process (subprocess), cancel within 2 s with connection closed, SIGINT/SIGTERM cancel, hits, miss reasons, search after renaming the db file, bounds, JSON round trip, `--dictionary` file is what the server searches, standalone server builds at startup, `run` emits the `dictionary` event and passes the path, baseline/no-context build nothing. `make check`: ruff, format, basedpyright 0 errors, 1476 passed. `tests/acceptance/`: 39 passed, 15 deselected (Postgres, no server here).

**Model run not done here:** the classifier denied `. ~/.config/data-agent/env` in this worktree session. Command for the main session (spend well under the $0.3 cap):

```
set -a && . ~/.config/data-agent/env && set +a && uv run data-agent run --task "How many orders in raw.orders belong to customer C0001 across its dirty customer_ref spellings? Call dictionary_search with keyword 'c0001' first, then count with one SQL query. Answer with the number." --type query --workspace runs/t42-real/workspace --duckdb runs/t42-real/shop.duckdb --config full --events-dir runs/t42-real/events
```

The `dictionary` event in the event file carries `elapsed_seconds`, `tables_profiled`, `tables_total`, `columns_profiled`, `timed_out`, `tables_not_profiled`, `columns_excluded`, `columns_high_cardinality`, `error`.

**Review round 1 (`da-review main`, Codex):** P1 deadline could overshoot by one statement: fixed, a timer now interrupts the session at the limit (commit 0ed8073). P2 skipped columns and reasons not reported: fixed, coverage counts columns skipped by name and by cardinality. P1 Postgres enum columns show as `USER-DEFINED` and are not sampled: not fixed. ktx reads `format_type()`, which names an enum by its type name, so ktx's own regex misses them too, and the catalog query is in `db_facts.py`, outside Authority. Known limitation.

**Review round 2 (`eng:code-review main`):** Spec: connect time not under the timer: fixed (eba56ef). `value_not_in_sample` printed when zero tables were sampled: fixed. Standards: `PostgresSession.query` copied `PostgresGateway._run`: fixed with a shared helper, same shape as the DuckDB side. `Sampled` renamed `TableSample`. Not fixed: `literal` re-export in `context_tools.py` (removing it needs a one-line import change in `verifier_tools.py`, outside Authority; suggest doing it in a later ticket); `context_tools.py` is now 560 lines (a `dictionary.py` would fix it, but the file name is outside Authority); `build_server(dictionary=...)` parameter (it is the wiring); `clock` parameter (the test seam); `dialect` class attribute on sessions (mirrors the gateway classes); the gateway helper extraction goes past "只加会话接口" but leaves behaviour unchanged and avoids duplicating the statement path. The type regex is ktx's verbatim, so the NOTICE wording stands.
- 2026-11-04 主会话：本地真实运行（worktree 42，`data-agent run --config full --duckdb runs/t42-real/shop.duckdb`，题目：数 C0001 各种脏拼写的订单数，先调 dictionary_search）：事件文件 `runs/t42-real/events/run-20261104T220302Z-4a4bc3a3.jsonl` 的 `dictionary` 事件：`tables_total=9, tables_profiled=9, columns_profiled=34, sample_rows=10000, elapsed_seconds=0.187, timed_out=false, columns_excluded=14, columns_high_cardinality=0`。`dictionary_search('c0001')` 返回 `raw.orders.customer_ref: 'c0001' (3), ' C0001' (2), 'C0001' (2)` 加覆盖范围一行（"9 of 9 base tables sampled ... built in 0.2 s"）。agent 另用 DISTINCT 查询找到字典没收的第 4 种拼写 `C-0001 `（字典只存前 100 个最常见值、按包含匹配，`C-0001` 不含 `c0001`，符合设计），答 9；4 轮，cost_usd $0.0041。PR #43 已开；初筛排在 `t41-screen` 之后。
- 2026-11-04 主会话：初筛 `t42-screen`（commit eba56ef；`ablate --split dev --config full --task dbt-cart-abandonment-recovery --task dbt-customer-cross-sell-insights --task dbt-multi-warehouse-stock-rebalance --task dbt-receivables-aging-buckets --trials 3 --budget 8`）：成功 **11/12**（cart 3/3、cross-sell 2/3、stock-rebalance 3/3、receivables 3/3；accept-2 full 7/12），过线。12 个 trial 里最长单次工具调用 288.7 s（都是 `Agent` 验证者），没有 1800 s 挂住；"Conflicting lock" 0 次。每个 trial 的 `dictionary` 事件：建字典 13.2–17.7 s，803/803 张基表，3,162 列，`timed_out=false`，无错误。连冒烟实际价 $1.1954（cost_usd $2.3907，13 个事件文件全在闲时）。观察：12 个 trial 里 `dictionary_search` 只被调用 1 次，工具本身不再是风险，但模型几乎不用它。PR #43 CI 绿、Codex app 无意见，squash 合入。
