# 134 — `evals dab --trace` 的开发集档 trace 也带工具入参和模型文字

Status: in-review
Blocked by: 121 (done)
Spec: ../spec.md
Claimed by: claude (opus-5.5)
Branch: worktree-134-dab-trace-content

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (no data-agent model calls)
- Outcome: —

## Comments

- 2026-11-24（工单 agent 最终回复，主会话照抄）：

- **Change:** `evals/dab.py:542-543,554` now sets `recorder = log if export is None else export.observing(log)` and hands `recorder` to `record_message`. This is the same as `src/data_agent/agent.py:2343`. When the export is off, or `start` failed and set `export = None` (`:512`), messages still go straight to `log`. The held-out tier is unchanged, because `RunExport.observing` returns `log` itself there. `tracing.py`, `trace_relay.py` and `tests/acceptance/` are not touched.
- **Tests** (`tests/test_dab_trace.py`):
  - `test_a_traced_run_gives_the_export_tool_inputs_and_texts` uses a stand-in export that wraps the log in the real `ObservedLog`. It checks that `observe` receives exactly one `text` ("Looking at prices first.") and one `tool_start` (`call-1`, `{"sql": "SELECT 1"}`), and that the event file has them too.
    - It fails before the fix. I put the `record_message(log, …)` line back and got `1 failed, 2 passed`, then restored the line.
  - `test_an_untraced_run_records_messages_into_its_event_log` checks that every `record_message` call gets an `EventLog`, and that `text` and `tool_start` reach `events.jsonl`.
  - Result: `uv run pytest tests/test_dab_trace.py -q` gives `3 passed`.
- **`make check`** passed (exit 0) on the final commit:
  - ruff and format: OK
  - basedpyright: `0 errors, 0 warnings, 0 notes`
  - pytest: `2864 passed, 2 skipped, 79 deselected in 696.61s`
  - web: `Test Files 30 passed`, `Tests 442 passed`
  - The acceptance tests are unchanged and pass.
- **Real run:** not done. The ticket doesn't require one, since DAB is no longer run (ADR-0005 Update 2026-11-22).
- **Review, `da-review main`:** no findings. Codex couldn't run the tests in its read-only sandbox (no writable temp directory), so its verdict comes from reading the code.
- **Review, `eng:code-review main`:**
  - **Spec:** no blocking findings. It confirmed the change stays within Authority, that the `export = None` path is handled, and that the new test fails before the fix.
  - **Standards:** no hard violations. Fixed in `0b66416`:
    - the stand-in's copy of `ObservedLog` now uses the real class;
    - the class-level registry became a closure factory;
    - the untraced test now also checks the event file;
    - the `dict(observed)` that dropped duplicate events became list assertions.
  - **Not fixed:**
    - The one-line `recorder = …` is duplicated between `agent.py` and `dab.py`. There are only two places, and CODING_STANDARDS.md asks for plain, explicit code, so it stays.
    - The `result_message()` builder is duplicated across test files. That spread existed before this ticket and is outside its scope.
