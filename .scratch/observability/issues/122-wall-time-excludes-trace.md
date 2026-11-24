# 122 — 运行页墙钟和回放时间线不算 trace 导出的时间

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

运行页的墙钟（`RunSummary.durationMs`）和回放时间线在运行结束处停下，不把 trace 导出的时间算进去：`trace`、`trace_export_error` 这类在中转停止后才写的事件不拉长运行时长。

## Authority

- May edit: `web/src/run-view/model.ts`、同目录的单元测试（新加或改非验收测试）
- Must not edit: `web/src/**/acceptance/`（已合入的验收测试）、后端 `src/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- 工单 114 发现的遗漏（`.scratch/observability/issues/114-langfuse-link.md` Comments "Follow-up"），人 2026-11-23 定"开工单修"。
- 现状：`summarize`（`web/src/run-view/model.ts:517`，`const end = events.at(-1)`）取最后一个事件作运行结束；`eventTimes`（`:316`）给回放每个事件算时间。112 起 `trace` 事件（以及失败时的 `trace_export_error`）在中转停止、父 span 导出之后才写（`src/data_agent/tracing.py` `RunExport.finish`），中转 `drain_timeout_s` 默认 10 s（`src/data_agent/trace_relay.py:295`），所以墙钟最多多出约 10 s。114 的截图：同样的事件，有 `trace` 18 s、没有 17 s。
- 做法：运行结束取最后一个不是导出类事件（`trace`、`trace_export_error`）的事件；把"导出类事件"写成一个常量集合。回放时这些事件仍要出现（114 的链接靠它显示），只是不推后时间线的终点：它们的回放时间不晚于运行结束（例如钳到运行结束时刻），自己定，写进最终回复。
- 运行还在跑（没有 `result`）时的行为不变。
- 对话页每轮的时长如果也用同一个函数，一并生效；不另改对话页组件。

## Acceptance

- [ ] 单元测试（修复前会失败）：同一组事件，末尾多一个 `trace` 事件（时间晚 10 s），`durationMs` 不变
- [ ] 单元测试：末尾是 `trace_export_error` 加 `trace` 时同样不变
- [ ] 单元测试：回放里 `trace` 事件仍然出现，114 的链接在回放到运行结束时出现（不晚于终点）
- [ ] 已合入的验收测试（含 `web/src/run-view/acceptance/trace-link.test.ts`）一字不改、全部通过
- [ ] `make check` passes

## Evidence required

`pnpm --dir web test` 输出；`make check`；一张截图或 DOM 检查：用一份带 `trace` 事件的运行（可用 `runs/t114/` 的造数脚本做法）对比改前改后的墙钟。

## Stop conditions

- 需要改已合入的验收测试才能做。
- 第二轮审查还不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
