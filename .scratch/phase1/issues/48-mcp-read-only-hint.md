# 48 — MCP 工具全部标 `readOnlyHint`，同一轮的调用并发执行

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
