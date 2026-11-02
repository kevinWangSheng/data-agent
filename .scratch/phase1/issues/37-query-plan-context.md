# 37 — 查询工具把执行计划、索引等确定性信息返回给模型

Status: in-review
Blocked by: 36（done）
Spec: ../spec.md
Claimed by: claude (opus-5-5) 2026-11-02
Branch: worktree-37-query-plan-context

## Goal

agent 写 SQL 前就能拿到数据库自己报出的事实，包括行数、类型、约束、索引和执行计划，由模型自己把查询写快。慢查询超时的时候，报错里直接带上执行计划和相关表的索引。所有配置都有这些工具，DAB 也有。

## 已和用户定下的（2026-11-02）

- **原则**：
  - 改写 SQL 由模型做，工具不自动改写。
  - 工具只返回确定性的事实，不返回"可能很贵"这类启发式判断，也不用阈值替模型做决定。
  - 兜底是超时（工单 36），超时报错可以接受。
  - 不在数据库侧加索引：DAB 的库属于榜单，真实用户的库按设计只读。
- **分层**（ADR-0003 Update 2026-11-02）：
  - 数据库自己能报出来的事实放进基础工具层，所有配置都有；
  - 需要推断或检索的留在上下文层。
- **DAB**：
  - DAB 包装层同样加这些工具，所有配置都带，不受层开关控制，和超时一样。
  - 它们是我们的工具层，不针对题目（ADR-0005）。
- **Skill**：这次不加查询写法。以后 data-eng-bench 开发集的失败归因里出现慢查询类问题，再按通用写法补。
- **数据库**：两条路径都只做 Postgres 和 DuckDB；SQLite、Mongo 这次不做，照样有超时兜底。
- **依据**：三家现成做法都在基础层给单表详情（含索引）和随时可调、不真跑的执行计划：
  - SignalPilot：`describe_table`、`explain_query`；
  - Postgres MCP Pro：`get_object_details`、`explain_query`；
  - Google MCP Toolbox：`list_tables`（含约束、索引）、`get_query_plan`（EXPLAIN JSON，不带 ANALYZE）。

## Authority

- May edit：
  - `src/data_agent/`（`gateway.py`、`engine/`、`mcp_server.py`、`context_tools.py`）
  - `evals/dab_server.py`
  - `tests/`（`tests/acceptance/` 除外）
  - `docs/setup.md`
- Must not edit：
  - `tests/acceptance/`
  - `skills/`
  - `src/data_agent/configs/`（不加、不改层开关）
  - `src/data_agent/verifier_tools.py`
  - DAB clone
  - 其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none（真实运行由主会话在服务器上做）
- Spend cap：$1（单题真实运行）

## Context

**现状**（2026-11-02 核对）：
- 基础工具只有 `query_database` 和 `list_tables`。`list_tables` 只给表名、类型和列名（`src/data_agent/mcp_server.py:15-26,70-84`）。
- 列类型和精确行数只有上下文层的 `hydrate` 给（`src/data_agent/context_tools.py:267-278`），`baseline` 和 `no-context` 拿不到。
- 代码里没有任何地方读索引、约束或执行计划。
- DAB 包装层的 `list_tables` 对 SQL 库只返回表名（`evals/dab_server.py:53-57,296-312`）。
- 超时只返回 `Query timed out after 150s`：网关在 `gateway.py:80,115`，DAB 在 `dab_server.py:184`。

**要做的，基础工具层（所有配置）**：
1. `list_tables`：每张表加上行数估计。
   - Postgres 照 SignalPilot `connectors/drivers/postgres.py` 的做法：`pg_stat_user_tables.n_live_tup`，取不到时用 `pg_class.reltuples`；两者都没有就写"未知"。
   - DuckDB 用 `duckdb_tables().estimated_size`。
   - 视图不给行数。
2. 新增 `describe_table(table)`：列（类型、可空）、主键、声明过的外键、索引（名称和定义）、行数估计。
   - Postgres 再加每列来自 `pg_stats` 的 `n_distinct` 和 `null_frac`；表没有统计时明说"没有统计"，不现场扫表。
   - Postgres 读 `pg_constraint`、`pg_indexes`（照 SignalPilot `postgres.py:350-360`）；DuckDB 读 `duckdb_constraints()`、`duckdb_indexes()`。
