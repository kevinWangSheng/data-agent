# 答案图表：上游做法与本仓库现状（取证，2026-11-23）

只读取证，不做设计决定。本仓库 HEAD 46a7582；nao 5bde830；SignalPilot f5eee22（`~/dev/AI/data-agent-refs/`，下面路径相对各自仓库根）。未调模型，未改代码。

## 结论摘要（只列事实）

1. nao 的 `display_chart` 不带数据：参数只有 `query_id`（引用本对话里某次 `execute_sql` 的结果 id）加图类型、`x_axis_key`、`series[].data_key` 等；后端只校验参数，返回 `{success, error?}`；数据由前端按 `query_id` 在消息历史里找回，用 recharts 2.15.4 画。内置图类型 15 种（含 `table`、`kpi_card`）。`display-chart.ts`、`chart-types.ts`、`display-chart.tsx` 等相关文件头部都没有 `@license Enterprise`（只查了头 5-8 行）。
2. SignalPilot 聊天里的图有两条路：(a) 笔记本里 matplotlib 存 PNG 到 `artifacts/`，答案 markdown 写 `![alt](artifacts/x.png)`，前端按会话文件清单解析成图片；(b) 仪表盘 JSON spec（`artifacts/<name>.dashboard.json`，echarts 渲染，渲染器含 Lightdash 的 LICENSE）。聊天答案内没有 vega-lite/plotly 路径；vega-lite 只在"报告库"预览已存的 spec。
3. 本仓库：答案是 markdown 文本（`Md`）加一张结果表（`answer.table`）；表是从 `tool_end` 文本解析出来的，文本里一次 `query_database` 最多显示 400 行/12000 字符；完整行只在沙箱的 `data/<id>.csv`。查询结果 id（`query_N`）只在开了沙箱时才写进结果文本末行。
4. `recharts ^3.10.1` 现只在评测页 `AblationChart.tsx` 用。
5. 澄清工具的"产品有、评测无"靠三处开关：环境变量 `DATA_AGENT_CHAT`、`build_options(turn=...)` 是否非 None、提示词追加段；有验收测试守住评测配置不变。
6. 沙箱镜像装了 matplotlib，网页后端只在 docker 沙箱里跑，文件写到宿主 workspace；但没有任何 HTTP 路由或前端组件显示 workspace 里的文件，`Md` 没有 `img` 处理，在线站点只导出 `events.jsonl` 与 `meta.json`。

## 1. nao `display_chart`

许可证：`apps/shared/src/chart-types.ts`、`apps/shared/src/tools/display-chart.ts`、`apps/backend/src/agents/tools/display-chart.ts`、`apps/backend/src/components/tool-outputs/display-chart.tsx`、`apps/frontend/src/components/tool-calls/display-chart*.tsx`、`apps/shared/src/chart-builder.tsx`、`apps/backend/src/components/ai/system-prompt.tsx`、`apps/frontend/src/hooks/use-source-query.ts`：头 5-8 行都没有 `@license`（逐个 grep）。nao 的 `@license Enterprise` 文件在 `apps/` 下集中在 SSO、budget、branding、license、backoffice（`grep -rln @license apps`），不含图表链路。根 `LICENSE` 说明：大部分 Apache-2.0，标了该注释的文件是商业许可。（只抽查了图表链路上的文件；链路外的依赖文件未逐个查。）

