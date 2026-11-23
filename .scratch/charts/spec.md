# 答案图表：agent 用结构化工具出图，三处页面渲染

日期：2026-11-23。决策来自当天的 grilling（Q1–Q7，人逐条确认）。取证笔记：`docs/research/2026-11-23-answer-charts.md`。

## Problem Statement

plan §2 定过运行页"最后给答案、表格或图"，图一直没做（`.scratch/web-ui/spec.md`、`.scratch/chat-ui/spec.md` 列为 Out of Scope）。现在答案只有 Markdown 文本加一张表（`web/src/chat-view/Turn.tsx:60-63`、`web/src/run-page/Side.tsx:136-154`），比较类别、看趋势这类问题只能读数字。

## Solution

- agent 新增一个 MCP 工具 `display_chart`（照 nao `apps/shared/src/tools/display-chart.ts`）：参数是图类型、引用哪次查询、x 轴列、系列列。数据不由模型传，由服务端按查询 id 取出、校验后放进工具返回值。
- 前端从这次调用的 `tool_end` 事件渲染图，用已有的 recharts（`web/package.json` 的 `^3.10.1`）。对话页、运行页、在线回放站点用同一个组件。
- 只在产品里有，评测里不挂；产品提示词里加一段"什么时候画图"的规则。

## Implementation Decisions

### 开关（Q2）

- `data-agent run` 新增 `--charts`，默认不带；带上时注册 `display_chart`、追加画图规则（见下）。
- 网页的表单运行和对话（`web/launcher.py`、`web/conversations.py`）拼命令行时带 `--charts`，和 `--network` 的传法一样。CLI 用户要图可以自己带。
- `evals/harbor_agent.py`、`evals/dab.py`、jaffle-shop 不带；`tests/acceptance/test_chat_eval_unchanged.py` 的"评测配置不变"检查把 `display_chart` 和画图规则纳入（评测的工具清单里没有它，提示词里没有那段规则）。
- 澄清工具靠 `chat`（`agent.py:1324-1336`）开关，只在对话里有；图表要在表单运行里也有，所以用单独的 `--charts`，不复用 `chat`。

### 查询 id（Q4）

- 带 `--charts` 的运行里，每次 `query_database`（以及返回表格结果的其他数据工具，实现时列清）都分配 `query_N` 并保存完整结果，现在只有开沙箱时才分配（`src/data_agent/mcp_server.py:409`，`src/data_agent/sandbox.py:435` 的 `QueryResults`）。结果文本末行的 id 说明照现在的格式写，前端照现在的做法剔掉这一行。
- id 在一次运行内编号；对话的每一轮是一次运行，所以只能引用本轮的查询，不跨轮。

### `display_chart` 工具（Q4、Q5）

参数：

| 参数 | 说明 |
|---|---|
| `query_id` | 本次运行里某次查询的 id |
| `chart_type` | `bar`、`line`、`area`、`scatter`、`pie` |
| `x` | x 轴（饼图是名称）用的列 |
| `series` | 一个或多个 `{column, label?}`；饼图只能一个 |
| `stacked` | 只对 `bar`、`area` 有效，默认 false |
| `title` | 图标题 |

服务端校验，不通过就返回错误说明，由 agent 自己改：

- `query_id` 不存在；列不在这次查询的结果里（错误里列出实际的列名）；
- 系列列不是数值；`scatter` 的 x 列不是数值；`pie` 不止一个系列，或者有负值；
- 数据点（行数 × 系列数）超过 500：提示先聚合或筛选再画；
- `stacked` 用在不支持的图类型上。

通过时，返回值是一个 JSON：图的定义加上画图用的数据（只含 `x` 和系列这几列，按查询结果的原顺序），数值是数字类型，不是字符串。工具标为只读（`tool_annotations.py` 的 `READ_ONLY`）。

### 画图规则（Q3）

带 `--charts` 时追加到系统提示词，照 nao 系统提示词里的图表要求写成通用规则（不针对任何题目，ADR-0005）：

