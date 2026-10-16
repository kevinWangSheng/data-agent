# 06 — 上下文策略工具

Status: in-review
Blocked by: 02, 05
Spec: ../spec.md
Claimed by: claude (worktree 06-context-tools)
Branch: worktree-06-context-tools

## Goal

agent 在宽 schema 上先 discover（只回引用和 ≤200 字摘要）再 hydrate 单个对象；能按关键词查列的真实取值；能拿到按 `_id` 匹配的 join 候选；关上下文策略时这些工具不暴露。

## Authority

- May edit: `src/data_agent/`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $1

## Context

ADR-0003；CONTEXT 的 Discover→hydrate、Dictionary search；上游 SignalPilot `analyze_project_db`、ktx 的契约（只抄思路）。

## Acceptance

- [ ] 接缝 B 中 discover、hydrate、dictionary search 的验收测试转绿
- [ ] 关上下文策略配置下这些工具不出现
- [ ] `make check` 通过

## Evidence required

MCP 客户端真实调用三个工具的输出。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 2
- Model spend: $0（没调模型）
- Outcome: 三个上下文工具和 join 候选完成；接缝 B 的 7 个工单 06 验收用例转绿；PR #8 退回第 1 轮已修（hydrate 先精确匹配 ref；带点名字的 ref 不再冲突）
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `516fa9b`、`1340257`、`5601917`）

**实现**
- 新模块 `src/data_agent/context_tools.py`；`mcp_server.build_server` 只在 `config.context` 为真时注册。所有 SQL 经 `run_query` 走只读网关。
- discover：按表名、列名打分，返回 `{"results": [{"ref", "summary"}]}`；summary ≤200 字、先放命中列；limit 1–50；支持非 ASCII 名称。
- hydrate：单个对象的行数、全部列与类型、join 候选。候选按 `<x>_id` 列找：先找以 x 命名的表（x/xs/xies，前缀 stg_、dim_，照 SignalPilot），再找有同名 `_id` 列的表，每列最多 25 个（照 ktx）。join 候选不单独做工具（验收测试定了三个工具，Spec 审查认为符合 spec）。
- dictionary_search：在文本列里找含关键词的真实取值并计数；每列 ≤20 值、每值 ≤80 字、≤50 列，超出提示换关键词；关键词 ≤200 字。
- catalog 按网关 10000 行上限分页读；dictionary 语句按行数上限与 100KB 长度上限分批。
- 验收测试只删了工单 06 的 xfail（4 个参数化用例 + 3 个 discover/hydrate/dictionary 测试；主会话核过 diff）。新增 `tests/test_context_tools.py` 16 个测试。

**检查与真实运行**：`make check` 0 错误，555 passed, 8 xfailed（都是工单 07）。`uv run python -I /tmp/06-evidence/demo.py <worktree>`（用 shop.sql 建 DuckDB，真实 MCP stdio 客户端启动 `data-agent mcp-server`）：
```
== full tools: ['dictionary_search', 'discover', 'hydrate', 'list_tables', 'query_database']
== discover({'query': 'web events', 'limit': 3})
{"results": [{"ref": "raw.web_events", "summary": "table, 40 columns; matching: event_id, event_name, event_category, event_label, event_value; columns: session_id, customer_id, page_url, page_title, referrer_url, utm_source, utm_medium, utm_campaign…"}]}
== hydrate({'ref': 'main.stg_orders'})
main.stg_orders (table, 37 rows, 5 columns)
  order_id INTEGER / customer_id VARCHAR / status VARCHAR / amount DECIMAL(10,2) / order_date DATE
Join candidates (by _id column name; check the key values before joining):
  order_id -> raw.orders.order_id
  order_id -> main.orders_enriched.order_id
  order_id -> raw.order_items.order_id
  customer_id -> main.stg_customers.customer_id
  customer_id -> raw.customers.customer_id
  customer_id -> main.customer_orders.customer_id
  customer_id -> main.customer_orders_fanout.customer_id
  customer_id -> main.orders_enriched.customer_id
  customer_id -> raw.web_events.customer_id
== dictionary_search({'keyword': 'gold'})
main.stg_customers.tier: 'Gold' (2), 'GOLD' (1), 'gold_tier' (1)
raw.customers.tier: 'Gold' (2), 'GOLD' (1), 'gold_tier' (1)
== no-context tools: ['list_tables', 'query_database']
```

**审查**
- da-review 第 1 轮 2 条 P2 全修（1340257，配先失败的测试）：catalog 超 10000 行悄悄截断（改分页）；dictionary 先按全局行数截断导致后面的列丢失（改为每列先取上限再合并）。
- da-review 第 2 轮 2 条 P2 全修（5601917，配先失败的测试）：500 个文本列的表生成的语句超网关 100KB（改按长度分批）；分词只认 ASCII（改支持 Unicode）。
- eng:code-review Spec 轴：验收测试改动只是删 xfail，合规；join 候选放 hydrate 满足 spec。已修 C1（候选只看单向，加共享 `_id` 列的表）、C2（dim_x 单数与 -ies 复数）。Standards 轴无硬规矩违规；已修 docstring 与实际不符、dictionary 输出无上界、补歧义 ref 与多批语句测试，及若干坏味道（`Run` 改名 `RunQuery` 等）。
- 修复已用满 2 轮，第 2 轮修复未再审，交给 PR 上的 Codex（第 7 步）。

