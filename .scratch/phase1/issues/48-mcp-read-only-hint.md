# 48 — MCP 工具全部标 `readOnlyHint`，同一轮的调用并发执行

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 48-mcp-read-only-hint)
Branch: worktree-48-mcp-read-only-hint

## Goal

`data-agent mcp-server` 注册的每个工具在 `tools/list` 里都带 `annotations.readOnlyHint = true`。Claude Code 只并发执行带这个注解的 MCP 工具（工单 43 的研究笔记已证明），所以主 agent 和验证者同一轮发出的多个工具调用不再逐个排队。

## 已和用户定下的（2026-11-05）

- 类别：**确定性修复**（ADR-0008 Update 2026-11-04）。验证是单元测试，加主会话一次单题真实运行。
- 照 `docs/research/2026-11-04-serial-mcp-calls.md` §4 的草稿做：新建 `src/data_agent/tool_annotations.py` 放一个 `READ_ONLY = ToolAnnotations(readOnlyHint=True)`；三个注册文件从它导入，11 处 `@server.tool()` 改成 `@server.tool(annotations=READ_ONLY)`。常量不能放在 `mcp_server.py`，会循环导入。
- 所有工具都确实只读（SQL 全走只读网关，`map_columns` 只读 workspace 文件），标注如实。
- 一个都不能漏：Claude Code 遇到一个没标注的工具会堵住它后面的整批调用，所以用测试锁住"全部为 True"。
- Postgres 运行不受益（网关里的锁，笔记 §2.4），记为已知限制，本工单不动网关。

## Authority

- May edit: `src/data_agent/tool_annotations.py`（新建）、`src/data_agent/mcp_server.py`、`src/data_agent/context_tools.py`、`src/data_agent/verifier_tools.py`（都只改装饰器和 import）、`tests/test_mcp_server.py` 或新测试文件
- Must not edit: `tests/acceptance/`、`src/data_agent/verifier_v2/`（工单 45 在改）、`src/data_agent/agents/`、`skills/`、守卫、网关、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- 研究笔记：`docs/research/2026-11-04-serial-mcp-calls.md`（§2 源码证据，§3 复现，§4 修法草稿）。
- 装饰器位置（main b62226f）：`src/data_agent/mcp_server.py:182,198,240,253`；`src/data_agent/context_tools.py:509,531,547`；`src/data_agent/verifier_tools.py:1390,1406,1452,1463`。
- mcp 2.3.0：`MCPServer.tool(annotations=...)`，`ToolAnnotations` 从 `mcp.types` 导入。
- 工单 45 同时在改 `verifier_tools.py` 的注册段（去掉 `verify_model_values`、加 4 个新工具）。谁后合入谁解决冲突；45 新加的工具也要带注解，若本工单先合入，主会话会在 45 合入前检查。

## Acceptance

- [ ] 测试：对每个配置 `build_server(...)` 后列出工具，断言每个工具的 `annotations.readOnlyHint is True`（修前全是 `None`，测试先红后绿）。
- [ ] `make check` 通过。
- [ ] （主会话）服务器真实运行：dbt-customer-cross-sell-insights × 1（full）。贴出同一轮多个 MCP 调用的时间戳，证明有重叠；对照 accept-2 同题 trial 的串行耗时。

## Evidence required

测试命令与输出；本地 `data-agent mcp-server --config full --duckdb <file>` 走 stdio 发 `tools/list`，贴出每个工具的 annotations。

## Stop conditions

- mcp 库的 `tool()` 不接受 `annotations` 参数（版本不符）。
- 任何工具实际上会写数据库或 workspace（这时 readOnlyHint 就不如实）。
- 第二轮审查没过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1（Codex 无意见；eng:code-review 无硬问题）
- Model spend: $0
- Outcome: in-review，branch `worktree-48-mcp-read-only-hint`（commit fdb9d15）；真实运行待主会话
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（worktree 48-mcp-read-only-hint）

