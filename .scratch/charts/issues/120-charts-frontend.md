# 120 — 答案图表：前端（图表组件，接入对话页、运行页、在线站点和时间线）

Status: ready-for-agent
Blocked by: —（119 只挡合并：spec 定组件可以先按事件样例开工）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

一个通用的图表组件 `Chart`（recharts）画 `display_chart` 返回的 5 种图（`bar` 含堆叠、`line`、`area`、`scatter`、`pie`）；对话页、运行页、在线站点的录播对话和运行页都把主 agent 成功的 `display_chart` 调用画在答案文字下面，按调用顺序；过程时间线里这次调用是"图表"步骤；明暗主题各有 6 个可读的系列色。118 合入的 `web/src/chart/acceptance/` 两个文件里的 `test.fails`（共 42 个）只删 `.fails` 后全部通过。

## Authority

- May edit: `web/src/chart/`（`Chart.tsx`、`model.ts` 的实现；验收测试以外；118 定的类型和 `ChartProps` 不改）；`web/src/index.css`（只加 `--chart-series-1` 到 `--chart-series-6`，三个主题块都加）；`web/src/run-page/`（`tools.ts` 的 `ToolKind` 加 `'chart'` 及其标签、名字、颜色；`Side.tsx` 的答案区；时间线和详情里图表步骤的显示）；`web/src/chat-page/`（答案区）；`web/src/site/`（只在站点页面需要跟着传参时）；`web/src/chat-view/`、`web/src/run-view/`（验收测试以外，只为页面需要加字段或导出，不改、不删现有字段）；对应的新 vitest 单元测试
- Must not edit: 验收测试的内容（`web/src/**/acceptance/`、`tests/acceptance/`、`tests/fixtures/`，含 `tests/fixtures/charts/run/`）；唯一例外是删掉 `chart-render.test.ts`、`answer-charts.test.ts` 里自己那些 `test.fails` 的 `.fails`（`test.fails.each` 变成 `test.each`），断言、夹具、输入一字不改；`web/package.json` 和 lockfile（recharts、jsdom 都已经在）；`src/`（后端是 119）、`evals/`（不改导出）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec `.scratch/charts/spec.md` "前端（Q5、Q6）"、"Testing Decisions" 的前端一条、"工单"表 120 行；Out of Scope：nao 的另外 10 种图、双 y 轴、图表导出和编辑。
- 提前开工：spec 定组件可以先按事件样例做。现在就照已合入的金样 `tests/fixtures/charts/run/`（`events.jsonl`、`meta.json`）做和测；合并要等 119 合入，并由主会话按 118 Comments 末尾的"Confirmation for the main session before merging 119"核对真实 `tool_end` 的形状和金样一致。
- 契约（工单 118 Comments 的 "Contracts fixed in tests, fixture and types" 第 7 条，`web/src/chart/Chart.tsx` 和 `model.ts` 的文件头）：
  - 类型 `ChartType`、`ChartSeries`、`ChartSpec`、`ChartRow`、`ChartPayload`；`AnswerChart { step, seq（tool_start 的 seq）, payload, kind, series, rows }`。`answerCharts(view)` 只取主 agent 成功、已结束、结果能解析成图的调用，按调用顺序；错误的、还开着的、解析不了的、验证子 agent 里的都不要。
  - `Chart({ payload, size? })`：根元素 `data-testid="chart"`、`data-chart-type`，显示标题；`react-dom/server` 下也能渲染（只出外壳）。给 `size` 时按固定尺寸画（jsdom 里 `ResponsiveContainer` 画不出图形，118 Comments 的 probe）；不给时怎么适应容器由本工单定。不开动画（每个系列 `isAnimationActive={false}`，照 `web/src/eval-page/AblationChart.tsx`）。系列 i 的颜色写 `var(--chart-series-{i % 6 + 1})`，饼图按扇区序号取色。堆叠的柱子同一个 x，不堆叠的并排。没有数据时显示标题和一行"没有数据"的说明，不画系列，不抛异常。
  - 位置：图在 `[data-testid="answer"]` 里、答案文字后面、按调用顺序，对话页（`Transcript`）、运行页（`RunPage`，现在还没有 `data-testid="answer"` 元素，要加）、站点的 `RecordedConversation` 和运行页都一样。错误的调用（金样的 `t-c3`）只在时间线里显示错误，不进答案区。
  - 时间线：`toolKind('mcp__data__display_chart') === 'chart'`，有自己的标签、名字、`--k-chart` 颜色（三个主题块都加）。
  - 配色：`--chart-series-1` 到 `-6` 写在 `index.css` 的三个主题块（浅色、系统暗色、`data-theme='dark'`），只用 `#hex` 或 `rgb(r, g, b)`，同一块里互不相同，两个暗色块取值一样，每个和 `--panel` 的对比度至少 3:1（WCAG 1.4.11）。
