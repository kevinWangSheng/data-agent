# 118 — 答案图表：验收测试（MCP server、开关、前端，全部不调模型）

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 118-charts-acceptance-tests)
Branch: worktree-118-charts-acceptance-tests

## Goal

spec "Testing Decisions"（`.scratch/charts/spec.md:80-87`）列的验收测试全部写好，分四组：
- MCP server：查询 id、`display_chart` 的参数、校验和返回值；
- 开关：`run --charts`、网页两条命令行、评测入口；
- 前端图表组件；
- 前端答案区和站点。

还没实现的行为按转绿的工单标预期失败：后端 `TICKET_119`（`xfail(strict=True)`），前端 `test.fails`（120）。现在就该成立的守护测试不标，现在就要通过。

契约写进代码和夹具（见 Context"本工单定下的契约"），前端只放类型和空壳。共用的事件样例 `tests/fixtures/charts/` 同时给 119 的回放测试和 120 的组件用，保证两边对得上。本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit:
  - `tests/acceptance/test_charts_server.py`、`tests/acceptance/test_charts_switch.py`（新建）；
  - `tests/fixtures/charts/`（新建）：事件样例 `run/events.jsonl` 和 `run/meta.json`；站点测试要用对话索引时，再加 `conversation/` 下的索引；
  - `web/src/chart/`（新建）：
    - `model.ts`：类型和 `answerCharts` 空壳，函数体抛出未实现；
    - `Chart.tsx`：组件空壳，props 签名定死，函数体抛出未实现；
    - `acceptance/chart-render.test.ts`：jsdom 下渲染组件；
    - `acceptance/answer-charts.test.ts`：node 环境，测模型、页面 SSR、站点、时间线种类、配色；
  - `web/package.json`、`web/pnpm-lock.yaml`：只加 `jsdom` 一个 devDependency（Recharts 在 jsdom 里固定尺寸能画出来，SSR 画不出来，见 Context 最后一条）。测试文件用 `// @vitest-environment jsdom` 按文件切环境，不改 `web/vite.config.ts`。