**改动**（fdb9d15）：新建 `src/data_agent/tool_annotations.py`，一个常量 `READ_ONLY`；`mcp_server.py`、`context_tools.py`、`verifier_tools.py` 的 11 处注册都传 `annotations=READ_ONLY`，只动装饰器和 import；`tests/test_mcp_server.py` 新增按全部配置参数化的测试，断言列出的每个工具都是只读。

**与工单草稿的一处不同**：常量用 Python 字段名 `read_only_hint=True`，不是 `readOnlyHint=True`。mcp 2.3.0 的 pydantic 字段是 snake_case，`readOnlyHint` 是线上别名，basedpyright 不接受别名写法。stdio 证据确认线上名是 `readOnlyHint`。

**测试**：`make check` 在分支上：ruff 通过、193 files already formatted、basedpyright 0 errors、`1586 passed, 1 skipped, 38 deselected, 40 xfailed in 451.40s`。修前 `uv run pytest tests/test_mcp_server.py -k annotated` 在 6 个配置上都红（`first extra item: 'query_database'`），修后 13 passed。

**真实 stdio 运行**：`data-agent mcp-server --config full --duckdb <tmp file>`，客户端脚本发 `tools/list`，每个工具的 annotations：

```
query_database       {'readOnlyHint': True}
list_tables          {'readOnlyHint': True}
describe_table       {'readOnlyHint': True}
explain_query        {'readOnlyHint': True}
discover             {'readOnlyHint': True}
hydrate              {'readOnlyHint': True}
dictionary_search    {'readOnlyHint': True}
map_columns          {'readOnlyHint': True}
check_model_schema   {'readOnlyHint': True}
audit_model_sources  {'readOnlyHint': True}
verify_model_values  {'readOnlyHint': True}
```

**审查第 1 轮**
- `da-review main`（Codex）：11 个工具标注一致，覆盖全部配置，无意见。
- `eng:code-review main` Standards：无硬问题。三条判断题都不改：11 处相同装饰器改动可以包一层（审查者同意显式更清楚）；单常量模块可以放进 `context_tools.py`（工单要求新模块）；`dict.fromkeys` 断言略巧但失败 diff 有用。审查者确认没有工具体有写路径，`CatalogCache.get` 有锁，并发调用不会在目录上竞争。
- `eng:code-review main` Spec：11 处全改，每个工具至少在一个配置里出现所以测试覆盖全部，无越权。两条小注：snake_case 字段名（见上）；测试查的是 Python 属性不是线上别名。不改：stdio 输出锁住了线上别名，工单的 Evidence 把这项查验分给真实运行。

**已知限制**（按工单）：Postgres 运行不受益（网关锁），不动。

**交接提醒**：工单 45 新加的 4 个验证工具也必须带 `annotations=READ_ONLY`，否则新测试会红。

### 2026-11-05 主会话

- 分支已 push，PR #45 已开。服务器真实运行（cross-sell × 1）排在 `t49-screen` 之后，服务器同一时间只跑一批。
- 2026-11-05 主会话：服务器真实运行 `t48-real`（commit fdb9d15；`ablate --split dev --config full --task dbt-customer-cross-sell-insights --trials 1 --budget 2`）：reward 1（1/1），trial 墙钟 767 s（accept-2 同题三个 full trial 是 938、1010、3609 s）。实际价 $0.3785（2 个事件文件都在高峰），内存峰值 1.9 GB。并发证据（`runs/t48-real/jobs/full/dbt-customer-cross-sell-insights__LkVZv8y/agent/events/`）：167 次 MCP 调用；同一秒内发出 ≥3 个调用的批次 30 个。按"批内结束时间跨度 ÷ 批总时长"看（串行时各调用依次结束，比值接近 1；并发时一起结束，比值接近 0）：accept-2 三个 trial 的中位数是 0.70、0.66、0.70；t48-real 是 0.32，其中 6 个 `hydrate` 一批总时长 7.3 s、结束时间跨度 1.0 s（0.13），3 个 `query_database` 一批 5.5 s、跨度 0（0.00）。注意 `tool_start` 时间戳是整条消息到达时一起记的，所以"同时开始"本身不能证明并发，结束时间的聚集才能。PR #45 CI 绿，Codex app 第一次 Failed，已评论重试。
