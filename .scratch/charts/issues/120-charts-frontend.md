# 120 — 答案图表：前端（图表组件，接入对话页、运行页、在线站点和时间线）

Status: ready-for-agent
Blocked by: —（119 只挡合并：spec 定组件可以先按事件样例开工）
Spec: ../spec.md
Claimed by: claude-code (Opus 5.5), worktree 120-charts-frontend
Branch: worktree-120-charts-frontend

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: —

## Comments

- 2026-11-23（工单 agent 最终回复，主会话照抄）：

**做了什么**
- **图表组件** `web/src/chart/Chart.tsx`：画 5 种图，直角坐标（bar/line/area）、scatter、pie 三条代码路径。
  - 不开动画；系列 i 用 `var(--chart-series-{i%6+1})`，饼图按扇区取色。
  - 2 个以上系列才有图例（饼图总有），图例文字用正文色，按系列顺序排。
  - 悬停提示照 `AblationChart` 的样式。
  - 没有数据时显示标题和 "No data to chart: the query returned no rows."，不画系列。
  - 不给 `size` 时，图在 260px 高的盒子里用 `ResponsiveContainer` 占满容器宽度。
- **挑图** `answerCharts` 和 `chartPayload`（`web/src/chart/model.ts`）：校验返回值的形状，不合格的不进答案区，并按 `tool_start` 的 seq 排序。
- **页面**：新加 `AnswerCharts.tsx`，接进 `Turn.tsx` 和 `Side.tsx`。运行页加了 `data-testid="answer"`，图在 `Clipped` 外面。
- **时间线**：`tools.ts` 加了 `'chart'` 类别（标签 `CHRT`，名字 `Answer chart`）。`index.css` 三个主题块都加了 `--k-chart` 和 `--chart-series-1..6`。
- **验收测试**：两个文件只删了 23 处 `.fails`，逐行比对过，其余没动。

**验证**
- `pnpm --dir web test`：Test Files 27 passed，389 passed | 10 expected fail（这 10 个是工单 117 的）。两个验收文件 42/42 ✓。
- `make check`（e914322）：ruff 通过，398 files already formatted，basedpyright 0 errors，pytest 2613 passed / 97 xfailed，web 389 passed，exit=0。输出在 `runs/t120/make-check-final.txt`。
- 普通构建 `pnpm --dir web build` 通过（`runs/t120/build-plain.txt`）。
- 站点构建：
  - 先导出：`uv run python -m evals export --manifest evals/manifest.json --out runs/t120/site --run runs/t120/featured/20261123T100100Z-c4a27f --conversation runs/t120/local/conversations/20261123T100000Z-ch4rt5`，结果 "export: 2 run(s), 1 conversation(s)"。
  - 再构建：`SITE_DATA=$PWD/runs/t120/site pnpm --dir web build`，结果 "✓ built in 522ms"（`runs/t120/site-build.txt`）。
  - 导出不接受重复的 run id，所以金样同时当录播对话的那一轮和精选运行时，精选运行用的是改了 id 的副本。
- 截图在 `runs/t120/`，都是最终提交上用真实浏览器拍的。每一页都用 DOM 查过：只有 1 个 `answer`，里面按顺序是 bar、line、area、scatter、pie；没有 `t-c3` 的图，`t-c3` 的错误只在时间线和详情里。
  - 本地对话页：`chat-light.png`、`chat-dark.png`
  - 本地运行页（选中 `t-c3`）：`run-light.png`、`run-dark.png`
  - 在线站点：`site-chat-light.png`（录播对话）、`site-run-dark.png`（运行页）
  - 悬停提示：`tooltip-bar.png`、`tooltip-pie.png`、`tooltip-scatter.png`
  - 详情面板里成功的图表步骤：`run-detail-chart-step.png`

**审查**（`eng:code-review main`，一轮；前端工单不跑 da-review）
- Standards 没有硬违规，Spec 没有阻断问题。
- 已修（提交 9b72d6e）：
  - x 列名正好叫 `y` 时，散点图所有点都画在一条竖线上；补了先红后绿的测试。
  - 同一列画成两个系列时 React key 重复；补了测试。
  - 两处按图类型分三种的判断合成一个 `chartKind`；`CHART_TYPES` 和 `ChartType` 绑定；去掉没人用的导出。
  - 堆叠柱子不再只给最上一段圆角；饼图悬停提示不再把扇区名写两遍。
