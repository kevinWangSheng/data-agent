# 114 — 前端：运行页和对话页的"在 Langfuse 打开"链接

Status: ready-for-agent
Blocked by: 112
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