3. 新增 `explain_query(sql)`：
   - SQL 先过只读网关的同一套检查，再跑 `EXPLAIN`，**不带 ANALYZE**；
   - 照原样返回数据库给的计划文本，保留各节点的估算行数、连接方式以及连接和过滤条件原文，不做摘要、不做判断；
   - 在网关的只读事务和超时下执行。
4. `query_database` 超时的时候，报错文本在原有的 `Query timed out after 150s` 后面附上：
   - 这条 SQL 的 `EXPLAIN` 输出；
   - SQL 里涉及的每张表的已有索引（用 sqlglot 取表名）。
   - `EXPLAIN` 本身失败的话只附失败原因，超时报错照常返回。

**上下文层**：
- `hydrate` 在现有输出（精确行数、列类型、连接候选）上补上可空、主键、外键、索引。
- 和 `describe_table` 共用同一份取数代码。精确行数照旧，不改成估计。

**DAB 包装层**（`evals/dab_server.py`，只对 `postgres`、`duckdb` 两种 db_type）：
- `list_tables` 带行数估计；
- 新增 `describe_table(db_name, table)` 和 `explain_query(db_name, sql)`；
- 超时报错附上计划和索引。
- 所用 SQL 都经 `governed()` 检查，再交给 DAB 的 `QueryDBTool.exec`。
- 不复制 DAB 代码（无许可证）。

**参照**：
- 本地 `data-agent-refs/SignalPilot/signalpilot/gateway/gateway/` 下的 `mcp/tools/query.py:310`（`explain_query`）、`mcp/tools/schema/catalog.py:12`（`describe_table`）、`connectors/drivers/postgres.py`。
- 复制代码要保留版权头并登记 `NOTICE`（Apache-2.0）。

**不做**：
- `EXPLAIN ANALYZE`、假设索引（hypopg）、索引推荐；
- 列的样本值和常见值（`dictionary_search` 已部分覆盖）；
- DuckDB 的列基数（要扫全表）；
- SQLite、Mongo；
- 改 Skill。

**合入之后**（主会话安排，plan §10 第 13b 项）：
- 基础工具变了，`accept-1` 的结果不再代表合入后的 agent。
- 按 ADR-0008 在合入后的 main 上重跑一次合并验收（开发集 30 题 × 3，full）。
- 这张工单没有对应的开发集失败模式，所以不做初筛。

## Acceptance

- [ ] 单元测试（DuckDB 夹具；Postgres 部分照 `tests/test_postgres.py` 标 integration）：
  - [ ] `list_tables` 每张表带行数估计，视图不带。
  - [ ] `describe_table` 返回列类型、可空、主键、外键、索引和行数估计。
    - Postgres 带每列 `n_distinct`、`null_frac`；表没有统计时如实说明。
  - [ ] `explain_query` 返回 `EXPLAIN` 的计划文本。
    - DDL 或 DML 被网关拦下，返回可读错误。
    - 传入 `EXPLAIN ANALYZE ...` 或其他非 SELECT 时被拦下，不会真跑查询。
  - [ ] 超时报错（测试里把超时调小）以 `Query timed out after` 开头，后面带计划文本和涉及表的索引。
    - `EXPLAIN` 失败时只附失败原因。
  - [ ] `baseline`、`no-context` 配置也能看到 `describe_table`、`explain_query`；上下文工具在这两个配置下照旧不出现。
  - [ ] `hydrate` 输出带可空、主键、外键、索引，原有内容不变。
  - [ ] DAB 包装层（假 DAB 夹具，`tests/test_dab.py`）：
    - Postgres、DuckDB 库的 `list_tables` 带行数估计；
    - `describe_table`、`explain_query` 可用，超时报错带计划；
    - SQLite、Mongo 的行为不变。