- 只在比较类别、看趋势、看构成或分布，并且数据点足够多时画图；单个数字、两三行的结果不画；
- 一个答案最多一两张图；
- 文字答案必须完整，图只是补充，不能用"见图"代替结论。

### 前端（Q5、Q6）

- 一个通用组件，三条代码路径：
  - 直角坐标图：`bar`（含堆叠）、`line`、`area`，结构相同，只换系列的组件；
  - `scatter`；
  - `pie`。
  配色、明暗主题、图例、悬停提示、数字格式、空数据和错误显示共用，照 `web/src/eval-page/AblationChart.tsx` 的写法。
- 位置：对话页和运行页的答案区，图放在文字答案下面，按调用顺序排；过程时间线里这次调用显示成"图表"步骤。工具返回错误的调用只在时间线里显示错误，不进答案区。
- 在线回放站点用同一个组件；站点只读 `events.jsonl`（`web/src/site/site-data.ts:26`），数据已经在 `tool_end` 里，不改导出。
- CLI 只输出文字答案，不变。

## 工单

| 工单 | 内容 | 依赖 |
|---|---|---|
| 118 | 验收测试：在 Testing Decisions 的接缝上写测试，标 `xfail`；人审后合入 main | 无 |
| 119 | 后端：`--charts`、查询 id 在带 `--charts` 时总是分配、`display_chart` 与校验、画图规则、网页两条命令行带 `--charts`、评测配置测试 | 118 |
| 120 | 前端：图表组件，接入对话页、运行页、在线站点和时间线 | 119（组件可以先按 Testing Decisions 的事件样例开工） |

前端工单按 AGENTS.md：不跑 Codex，PR 由一个上下文干净的 Opus 5.5 子 agent 审。

## Testing Decisions

单元测试不调模型。

- **开关**：不带 `--charts` 时工具清单里没有 `display_chart`、提示词里没有画图规则；带上时都有；网页两条命令行带 `--charts`；Harbor 拼的命令行、DAB 的选项里没有。
- **查询 id**：带 `--charts`、不开沙箱时，`query_database` 的结果也带 `query_N`，连续两次查询编号递增。
- **`display_chart` 校验**：上面列的每种错误各一个用例，错误文本里有实际列名或上限；合法的 5 种图各一个用例，返回的数据只含所选列、顺序和查询结果一致、数值是数字。
- **前端**（120）：用固定的 `tool_end` 事件样例渲染 5 种图，不崩、系列数和点数对；错误的调用不进答案区；站点页面用同一份样例能渲染；明暗主题下配色都可读。

## 验收

- `make check` 通过（含评测配置不变的测试）。
- 网页对话里问两三个适合画图的问题（仓库夹具或 jaffle-shop），比如按月看趋势、按类别比较：图画对了（和查询结果对得上），明暗主题都正常，文字答案是完整的。
- 一个不适合画图的问题（单个数字）：没有画图。
- 导出一次带图的运行到在线站点（`web-export`），站点上能显示这张图。
- 不跑分。

## Out of Scope

- nao 的另外 10 种图、柱线混合、双 y 轴、自定义图类型。
- 跨轮引用查询结果；内联数据。
- 沙箱里用 matplotlib 画 PNG 并在网页显示（Q1 没选）。
- 评测里画图。
- 图表的导出（PNG、CSV 下载）和编辑。

## Further Notes

- 上游：nao 的 `display_chart` schema（`apps/shared/src/tools/display-chart.ts`）、前端渲染（`apps/frontend/src/components/chat-messages/`）、系统提示词里的图表要求；取证时查过的这些文件头部都没有 `@license Enterprise`（取证笔记 §1），只照思路和参数设计，不整段复制；若复制代码，按 AGENTS.md 保留版权头并登记 `NOTICE`。
- SignalPilot 走 matplotlib PNG（`web/components/chat/markdown/image.tsx`、`web/lib/chat-file-refs.ts`），它的 echarts 渲染器目录带 `LICENSE.lightdash`，不参考。
- recharts 2.x（nao）到 3.x（我们）的 API 差异未确认，照 3.x 文档写。
