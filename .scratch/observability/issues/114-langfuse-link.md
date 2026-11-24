# 114 — 前端：运行页和对话页的"在 Langfuse 打开"链接

Status: in-review
Blocked by: 112
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5)
Branch: worktree-114-langfuse-link

## Goal

一次运行的事件里有 `trace` 事件时，运行页和对话页（每一轮）显示一个"在 Langfuse 打开"的链接，指向这条 trace；没有 `trace` 事件（导出关闭）时不显示。`traceLink` 实现，111 合入的 `web/src/run-view/acceptance/trace-link.test.ts` 三个 `test.fails` 只删 `.fails` 后全部通过。

## Authority

- May edit: `web/src/run-view/trace.ts`（实现 `traceLink`，类型不改）；`web/src/run-page/`（只动运行页摘要区，加链接）；`web/src/chat-page/`（只动每轮的元信息行，加链接）；`web/src/chat-view/`（验收测试以外；需要时给轮次的视图加一个字段带上链接，不改、不删现有字段）；`web/src/site/`（只在站点页面需要跟着传参时）；对应的新 vitest 单元测试
- Must not edit: 验收测试的内容（`web/src/**/acceptance/`、`tests/acceptance/`、`tests/fixtures/`）；唯一例外是删掉 `trace-link.test.ts` 里自己那三个 `test.fails` 的 `.fails`，断言、夹具、输入一字不改；`src/`（`trace` 事件由 112 写）、`evals/`（导出行为）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec `.scratch/observability/spec.md` "网页（Q11）"、"Testing Decisions" 的网页一条、"工单"表 114 行；Out of Scope："网页里直接嵌 Langfuse 的界面（只给链接）"。
- 事件契约（工单 111 Context 第 3 项）：`trace` 事件字段 `trace_id`（32 位十六进制）、`url`、`forwarded_spans`、`forwarded_llm_requests`，中转停下后写一次，和 `result` 的先后不固定。`traceLink` 返回 `{ traceId: trace_id, url }`，没有时 `null`。网页没有 DOM 测试库，所以接缝是这个纯函数；链接的渲染由本工单自己的单元测试（照 `web/src/chart/acceptance/answer-charts.test.ts` 用 `react-dom/server` 出 markup 的做法）和截图证明。
- 一次对话的每一轮是一次运行、一条 trace：对话页每轮用这一轮自己的事件（验收测试第 3 条）。
- 放在哪里、写什么（spec 没钉，照最保守、和现有界面一致的做法，最终回复列进"待人定"）：
  - 对话页：`web/src/chat-page/Turn.tsx` 的 `TurnMeta`，"Full run view →" 后面；运行页：`web/src/run-page/Side.tsx` 摘要区（指标和 tokens 那一块）。
  - 文字用英文（现有标签都是英文），如 "Open in Langfuse ↗"；`target="_blank"`、`rel="noreferrer"`，样式用现有的 `link` 类。
  - 运行中还没有 `trace` 事件时不显示；`trace_export_error` 不显示（spec 没要求）。
  - 运行页和站点共用组件，站点的运行页和录播对话里，事件有 `trace` 时也照常显示（不为站点另加分支）。导出是否保留 `trace` 事件、公开站点上要不要这个链接，不归本工单，列进"待人定"。
- 截图用的事件：仓库里没有带 `trace` 事件的网页夹具，不改夹具。在 worktree 的 `runs/t114/`（不进 git）复制一份现有夹具运行（如 `tests/fixtures/web/real/query-shop/`），末尾加一条 `trace` 事件（`url` 用 `https://langfuse.example/...` 这类假地址），再造一个两轮对话（第二轮的 `trace_id` 不同）；本地版用 `uv run data-agent web --runs-dir runs/t114/...` 起（布局照 `tests/fixtures/chat/`），站点用 `SITE_DATA=<导出目录> pnpm --dir web dev`（布局见 `web/site-data.ts`）。
- 前端工单：按 AGENTS.md，第 5 步只跑 `eng:code-review main`，不跑 `da-review`；第 7 步由主会话派一个上下文干净的 Opus 5.5 子 agent 只读审 PR。
- 只用 pnpm。

## Acceptance

- [ ] `trace-link.test.ts` 三个测试只删 `.fails` 后通过，其余一字未改
- [ ] 单元测试：运行页、对话页的 markup 在有 `trace` 事件时有指向 `url` 的链接（新标签页打开），没有时没有；两轮对话各自链到自己那一轮的 `url`
- [ ] 现有测试全绿（运行页、对话页、站点页面照常）
- [ ] `pnpm --dir web test`、`pnpm --dir web build` 通过
- [ ] `make check` 通过

## Evidence required

`pnpm --dir web test`（`trace-link.test.ts` 那几行）和 `make check` 的输出。用 Playwright 打开真实浏览器截图（存在 worktree 的 `runs/t114/`，路径写进最终回复）：运行页有链接、对话页两轮各有自己的链接、一个没有 `trace` 事件的运行没有链接。真的 Langfuse 能不能打开这条 trace 由主会话在 spec "验收"那次真实对话里核对。