- [ ] `tests/acceptance/` 原样全部通过。
- [ ] `docs/setup.md`：写明新工具和超时报错的内容。
- [ ] `make check` 通过。
- [ ] 本地真实运行：对 DuckDB 夹具起 `data-agent mcp-server --config baseline`，调一次 `describe_table` 和 `explain_query`，贴出输出。
- [ ] （主会话，`accept-1` 跑完后）服务器真实运行 imdb query1 × 1（no-context）。
  - 贴出 agent 调用的新工具，以及超时报错里的计划文本（如果出现超时）；
  - 对照工单 36 的 8 分 24 秒、2 次超时；
  - 计划里能看到慢查询慢在哪，例如套了函数的连接条件，就把这一点记进 Comments。这一步同时核实工单 36 留下的根因问题。

## Evidence required

单元测试输出；本地 MCP 调用输出；主会话的 `da-remote` 命令与输出。

## Stop conditions

- 只有绕过只读网关的检查才能跑 `EXPLAIN`，在 DAB 路径上也一样。
- 要改 `tests/acceptance/` 才能通过。
- DAB 的 `QueryDBTool` 跑不了 `EXPLAIN`，又不能在不改 DAB clone、不复制 DAB 代码的前提下解决。
- 第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清楚试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1（eng:code-review；da-review 未完成）
- Model spend: $0（没有调 agent 模型；Codex 审查的花费不在这里计）
- Outcome: in-review
- Human review minutes: —

## Comments

- 2026-11-02 对齐讨论：起因是 DAB 试跑 imdb 上的慢查询（见工单 36 Context）。
  - "两边套 `regexp_replace` 导致逐对比较"这一说法没有核实，Postgres 也可能对表达式等值条件走哈希连接。
  - 具体慢在哪，要看那几条查询的 `EXPLAIN`。这一点在本工单的服务器真实运行里核实。
- 2026-11-02 triage（和用户逐项确认）：
  - 原先的推荐是"只在超时时给计划"。用户质疑：执行前不给索引和 schema，模型只能靠猜。
  - 核对后确认：我们执行前确实不给索引、约束、行数估计和计划；三家现成做法（SignalPilot、Postgres MCP Pro、Google MCP Toolbox）都在基础层提供。
  - 因此改成现在的范围：基础层给事实，上下文层只留策略。
  - 用户同意，并接受代价：要改 ADR-0003，baseline 会变强，合入后要重跑合并验收。
- 2026-11-02 实现（commits `8551baf`、`8659c4e`）：
  - **新模块** `src/data_agent/db_facts.py`：行数估计、列/可空、主外键（`pg_get_constraintdef` / `duckdb_constraints()` 原文）、索引定义、Postgres `pg_stats`、计划文本、超时报告。catalog、`Table`、`find_table` 从 `context_tools.py` 移入这里，供 `describe_table`、`hydrate` 和 DAB 共用。
  - **网关**：`Gateway.explain(sql, row_limit)` 先用 `check()` 检查内层 SQL（含行数上限），再在前面加 `EXPLAIN`（不带 ANALYZE），在原有只读连接和超时下执行；新增 `QueryTimedOut`。传入 `EXPLAIN …` 会被网关当作 Command 拦下。
  - **超时报错**：MCP 框架前缀 `Error executing tool query_database: ` 之后，正文以 `Query timed out after Ns` 开头；接着是 `Plan (EXPLAIN, without running the query):` 和计划文本，再是 `Indexes on the tables this query reads (from pg_indexes|duckdb_indexes()…):`，每张表一行或多行 `表: 定义`，没有写 `none`。`EXPLAIN` 失败时只附 `EXPLAIN failed: 原因`。
  - **DAB 包装层**（只对 postgres、duckdb）：
    - `list_tables` 返回 `{"name","type","rows_estimate"}`，取不到写 `"unknown"`，视图不带；
    - 新增 `describe_table(db_name, table)`、`explain_query(db_name, sql)`；
    - 超时附计划和索引；
    - 全部 SQL 经 `governed()` 后交给 `QueryDBTool.exec`；
    - SQLite、Mongo 行为不变，调两个新工具返回可读错误。
  - **Authority 之外的改动**：`NOTICE` 登记了 `db_facts.py` 里改写自 SignalPilot `postgres.py` 的行数估计和 `pg_indexes` 查询，工单要求登记，但它不在 Authority 列表里。假 DAB 的 `fake_sqlalchemy.py` 加了 EXPLAIN 和 catalog 的固定回答。
