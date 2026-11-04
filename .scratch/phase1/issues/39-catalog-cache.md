# 39 — 表目录缓存：元数据工具不再每次重读整个 information_schema

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 39-catalog-cache)
Branch: worktree-39-catalog-cache

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only; no model call in the ticket session)
- Outcome: done（PR #39）；服务器 cross-sell full 1/1，describe_table 8.45 s、验证工具 5–8 s
- Human review minutes: —

## Comments

- 2026-11-04 工单会话（worktree 39-catalog-cache，commits 7eff120、31a5fbf）。照 SignalPilot schema_cache 的思路写，未复制代码，不登记 NOTICE。
  - 改动：`gateway.py` 加 `Gateway.catalog_key()`，DuckDB 返回库文件和 `.wal` 的 `(mtime_ns, size)`，Postgres 返回 `None`；`db_facts.py` 加 `CatalogCache(run, key, ttl_seconds=300, clock)`，有键的网关只在键变时重读，无键的按 TTL，键在读之前、同一把锁下取，只存 `list[Table]`，`describe` 多一个可选的 `catalog`；`mcp_server.py` 每个服务进程一份缓存，交给 `describe_table`、上下文工具和验证工具；`verifier_tools.py` 的 `map_model`/`audit`/`verify_values`/`register_verifier_tools` 改收一个 `load` 可调用对象。
  - 单元测试 `uv run pytest tests/test_catalog_cache.py -q` → 5 passed in 2.15s（两次 describe_table 只读 1 次目录；另一连接建表后下次可见；子进程读写打开并建表成功；无键时 TTL 299 s 不重读、300 s 重读；hydrate + check_model_schema + discover + describe_table 合计 1 次目录读）。
  - `make check` → 1450 passed, 1 skipped, 37 deselected in 421.87s，ruff/format/basedpyright 全过。`tests/acceptance` 原样 39 passed, 15 deselected。
  - 本地真实运行（stdio 起 `data-agent mcp-server --config full --duckdb <shop 夹具>`，连续两次 describe_table）：`describe_table(raw.orders): 131.8 ms`，`describe_table(raw.customers): 57.9 ms`；第二次剩下的耗时是单表 facts 查询（COLUMNS_SQL、keys、indexes），不是目录读。
  - 失效键实测：另一连接建表后 `catalog_key` 的 mtime_ns 变、size 不变，键有效。
  - 工单未列出的调用方：`evals/dab_server.py:414` 也调 `describe`，而 `evals/` 不在 Authority 内，所以 `describe` 的 `catalog` 参数可选，没有缓存时按原来方式重读。verifier_tools 三个函数原本只收 `run`，加 `load` 参数是换成带缓存版本的最小改动。
  - 审查 1（`da-review main`，Codex）：无发现。审查 2（`eng:code-review main`）：无 hard violation、无阻塞缺陷。已修：`LoadCatalog` 改在 `Table` 之后定义；verifier_tools 直接从 db_facts 导入 `LoadCatalog`。不修：verifier_tools 里参数叫 `load` 而不是 `catalog`（该模块里 `catalog` 已是表列表的名字）；`(run, load)` 成对传递的 data clump，超出 Authority；`describe` 的可选参数，待 `evals/` 可改时改成必填（建议记到工单 41 或 42）。
  - 验收项"第二次不再查 information_schema"按 Goal 理解为不再读整个目录；describe_table 第二次仍会为单张表跑一次 `information_schema.columns` 过滤查询，不是本工单要缓存的对象。
  - 工单会话未能跑 Postgres 集成测试（source env 文件被权限拦截）。
- 2026-11-04 主会话：Postgres 集成测试用临时 `postgres:17` 容器（127.0.0.1:15439，跑完删除）：`uv run pytest tests/test_db_facts.py tests/test_postgres.py -q -m integration` → 21 passed, 39 deselected in 5.94s，含 `test_postgres_catalog_is_kept_for_the_ttl`。PR #39。服务器真实运行 dbt-customer-cross-sell-insights × 1 排在 `t38-real` 之后。
- 2026-11-04 主会话：服务器真实运行 `t39-real`（commit 31a5fbf；`ablate --split dev --config full --task dbt-customer-cross-sell-insights --trials 1 --budget 2`）：reward 1（1/1），120 轮，cost_usd $0.2029；连冒烟实际价 $0.1756（全在闲时）。事件文件 `runs/t39-real/jobs/full/dbt-customer-cross-sell-insights__M26LSax/agent/events/` 的单次工具耗时（tool_start 到 tool_end）：`describe_table` n=2 中位数 8.45 s、最大 9.40 s（accept-2 中位数 14 s）；验证工具 `audit_model_sources` n=4 中位数 7.99 s、`check_model_schema` n=4 5.69 s、`verify_model_values` n=4 5.09 s、`map_columns` n=4 1.46 s（accept-2 单次中位数 27–85 s）；`list_tables` n=5 1.95 s；`query_database` n=105 0.82 s。两次验证者调用各 127 s 和 138 s，报告全 PASS/N/A。另：`dictionary_search` 1 次挂满 1800.99 s，是工单 42 的问题，与本工单无关。PR #39 CI 绿、Codex app 无意见，squash 合入。
