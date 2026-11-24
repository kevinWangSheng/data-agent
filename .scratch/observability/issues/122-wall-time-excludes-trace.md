# 122 — 运行页墙钟和回放时间线不算 trace 导出的时间

Status: ready-for-agent
Blocked by: 121
Spec: ../spec.md
Claimed by: claude (opus 5.5)
Branch: worktree-122-wall-time-excludes-trace

## Goal

运行页的墙钟（`RunSummary.durationMs`）和回放时间线在运行结束处停下，不把 trace 导出的时间算进去：`trace`、`trace_export_error` 这类在中转停止后才写的事件不拉长运行时长。

## Authority

- 人 2026-11-24 定：扩到后端（PR #130 第 7 步审查发现只改网页会把截止时间到点的运行少算几分钟，见 Comments）。
- May edit:
  - `src/data_agent/agent.py`：只改写 `result` 的那一处（`:2418-2430`，`export.finish` 前后）
  - `evals/dab.py`：只改写 `result` 的那一处（`:575-581`）
  - `src/data_agent/web/runs.py`：`duration_ms` 的算法（`:173`）
  - `web/src/run-view/model.ts`
  - 对应的单元测试（`tests/test_agent*.py`、`tests/test_dab*.py`、`tests/test_web_runs*.py` 或新文件，`web/src/run-view/model.test.ts`）
- Must not edit: `tests/acceptance/`、`web/src/**/acceptance/`（已合入的验收测试）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- 工单 114 发现的遗漏（`.scratch/observability/issues/114-langfuse-link.md` Comments "Follow-up"），人 2026-11-23 定"开工单修"。
- 后端写事件的真实顺序（主会话 2026-11-24 对照代码核实）：
  - `agent.py`：CLI 退出后写 `answer`（`:2404`），再 `export.finish`（`:2418-2423`；停中转、等排空，`drain_timeout_s` 默认 10 s，`trace_relay.py:295`），写出 `trace_export_error`（0 到多条）和 `trace`，`finish` 失败时改为一条 `error`（`source: "trace"`，`tracing.py:395-407`），最后写 `result`（`:2424`）。`export.start` 失败时那条 `error`（`source: "trace"`）紧跟在 `task` 之后（`:2286-2289`）。
  - `evals/dab.py:577-581` 同样是先 `export.finish` 再写 `result`。
  - 截止时间到点、花费上限、轮数上限停下时，停下那一刻不写事件（`agent.py:2391-2399`），只有 `result` 带停下原因。所以"导出前最后一个事件"不是运行结束时刻（第 2 轮的做法在截止时间的运行上会少算整段未完成的工具调用，PR #130 审查）。
  - `src/data_agent/web/runs.py:173` 历史页的 `duration_ms` 用 `result.ts`，也包含导出等待；静态站的 `rowOf`（`web/src/data/source.ts:162-171`）取 `buildRunView` 的 `durationMs`，两边要一致。
- 做法：
  1. 后端：`result` 事件加一个字段 `ended_ts`（和 `ts` 同格式，`events.py:93` 的 `datetime.now(UTC).isoformat()`），在 `export.finish` 之前取；没有导出时也写（就是写 `result` 前的时刻）。两个写 `result` 的地方都加。只加字段，`result` 其余字段和事件顺序不变。
  2. `runs.py`：`duration_ms` 用 `result.ended_ts`，没有时退回 `result.ts`。
  3. `model.ts`：有 `result.ended_ts` 时，墙钟到它为止，回放里晚于它的事件时间钳到它（`trace` 和 `result` 照常出现，114 的链接靠 `trace` 显示）；没有这个字段的旧文件，行为和 main 一致（取最后一个事件）。第 2 轮"导出前那个事件"的推断去掉。
- 运行还在跑（没有 `result`）时的行为不变。对话页每轮的时长用同一个函数，一并生效；不另改对话页组件。

## Acceptance

- [ ] 单元测试（修复前会失败）：`agent.py` 的运行在开导出时，`result.ended_ts` 早于导出类事件的 `ts`、早于 `result.ts`；不开导出时也有 `ended_ts`
- [ ] 单元测试：`evals/dab.py` 写的 `result` 同样带 `ended_ts`
- [ ] 单元测试：`runs.py` 的 `duration_ms` 用 `ended_ts`；没有时用 `result.ts`
- [ ] 单元测试（修复前会失败）：`… answer, trace(+10 s), result(+10 s, ended_ts = answer 之后)`，`durationMs` 到 `ended_ts`；截止时间停下的运行（`tool_start` 之后很久才 `ended_ts`）墙钟到 `ended_ts`，不少算
- [ ] 单元测试：回放里 `trace` 和 `result` 仍然出现，时间不晚于 `ended_ts`，114 的链接在回放到终点时出现
- [ ] 单元测试：没有 `ended_ts` 的旧文件、还在跑的运行，行为和 main 一致；同一份事件，`rowOf` 和 `runs.py` 给出的时长一致
- [ ] 已合入的验收测试一字不改、全部通过
- [ ] `make check` passes

## Evidence required

`pnpm --dir web test` 输出；`make check`；一张截图或 DOM 检查：用一份带 `trace` 事件的运行（可用 `runs/t114/` 的造数脚本做法）对比改前改后的墙钟。

## Stop conditions

- 需要改已合入的验收测试才能做。
- 第二轮审查还不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 2
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

- 2026-11-24（工单 agent 退回第 1 轮最终回复，主会话照抄）：

