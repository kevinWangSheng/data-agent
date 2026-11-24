# 117 — 前端：外部 MCP 工具的显示和类别

Status: ready-for-agent
Blocked by: 116
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

运行页和对话页把 `mcp__<server>__<tool>`（`data` 以外的 server）显示成 "server · tool"，归入新类别 `'external'`，有自己的标签、名字和颜色；结果按文本显示，不解析成表。`mcp__data__*` 的显示和表格解析不变。115 合入的 `web/src/run-page/acceptance/external-tools.test.ts` 里 7 个 `test.fails` 只删 `.fails` 后全部通过，守门测试照旧通过。

## Authority

- May edit: `web/src/run-page/tools.ts`（`ToolKind` 加 `'external'`，`shortTool`、`toolKind`、`KIND_TAG`、`KIND_NAME`）；`web/src/index.css`（只加 `--k-external`，三个主题块都加）；`web/src/run-page/`、`web/src/chat-page/` 里因为新类别要跟着改的地方（只为让外部工具显示对）；`web/src/run-view/model.ts`（只在守门测试显示表格解析会碰到外部工具时，保持"只解析 `mcp__data__`"）；对应的新 vitest 单元测试
- Must not edit: 验收测试的内容（`web/src/**/acceptance/`、`tests/acceptance/`、`tests/fixtures/`）；唯一例外是删掉 `external-tools.test.ts` 里自己那 7 个 `test.fails` 的 `.fails`，断言、夹具、输入一字不改，守门测试不动；`src/`（后端是 116）、`evals/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec `.scratch/mcp/spec.md` "网页（Q9）"、"Testing Decisions" 的网页一条、"工单"表 117 行；Out of Scope：外部工具结果的表格解析和图表。
- 契约（工单 115 Context 第 7 项、Comments 的契约第 7 条，写在 `external-tools.test.ts` 文件头）：`toolKind('mcp__mongo__find') === 'external'`；`shortTool('mcp__mongo__find') === 'mongo · find'`（U+00B7，两侧各一个空格；工具名里的 `-`、`_` 原样保留，如 `mongo · list-collections`）；`KIND_TAG.external`、`KIND_NAME.external` 非空且和 `other` 的不同；外部工具的结果是文本，看起来像表也不解析（`web/src/run-view/model.ts:183` 的 `DATA_TOOL_PREFIX`）。
- 标签文字由本工单定（115 "spec 里的解读"第 5 条）：spec 写的是"外部工具"，界面现有标签都是英文（`tools.ts` 的 `KIND_TAG`、`KIND_NAME`）。照最保守、和现有界面一致的做法：标签如 `EXT`，名字如 `External MCP tool`；颜色 `--k-external` 取一个和现有 `--k-*` 区分得开的颜色，明暗主题都加（`Tag` 和时间轴都用 `var(--k-${kind})`，`web/src/run-page/ui.tsx:34`、`TimeAxis.tsx:88`）。最终回复把文字和颜色列进"待人定"。
- 外部工具不算查询（`outline.ts` 的 `isSql`）、不算改动（`isChange`）；对话页的卡片不标库名（库名只属于数据查询）。都照现状，不另加。
- 和工单 120 的交叉：120 在同一个 `ToolKind` 里加 `'chart'`（`mcp__data__display_chart`）。两张单可能并行，后合并的那张 rebase 时两种类别都保留。
- 截图用的事件：仓库里没有带外部工具的网页夹具，不改夹具。在 worktree 的 `runs/t117/`（不进 git）复制一份现有夹具运行（如 `tests/fixtures/web/real/query-shop/`），插一对 `mcp__mongo__find` 的 `tool_start`、`tool_end`（结果是表格样子的文本，照验收测试的 `TABLE_TEXT`），本地版用 `uv run data-agent web --runs-dir ...` 起（对话布局照 `tests/fixtures/chat/`），或站点用 `SITE_DATA=<导出目录> pnpm --dir web dev`（布局见 `web/site-data.ts`）。
- 前端工单：按 AGENTS.md，第 5 步只跑 `eng:code-review main`，不跑 `da-review`；第 7 步由主会话派一个上下文干净的 Opus 5.5 子 agent 只读审 PR。
- 只用 pnpm。

## Acceptance

- [ ] `external-tools.test.ts` 的 7 个测试只删 `.fails` 后通过，守门测试照旧通过，其余一字未改
- [ ] 现有测试全绿（`outline.test.ts` 的 `toolKind` 表、`tool-views.test.ts` 的 `KIND_NAME` 断言照旧）
- [ ] `pnpm --dir web test`、`pnpm --dir web build` 通过
- [ ] `make check` 通过

## Evidence required

`pnpm --dir web test`（`external-tools.test.ts` 那几行）和 `make check` 的输出。用 Playwright 打开真实浏览器截图（存在 worktree 的 `runs/t117/`，路径写进最终回复）：运行页时间线和详情里外部工具显示 "mongo · find"、新标签和颜色，结果是文本不是表；对话页的卡片同样显示 "mongo · find"；明暗主题各一张。真实 MongoDB 运行的显示由主会话在 spec "验收"那次网页对话里核对。

## Stop conditions

- 要改验收测试、事件格式或 `src/` 才做得到。
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