**不修的理由**：多 catalog 下同名表合并列表（现在每次只连一个 DuckDB 文件，问题是潜在的）；hydrate 每次 count(*)（行数属于对象全文，DuckDB 代价低）；discover 的 limit 参数（照 ktx discover_data）；每次调用重读 catalog（dbt run 会改 schema，缓存会过期）；CATALOG_SQL 与 LIST_TABLES_SQL 形状不同，不合并。

**已知限制**：dictionary_search 每次扫所有文本列，超大库靠 50 列早停与网关超时兜底，没像 ktx 那样只对类别列预采样；Postgres 方言未测（SQL 只用 information_schema、strpos、CAST AS VARCHAR、窗口函数），由工单 08 覆盖。

### 2026-10-16 第 7 步：PR #8 退回第 1 轮（主会话）

CI 两次全绿。退回：原因如下，在原分支 `worktree-06-context-tools` 上修，修完提交，不 push、不 rebase（需要同步 main 时用 `git merge origin/main`）。

1. **Codex P2（`src/data_agent/context_tools.py:134` `find_table`）属实**：先 `strip().lower()` 再匹配，discover 返回的带首尾空格的带引号标识符（如 DuckDB 表 `"events "`）在 hydrate 里找不到；大小写折叠会让 Postgres 里只差大小写的带引号标识符变成歧义。改为：先按 ref 原样精确匹配限定名，命中即返回；没命中再做现在的规范化与裸名查找。配一个修复前会失败的测试（带空格的表名、只差大小写的两张表）。

修完 `make check`，跑一次修复相关的真实 MCP 调用，再跑一轮 `da-review main`；最终回复给出要追加的 Comments 与 Ledger（Attempts 2）。

### 2026-10-16 Ticket agent, round-1 return handed back (commits `bdad8c1`, `b7a7a5b`)

**Fix 1 (`bdad8c1`): Codex P2 on `find_table`.** `find_table` now returns a table whose `table.ref == ref` exactly before trying anything else. Only if nothing matches does it fall back to the old trimmed, lowercased match on the full ref or bare name. Two new tests, both failed before the fix:
- `test_hydrate_finds_a_discovered_name_with_surrounding_spaces`: real stdio MCP. Discover returns the ref `"main.events "` (quoted table name with a trailing space), and hydrate accepts it.
- `test_find_table_prefers_the_exact_ref_over_a_case_folded_match`: unit test on `find_table`. With `public.Orders` and `public.orders` both present, each exact ref returns its own table, and `ORDERS` still reports ambiguous. This one can't be a stdio test because DuckDB won't create two names that differ only in case (`Catalog Error: Table with name "t" already exists!`).

**da-review round 3 (`/tmp/06-evidence-r1/da-review-3.txt`).** Found one new P2, which was in the code before this round: a schema or table name containing a dot made refs collide. `a."b.c"` and `"a.b".c` both became `a.b.c`, so hydrate could silently show the wrong table.

**Fix 2 (`b7a7a5b`).** In `Table.ref`, a name part is double-quoted only if it contains `.` or `"`. Ordinary refs (`raw.web_events`) are unchanged, so the acceptance tests are untouched. New test `test_refs_of_dotted_names_do_not_collide` (stdio, discover then hydrate) failed before the fix: `['a.b.c', 'a.b.c']`. This fix has not been through da-review again; the PR's Codex review covers it.

**Checks:** `make check`: 0 errors, 558 passed, 15 deselected, 8 xfailed.

**Real MCP runs:** `uv run python -I /tmp/06-evidence-r1/demo.py <worktree>` and `.../dots.py <worktree>`, starting `data-agent mcp-server --config full` over stdio:
```
== discover({'query': 'events'})  -> "ref": "main.events "
== hydrate({'ref': 'main.events '}) is_error=False
main.events  (table, 0 rows, 2 columns)
  event_id INTEGER
  customer_id VARCHAR
Join candidates (by _id column name; check the key values before joining):
  customer_id -> main.customers.customer_id
== hydrate({'ref': 'events'}) is_error=True   (a bare name without the space is a different name; expected)
== discover({'query': 'c'}) -> refs "\"a.b\".c", "a.\"b.c\""
== hydrate({'ref': 'a."b.c"'}) -> a."b.c" (table, 0 rows, 1 columns) / left_x INTEGER
== hydrate({'ref': '"a.b".c'}) -> "a.b".c (table, 0 rows, 1 columns) / right_y INTEGER
```