## Comments
- **2026-11-24 (claude opus 5.5, round 2 after the return, commits `43c48f2`, `e9d408b`, `0c70651` on top of `11a9bf5`)**
  - **What changed (only `web/src/run-view/model.ts` and `model.test.ts`):**
    - An export event is a `trace`, a `trace_export_error`, or an `error` whose `source` is `"trace"`. One function, `runEndIndex`, finds where the run ended. `summarize` and `eventTimes` both use it, so the lookup is no longer written twice.
    - Export events only count when they sit in the block at the end of the file, made of export events and `result`. The run then ends at the event just before the first of them: `answer, trace, result` ends at `answer`, and the old `result, trace` order ends at `result`. Without such a block, the run ends at its last event as before.
    - In replay, every event after the run's end has its time capped at the run's end. If the end event has no `ts`, those events get no time and add no wait.
  - **Tests:** 10 cases in describe `export events`.
    - They cover the real order; a `trace_export_error` before the `trace`; the failure path `error` (source trace); an error from another source; the old order; replay of both orders (`trace` and `result` both appear, no time is later than the run's end, the 114 link shows at the end); a run with no export events; a running run; a run-end event with no `ts`; and an export that failed to start.
    - The four real-order tests and the no-`ts` test failed before the change. The export-start test failed against `43c48f2`.
    - The round-1 test "an export event before the result keeps its own, earlier time" is removed, and the bare `clock[3]` index is gone.
    - The merged acceptance tests have no diff: `git diff main...HEAD --stat` lists only `model.ts` and `model.test.ts`.
  - **Checks:**
    - `make check` on `0c70651` exited 0: ruff "All checks passed!", basedpyright 0 errors, pytest 2850 passed and 2 skipped, web vitest 30 files and 452 tests passed. Log: `runs/t122/check-r2c.log`.
  - **Before/after on real-order data:**
    - `runs/t122/make_data_r2.py` takes the query-shop fixture and builds four runs. In three the export takes 10 s and `result` comes after it: one with a `trace`, one whose export failed (`error`, source trace), one with no export. The fourth has an `error` (source trace) right after `task`, as when the export fails to start.
    - `node --experimental-strip-types runs/t122/compare_r2.ts`, wall time in ms: trace run 26948 → 16715; failed-export run 26948 → 16715; no-export run 16948 → 16948; export-start-failure run 16948 → 16948.
    - Browser on the bundle built from final HEAD (port 8823): all four runs show "wall time 17 s". The trace run shows the Langfuse link. Screenshot: `runs/t122/run-page-after-r2.png`.
    - The 27 s "before" figure comes from `compare_r2.ts` only. I did not build a pre-fix bundle to look at it in the browser.
    - The run now ends at the last `text` (16.7 s), not at the 16.9 s that `result` had with no export. That is how the ticket defines the end: the event before the export starts.
  - **Reviews:**
    - `da-review` was not run, because this ticket is web-only.
    - `eng:code-review main` ran on `43c48f2` (Standards and Spec sub-agents). `e9d408b` and `0c70651` were not re-reviewed.
  - **Review findings fixed:**
    - Spec: `src/data_agent/agent.py:2288` writes `error` (source trace) right after `task` when `export.start` fails. With `43c48f2`, such a run ended at `task`: wall time about 0, and the whole replay at one moment. Fixed in `e9d408b`, with a test.
    - Spec and Standards: the `trace.ts` edit was outside Authority. Reverted in `0c70651`.
    - Standards: the test comment named the wrong file for `RunExport.finish`. Corrected.
  - **Review findings not fixed, with reasons:**
    - **The repeated `'trace'` literal (asked for in the return comment):** fixing it means editing `trace.ts`, which is outside Authority, so I left it. If you want it, add `export const TRACE_EVENT = 'trace'` to `model.ts`, use it in `isExportEvent`, and change `trace.ts:4` to import it and `trace.ts:13` to compare against it.
    - **Run-end event without `ts`:** the case with no export events matches the code before the change (wall time 0, times as before). With export events and an `answer` without `ts`, the wall time is 0 and the later events get no time. Before the change the wall time was 25 s. I followed the return comment, which asked that replay no longer end late in this case.
    - **`'trace_export_error'`, and `'trace'` used as an error `source`, are still bare strings:** each appears once, in one function.
    - **The test imports `run-page/replay` and `./trace`:** Acceptance asks for the 114 link to show when replay reaches the end, which needs both.
    - **Two calls to `clockOf`:** they were already there before this ticket.
    - **A first export event at index 0 falls back to the last event:** `version` is always the first event.
  - **Known limitations:**
    - On the chat page, the roughly 10 s export wait moves into the gap before the next turn. At 1x with idle gaps not skipped, replay still waits there. The ticket only covers each turn's duration.
    - If the export fails to start and the CLI then writes nothing before `result` (`version, task, error(trace), result`), the run still ends at `task`. That needs both failures at once.
  - **Leftovers:** `build:local` rewrote `src/data_agent/web/static/`, which git ignores. The evidence scripts and logs are in `runs/t122/`.

- 2026-11-24（主会话）退回第 2 轮：原因——PR #130 第 7 步审查（Opus 子 agent，只读）的第 1 条属实（主会话对照 `agent.py:2391-2399` 核实）：截止时间等停下时不写事件，"导出前那个事件"会把墙钟少算几分钟，比 main 更糟；第 2 条也属实：历史页 `runs.py:173` 仍含导出等待。根因是事件文件不记运行结束时刻，只改网页修不了；这是主会话第 1 次退回改写工单时选错了做法（agent 第 1 轮已建议改后端）。人 2026-11-24 定扩到后端，Authority、Context、Acceptance 已改。等 121 合入后在原分支上接着做（先把 main 合进来）；改了后端，第 5 步要跑 `da-review`，第 7 步等 Codex GitHub app。PR #130 不关，在同一分支上继续推。审查的 3 条小问题（结束事件没有 `ts`、回放中 export-start `error` 让时间回退、测试注释断行）随新做法一并消失或顺手修。
