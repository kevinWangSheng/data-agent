# 36 — DAB 路径的查询超时：和网关一样 150 秒，并在数据库端真正停下

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 36-dab-query-timeout)
Branch: worktree-36-dab-query-timeout

## Goal

agent 在 DAB 上的每条查询最多跑 150 秒。超时后：
- 数据库端的查询被停掉；
- agent 拿到明确的超时错误，可以换写法。

## Authority

- May edit：`evals/`（`dab_server.py`、`dab.py`）、`tests/`（`tests/acceptance/` 除外）、`docs/setup.md`
- Must not edit：`src/`（`TIMEOUT_SECONDS` 只 import 不改）、`skills/`、`scripts/`、`tests/acceptance/`、DAB clone、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none（真实运行由主会话在服务器上做）
- Spend cap：$1（单题真实运行）

## Context

- 起因：DAB 试跑（plan §10 第 9 项，2026-11-01/02）中，imdb 上 agent 写了 3 条两边都套 `regexp_replace(...)` 的连接查询。
  - 每条都跑到 MCP 层约 1800 秒的上限才被打断，3 条共约 90 分钟。
  - agent 最后仍然答出了（exit 0，$0.02）。
  - 属于 plan §10 第 12 项"修复 DAB 试跑暴露的问题"。
- 现状（2026-11-02 核对代码）：
  - 我们的网关有 150 秒超时：`src/data_agent/gateway.py:20` `TIMEOUT_SECONDS = 150`。Postgres 用连接参数 `-c statement_timeout`（`gateway.py:127-132`），DuckDB 用计时器调 `con.interrupt`（`gateway.py:72-80`）。
  - DAB 路径不走网关：`evals/dab_server.py:156-165` 的 `query_database` 只用 `governed()` 做只读校验和加 LIMIT，然后交给 DAB 自带的 `QueryDBTool.exec`。DAB 那边没有超时。
- 未确认：
  - DAB `QueryDBTool` 和 `db_utils` 怎么建连接，本地 clone 不全，要看服务器上钉的 `6ad9d68`。
  - 能不能在不改 DAB clone 的前提下给连接加 `statement_timeout`（比如连接参数、在 SQL 前加 `SET LOCAL`、或者包一层连接对象）。
  - MCP 层打断之后，Postgres 端的查询是否还在跑。
- 做法：
  - 优先在数据库端停下：Postgres 用 `statement_timeout`；Mongo 用 `maxTimeMS`；SQLite/DuckDB 用中断。哪个库做不到，就在 Comments 里写明。
  - 超时值复用 `TIMEOUT_SECONDS`，不另设常量。
  - 超时返回给 agent 的错误文本和网关一致：`Query timed out after 150s`。
- 不能复制 DAB 的代码（无许可证），只能按路径 import，或者在我们这一侧包装。

## Acceptance

- [ ] 测试（假 DAB 夹具）：
  - [ ] 慢查询在 150 秒内返回超时错误（测试里把超时调小）。
  - [ ] 超时之后，同一个 db 的下一条查询仍然能正常执行。
- [ ] Postgres：测试证明超时设在数据库端（`statement_timeout` 或等价机制），不只是客户端放弃等待。
- [ ] `docs/setup.md`：写明 DAB 路径的超时和各数据库的停止方式。
- [ ] `make check` 通过
- [ ] （主会话）真实运行：服务器上跑 imdb query1 × 1（no-context），贴出超时事件和总耗时。

## Evidence required

单元测试输出；主会话的 `da-remote` 命令与输出。

## Stop conditions

- 要改 DAB clone 或复制 DAB 代码才能做到。
- Postgres 端无法设超时，只能在客户端放弃等待。
- 第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清楚试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1（eng:code-review；da-review 因 Codex 满载未完成，主会话补跑）
- Model spend: $0
- Outcome: 实现完成，待补跑 da-review 和服务器真实运行
- Human review minutes: —

## Comments
- 2026-11-02 工单 agent（主会话转抄）：
  - 实现：DAB 每次查询都新开连接，所用驱动名在各库模块里固定（6ad9d68：`import sqlite3` / `import duckdb` / `import sqlalchemy` / `from pymongo import MongoClient`，从 GitHub 核对）。`evals/dab_server.py` 启动时把这些名字换成我们的包装，不改 DAB clone、不复制 DAB 代码。
    - Postgres：在连接参数 `options` 里加 `-c statement_timeout`，保留 DAB 的 `default_transaction_read_only=on`，由服务端取消。
    - MongoDB：客户端 `timeoutMS`，pymongo 作为 `maxTimeMS` 发给服务端。
    - SQLite/DuckDB：计时器对本次查询开的连接调 `interrupt()`。
    - 超时值复用 `TIMEOUT_SECONDS`；错误文本 `Query timed out after 150s`；`--query-timeout` 只给测试调小。
  - 测试（假 DAB 改成和真 DAB 一样按库分模块；`tests/test_dab.py`）：
    - 4 种库（SQLite、DuckDB、Postgres、Mongo）的慢查询都在约 0.5s 返回超时，同库下一条正常。
    - Postgres 连接带 `statement_timeout=500` 且只读选项保留。
    - 默认值等于 `TIMEOUT_SECONDS*1000`。
    - Mongo 客户端侧 `NetworkTimeout` 也算超时。
    - 截止后才发生的别种失败（连不上服务器）保留原错误。
  - 真实运行（本地 postgres:17 + SQLAlchemy 2.0.41 + psycopg2 2.9.9；按 DAB postgres exec 的形状写的探针，未用 pandas 读结果，因为 PyPI 不通；容器已停）：
    - 会话设置 `statement_timeout=2000`、`default_transaction_read_only=on`。
    - `SELECT pg_sleep(30)` 在 2.02s 被服务端取消，原错误 `(psycopg2.errors.QueryCanceled) canceling statement due to statement timeout`。
    - 经 `QueryTimeout.run` 返回 `Query timed out after 2s`（2.03s）。
    - `pg_stat_activity` 里没有残留的 pg_sleep；下一条 `SELECT 1` 正常。
  - 未确认：真 pymongo 4.13.2 的 `timeoutMS`/`maxTimeMS` 行为没有实测（PyPI 不通），只由假 DAB 覆盖。
  - make check：1411 passed, 1 skipped（3fb3754）。
  - 审查：
    - da-review main 三次都报 "Selected model is at capacity"，未完成，需补跑。
    - eng:code-review（Standards + Spec）已修：
      - 过期标志只在计时器真正中断了 SQLite/DuckDB 连接时才算超时，Postgres/Mongo 截止后别种失败保留原错误。
      - 加 Mongo `NetworkTimeout`。
      - 超时错误按 DAB 带括号的写法匹配。
      - 变量改名 `attribute`。
    - eng:code-review 不修：
      - 和 `gateway.py` 重复的 options 拼接、超时文本：要改 `src/`，Authority 不允许。
      - Mongo 的 `list_tables` 不需要单独处理：DAB 的 `MongoListDBTool.exec` 就在 `mongo_utils` 里用 `MongoClient`，已被覆盖。
      - 两个包装类形状相近、假驱动里的 sleep 代码重复、测试里 `elapsed < 10` 的墙钟断言：小的判断项，不改。
  - 待主会话：服务器上跑 imdb query1 × 1（no-context），贴超时事件和总耗时。
  - 分支 worktree-36-dab-query-timeout，提交 f96bdcc、3fb3754。
