# 51 — 工具计时改用 PostToolUse 的 duration_ms，报告出分位数

Status: in-progress
Blocked by: —
Spec: ../../../docs/research/2026-11-05-foundation-latency-audit.md（§1）
Claimed by: claude (worktree 51-tool-timing-percentiles, 2026-11-05)
Branch: worktree-51-tool-timing-percentiles

## Goal

每次工具调用（主 agent 和验证器内部，内置工具和 MCP 工具）都在事件文件里有一条准确的执行耗时；`ablate` 和 `evals accept` 的报告直接给出每 trial 墙钟、验证器墙钟、各工具耗时的 p50/p90/p95/p99、超时次数和工具调用次数，过线判定不用再手算。

## 已和用户定下的（2026-11-05，grilling Q3、Q4、Q17、Q18）

- 计时来源：SDK 的 PostToolUse 和 PostToolUseFailure 钩子输入里的 `duration_ms`；同时在 `tool_start`/`tool_end` 里补记 `parent_tool_use_id`。
- 两份报告（`ablate`、`evals accept`）都出：每 trial 墙钟、验证器墙钟、各工具耗时的 p50/p90/p95/p99、超时次数、工具调用次数。
- 工单 47 的过线标准不变（≥8/15，验证器墙钟中位数 ≤155 s）；p95/p99 只用来观察，不加新的过线条件。
- 如果真实运行里 MCP 工具或验证器内部的工具调用拿不到 `duration_ms`，就停下交回（不要自行改成 MCP 服务端计时，那是要人定的新设计）。

## Authority

- May edit: `src/data_agent/agent.py`（`record_message`、`build_options` 的钩子）、`src/data_agent/events.py`、`evals/process.py`、`evals/report.py`、`evals/ablate.py`（只改报告输出）、`evals/accept.py`（只改报告输出）、`tests/`（`tests/acceptance/` 除外）
- Must not edit: `src/data_agent/verifier_v2/`、`src/data_agent/mcp_server.py`、`src/data_agent/verifier_tools.py`、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话跑）
- Spend cap: $0.5（本地；服务器单题由主会话跑）

## Context

- 现状：`agent.py:794-805` 的 `tool_start`/`tool_end` 来自 SDK 消息流，一批并行调用的结果一起回来，耗时等于整批最慢的那个（t47-screen：Glob p99 74 s，不和其他调用重叠时 0.4 s）。
- SDK 0.2.168：PostToolUse / PostToolUseFailure 输入带 `tool_use_id`、`duration_ms`（`claude_agent_sdk/types.py:357-379`），在子 agent 里触发时带 `agent_id`（`types.py:329-345`）；消息带 `parent_tool_use_id`（`types.py:1368/1383`）。
- 钩子挂载点：`agent.py:720-737` 的 `all_hooks`；验证器钩子在 `verifier_v2/hooks.py`，`verifier_start`/`verifier_end` 按 `agent_id` 配对。
- 超时判定：网关报错的固定前缀 "Query timed out after"（`gateway.py:148/254`），不做别的推测。
- 报告路径：`evals/process.py:46-101`（`trial_metrics`/`task_metrics`/`job_metrics`）→ `report.py:230` 写 `report.json` → `ablate.py:633-636` 读取；`accept.py` 是否走同一路径要核实（`accept.py:38` 只 import 了 report 的工具函数）。
- 验证器墙钟：`verifier_start`/`verifier_end` 按 `agent_id` 配对后取时间段并集；没有 `verifier_end`（被超时掐断）的，用 Agent 调用的 `tool_end` 或 trial 结束时间补，并单独计数。
- 参考算法：本机 `runs/t47-screen/foundation-audit/tooltimes.py`、`a3/lat2.py`（只作对照，不进仓库）。

## Acceptance

- [ ] 新事件 `tool_timing`：`tool`、`tool_use_id`、`agent_id`（主 agent 为空）、`duration_ms`、`failed`、`timed_out`；单元测试用假的钩子输入覆盖成功、失败、子 agent、网关超时四种情况。
- [ ] `tool_start`/`tool_end` 带 `parent`（`parent_tool_use_id`），有测试。
- [ ] `evals/process.py` 的 trial 指标有 `wall_s`、`tool_calls`、`verifier_wall_s`、各工具 `durations` 和 `timeouts`；job/task 汇总出 p50/p90/p95/p99；用含并行批的事件样例证明分位数取自 `duration_ms`，不取自 `tool_end − tool_start`（修复前会失败）。
- [ ] `ablate` 和 `evals accept` 的 markdown 报告都出现这些列；老事件文件（没有 `tool_timing`）照常出报告，这些列标为缺失，不报错。
- [ ] `make check` 通过。

## Evidence required

- 测试输出。
- 主会话的真实运行：服务器 `dbt-receivables-aging-buckets` × 1（full）。事件里内置工具（Bash、Read）、MCP 工具、验证器内部工具都有 `duration_ms`；报告里有分位数表。命令和输出贴到 Comments。

## Stop conditions

- MCP 工具或子 agent 里的工具没有 `duration_ms`。
- 必须改 `tests/acceptance/` 才能做下去。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
