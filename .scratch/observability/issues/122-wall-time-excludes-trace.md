# 122 — 运行页墙钟和回放时间线不算 trace 导出的时间

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: claude (opus 5.5)
Branch: worktree-122-wall-time-excludes-trace

## Goal

运行页的墙钟（`RunSummary.durationMs`）和回放时间线在运行结束处停下，不把 trace 导出的时间算进去：`trace`、`trace_export_error` 这类在中转停止后才写的事件不拉长运行时长。

## Authority

- May edit: `web/src/run-view/model.ts`、同目录的单元测试（新加或改非验收测试）
- Must not edit: `web/src/**/acceptance/`（已合入的验收测试）、后端 `src/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- 工单 114 发现的遗漏（`.scratch/observability/issues/114-langfuse-link.md` Comments "Follow-up"），人 2026-11-23 定"开工单修"。
- 现状：`summarize`（`web/src/run-view/model.ts:517`，`const end = events.at(-1)`）取最后一个事件作运行结束；`eventTimes`（`:316`）给回放每个事件算时间。
- 后端写事件的真实顺序（主会话 2026-11-24 对照代码核实，原版本照 114 的造数写成 `trace` 在 `result` 之后，是错的）：CLI 退出后先写 `answer`，再调 `export.finish`（`src/data_agent/agent.py:2418-2423`；停中转、等排空，`drain_timeout_s` 默认 10 s，`src/data_agent/trace_relay.py:295`），写出 `trace_export_error`（0 到多条）和 `trace`，失败时改为一条 `error`（`source: "trace"`，`src/data_agent/tracing.py:395-407`），最后才写 `result`。所以 `result` 的 `ts` 已经包含导出等待；后端这个顺序是有意的（`result` 是读者认的文件末尾，`web/runs.py`），不改。
- 做法（只改 `model.ts`）：导出类事件 = `trace`、`trace_export_error`、`source` 为 `"trace"` 的 `error`，写成一个常量集合或判断函数。运行里有导出类事件时，运行结束取第一个导出类事件之前那个事件的时间（导出开始的时刻）；没有时照旧取最后一个事件。墙钟用这个结束时刻；回放里导出类事件和其后的 `result` 照常出现（114 的链接靠 `trace` 显示），时间钳到运行结束。也要兼容旧造数里 `trace` 在 `result` 之后的顺序。
- 运行还在跑（没有 `result`）时的行为不变。
- 对话页每轮的时长如果也用同一个函数，一并生效；不另改对话页组件。

## Acceptance

- [ ] 单元测试（修复前会失败）：真实顺序 `… answer, trace(+10 s), result(+10 s)`，`durationMs` 等于到 `answer` 为止的时长
- [ ] 单元测试：真实顺序里多一条 `trace_export_error`；以及失败路径 `answer, error(source: trace, +10 s), result`，墙钟同样不含等待
- [ ] 单元测试：旧顺序 `result, trace(+10 s)` 同样不拉长墙钟
- [ ] 单元测试：回放里 `trace` 和 `result` 仍然出现，时间不晚于运行结束，114 的链接在回放到终点时出现
- [ ] 单元测试：没有导出类事件的运行、还在跑（没有 `result`）的运行、运行结束那个事件没有 `ts` 时，行为和改前一致
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

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: —

## Comments

- 2026-11-24（工单 agent 最终回复，主会话照抄）：

- **2026-11-24 (claude opus 5.5, branch `worktree-122-wall-time-excludes-trace`, commit `11a9bf5`)**
  - **What I did:** `web/src/run-view/model.ts` now has a constant `EXPORT_EVENTS = {trace, trace_export_error}` and a function `runEnd` (the last event that isn't an export event). `summarize` takes the run's end from it. `eventTimes` caps each export event's time at the run's end. Five new unit tests are in `model.test.ts` (describe `export events`). Three of them failed before the change and pass after.
  - **Checks:**
    - `pnpm --dir web exec vitest run src/run-view`: 119 passed.
    - `make check` exited 0 (log in `runs/t122/check.log`): ruff "All checks passed!", basedpyright 0 errors, pytest 2850 passed and 2 skipped, web vitest 30 files and 447 tests passed.
    - The merged acceptance tests have no diff.
  - **Before/after on made-up data:** `runs/t122/make_data.py`, a copy of the t114 script with the `trace` event moved to 10 s after `result`.
    - `runs/t122/compare.ts`: wall time 26948 ms before, 16948 ms after. The run without `trace` is 16948 ms both times.
    - Browser at port 8822: run page shows "wall time 17 s" for both runs, the timeline ends at 0:16, and the "Open in Langfuse" link shows. Screenshot: `runs/t122/run-page-after.png`.
  - **Why it stops here (eng:code-review Spec axis, checked against the code):**
    - `src/data_agent/agent.py:2418-2423` runs `export.finish` (stops the relay and waits for it to drain), writes `trace_export_error` and `trace`, and only then writes `result`. The code comment there reads "Before `result`, which readers take as the event file's end". It is the only writer of these events.
    - So `result`'s `ts` already includes the drain wait. Since `runEnd` picks `result`, real runs keep the stretched wall time and replay timeline.
    - The ticket's Context and ticket 114's data put `trace` after `result`. That order exists only in the acceptance fixture and the made-up screenshot data. So the tests and the screenshot above show only the made-up order and are not evidence for real runs.
    - One new test, "an export event before the result keeps its own, earlier time", treats the real-order stretch as correct.
    - No real run with a `trace` event exists under `runs/` in the main checkout or any worktree, so the real gap is unmeasured. The 10 s is the `drain_timeout_s` default, not a measurement.
  - **Decision needed, the approach:**
    - **(a) Inside `model.ts` (Authority allows it):** end the run at the last event before the trailing export events, and pull `result` back to that time in replay. This moves the time of an event that isn't an export event, and the run then ends at `answer` or whatever came before the export.
    - **(b) In the backend (outside Authority):** record the run's end before `export.finish`, for example a field on `result`.
    - **Recommendation:** (b), since it fixes the cause. If you choose (a), the tests must cover both orders, a run with no `result`, and a run-end event with no `ts`.
  - **Smaller review findings, not fixed until the approach is decided:**
    - The run-end lookup is written twice (`model.ts:328-330` and `:534-535`).
    - When the run-end event has no `ts`, the wall time is 0 but replay still ends late.
    - `clock[3]` in the test is a bare magic index.
    - The header comment has a doubled "and".
    - The test imports `run-page/replay` (there is precedent for this).
    - `trace.ts` repeats the `'trace'` string literal.
  - **Codex `da-review` not run:** this is a web-only ticket, so AGENTS.md skips it.
  - **Leftovers:** `pnpm --dir web build:local` wrote `src/data_agent/web/static/`, which git ignores. Nothing was pushed.

- 2026-11-24（主会话）退回：原因——工单前提错了（主会话写工单时照 114 的造数，没对照 `agent.py` 的写事件顺序）。Context 和 Acceptance 已按真实顺序改写；做法仍只改 `model.ts`，Authority 不变。在原分支 `worktree-122-wall-time-excludes-trace` 上接着做，顺手处理上一轮审查的小问题（运行结束查找写了两遍、结束事件没有 `ts` 时回放仍偏晚、`clock[3]` 魔数、注释里重复的 "and"、`'trace'` 字面量重复）；"export event before the result keeps its own, earlier time" 那条测试和新做法矛盾，改掉。