**工具 schema**（`apps/shared/src/tools/display-chart.ts`）
- 数据来源是引用，不是内联：`query_id`："The id of a previous `execute_sql` tool call's output to get data from."（`:132`、`:220`、`:234`）。
- `chart_type`：内置枚举或项目自定义图类型名（`:133`、`:235`）；`'table'` 单独一个分支（`TableInputSchema`，`:219-224`，带 `conditional_formats`）。
- 图的参数（`ChartInputObjectSchema`，`:131-190`）：`x_axis_key`、`x_axis_type`（date/number/category，可 null，`:15`、`:135`）、`x_axis_label`、`series[]`（至少 1 个）、`y_axis_min/max/label`、`y_axis_right_*`（仅 mixed）、`show_data_labels`、`hide_total`、`title`。
- 每个系列（`SeriesConfigSchema`，`:53-72`）：`data_key`（SQL 结果列名）、`color`、`label`、`value_format`（d3 格式、前后缀、compact）、`is_total`、`series_type`（bar/line/area，仅 mixed）、`y_axis`（left/right，仅 mixed）。
- `kpi_card` 可省 x 轴字段，多一个 `comparison_mode`（`:206-217`）。
- 输入用 `superRefine` 按 `chart_type` 分派校验（`:270-283`）。

**返回**：`OutputSchema = {_version?, success, error?}`（`:285-289`）。后端 execute（`apps/backend/src/agents/tools/display-chart.ts:15-80`）：
- table：`getQueryResult(context, query_id)` 找不到就返回 error "Run execute_sql first and reference its Query ID."（`:16-27`）。
- 图：校验 x 轴必填（`:49`）、饼/环图恰 1 个系列（`:54`）、系列非空（`:63`）、堆叠图至少 2 个系列（`:68`）；通过后 `context.generatedArtifacts.charts.push(input)`（`:78`），返回 `success: true`。`:76` 有 TODO："check that the chart is displayable and that the data is valid"，即不检查数据。
- 给模型看的结果文字：成功只有 "Visualization displayed successfully."，失败是 "Could not display the visualization: <error>"（`apps/backend/src/components/tool-outputs/display-chart.tsx:5-10`）。
- 工具描述：'Display a chart visualization or formatted table of the data from a previous `execute_sql` tool call.'（`display-chart.ts:11`）。

**结果 id 从哪来**：`execute_sql` 的输出带 `id`，模型看到的文字里有 `Query ID: <id>`（`apps/backend/src/components/tool-outputs/execute-sql.tsx:39`、`:56`）；id 默认 `query_<uuid 前 8 位>`，也可由调用方指定（`apps/backend/src/agents/tools/execute-sql.ts:84`）。`getQueryResult` 先查本次运行内存缓存，再回落到该对话已存消息（`apps/backend/src/services/query-result.service.ts:10-23`），所以跨轮可引用。

**提示词怎么要求用**（`apps/backend/src/components/ai/system-prompt.tsx:193-250`，"Chart Rules" 一节）：逐类型的用法句，如 x_axis_type 何时用 date/category（`:196-199`）、scatter（`:201`）、radar（`:204`）、stacked_area 需 2+ 系列且数据要先 pivot（`:210`）、`*_100`（`:214`）、horizontal_bar（`:219`）、pie/donut 恰 1 个系列且前 10 之外并入 Other（`:224`）、mixed 的 series_type 与 y_axis（`:233`）、表格条件格式用 `chart_type: table`，"Never fake it with emoji"（`:239-245`）。没有"必须画图"的硬要求，由模型按问题决定。上下文压缩时 `display_chart` 的调用不被清除（`apps/backend/src/agents/providers.ts:78`）。
- 内置图类型（`apps/shared/src/chart-types.ts:1-17`）：bar、stacked_bar、stacked_bar_100、horizontal_bar、horizontal_bar_100、line、area、stacked_area、stacked_area_100、mixed、pie、donut、kpi_card、scatter、radar（15 种）；加 `table`。