- 共用部分照 spec：配色、明暗主题、图例、悬停提示、数字格式、空数据和错误显示，照 `AblationChart.tsx` 的写法；recharts 3.x（`web/package.json` `^3.10.1`），API 照 3.x 文档。上游 nao 的前端渲染（`apps/frontend/src/components/chat-messages/`）只参考思路，不整段复制（spec Further Notes）；复制就按 AGENTS.md 保留版权头、登记 `NOTICE`。
- 参考实现：118 写过一份没提交的参考实现，`/Users/shenghuikevin/dev/AI/data-agent/.claude/worktrees/118-charts-acceptance-tests/runs/ref118/`（`web.patch`、`web-ref/`；只读，不在 git 里，worktree 清掉后可能不在）。可以看，照本工单的契约和现有界面重写，不直接套补丁。
- 和工单 117 的交叉：117 在同一个 `ToolKind` 里加 `'external'`。两张单可能并行，后合并的那张 rebase 时两种类别都保留。
- 留给人定、照最保守做法先做的（最终回复列进"待人定"）：图的默认高度和宽度、图例和悬停提示的样式、时间线标签文字（英文，如 `CHART` / `Chart`，现有标签都是英文）、详情面板里图表步骤显示参数还是同时画图、6 个系列色的具体取值。
- 截图：本地版用 `uv run data-agent web --runs-dir ...`，`runs-dir` 放金样的一份副本（在 worktree 的 `runs/t120/`，不进 git，布局照 `tests/fixtures/chat/`）；在线站点用站点构建：把金样导成 `SITE_DATA` 目录（布局见 `web/site-data.ts`，能用 `uv run python -m evals export --out runs/t120/site --run <金样副本>` 就用，导不出就照 `site-data.ts` 读的 `index.json`、`runs/<id>/` 手拼），`SITE_DATA=runs/t120/site pnpm --dir web build` 后 `pnpm --dir web preview` 打开。
- 前端工单：按 AGENTS.md，第 5 步只跑 `eng:code-review main`，不跑 `da-review`；第 7 步由主会话派一个上下文干净的 Opus 5.5 子 agent 只读审 PR。
- 只用 pnpm。

## Acceptance

- [ ] `chart-render.test.ts`、`answer-charts.test.ts` 的 42 个测试只删 `.fails` 后通过，其余一字未改
- [ ] 现有测试全绿（运行页、对话页、站点页面、`outline.test.ts` 的 `toolKind` 表照旧）
- [ ] `pnpm --dir web test`、`pnpm --dir web build`（含 `SITE_DATA` 指向金样导出的站点构建）通过
- [ ] `make check` 通过

## Evidence required

`pnpm --dir web test`（两个验收文件那几行）和 `make check` 的输出；站点构建命令和输出。用 Playwright 打开真实浏览器截图（存在 worktree 的 `runs/t120/`，路径写进最终回复）：对话页、运行页、在线站点（构建产物 `preview`）的录播对话和运行页，答案文字下面 5 张图、没有 `t-c3`；时间线里图表步骤和 `t-c3` 的错误；浅色和暗色主题各一张。真实运行（网页对话问两三个适合画图的问题、导出到站点）由主会话在 119 合入后按 spec "验收"做。

## Stop conditions

- 要改验收测试、金样、118 定的类型和 props，或 `src/` 才做得到。
- 119 的真实 `tool_end` 和金样不一样（由主会话核对；这时退回 119，不改金样）。
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