- **测试**：
  - `make check`：exit 0，1435 passed, 1 skipped。
  - 本机临时 Postgres 17（`/tmp/da37-pg`，端口 54337，测完已停）上跑 `tests/test_db_facts.py tests/test_postgres.py tests/acceptance/test_seam_b_mcp_server.py -m integration`：28 passed。
  - `tests/acceptance/` 没有改动，全部通过。
  - 第一次 `make check` 有 3 个 `test_harbor_agent` 用例在 `uv build --wheel` 失败；单独重跑 54 passed，之后两次完整 `make check` 都全绿。判断是构建时我在改工作区文件，属于偶发，未确认。
- **本地真实运行**：`uv run python /tmp/da37-run/client.py <worktree> /tmp/da37-run/shop.duckdb`，用 stdio 起 `data-agent mcp-server --config baseline --duckdb shop.duckdb`：
  - tools: `['describe_table', 'explain_query', 'list_tables', 'query_database']`
  - `list_tables`：`raw.orders (base table, ~37 rows): order_id, customer_ref, status, amount, order_date`
  - `describe_table raw.orders`：`raw.orders (table, ~37 rows, 5 columns)`，各列 `, nullable`，`Keys: none declared`，`Indexes: none`
  - `explain_query`（orders JOIN order_items）：DuckDB 原样计划，含 `HASH_JOIN`、`Conditions: order_id = order_id`、各节点 `~14 Rows` / `~74 Rows`
  - `explain_query "EXPLAIN ANALYZE SELECT * FROM raw.orders"`：`is_error=True`，`Query blocked by the read-only gateway: Blocked: Command statements are not allowed (read-only mode)`
- **审查**：
  - `da-review main`：三次都因 "Selected model is at capacity" 中断，没有给出结论，需要主会话重跑。
  - `eng:code-review main`（新上下文）：Standards 1 中 3 低，Spec 4 低，没有高危。
    - 已修（`8659c4e`）：
      - 超时时的计划按 `MAX_ROWS` 做，不是这条查询自己的 `row_limit`。改成透传 `row_limit`，加了先失败、修后通过的 Postgres 测试 `test_postgres_timeout_plans_the_query_with_its_own_row_limit`。
      - `hydrate` 的列行改为复用 `column_line`。
    - 不修：
      - `context_tools` 保留 `Table`、`find_table` 的再导出（中）：调用方 `verifier_tools.py`（Must not edit）和 `evals/jaffle.py`（Authority 之外）还从旧路径导入。
      - `sql_run`/`sql_explain` 以及两个 server 的 docstring 重复（低）：两个 server 签名不同，不值得抽公共代码。
      - 分析过的空 Postgres 表显示 `rows unknown`（低）：工单要求照 SignalPilot 的 `NULLIF` 写法。
      - `hydrate` 列行加了 `, nullable` 后缀（低）：工单要求 hydrate 补可空；原有 `列 类型` 原样留作前缀。
      - DAB postgres/duckdb 的 `list_tables` 失败时直接抛 `ToolError`（低）：`results.reply` 失败时同样抛 `ToolError`，区别只是不截断长错误。
- **未核实**：
  - 本机 DAB clone 没有 `db_utils`，真实 DAB 的 `QueryDBTool` 能不能执行 `EXPLAIN` 只在假 DAB 上验证过，要靠服务器上 imdb query1 那次真实运行核实（工单的停止条件之一）。
  - `evals/attribute.py` 只把 `list_tables` 的输出算作"见过的名字"，新工具输出里的名字只能靠它的猜测路径覆盖。`LISTED_TABLE` 正则对新格式仍然匹配。
- 2026-11-02 主会话：da-review 在 Codex 容量满时三次中断，用 PR 上的 Codex GitHub app 审查代替（同工单 36）。
