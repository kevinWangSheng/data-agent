# 36 — DAB 路径的查询超时：和网关一样 150 秒，并在数据库端真正停下

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
