# 39 — 表目录缓存：元数据工具不再每次重读整个 information_schema

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

MCP 服务进程里缓存表目录（`load_catalog` 的结果），库没变就直接用缓存，库变了自动刷新。`describe_table`、上下文工具、验证工具都用这份缓存；缓存本身不占着库连接。

## 已和用户定下的（2026-11-04）

- 类别：**确定性修复**（ADR-0008 Update 2026-11-04）。验证方式是单元测试，加一次单题真实运行；不做初筛。
- 照 SignalPilot 做进程内缓存（`signalpilot/gateway/gateway/connectors/schema_cache.py:8,134`，TTL 300 秒）。失效规则这样定，实现时不要改：
  - **DuckDB**：缓存键是库文件和同名 `.wal` 文件的 `(mtime_ns, size)`；任何一个变了就重读。`dbt run` 改了库，下次调用自然拿到新表。不用 TTL。
  - **Postgres**：TTL 300 秒，照 SignalPilot。
- **不占着库文件**：缓存只存读出来的结果，读完立即关闭连接（DuckDB 网关现在每条语句都会开关连接，`gateway.py:76-103`，保持这个行为）。
- 只缓存表目录（表、列、类型），不缓存查询结果、行数估计和索引。

## Authority

- May edit：`src/data_agent/db_facts.py`、`src/data_agent/context_tools.py`、`src/data_agent/mcp_server.py`、`src/data_agent/gateway.py`（只能加暴露缓存键需要的东西）、`src/data_agent/verifier_tools.py`（只能把 `load_catalog(run)` 的调用换成带缓存的版本，别的不动）、`tests/`（`tests/acceptance/` 除外）
- Must not edit：`skills/`、`src/data_agent/configs/`、`evals/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none（服务器上的真实运行由主会话做）
- Spend cap：$0.3（单题真实运行）

## Context

- 现象（accept-2，`runs/accept-2/reading/design-audit/audit-db-tools.md` 第 3 条）：`describe_table` 查一次元数据，中位数 14 秒、最慢 81 秒；验证工具累计约 49 个工具小时，单次调用中位数 27–85 秒。
- 根因：`load_catalog`（`src/data_agent/db_facts.py:160-173`）每次都分页读完整个 `information_schema`。约 2400 个模型的库要读好几页，每页都新开一个 DuckDB 连接。调用方：`db_facts.py:250`、`context_tools.py:191,207,230`，以及 `verifier_tools.py:539,606,1186,1409,1450,1472`。
- 上游：SignalPilot 把 schema 放进 `schema_cache`，TTL 300 秒，`invalidate()` 在 `:225`。复制代码要保留版权头并登记 `NOTICE`；只照思路写的不用登记。
- 后续：工单 41（输出上限）和 42（`dictionary_search`）都依赖这张工单的缓存。

## Acceptance

- [ ] 单元测试（DuckDB 夹具）：
  - [ ] 连续两次调用 `describe_table`，第二次不再查 `information_schema`。用计数的 `run` 包装或 mock 来验证。
  - [ ] 另一个连接往库里新建一张表之后，下一次调用能看到这张表（文件变了，缓存失效）。
  - [ ] **不占着库文件**：读完表目录、缓存建好之后，另一个**进程**能以读写模式打开同一个 DuckDB 文件，并成功建表。用 subprocess 来测，不能用同一进程里的连接。
  - [ ] Postgres（照 `tests/test_postgres.py` 标 integration）：300 秒内不重读；可以把 TTL 参数化，在测试里调小。
  - [ ] 验证工具（`verifier_tools.py`）和上下文工具走同一份缓存。
- [ ] `tests/acceptance/` 原样全部通过。
- [ ] `make check` 通过。
- [ ] 本地真实运行：用 `tests/fixtures` 里的 DuckDB 夹具起 `data-agent mcp-server --config full`，连续调两次 `describe_table`，贴出两次的耗时。
- [ ] （主会话）服务器真实运行：dbt-customer-cross-sell-insights × 1（full），贴出 `describe_table` 和验证工具的单次耗时，对照 accept-2 的中位数 14 秒。

## Evidence required

单元测试输出；本地两次调用的耗时；`make check` 输出。

## Stop conditions

- 要在服务进程里一直开着 DuckDB 连接才能做缓存。
- 失效规则需要偏离上面定下的做法（例如发现 dbt 写库后 mtime 不变）：停下，贴出证据。
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