- Must not edit（参考实现例外：为验证测试，可以临时改这些文件，但不提交，验证完撤掉）：
  - `src/` 和 `evals/` 下的任何文件。后端不放空壳，xfail 的失败原因靠测试自己先断言"选项还不存在"来说明，见 Context。
  - 已有的 `tests/acceptance/` 文件（含 `test_chat_eval_unchanged.py`、`seam_support.py`，只能 import）和 `tests/fixtures/` 里已有的文件。
  - `web/src/` 里已有的文件：`index.css`、`run-page/tools.ts`、`chat-page/Turn.tsx`、`run-page/Side.tsx`、`run-view/model.ts`、`site/` 等，都由 120 改。
  - `Makefile`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`。
- Server access: none
- Spend cap: $0（不调模型；spec 的单元测试不调模型，`.scratch/charts/spec.md:82`，本工单没有真模型测试）

## Context

- spec：`.scratch/charts/spec.md`，各节分别是：
  - "开关（Q2）" `:17-22`
  - "查询 id（Q4）" `:24-27`
  - "`display_chart` 工具（Q4、Q5）" `:29-49`
  - "画图规则（Q3）" `:51-57`
  - "前端（Q5、Q6）" `:59-68`
  - "Testing Decisions" `:80-87`

  取证笔记：`docs/research/2026-11-23-answer-charts.md`（§3.2 结果 id 和事件格式，§3.4 澄清工具的开关做法，§3.5 站点）。
- spec 的一处路径笔误：对话页答案区在 `web/src/chat-page/Turn.tsx:60-63`，spec `:7` 写成了 `chat-view/`。
- 先例：
  - 本工单的写法照工单 88（`.scratch/chat-ui/issues/88-chat-acceptance-tests.md`）和 73（`.scratch/web-ui/issues/73-web-acceptance-tests.md`）：按工单分标记（`TICKET_NN = pytest.mark.xfail(strict=True, reason=...)`），守护测试不标。
  - MCP stdio 客户端照 `tests/acceptance/test_seam_b_mcp_server.py:43-79`（`mcp_server`、`call`、`text_of`、`payload`）。
  - `run` 停在 SDK transport 的 `connect`、不调模型就拿到选项：`tests/test_network_option.py:80-120`；`tests/acceptance/test_chat_eval_unchanged.py:81-153` 还会按选项起 MCP server 列出工具。
  - 网页两条命令行用 `Recorder` 记录 `start_run` 的参数：`tests/test_network_option.py:123-188`。
  - Harbor 命令行：`tests/test_harbor_agent.py:173`（`stub_args`）、`:343`。
  - 站点 SSR：`web/src/site/RecordedConversation.test.ts`（`staticSource` 加内存里的文件表）。
  - 运行视图：`web/src/run-view/acceptance/run-view.test.ts`。
- 代码现状（HEAD a6e918b）：
  - id 只在有沙箱时分配：`mcp_server.py:297`（`results = None if sandbox is None ...`）、`:407-409`；`QueryResults` 在 `sandbox.py:435-466`，现在的说明行是 `[result id: query_N; list it in sandbox_exec's data_files ...]`（`:448-454`）。
  - `run --sandbox` 默认 `docker`（`cli.py:319`），网页后端也总给 `--sandbox docker`（`src/data_agent/web/launcher.py:166`、`src/data_agent/web/conversations.py:142`），所以"带 `--charts`、不开沙箱"只能在 `mcp-server` 不带 `--sandbox` 时出现。查询 id 一组在 `mcp-server` 这一层测。
  - 澄清工具的开关靠环境变量 `CHAT_ENV`（`tool_annotations.py:77-83`，`agent.py:1621`）；沙箱和网络靠 `mcp-server` 的命令行参数（`agent.py:1576`、`cli.py:252-260`）。
  - 前端：
    - 工具结果文本由 `resultText` 取出（`web/src/run-view/model.ts:211-234`）：返回 `str` 的工具是 `{"result": "..."}` 信封，会被拆开；返回 dict 的工具（如 `discover`），事件里的 `content` 就是那个 dict 的 JSON。真实例子：`tests/fixtures/web/real/dbt-cart-abandonment/events.jsonl` 第 24 号事件，`discover` 的 `content` 是 `{"results":[...]}`；`query-shop/events.jsonl` 第 11 号事件，`query_database` 的 `content` 是 `{"result":"..."}`。
    - 说明行的正则在 `model.ts:241`；工具种类在 `web/src/run-page/tools.ts:24-39`。
    - 主题色块有三处：`web/src/index.css:11`（`:root`）、`:66-67`（系统深色）、`:119`（`data-theme='dark'`）。
- 与并行工单的关系：工单 111–117（可观测性 `.scratch/observability/spec.md`、外部 MCP `.scratch/mcp/spec.md`）同时在做，会改 `build_options`（`agent.py:1510`）、`cli.py`，外部 MCP 还会碰网页命令行和"评测不挂"的测试。所以本工单不改 `src/`，不在 `build_options` 加参数，不改 `test_chat_eval_unchanged.py` 和它的记录文件，减少冲突。

### 本工单定下的契约（写进测试和夹具，最终回复逐项列出供人审）

1. **开关怎么传到 server（本工单定、人审）**：
   - `data-agent run --charts`（store_true，默认关）；
   - `data-agent mcp-server --charts`（store_true，默认关），和 `--sandbox`、`--network` 一样走 server 的命令行参数；
   - `build_options` 在开着时把 `--charts` 加进 server 的 `args`。
   - 不用环境变量：参数不会从 shell 继承，不需要像 `CHAT_ENV` 那样每次显式置空。
   - `--source` 多库 server 也认这个参数。
   - 测试对开关的检查只看结果：工具清单（按选项真起 server 列出）和 `system_prompt`。只有 `mcp-server --charts` 这个参数名被直接写进测试。
2. **查询 id**：带 `--charts` 的 server，不管有没有沙箱，每次成功的 `query_database` 结果文本最后一行都是 id 说明。
   - 这一行要能被前端现有的正则 `^\[result id: [^\]]+\]$` 匹配，并以 `[result id: query_N` 开头，紧跟 `;` 或 `]`。之后的说明文字由 119 定，测试不看。
   - 编号从 `query_1` 开始，连续递增；多库 server 各库共用一套编号。
   - 不带 `--charts` 也不带沙箱时，结果文本不变：没有这一行。
   - 只测 `query_database`。spec `:26` 的"其他返回表格的数据工具，实现时列清"由 119 列出，写进 Comments，本工单不约束。
3. **`display_chart` 的参数**（MCP 列出的 inputSchema）：
   - 属性只有 `query_id`（string）、`chart_type`（enum：`bar`、`line`、`area`、`scatter`、`pie`）、`x`（string）、`series`（数组，至少 1 项，每项 `{column: string, label?: string}`）、`stacked`（boolean，默认 false）、`title`（string）。
   - 必填：`query_id`、`chart_type`、`x`、`series`、`title`。spec 的参数表里只有 `label` 带"?"，所以 `title` 定为必填。
   - 多库 server 也没有 `database` 参数（整次运行一个工具，和 `sandbox_exec` 一样）。
   - 注解 `readOnlyHint: true`（`READ_ONLY`）。
4. **校验失败**：返回 MCP 错误结果（`isError: true`，即 `ToolError`；事件里 `is_error: true`，前端靠它把这次调用挡在答案区外）。错误文本要求如下：
   - `query_id` 不存在：文本里有这个 id；
   - 列不存在（x 列、系列列各一例）：文本里有这次查询的每一个实际列名；
   - 系列列不是数值：文本里有该列名；
   - `scatter` 的 x 列不是数值；
   - `pie` 有 2 个系列；
   - `pie` 有负值；
   - 数据点（行数 × 系列数）超过 500：文本里有 `500`。250 行 × 2 个系列 = 500 能通过，251 行 × 2 个系列 = 502 被拒；
   - `stacked: true` 用在 `line`、`scatter`、`pie` 上。

   "数值"指每个非 NULL 的值都是 int、float 或 Decimal（bool 不算）。测试只用明确的数值列和文本列，不测 bool、全 NULL 这种边界。
5. **成功时的返回值**：工具返回 dict（照 `discover`），事件里 `tool_end.content` 就是它的 JSON，不套 `{"result": ...}`：

   ```json
   {
     "chart": {"query_id": "query_2", "chart_type": "bar", "x": "tier",
               "series": [{"column": "online", "label": "Online"}, {"column": "store", "label": "store"}],
               "stacked": true, "title": "Revenue by tier and channel"},
     "data": [{"tier": "Gold", "online": 410.5, "store": 300}, ...]
   }
   ```

   - `chart` 是校验过、补齐了默认值的参数：没给 `label` 就用列名，没给 `stacked` 就是 false。
   - `data` 每行一个对象，键只有 x 列和系列列，行序和查询结果一致。
   - 数值是 JSON 数字（Decimal、int、float 都是）。非数值、非 NULL 的值写成 `str(value)`，和 `format_rows` 表格里显示的一样，例如 DATE 写成 `"2024-01-01"`。NULL 写成 `null`。
   - 测试按解析后的 JSON 比较，键序和数字写法都不影响。
6. **事件样例 `tests/fixtures/charts/run/`**：一条手写的查数运行，事件格式照 `tests/fixtures/web/agent-tools/events.jsonl`。
   - 三次 `query_database`。SQL 都是自带数据的 `SELECT ... FROM (VALUES ...)`，在空的 DuckDB 上就能跑。
     - 结果文本照现在 `format_rows` 加沙箱说明行的格式写，网页运行总带沙箱。
     - 列包含 DATE、DECIMAL、INTEGER、DOUBLE、文本。
   - 六次 `display_chart`，按调用顺序：
     - 5 次成功，每种图各 1 次：`bar` 堆叠、2 个系列；`line`；`area`；`scatter`；`pie`。5 张图的标题互不相同。
     - 1 次失败（引用了不存在的列，`is_error: true`）。
   - 文字答案（`answer` 事件），`result` 事件。
   - 成功调用的 `tool_end.content` 就是契约 5 的返回值。后端回放测试会核对：把样例里的查询和 `display_chart` 输入照原样发给真实的 `mcp-server --charts`，返回值必须和样例相等。
   - 这样 120 可以照样例先开工，119 合并后两边不会对不上。
7. **前端接口**（`web/src/chart/`，类型和空壳由本工单写）：
   - `model.ts`：
     - 类型 `ChartType`、`ChartSeries {column; label}`、`ChartSpec`、`ChartRow = Record<string, number | string | null>`、`ChartPayload {chart: ChartSpec; data: ChartRow[]}`。
     - `AnswerChart {step: string /* tool_use_id */; seq: number; payload: ChartPayload; kind: 'cartesian' | 'scatter' | 'pie'; series: number; rows: number}`：`rows` 是行数（不是契约 4 的数据点数）；`kind` 对应 spec 的三条代码路径，`bar`、`line`、`area` 都是 `cartesian`。
     - `answerCharts(view: RunView): AnswerChart[]`：主 agent 成功的 `display_chart` 调用，按 `tool_start` 顺序。出错的、还没结束的、结果解析不了的都不算。
     - 不给 `RunView`、`FinalAnswer`、`Step` 加字段：工单 73 的验收测试用 `toEqual` 比较它们。
   - `Chart.tsx`：
     - `export function Chart(props: { payload: ChartPayload; size?: { width: number; height: number } })`。
     - 根元素带 `data-testid="chart"`、`data-chart-type`，并显示 `title`。
     - 给了 `size` 就画固定尺寸。测试都给 `size`：probe 显示 jsdom 里 `ResponsiveContainer` 画不出东西。没给 `size` 时用什么自适应方式，由 120 定。
     - 第 i 个系列的颜色是 `var(--chart-series-{i % 6 + 1})`；饼图按扇区序号取色。
     - `data` 为空时显示标题和一句空数据提示，不崩。
   - 页面：
     - 对话页和运行页的图都在 `[data-testid="answer"]` 里，排在答案文字后面，按调用顺序。对话页已有这个 testid（`Turn.tsx:61`）；运行页要由 120 给答案块加上。
     - 站点（`web/src/site/`，用 `staticSource`）用同一份样例，结果一样。
   - 时间线：`toolKind('mcp__data__display_chart')` 是 `'chart'`。界面是英文，标签由 120 定，写作 "Chart"，即 spec `:62` 的"图表"。测试用 `expect(...).toBe('chart')` 写，不用 `=== 'chart'`：现在的 `ToolKind` 联合类型里没有 `'chart'`，`===` 过不了类型检查。
   - 配色：
     - `web/src/index.css` 的三个色块里都要定义 `--chart-series-1` 到 `--chart-series-6`；
     - 两个深色块的值相同；
     - 同一色块里的 6 个值互不相同；
     - 每个值和本色块的 `--panel` 对比度至少 3:1（WCAG 1.4.11 非文字对比度）。
     - 测试读 CSS 文本计算。
8. **画图规则**：
   - 测试只检查规则在不在，不检查措辞。带 `--charts` 时 `system_prompt` 里出现 `display_chart`；不带时、在评测入口里都不出现。
   - 由此给 119 的约束：规则正文要点名 `display_chart`。

### 各组要写的测试

- `test_charts_server.py`（MCP stdio，全部 `TICKET_119`；夹具库用 `seam_support.build_shop_duckdb` 或空 DuckDB）：
  - 带 `--charts` 不带沙箱：`query_database` 带 id，连续两次是 `query_1`、`query_2`。
  - 多库 `--source` 加 `--charts`：两个库共用编号，`display_chart` 没有 `database` 参数。
  - `display_chart` 的参数 schema 和注解。
  - 校验失败的每一种（契约 4），每种一个用例，可以参数化。
  - 合法的 5 种图各一个用例：返回的 `data` 只含所选列；行序和查询结果一致；数值是 JSON 数字（Python 端 `type(v) in (int, float)`）；DATE 写成 ISO 文本。
  - 回放事件样例（契约 6）。

  每个用例开头先断言 `data-agent mcp-server --help` 里有 `--charts`，失败信息写明"ticket 119: mcp-server has no --charts yet"，再起 server。这样 `--runxfail` 看到的是"功能没做"，不是"server 没起来"。
- `test_charts_switch.py`：
  - 守护测试，不标记，现在就通过，以后也要一直通过：
    - `run` 不带 `--charts` 时，工具（含按选项起 server 列出的 MCP 工具）和 `system_prompt` 里都没有 `display_chart`。
    - Harbor `_run_command` 里没有 `--charts`。
    - DAB 的 `run_query` 拿到的选项里，工具、MCP 工具、`system_prompt` 都没有 `display_chart`。
    - 不带 `--charts` 也不开沙箱的 `mcp-server`，`query_database` 结果没有 id 行。
  - 标 `TICKET_119`：
    - `run --charts`：MCP 工具里有 `display_chart`，`system_prompt` 里有 `display_chart`。
    - 网页表单的查数运行（`Launcher.submit`）和对话的一轮（`Conversations.send`），命令行都带 `--charts`。

  评测入口直接 import `test_chat_eval_unchanged` 里的 `captured`、`harbor_dbt`、`dab_query`、`Captured`，不改那个文件。它的开头写明"no ticket removes or edits them"，所以 spec `:21`"把 `display_chart` 和画图规则纳入该文件的检查"在本工单落到新文件里。那个文件的 `surface()` 不看 `system_prompt`，提示词这一条是新加的检查。

  带 `--charts` 的 `run` 用例也要先断言 `data-agent run --help` 里有 `--charts`，再调 `cli.main`。
- 前端（全部 `test.fails`，由 120 去掉）：
  - `chart-render.test.ts`（jsdom）：
    - 用样例里 5 次成功调用的 payload 各渲染一次，不抛异常；
    - 系列层数等于 `series` 数：`.recharts-bar`、`.recharts-line`、`.recharts-area`、`.recharts-scatter`、`.recharts-pie`；
    - 图形元素数：bar 的矩形数、scatter 的符号数都等于行数 × 系列数，pie 的扇区数等于行数；line 和 area 的行数在 `answerCharts` 的 `rows` 上测，渲染时只断言每条曲线的 path 不空；
    - 系列颜色用 `var(--chart-series-N)`；
    - 空 `data` 不崩，显示标题。
  - `answer-charts.test.ts`（node）：
    - `answerCharts(buildRunView(样例))` 返回 5 张图：顺序、`step`、`kind`、`series`、`rows` 都对，失败那次调用的 id 不在其中；
    - SSR 对话页 Transcript 和站点（录制对话，以及站点的运行页）：`[data-testid="answer"]` 里有 5 个 `data-testid="chart"`，按调用顺序（看 `data-chart-type` 和标题），都排在答案文字后面；
    - `toolKind` 是 `'chart'`；
    - 配色（契约 7）。
- 不写真模型测试：spec `:82` 定了单元测试不调模型。spec "验收"一节 `:89-95` 的网页实测由主会话在 119、120 合并前做。

## Acceptance

- [ ] spec "Testing Decisions" 每一条（开关、查询 id、`display_chart` 校验、前端）都有断言，契约 4 列的每种错误、5 种合法图各至少一个用例
- [ ] 标了预期失败的测试当前都以预期失败通过（`xfail(strict=True)` / `test.fails`），没有被跳过的；守护测试当前通过
- [ ] 每个预期失败的测试，失败原因都是功能还没做，不是夹具或 server 没起来。最终回复给每个文件贴一个用 `--runxfail`（vitest 临时去掉 `.fails`）跑出的真实失败原因，例如：
  - "ticket 119: mcp-server has no --charts yet"
  - `answerCharts` 抛出未实现
  - 答案区里 0 个 chart
- [ ] 后端两组、前端两组都临时写了参考实现（不提交），并做了验证：
  - 去掉预期失败标记后都能通过；
  - 参考实现的 `display_chart` 回放样例，和样例相等；
  - 每组至少一个反向检查会失败，例如：数据点上限放到 502 才拒；DECIMAL 写成字符串；评测入口多了 `display_chart`；失败的调用进了答案区；某个系列颜色和 `--panel` 的对比度不够。
- [ ] 夹具里没有密钥、地址、主机名（`python -m evals scrub` 对 `tests/fixtures/charts/` 输出 0 处改动）
- [ ] Context"本工单定下的契约"1–8 都写在测试、夹具或 `web/src/chart/` 的类型里，最终回复逐项列出，供人审
- [ ] `make check` 通过

## Evidence required

- `make check` 的完整输出。
- `uv run pytest tests/acceptance/test_charts_server.py tests/acceptance/test_charts_switch.py -v` 的输出：每个用例的名字和 xfail/pass。
- `pnpm --dir web test` 里本工单测试的部分：每个用例的名字和预期失败。
- 每个文件一条 `--runxfail` 的失败原因。
- 参考实现和反向检查的结果。
- 脱敏命令的输出。
- 最终回复里的契约清单。
- 交给主会话的一条确认命令：119 合并前，主会话用 `--charts` 真跑一次网页对话，核对真实 `tool_end.content` 的形状和契约 5、事件样例一致（是 dict 的 JSON，不套 `{"result": ...}`）。不一致就退回 119，不改样例。

## Stop conditions

- spec 的某条行为没法在约定的接缝上测，或者要改 Authority 以外的文件（`src/`、`evals/`、已有的测试和 web 文件）才能测。
- 守护测试写出来现在就不通过。
- Recharts 在 jsdom 里给了固定尺寸还是画不出某种图，前端渲染一组写不成（probe 五种都画出来了，见 Comments）。
- spec 自相矛盾，或缺一个需要人定的决定。契约的具体字段名、参数名不算，由本工单定、人审。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1 (Fable in place of `da-review`, plus `eng:code-review`; one round of fixes)
- Model spend: $0 (no data-agent model calls)
- Outcome: —

## Comments

- 2026-11-23（写工单时，主会话抄录前的取证）：
  - 在 scratch 目录装了 `recharts@3.10.1`、`react@19`、`jsdom`，做了 probe：
    - `react-dom/server` 渲染 BarChart、PieChart、LineChart，输出只有外壳（127 字符），没有任何图形元素；
    - jsdom 里给固定 `width`/`height`：bar 有 6 个矩形（3 行 × 2 个系列），pie 有 3 个扇区，line 有 2 条曲线、6 个点，scatter 有 3 个符号；
    - `ResponsiveContainer` 不管有没有 `ResizeObserver` 替身、有没有 `initialDimension`，都是 0 个矩形；
    - 图表组件加 `responsive` 属性时有 3 个矩形。
    - 五种图的系列层类名都核对过：`.recharts-bar` 2、`.recharts-line` 1、`.recharts-area` 2（堆叠，曲线 path 不空）、`.recharts-scatter` 1（3 个符号）、`.recharts-pie` 1（3 个扇区）；`fill`/`stroke` 写 `var(--chart-series-N)` 会原样出现在 DOM 里（`Cell` 也是）。
    - `web/node_modules/vitest` 里有 `vitest-environment` docblock 的处理代码，按文件切环境可用。
  - 留给人定或 119 处理的事项（不影响本工单的测试）：
    1. `SANDBOX_RULE`（`agent.py:197-200`）要求"plotting"在 `sandbox_exec` 里做，和带 `--charts` 时的画图规则冲突。这条规则也在 DAB 提示词里（`evals/dab.py:111`），评测提示词不能动。119 要定带 `--charts` 时怎么处理，比如只在查数提示词里换一句。
    2. 网页表单起的 dbt 运行带不带 `--charts`：spec `:20` 没区分。测试只要求查数运行和对话带，dbt 不测，由 119 定并写进 Comments。
    3. 除 `query_database` 外，哪些工具也分配 id（spec `:26`），由 119 列出。

- 2026-11-23（工单 agent 最终回复，主会话照抄）：

**What was added**
- `tests/acceptance/test_charts_server.py`: 21 tests, all marked to fail until ticket 119.
- `tests/acceptance/test_charts_switch.py`:
  - 5 guard tests with no mark, which pass now;
  - 3 tests marked for ticket 119.
- `tests/fixtures/charts/run/`: `events.jsonl` and `meta.json`.
- `web/src/chart/model.ts` and `web/src/chart/Chart.tsx`: types and stubs that throw "not implemented".
- `web/src/chart/acceptance/chart-render.test.ts` (jsdom) and `answer-charts.test.ts` (node): 42 tests, all `test.fails` until ticket 120.
- `web/package.json` and the lockfile: only `jsdom` and its own dependencies (vitest's peer line updates with it).
- Nothing under `src/` or `evals/` changed.

**Test runs**
- `make check`: exit 0.
  - ruff: "All checks passed!"; 371 files already formatted.
  - basedpyright: 0 errors, 0 warnings.
  - pytest: `2595 passed, 2 skipped, 77 deselected, 24 xfailed`.
  - web: `Tests 320 passed | 42 expected fail (362)`.
- The two backend acceptance files alone (`pytest … -v`): `5 passed, 24 xfailed`, nothing skipped. The guards are:
  - a run without the switch has no chart tool and no rule;
  - Harbor never passes the switch;
  - the Harbor run has no chart tool and no rule;
  - the DAB session has no chart tool and no rule;
  - a server without the switch or a sandbox adds no id line.
- The two web files alone (`pnpm exec vitest run src/chart`): `42 expected fail (42)`.

**Why each expected-fail test fails today** (backend run with `--runxfail`, web run with `.fails` stripped)
- `test_charts_server.py`: 21 × `AssertionError: ticket 119: mcp-server has no --charts yet`.
- `test_charts_switch.py`:
  - `ticket 119: run has no --charts yet`;
  - 2 × `assert '--charts' in ['--task=Revenue by month', '--type', 'query', …]`.
- `chart-render.test.ts`: `Error: Chart: not implemented (ticket 120)`.
- `answer-charts.test.ts`:
  - 5 × `answerCharts: not implemented (ticket 120)`;
  - the chat page and recorded conversation fail with `expected [] to deeply equal ['bar','line','area',…]`, meaning 0 charts in the answer area;
  - the run pages fail with `expected [] to have a length of 1` (no answer element yet);
  - the timeline test fails with `expected 'schema' to be 'chart'`;
  - the palette tests fail with `--chart-series-1: expected undefined to be defined`.
- The run page renders on the server without crashing.

**Reference implementations and reverse checks**
- I wrote temporary reference implementations for both backend and frontend; they were not committed and are reverted.
- They are saved in `runs/ref118/` (`backend.patch`, `web.patch`, `web-ref/`), which git does not track.
- With the reference in place and the marks removed, every test passes: backend 29/29, web 42/42. Replaying the fixture against the reference server gives exactly the recorded `display_chart` results.
- Backend: each of these 11 deliberate breakages turns at least one test red:
  - point limit moved to 502;
  - DECIMAL written as text;
  - missing default label;
  - `run --charts` on by default, which reaches the Harbor run;
  - Harbor's command passing `--charts`;
  - DAB's prompt naming `display_chart`;
  - an id line without `--charts`;
  - `stacked` allowed on `line`;
  - `stacked` allowed only on `bar`;
  - next to a sandbox, the chart tool keeping its own result store;
  - `line` refused even unstacked.
- Frontend: each of these 10 turns at least one test red:
  - a failed call going into the answer;
  - charts drawn outside the run page's answer block;
  - a light-theme colour below 3:1 contrast;
  - the two dark blocks disagreeing;
  - colours not wrapping after six;
  - only the first series drawn;
  - rows counted as data points;
  - `stacked` ignored on bars;
  - a verifier's call going into the answer;
  - an empty chart drawing series instead of a note.
- With recharts animation turned on, 6 render tests fail in jsdom. So "draws without animation" is now written into the `Chart.tsx` contract.

**Scrub:** `python -m evals scrub tests/fixtures/charts` gives `changed 0 file(s) of 2; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0`.

**Reviews**

The Fable read-only review stood in for `da-review`, then `eng:code-review main` ran its Standards and Spec checks.

Fixed in `739aa58`:
- added a test of `--charts` together with `--sandbox local` (one id line per result, and the chart tool reads them);
- stacked `area` now succeeds in one test;
- the control call in the `stacked-*` refusal tests now changes only `stacked`;
- added a test that a verifier's `display_chart` call stays out of the answer (contract 7);
- added a geometry check that stacked versus side-by-side bars differ;
- the empty-data test now also requires that no series is drawn;
- the animation and colour-notation requirements are written into the `Chart.tsx` contract;
- the dark-block colours are compared by value, not as strings;
- the Harbor guard no longer matches the brittle `" run --type dbt "` text;
- the switch file's docstring no longer reads as if it tests something it doesn't;
- `chart-render` reads the fixture with the existing `parseEventLines`;
- a loop variable no longer shadows `call`;
- the check "exactly 5 charts on the whole page" became "the failed call is no chart anywhere", so ticket 120 may show charts in the timeline.

Not fixed, with reasons:
- The five chart types are listed three times. Each test file pins the contract on its own.
- `AnswerChart.kind`, `.series` and `.rows` can be derived from the payload. They are frozen in contract 7.
- The helper `has_charts` also asserts that the tool and the rule agree. Its docstring says so, and contract 8 asks for that pairing.
- Copied MCP stdio helpers and `Recorder`: the ticket forbids editing the existing test files they come from.
- "Not through the environment" (contract 1) is not enforced. The contract says to check results only.

**Left for 119 and the human (unchanged from when the ticket was written)**
- `SANDBOX_RULE` (telling the agent to do plotting in the sandbox) conflicts with the charting rule.
- Whether web dbt runs get `--charts`.
- Which tools other than `query_database` allocate ids.

**Confirmation for the main session before merging 119:** run one web conversation turn with `--charts` and a chart-worthy question. Then check the run's `tool_end` events for `mcp__data__display_chart`, for example:

```
grep '"mcp__data__display_chart"' runs/<run-id>/events.jsonl | grep tool_end
```

`content` must be the dict's own compact JSON, `{"chart":{…},"data":[…]}`, not wrapped in `{"result":…}`, and shaped like the fixture. If it differs, return 119; don't change the fixture.

## Contracts fixed in tests, fixture and types (for human review)
1. **The switch:**
   - `data-agent run --charts` and `data-agent mcp-server --charts`, both `store_true`, off by default, also accepted with `--source`.
   - The run is judged only by the tools its MCP server lists and its `system_prompt`.
   - The web run form's query runs and conversation turns pass `--charts`.
   - The Harbor command, the Harbor run and DAB have no `display_chart` in their tools or prompt.
2. **Query ids:** with `--charts`, with or without a sandbox, every successful `query_database` result ends with exactly one id line:
   - it matches the frontend's pattern `^\[result id: [^\]]+\]$`;
   - it starts with `[result id: query_N`, followed by `;` or `]`;
   - ids run `query_1`, `query_2`, …, one numbering across databases;
   - with neither `--charts` nor a sandbox there is no id line, and the last line stays `(N rows)`.
3. **`display_chart` parameters** (checked by meaning, with `$ref` and `anyOf` resolved):
   - exactly `query_id`, `chart_type` (enum of the five types), `x`, `series` (array, `minItems` 1, items `{column, label?}`), `stacked` (boolean, default false) and `title`;
   - required: `query_id`, `chart_type`, `x`, `series`, `title`;
   - no `database` parameter with several databases;
   - `readOnlyHint: true`.
4. **Check failures** are MCP error results (`is_error`) with no traceback:
   - an unknown id: the text names it;
   - a missing x or series column: the text lists every column of the result;
   - a non-numeric series column: the text names it;
   - `scatter` with a non-numeric x;
   - `pie` with 2 series;
   - `pie` with negative values;
   - more than 500 points: 251×2 is refused and the text has `500`, while 250×2 passes;
   - `stacked` on `line`, `scatter` or `pie`.
5. **Success:** the result is a dict.
   - `chart` holds the checked arguments, with `label` defaulting to the column name and `stacked` to false.
   - `data` has one object per row, with only the x and series keys, in the query's order.
   - Numbers (Decimal too) are JSON numbers, DATE values are ISO text, and NULL is `null`.
6. **Fixture:**
   - three queries covering DATE, DECIMAL, INTEGER, DOUBLE and text columns;
   - six `display_chart` calls: bar (stacked, 2 series), line, one error (a missing column), area, scatter, pie, all with distinct titles;
   - then the answer and the result;
   - content is compact JSON, as in the real recordings;
   - it replays against a real `mcp-server --charts`.
7. **Frontend interface:**
   - `ChartType`, `ChartSeries`, `ChartSpec`, `ChartRow`, `ChartPayload`.
   - `AnswerChart {step, seq (seq of the tool_start), payload, kind, series, rows}`. `answerCharts` keeps only the main agent's successful calls, in order, leaving out errors, open calls, unparseable results and verifier calls. `seq` was undefined in the ticket; this is my choice.
   - `Chart({payload, size?})`: root with `data-testid="chart"` and `data-chart-type`, showing the title, and rendering under server-side rendering too. Fixed size when `size` is given. No animation. Colours `var(--chart-series-{i%6+1})`, pie slices by index. Stacked bars of one row share an x. Empty data shows the title and a note and draws no series.
   - Charts sit inside `[data-testid="answer"]`, after the answer text, in call order, on the chat page, the run page and both site views.
   - `toolKind('mcp__data__display_chart')` is `'chart'`.
   - The palette: `--chart-series-1` to `--chart-series-6` in all three theme blocks, as `#hex` or `rgb(r, g, b)` literals, distinct within a block, equal across the two dark blocks, and at least 3:1 against `--panel`.
8. **Charting rule:** it names `display_chart`. The prompt has the rule exactly when the tool is present, and has neither without the switch or on the evaluation entries.

Two constraints go beyond the ticket's wording and are worth a decision when you review: the palette must be written as `#hex` or `rgb(r, g, b)` (no `oklch()` or `hsl()`), and the chart draws without animation.

- 2026-11-23（主会话）：PR CI 绿；Codex GitHub app 审查（`@codex review` 触发）完成，无意见。等人审验收测试后合并。