**前端怎么渲染**
- 图画在工具调用组件里，不在答案 markdown 里：`apps/frontend/src/components/tool-calls/index.tsx:38` 把 `display_chart` 映射到 `DisplayChartToolCall`（`display-chart.tsx`，894 行）。
- 数据在前端按 `query_id` 找：`useSourceQuery(chartConfig?.query_id)`（`display-chart.tsx:115`）→ 在消息列表里找最近一次同 id 的 `execute_sql` 输出（`apps/frontend/src/hooks/use-source-query.ts:11-37`，`lib/execute-sql-messages.ts`）。找不到或为空时显示 "Could not display the chart because the data is missing / empty"（`display-chart.tsx:207`、`:215`）。
- 绘制在 `@nao/shared` 的 `buildChart`（`apps/shared/src/chart-builder.tsx:371`），用 recharts 的 BarChart（`:708`、`:973`）、AreaChart（`:1140`）、ComposedChart（`:1250`，line/mixed）、ScatterChart（`:1447`）、RadarChart（`:1489`）、PieChart（`:1528`）。`recharts` 版本固定 `2.15.4`（`apps/frontend/package.json:80`）。
- 另有：图/数据/SQL 三种视图切换（`display-chart.tsx:71`）、日期范围选择、系列开关、编辑对话框（1174 行）、导出图片/数据、存进 story。

## 2. SignalPilot

许可证 Apache-2.0（根 `LICENSE`）。例外：`signalpilot/web/dashboard-renderer/` 有 `LICENSE.lightdash` 与 `UPSTREAM.md`，说明该渲染器带 Lightdash 来源代码，复制前要另看（未读）。

**聊天里的图（matplotlib PNG → 文件清单 → markdown 图片）**
- 产出：agent 在笔记本里用 matplotlib 画图，`fig.savefig(sp.artifact_path("revenue_by_month.png"))`；`artifact_path` 返回本次聊天的 artifacts 目录下的路径并建目录（`signalpilot/notebook-server/signalpilot/_sdk/_artifacts.py:70`；导出 `_sdk/__init__.py:120`）。主题（深色背景、字体色等）写在同文件顶部常量，`sp.init()` 套到 matplotlib `rcParams`（`signalpilot-plugin/skills/notebook/SKILL.md:40-44`）。
- 提示：`signalpilot-plugin/skills/notebook/SKILL.md:143-175`。要点：存文件只是让它存在，要在答案里"引用一次、单独一行"才可见（`:150-154`，`![alt](artifacts/name.png)`）；一个图一个 cell 一个 figure，不要多子图（`:167-170`）；不要传 dpi/facecolor（`:172-173`）；"You cannot see the image"，先检查数据（x 排序、≤8 个系列、≤24 个类别、无空值，`:174-176`）；不要用字符画（`:177`）。
- 镜像：`signalpilot/notebook-server/Dockerfile:20` 装 `altair matplotlib plotly` 等。
- 网页：`signalpilot/web/lib/chat-file-refs.ts:1-40` 把 `artifacts/x.png` 或沙箱绝对路径规整成清单里的相对路径；`signalpilot/web/components/chat/markdown/image.tsx:31-56` 的 `img` 覆盖：带协议的当外链图，相对路径/沙箱路径按会话文件清单（`ChatUiContext.files`）解析，运行中找不到显示占位，结束后找不到显示 "not available"；`:58+` 的 `FileImage` 取文件对象 URL 显示（`use-file-object-url`，另有 `artifact-lightbox.tsx`、`chat-artifact-card.tsx` 缩略卡与下载）。
- 防呆：`rehype-harden` 会丢掉不是 URL 的相对目标，所以用哨兵域名 `https://conversation-files.invalid` 过一遍再还原（`chat-file-refs.ts:12-20`）。

**仪表盘（结构化 spec，echarts）**：agent 写 `artifacts/<name>.dashboard.json`（`signalpilot-plugin/skills/dashboard/SKILL.md:9`），schema `signalpilot-plugin/skills/dashboard/dashboard.schema.json`（chart 类型含 kpi、table、combo(bar/line/area)、heatmap、pie、scatter：`:365`、`:412`、`:445`、`:513`、`:570`、`:610`、`:645`），数据集是 SQL 或内联行，快照在 `artifacts/datasets/<name>.csv`（`gateway/gateway/dashboards/schema.py:1-20`）。渲染用 `echarts-for-react`（`web/dashboard-renderer/charts/echarts-tile.tsx:4-7`）。