## Stop conditions

- 要改验收测试、`trace` 事件的字段或 `src/` 才做得到。
- spec 自相矛盾，或缺一个需要人定、又没法按上面"最保守"做法先做的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
- 2026-11-23（工单 agent，提交 dde5099、e50bf93）：

**What changed**
- `web/src/run-view/trace.ts`: `traceLink` uses the last `trace` event, wherever it sits relative to `result`. It returns `null` when there is no such event, when `trace_id` or `url` is missing, or when the url doesn't start with `http(s)://`.
- `web/src/chat-view/model.ts`: `TurnView` gets a new field, `trace: TraceLink | null`, computed from that turn's own events. `RunView` is unchanged.
- Chat page: `chat-page/Turn.tsx` `TurnMeta` puts the link after "Full run view →".
- Run page: `run-page/Side.tsx` puts the link in the metrics block, below tokens. It shows even when there are no tokens.
- New `run-page/LangfuseLink.tsx`: an `<a className="link" target="_blank" rel="noreferrer">`.
- `run-page/RunPage.tsx` changes outside the summary area: it works out the link from the same revealed events as the view and passes it to `Side`. This is the only route, since `Side` has no access to the events.

**Evidence**
- `pnpm --dir web test` on HEAD `e50bf93`: 30 files, 442 tests passed. The `trace-link.test.ts` lines:
  ```
  ✓ src/run-view/acceptance/trace-link.test.ts > traceLink > a run with a trace event links to its trace
  ✓ src/run-view/acceptance/trace-link.test.ts > traceLink > a run without a trace event has no link
  ✓ src/run-view/acceptance/trace-link.test.ts > traceLink > each turn of a conversation links to its own turn's trace
  ```
- New `web/src/run-view/trace-link-markup.test.ts` (9 tests, rendered to markup with `react-dom/server`):
  - Run page: the link appears with the `trace` event after `result` and before it, and is absent without one.
  - Chat page, two turns: each turn's markup has its own url and not the other's.
  - A turn without a `trace` event, and a still-running turn, have no link.
  - A non-http url gives no link.
- `pnpm --dir web typecheck` and `pnpm --dir web build` both pass on HEAD.
- `make check` passes (log in `runs/t114/make-check.log`). It ran on `dde5099`, not on HEAD:
  - ruff: All checks passed. Formatting: 410 files already formatted.
  - basedpyright: 0 errors.
  - pytest: 2850 passed, 2 skipped. Web: 442 passed. exit=0.
  - `e50bf93` touches only `web/`, and I re-ran web typecheck, test and build on it.
- Screenshots, taken on HEAD with the local server (`data-agent web --runs-dir runs/t114/local`, data made by `runs/t114/make_data.py` from a copy of query-shop):
  - `runs/t114/run-page-with-trace.png`: the link in the run page summary.
  - `runs/t114/run-page-without-trace.png`: no link.
  - `runs/t114/chat-turn-1.png`: both turns of the conversation, each with its own link. A DOM check in the browser gave turn 1 → `…/0af76519…` and turn 2 → `…/4bf92f35…`.
  - The "RUNNING" chip in the sidebar comes from the hand-made data, not from this change.
- Replay in the browser: the link is there before Replay, gone mid-replay, and back once the replay reaches the `trace` event.
- Site pages (the site's run page and recorded conversation): no test or screenshot shows the link there. 未确认. They use the same components, so I expect it to show.

**Reviews**
- `eng:code-review main`, Standards: no hard violations.
  - Fixed: the revealed events were worked out twice in `RunPage.tsx`; the import order in `Turn.tsx`.
  - Not fixed: placement of the test file. It tests one feature across both pages, so it sits next to `trace.ts`.
- Spec: no missing or wrong requirements; Authority respected; the acceptance file has exactly three `.fails` removals. The reviewer flagged three small additions, all kept:
  - The url guard, so an href can never be `javascript:`. It can't hide a real link: `base_url` is also the OTLP endpoint (`tracing.py:157`), so a host without `http(s)://` would fail to export anything.
  - A `title` attribute showing the trace id.
  - The `RunPage` pass-through, which is required.
- No `da-review`, because this is a frontend ticket.

**Follow-up (an omission, not planned work)**
- `summarize` and `eventTimes` in `web/src/run-view/model.ts` end the run at its last event. The `trace` and `trace_export_error` events are written after the relay stops, so they stretch the wall time and the replay timeline.
- The relay's `drain_timeout_s` defaults to 10.0 (`trace_relay.py:295`), so the stretch could be up to about 10 s; that figure is the reviewer's estimate. The screenshots show it: 18 s with `trace`, 17 s without, on otherwise identical events.
- This has been on main since 112. The fix belongs in `model.ts`, outside this ticket's Authority, so it needs its own ticket.

**待人定**
1. Placement and wording: after "Full run view →" on the chat page, the run page summary below tokens, text "Open in Langfuse ↗".
2. `trace_export_error` isn't shown.
3. Whether the public site should show the link, and whether the export should keep `trace` events.
4. Keeping the url guard, which wasn't asked for.