- 审查后自查又修了两个（提交 e914322，各补了先红后绿的测试）：
  - x 是数字的类别轴（比如年份）刻度显示成 `2K|2K|2K`，现在照原值显示 2021。
  - 散点图悬停提示第二行写的是 x 轴名而不是系列名，现在显示 "Order amount 52"。
- 不修的和理由：
  - 提示卡片外壳重复了 3 处、bar/line/area 三个分支有重复属性：每个分支的属性不同，合并不划算。
  - 没有文字答案的运行不显示图：图放在答案区，没有答案就没有答案区，这是最保守的做法。

**偏离、已知问题和要核对的事**
- `--k-chart` 越出了 Authority：它那行只写了加 `--chart-series-*`，但 Context 的契约要求"`--k-chart` 颜色（三个主题块都加）"，我照契约加了。
- 首屏包变大：recharts 原来只在评测页的懒加载包里（390 kB），现在进了主包，主包从约 805 kB 变成 1.27 MB。改成懒加载会让服务端渲染的验收测试拿不到图，所以没改。超过 500 kB 的构建警告原来就有。
- 核对 119 时请顺便看：真实返回值里 `title` 或 series 的 `label` 缺了，图会被直接丢掉，不报错。金样里两者都有。
- 对话页的顺序是文字、结果表、图（都在文字下面）；运行页的结果表在答案块外面。

**待人定**（都按最保守的做法先做了）
- 图高 260px、宽度跟着容器。
- 图例和悬停提示照 `AblationChart` 的样式。
- 时间线标签 `CHRT`：标签宽 34px，五个字母的 `CHART` 放不下。名字 `Answer chart`，颜色浅色 `#93408a`、暗色 `#e08ad3`。
- 详情面板只显示参数和原始结果，不画图。
- 系列色：浅色 `#2a78d6 #d95926 #13936a #b07800 #cc4f7e #008300`，暗色 `#3987e5 #d95926 #199e70 #c98500 #d55181 #0ca30c`（两个暗色块一样）。都用 dataviz 的校验脚本对 `--panel` 检查过，五项全过，包括 3:1 对比度和色盲区分。

- 2026-11-23（主会话，第 7 步：上下文干净的 Opus 子 agent 只读审 PR #123，主会话逐条对照代码核实）：
  - 退回：数值轴刻度丢精度。`web/src/chart/Chart.tsx:54` 的 `compact`（`notation: 'compact'`、`maximumFractionDigits: 1`）用在 bar/line/area 的 y 轴和 scatter 两轴（`:59`、`:116`、`:201-202`）。0.012–0.045 的比率，刻度显示成 `0,0,0,0,0.1`；最大值 3 的堆叠面积图，0.75、2.25 显示成 0.8、2.3。比率、占比是常见查询结果。要按刻度步长或值域定小数位（或只在 |v| ≥ 1000 时用 compact），配先红后绿的测试。
  - 退回：x 列为 NULL 时显示错误。`ChartRow` 契约允许 NULL。饼图 `nameKey={chart.x}`（`:227`）遇 null 名字落成系列列名；柱图丢掉 null 的类别刻度；`scatterPoints`（`:190-192`）只过滤系列值是数字，不过滤 x，null x 的点没画出来。要给 null 一个显式标签（如 `(null)`），散点要求 x 也是数字，配测试。
  - 退回：有行但没有可画的东西时图是空框。`:247` 只在 `data.length === 0` 时显示"没有数据"提示；散点的系列值全是 NULL、饼图的值全是 0 时，只剩标题和空框。要把"没有可画的点"也算作空，配测试。
  - 退回：recharts 进了主包（约 805 kB → 1.27 MB，每个页面包括在线站点都变慢）。原来不改的理由不成立：`answer-charts.test.ts:145-160` 的 `expectChartsInAnswer` 只查 `data-testid="chart"` 外框、`data-chart-type` 和标题，这些由 `Frame` 渲染、不依赖 recharts。外框照常渲染，画图部分懒加载；验收测试照原样通过（只删过 `.fails`，其余一字不改）。如果懒加载后某个已合入的验收测试确实过不了，停下交回，写明是哪条、为什么。
  - 不修（记成已知限制）：图的 `role="img"` 外层盖住了 recharts 的键盘导航和图例文字（无障碍，小问题）；系列很多时图例挤占 260px 的图区、长类别标签被 recharts 默认隐藏、饼图超过 6 片颜色循环（契约 `i % 6` 定的）；`ScatterTip` 测试用的是手写的 tooltip payload（工单 agent 说用浏览器截图核过，未独立核实）。