**vega-lite**：只在报告库预览已存 spec 时用（`web/components/reports/chat-report-library.tsx:47`、`:177-190`，`VegaEmbed`，`mode: "vega-lite"`）。聊天答案路径里没找到 agent 产出 vega-lite spec 的代码（未全库搜索，只看了 web 与 plugin 技能）。plotly 在 notebook 镜像里装了，聊天技能里没有要求用。

**聊天里的 mermaid**：`web/components/chat/markdown/fence-renderers.tsx:3-12` 把 ```mermaid 围栏渲染成图（含 `xychart-beta`、`pie`，`:24`、`:29`）；注释说这是"模型不用提示也会写"的唯一图语法。

> 计划里 plan §2 提到的 `standalone_chat_tools.py` 路径未核对。

## 3. 本仓库现状

### 3.1 答案怎么渲染
- 对话页：`web/src/chat-page/Turn.tsx:60-63`，`<Md>{turn.run.answer.text}</Md>` 后跟 `answer.table` 的 `DataTable`（最多 50 行）。
- 运行页：`web/src/run-page/Side.tsx:136-154`，`<Md>{answer.text}</Md>`；`answer.table && answer.sqlStep` 时 "Result table"，`DataTable max={12}`；推断来源的标签 "last query before the answer · #N"（`:49`、`:75`、`:149`）。
- `Md` 是 react-markdown + remark-gfm（`web/src/code/markdown.tsx:5-12`），没有 `img`/代码围栏的自定义组件；该文件来自 SignalPilot（`:2`，已登记 NOTICE）。
- 工单 87（`.scratch/web-ui/issues/87-answer-table-fallback.md`，done）：`answer` 事件 `sql_tool_use_id` 为 null 时取答案之前最后一条成功查询（`QUERY_TOOLS`：`query_database`、`query_labels`），`FinalAnswer.sqlInferred?: true`。代码 `web/src/run-view/model.ts:526-548`（`finalAnswer`），类型 `:130-150`。agent 写 `answer` 事件时 `sql_tool_use_id` 为 null 的原因见工单 87 Context（`agent.py:1451` 一带，当时行号；本次未重核）。

### 3.2 查询结果在事件里长什么样
- `tool_end` 事件：`content` 是 SDK 的工具结果块原样（`src/data_agent/agent.py:1795-1805`）；`sql` 事件另记 SQL 与 database（`:1785-1792`）。
- 文本格式：`format_rows`（`src/data_agent/mcp_server.py:144-169`）：`a | b` 表头、横线、行、`(n rows)`；显示上限 `DISPLAY_FLOOR=50`、`DISPLAY_CHAR_BUDGET=12000`、`DISPLAY_MAX_ROWS=400`（`:134-136`），超出追加 `[INCOMPLETE DISPLAY] ...`。
- 前端解析：`parseTable`（`web/src/run-view/model.ts:250-264`）。行数与页脚对不上就返回 null（没有表）。`tool_end` 里：`step.table = step.tool.startsWith('mcp__data__') && !step.isError ? parseTable(text) : null`（`:415`）。表只有 `{columns, rows}`，单元格都是字符串，没有类型信息。
- 结果 id：有沙箱时 `query_database` 返回 `format_rows(result)` + `\n[result id: query_N; list it in sandbox_exec's data_files to read all N rows as data/query_N.csv]`（`mcp_server.py:409`，`sandbox.py:448-454`）。无沙箱（`results is None`）没有这一行（`mcp_server.py:407-408`）。前端识别该末行并从表里剔除（`model.ts:241`、`:252`），但不保存 id：`Step`/`ResultTable` 里没有 id 字段（`model.ts:60-` 的 `Step`、`ResultTable`；未逐字段核，查 `grep "result id"` 在 `web/src` 只有 `model.ts:241` 一处正则与注释）。
- id 编号：`QueryResults` 每个 MCP server 一份（`sandbox.py:436-446`，`mcp_server.py:333`），`query_1, query_2, ...`，跨数据库统一编号。对话每轮起一个新会话/MCP server，id 是否每轮重新从 `query_1` 开始：推断是（`QueryResults()` 在 `register_sandbox_tool` 内创建），**未确认**，没做运行验证。
- `sandbox_exec` 的 `data_files`：`execute_tool` 在执行前把选中的结果写成 `data/<id>.csv`（`sandbox.py:648-650`；工具描述 `:686-688`；spec `.scratch/agent-tools/spec.md:36`、`:125`）。CSV 含全部行（最多网关上限 `MAX_ROWS=10_000`，`gateway.py:20`），表头一行，NULL 为空。
- 答案依据哪次查询：靠 `answer` 事件的 `sql_tool_use_id`（新文件恒为 null）；没有按"结果 id"引用的机制。

### 3.3 recharts 现状
- `web/package.json:25`：`"recharts": "^3.10.1"`。唯一使用处：`web/src/eval-page/AblationChart.tsx:5`（BarChart、ErrorBar、LabelList 等；评测页的消融分数图）。`.scratch/web-ui/spec.md:84` 写明"recharts（只用于评测页）"。
- 对话页、运行页、在线回放里没有 recharts。

### 3.4 澄清工具"产品有、评测无"的做法（供图表工具参考）
- 工具注册：`src/data_agent/mcp_server.py:299-300`，`os.environ.get(CHAT_ENV) == "1"` 才 `register_clarification_tool`（`:312-326`，只读、`check_clarification` 校验问题与 2-5 个选项）。
- 开关变量：`CHAT_ENV = "DATA_AGENT_CHAT"`，定义在 `src/data_agent/tool_annotations.py:77-83`（放这里是为了 agent 不用 import server）；`build_options` 给 MCP server 的环境里恒设置它，`turn is None` 时为空串，所以 shell 里导出的值进不了评测（`agent.py:1621`）。`--chat` 开关来自 CLI。
- 工具名：`CLARIFICATION_TOOL = "mcp__data__clarification"`（`agent.py:115`）。
- 提示词：`CLARIFICATION_PROMPT`（`agent.py:173-182`）只在 `system_prompt(..., chat=True)` 追加（`:1336-1337`），`build_options` 用 `chat=turn is not None` 调（`:1638`）。
- 钩子：`clarification_hooks`（`agent.py:1457-1488`）只在 `turn is not None` 时挂（`:1617-1619`）。
- 前端：事件 `clarification`（`agent.py:1481`）→ `web/src/chat-view/model.ts` 的 `Clarification`（`:37-80`、`:117`）。
- 守护测试：`tests/acceptance/test_chat_eval_unchanged.py`（评测两条入口的 options 里没有名含 clarification 的工具、没有 resume 和 `CLAUDE_CONFIG_DIR`，`:16`、`:186`）。
- 另有一条已有的运行级开关做法：web 工具由 `network` 参数决定装载或拒绝（`agent.py:1606`）。
- `build_options` 签名：`agent.py:1510-1530`，参数 `turn: ConversationTurn | None`、`sandbox`、`network`、`dbt`。

### 3.5 在线回放站点能否显示同样的图
- 站点是静态的：`web/vite.config.ts` 非 local 构建用 `siteData()` 与 `spaFallback()`（`:14`）；`web/site-data.ts:26` 的 `RUN_FILE = /^runs\/([^/]+)\/(meta\.json|events\.jsonl)$/`，每个 run 只导出这两个文件；`evals/export.py` 头部注释同（`:28-32`）。
- 录制对话由 `web/src/site/RecordedConversation.tsx` 用 `Transcript`（`chat-page/Transcript.tsx`）回放，和本地对话页同一组件，只读（`:1-8`）。
- 所以：凡是只靠 `events.jsonl` 里已有字段（工具输入、`tool_end` 文本）就能画的图，站点同样能画（共用同一视图模型与组件，只读静态数据）。依赖 workspace 里文件（CSV、PNG）的东西，站点拿不到：导出里没有 workspace。未实际构建站点验证，是从导出文件清单和组件复用推出的。

## 4. `sandbox_exec` 画 matplotlib PNG 现在是否可行

- 镜像：`sandbox-image/Dockerfile:5-12` 装了 duckdb、dbt、pandas、pyarrow、matplotlib。（镜像本身是否已在本机构建过、matplotlib 实际可 import：未运行验证。）
- 后端：网页后端只给 `--sandbox docker`（`src/data_agent/web/launcher.py:164-166`，`conversations.py:142`）；`local` 后端用的是宿主 Python，镜像里的包不保证有（未确认）。
- 产出可见性：docker 沙箱把 workspace 绑定挂载为 `/workspace`（`sandbox.py:277`），所以文件写到宿主 `<runs_dir>/<id>/workspace/`（单次运行，`launcher.py:149`）或对话共用的 `workspace/`（`conversations.py:128`）。工具描述要求把给用户的文件放 `outputs/`（`sandbox.py:680-681`），`outputs/` 下新建或改动的文件在回复末尾列在 `output_files:`（`sandbox.py:547-572`、`:651-655`）。
- 提示词：没有 matplotlib 或画图的说明（`grep matplotlib` 在 `src`、`skills`、`docs/setup.md`、`pyproject.toml` 无结果，只有 `sandbox-image/Dockerfile:12`）。spec 有一句"查询结果需要计算、合并或画图时，用 `sandbox_exec` 并通过 `data_files` 传入结果"（`.scratch/agent-tools/spec.md:89`），未核对它是否已进查数提示词。
- 网页显示：没有。`web/src/run-page/tool-views.ts:76-91` 只解析 `output_files:` 清单为文本；`src/data_agent/web/app.py` 的路由只有 `/api/options`、`/api/runs`、`/api/runs/{id}/events`、`/api/conversations*`，加静态与 SPA 兜底（`:86-203`），没有读 workspace 文件的路由；`Md` 无 `img` 处理（3.1）。
- agent 看不到图：SignalPilot 的技能里明说 "You cannot see the image"（`SKILL.md:174`）；本仓库同理（工具返回文本）。未核对 Claude Code 内置 `Read` 读 PNG 是否经 workspace guard 放行（`FILE_TOOLS` 受 guard 约束，`agent.py:117`）。

## 5. 两种机制各要改哪些文件（只列落点，不是设计）

### (a) 结构化图表工具 + 前端 recharts

Python
- `src/data_agent/mcp_server.py`：注册 `display_chart`（照 `register_clarification_tool`，`:312`）；按"查数结果 id"校验引用（`QueryResults`，`sandbox.py:436`）。现在 id 只有开沙箱才有（`mcp_server.py:407-409`），无沙箱的运行需要另想。
- `src/data_agent/tool_annotations.py`：新开关或复用 `CHAT_ENV`（`:77-83`）。评测里移除：同澄清，只在产品环境装载。
- `src/data_agent/agent.py`：`build_options` 里工具名常量（`:115` 一带）、系统提示追加段（`:173`、`system_prompt` `:1324`）、必要时 `tool_end`/事件字段；`BUILTIN`/`allowed_tools` 列表里加该工具（未核对该列表位置）。
- 新增或扩展事件：图的参数本来就在 `tool_start` 的 input 里，不一定要新事件（未定）。
- 测试：`tests/test_clarification.py` 的做法；`tests/acceptance/test_chat_eval_unchanged.py` 加"评测里没有 display_chart"。

前端
- `web/src/run-view/model.ts`：`Step` 加解析（`tool_end` 处 `:415` 附近）；`ResultTable` 现在是全字符串、无 id、最多 400 行（`parseTable` `:250`），画图要数值类型转换，要保存 `[result id: ...]`（`:241`）。`FinalAnswer` 或 chat 的 `Turn` 要能拿到本轮的图。
- `web/src/chat-view/model.ts`、`web/src/chat-page/Turn.tsx:60-63`、`web/src/run-page/Side.tsx:136-154`：显示位置。
- 新组件（如 `web/src/chat-page/Chart.tsx`）：recharts 3.10.1（已装，`package.json:25`；nao 用 2.15.4，API 抄时要对照 3.x，未核对差异）。
- `web/src/run-page/ToolPanels.tsx`、`tool-views.ts`：该工具调用在步骤详情里的显示（未读，位置待确认）。
- 站点数据不用改（共用组件、`events.jsonl`），前提是图所需数据全在 `tool_end` 文本里（受 400 行/12000 字符显示上限）。

### (b) 沙箱里画图片文件 + 前端显示图片

Python
- `src/data_agent/web/app.py`：新增只读路由，按 run/conversation id 取 workspace 里的文件（`/api/runs/{id}/files/...`）；要做路径逃逸与符号链接防护（参照 `sandbox.write_file`/`outputs_snapshot` 的 `O_NOFOLLOW` 做法，`sandbox.py:476`、`:547`）。
- `src/data_agent/web/launcher.py`、`conversations.py`：workspace 路径已知（`:149`、`:128`），可能要保证清理策略。
- 提示词：`SYSTEM_PROMPT`（查数）或 skill 里加画图规则（照 SignalPilot `notebook/SKILL.md:143-177`；ADR-0005 禁止针对榜单题目的 Skill 内容，通用画图规则不违反，但要核）。
- 评测：画图提示只加在产品里，或评测里本来就不装 `sandbox_exec`（DAB 现有路径未核对）。
- `sandbox-image/Dockerfile`：已有 matplotlib，无须改。

前端
- `web/src/code/markdown.tsx`：给 `Md` 加 `img` 覆盖，按 run/conversation 解析 `outputs/x.png`（照 `signalpilot/web/components/chat/markdown/image.tsx` 与 `chat-file-refs.ts`，Apache-2.0，需登记 NOTICE 并保留版权头）。`Md` 要知道当前 run/conversation id（现在没有上下文）。
- 或者从 `sandbox_exec` 的 `output_files:` 清单（`tool-views.ts:76-91` 已解析）生成缩略图卡片。
- 在线站点：导出不含 workspace（`evals/export.py`、`web/site-data.ts:26`），要显示图需要扩展导出和 `RUN_FILE`；这部分是改动，不是现成能力。
- 事件文件不含图片字节，回放旧录制时没有图（新运行才有）。

## 未确认项

- nao：`recharts` 2.15.4 与本仓库 3.10.1 的 API 差异；`display-chart.tsx` 里 `Customized`、tooltip 等细节未逐段读；链路外依赖文件的 `@license` 头未逐个查。
- nao 对话里 `execute_sql` 输出是否完整存库（前端靠消息历史取数）：只看了 `getQueryResultByQueryId` 的调用，没读实现。
- SignalPilot：聊天答案内是否另有 vega-lite 产出路径（只搜了 web 与 plugin）；`standalone_chat_tools.py` 未读；dashboard 渲染器的 Lightdash 许可范围未读。
- 本仓库：对话每轮 `query_N` 是否重新编号；`local` 沙箱后端是否有 matplotlib；docker 镜像在本机是否已构建；`FILE_TOOLS` 读 PNG 是否被 guard 放行；查数提示词里是否已有 `data_files` 画图那句（`agent.py:199` 有 `data_files` 字样，上下文未读）；`agent.py` 里 `allowed_tools` 列表的确切位置。
- 没有跑任何东西（不构建站点、不起 docker、不调模型），所有"能否"判断都是读代码得出的。
