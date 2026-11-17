# 89 — agent 自己选库：多数据源

Status: ready-for-agent
Blocked by: 88
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`data-agent run` 能接收一份数据源清单，agent 看得到全部库、每次查询自己选库，每条查询的事件记下库名。只给一个库时（评测、单次运行），工具定义和行为和现在完全一样。

## Authority

- May edit: `src/data_agent/mcp_server.py`、`gateway.py`、`context_tools.py`、`db_facts.py`、`label_tables.py`、`tool_annotations.py`、`events.py`、`cli.py`、`agent.py`（只改数据源清单进选项和系统提示的部分）、`src/data_agent/web/sources.py`（只为把 TOML 解析挪成共用，不改格式和校验规则）；`skills/sql-workflow/`、`skills/duckdb-sql/`、`skills/postgres-sql/`（只加"多个库时先看库清单再选"这类通用说明，不写题目内容，ADR-0005）；`tests/`（新增单元测试）
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec 第 3、20 条。契约（参数名、事件里库名的字段）以工单 88 合入的为准。
- 先例：nao `apps/shared/src/tools/execute-sql.ts:29-34`（`database_id`，多库时必填）、`apps/backend/src/components/ai/system-prompt.tsx:335-337`、`:371-388`（两个库以上才在系统提示列库清单）；SP `query_database(sql, row_limit, connection_name=None)`（`SignalPilot/signalpilot/gateway/gateway/mcp/tools/query.py:152-157`）。路径相对 `~/dev/AI/data-agent-refs/`。
- 只配一个库时不注册库名参数，`mcp__data__*` 的工具清单和参数一字不变（评测配置不变一组会断言）。
- 跨库：分别查再合并，不做一条 SQL 跨库 join；每个库各走一遍只读网关的规则。
- 表目录缓存、数据库事实、标签查询按库分开，不能把 A 库的表报成 B 库的。
- 和 90 并行时都会改 `agent.py`、`cli.py`，后合并的一方 rebase。

## Acceptance

- [ ] 多库时每个查数和上下文工具都能按库名查到对的库；库名不存在时报错并列出可用的库名（单元测试）
- [ ] 每条查询事件记下库名；单库时事件照旧能被现有运行页读出（单元测试）
- [ ] 连接串不出现在参数、系统提示、事件里（单元测试）
- [ ] 评测配置不变一组（`tests/acceptance/test_chat_eval_unchanged.py`）一直通过
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实运行由主会话做（工单 agent 加载不了模型环境）：最终回复写好要跑的命令，主会话跑完把命令和真实输出抄进 Comments。命令：用工单 88 的两个夹具库跑一条要两个库才能答对的查数（场景 g 的问题），贴出答案和每条查询事件的库名；再用单个 `--duckdb` 跑一条现有快速上手的查数，确认和改之前一样。

## Stop conditions

- 单库时做不到工具定义一字不变。
- spec 自相矛盾，或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
