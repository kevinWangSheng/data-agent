# 134 — `evals dab --trace` 的开发集档 trace 也带工具入参和模型文字

Status: ready-for-agent
Blocked by: 121
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`evals dab --trace` 导出的 trace 和 `data-agent run` 一样：开发集档有每次工具调用的入参和模型每一步的文字（工单 121 的做法），留出集档不变。

## Authority

- May edit: `evals/dab.py` 里把消息记进事件的那一处（`:552` 的 `record_message(log, message, tools)`）、对应的单元测试（`tests/test_dab*.py` 或新文件）
- Must not edit: `tests/acceptance/`、`src/data_agent/tracing.py`、`src/data_agent/trace_relay.py`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`web/`
- Server access: none
- Spend cap: none

## Context

- 来源：工单 121 的 Codex 第 1 轮 P2（`.scratch/observability/issues/121-trace-content-from-events.md` Comments），主会话对照代码核实：`evals/dab.py:552` 直接把 `log` 交给 `record_message`，没有经过导出的观察层，所以 DAB 的 trace 拿不到入参和文字。
- 121 的做法（121 合入后以 main 为准，下面的行号取自 121 分支 `e3fcbb1`，开工前重新核对）：`src/data_agent/agent.py:2343` 是 `recorder = log if export is None else export.observing(log)`，再把 `recorder` 交给 `record_message`；`RunExport.observing` 在 `src/data_agent/tracing.py:431`。
- 做法：`evals/dab.py` 照 `agent.py` 同样接上（导出关闭时仍直接用 `log`）。

## Acceptance

- [ ] 单元测试（修复前会失败）：开发集档导出时，DAB 运行的 `tool_start` 入参和 `text` 交给了导出（用替身导出，断言 `observing` 收到的事件）
- [ ] 单元测试：导出关闭时，`record_message` 收到的仍是原来的 `log`
- [ ] 已合入的验收测试一字不改、全部通过
- [ ] `make check` passes

## Evidence required

`make check` 输出；单元测试输出。真实运行不需要（DAB 不再跑，ADR-0005 Update 2026-11-22）。

## Stop conditions

- 需要改 `tracing.py` 或 `trace_relay.py` 才能接上。
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
