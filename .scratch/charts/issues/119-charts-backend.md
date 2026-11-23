# 119 — 答案图表：后端（`--charts`、查询 id、`display_chart`、画图规则、网页命令行）

Status: in-review
Blocked by: 118（done）
Spec: ../spec.md
Claimed by: claude (worktree 119-charts-backend)
Branch: worktree-119-charts-backend

## Goal

`data-agent run --charts` 和 `data-agent mcp-server --charts`（默认都关）打开图表：server 注册 `display_chart`，每次成功的 `query_database` 都分配 `query_N` 并保存完整结果（不管开没开沙箱），系统提示词追加画图规则；网页的表单运行和对话一轮拼命令行时带 `--charts`。不带 `--charts` 时，工具清单、提示词、结果文本和现在一字不变；评测入口（Harbor、DAB、jaffle-shop）不带。

验收的主体是 118 合入 main（d42c579）的测试：`tests/acceptance/test_charts_server.py`、`tests/acceptance/test_charts_switch.py` 里标 `TICKET_119` 的用例全部转绿，守护测试保持通过。转绿时只删 `TICKET_119` 标记（含 `test_charts_server.py:45` 的 `pytestmark = TICKET_119`）和因此没用的定义（`TICKET_119 = pytest.mark.xfail(...)`），断言、夹具、输入一字不改（AGENTS.md 硬规矩）。测试没覆盖的 spec 行为另写单元测试。

## Authority

- May edit:
  - `src/data_agent/mcp_server.py`：`--charts` 下的结果存储和 id 行、注册 `display_chart` 和校验（单库、`--source` 多库都要）；
  - 新增 `src/data_agent/charts.py`（或同名模块）：参数模型、校验、返回值的组装，放 `display_chart` 的逻辑，`mcp_server.py` 只做注册；
  - `src/data_agent/sandbox.py`：只在需要时把 `QueryResults` 挪成和沙箱无关的位置，或改它的说明行（见 Context"查询 id"）；`sandbox_exec` 的行为不变；
  - `src/data_agent/cli.py`：`run` 和 `mcp-server` 的 `--charts`（store_true），`run` 把它传给 `agent.run`；
  - `src/data_agent/agent.py`：只改
    - `build_options`（`:1511`；开着时把 `--charts` 加进 MCP server 的 `args`，`:1570-1580` 一带，并把 `display_chart` 加进允许的工具）；
    - `system_prompt`（`:1322`；开着时追加画图规则）；
    - `run`（`:2062`）的参数，把 `charts` 一路传到上面两处；
    - 任务提示词的拼装（`:2188-2196`，`QUERY_PROMPT.format(...)`）：`SANDBOX_RULE` 是拼进 `QUERY_PROMPT` 的，`--charts` 下那一句的替换在这里做（见 Context"待人定 1"）；
    - 为此新增的常量；
  - `src/data_agent/tool_annotations.py`：`display_chart` 用 `READ_ONLY`；
  - `src/data_agent/web/launcher.py`、`src/data_agent/web/conversations.py`：拼命令行时带 `--charts`，写法照同一处的 `--network`；
  - `tests/acceptance/test_charts_server.py`、`tests/acceptance/test_charts_switch.py`：**只删** `TICKET_119` 标记和因此没用的定义；
  - `tests/`：新增单元测试文件（如 `tests/test_charts.py`），或在已有的非验收测试文件里加用例；
  - `NOTICE`：只在复制了上游代码时登记。
- Must not edit:
  - `tests/fixtures/charts/`（118 合入的验收夹具，事件形状由它定；120 正照它开工）；
  - `tests/acceptance/` 下的其他文件，包括 `test_chat_eval_unchanged.py`、`seam_support.py` 和它们的记录文件；
  - `evals/`（评测入口不带 `--charts`，提示词不变）；
  - `SANDBOX_RULE`、`ANSWER_RULE` 常量本身（`agent.py:189-200`，DAB 的 `evals/dab.py:111` 也用它们）；
  - `web/`（前端由 120 做）、`skills/`、`Makefile`、`.github/workflows/`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access: none
- Spend cap: 工单 agent $0（单元测试不调模型，`.scratch/charts/spec.md:82`）。真实模型运行由主会话跑（见 Evidence required），两三条查数，属于预授权的小运行；可比的先例是工单 97 用同一夹具库的一条查数运行，$0.005（`.scratch/agent-tools/issues/97-sandbox-exec.md` Comments）。

## Context

- spec：`.scratch/charts/spec.md`："开关（Q2）" `:17-22`、"查询 id（Q4）" `:24-27`、"`display_chart` 工具（Q4、Q5）" `:29-49`、"画图规则（Q3）" `:51-57`、"工单"表 `:70-76`（本工单那一行）、"Testing Decisions" `:80-87`。取证笔记：`docs/research/2026-11-23-answer-charts.md`（§1 nao 的 schema 和图表要求，§3.2 结果 id 和事件格式，§3.4 澄清工具"产品有、评测无"的做法，§5(a) 后端落点）。
- **118 定下的契约是本工单的规格**：`.scratch/charts/issues/118-charts-acceptance-tests.md` 的 Context"本工单定下的契约" 1–8 和文末"Contracts fixed in tests, fixture and types"。要点：
  - 开关走 server 的命令行参数 `--charts`，不用环境变量；`--source` 多库 server 也认。
  - id 行要匹配前端正则 `^\[result id: [^\]]+\]$`，以 `[result id: query_N` 开头，紧跟 `;` 或 `]`；之后的说明文字由本工单定。编号从 `query_1` 起、多库共用一套。不带 `--charts` 也不开沙箱时没有这一行。
  - `display_chart` 的参数、必填项、错误文本、成功返回值（dict，`tool_end.content` 是它的 JSON，不套 `{"result": ...}`；Decimal 也是 JSON 数字，DATE 是 ISO 文本，NULL 是 null）。
  - 规则正文要点名 `display_chart`（契约 8）。
- **参考实现**：118 的工单 agent 写过一份能让全部后端测试转绿的参考实现，没提交：`/Users/shenghuikevin/dev/AI/data-agent/.claude/worktrees/118-charts-acceptance-tests/runs/ref118/backend.patch`（反向检查脚本 `reverse_backend.py` 在同一目录）。可以照它做，但它只为验证测试，没过审查：
  - 画图规则只是一行占位（`"Charts: call display_chart only for comparisons, trends or shares."`），要照下面"画图规则"重写；
  - 没碰 `SANDBOX_RULE`（待人定 1）；
  - 网页表单只在查数运行上带 `--charts`（`*(("--charts",) if task_type == "query" else ())`），和本工单待人定 2 的做法不同；那个 worktree 以后可能被清掉，开工时先把需要的文件拷到本 worktree 的 `runs/`。
- 代码现状（main 3a657ab）：
  - id 只在有沙箱时分配：`mcp_server.py:297`（`results = None if sandbox is None else register_sandbox_tool(...)`）、`:407-409`；`QueryResults` 在 `sandbox.py:435-466`，说明行现在是 `[result id: query_N; list it in sandbox_exec's data_files ...]`。同时开 `--charts` 和沙箱时，两个工具读同一份结果（118 的测试有这一条：一个结果只有一行 id）。不开沙箱时的说明行不能提 `sandbox_exec`。
  - `run --sandbox` 默认 `docker`（`cli.py:319`），网页后端总给 `--sandbox docker`（`web/launcher.py:166`、`web/conversations.py:142`）。
  - 澄清工具的开关和注册：`tool_annotations.py:77-83`、`mcp_server.py:312-326`、`agent.py:1621`；`sandbox_exec` 的注册 `mcp_server.py:329-346`，参数加进 server 的命令行 `agent.py:1576` 一带、`cli.py:252-260`。
  - `system_prompt`：`agent.py:1322-1338`（`chat` 时追加 `CLARIFICATION_PROMPT`，画图规则照这个追加）。
  - 返回 dict 的工具先例：`discover`；事件里的样子见 `tests/fixtures/web/real/dbt-cart-abandonment/events.jsonl` 第 24 号事件。
  - `format_rows`：`mcp_server.py:144`，非数值、非 NULL 的值照它的显示写成文本。
- 上游（路径相对 `~/dev/AI/data-agent-refs/`）：nao `nao/apps/shared/src/tools/display-chart.ts:131-190`（参数设计，`SeriesConfigSchema` `:53-72`）、`nao/apps/backend/src/agents/tools/display-chart.ts:15-80`（服务端校验，nao 不查数据，我们要查），系统提示词的 "Chart Rules" `nao/apps/backend/src/components/ai/system-prompt.tsx:193-250`（取证笔记 §1）。这些文件头部没有 `@license Enterprise`，但只照思路写；若复制代码，保留版权头并登记 `NOTICE`。
- **画图规则**（spec `:51-57`）：写成通用规则，不针对任何题目（ADR-0005）：只在比较类别、看趋势、看构成或分布、数据点够多时画；单个数字、两三行的结果不画；一个答案最多一两张图；文字答案必须完整，图只是补充，不能用"见图"代替结论。正文点名 `display_chart`，并说明 `query_id` 只能引用本次运行（对话的本轮）的查询。
- **事件夹具不能变形**：`tests/fixtures/charts/run/` 是合入的验收夹具，120（前端）可以照它并行开工。回放测试会把夹具里的查询和 `display_chart` 输入发给真实的 `mcp-server --charts`，返回值必须和夹具相等。实现和夹具对不上时，改实现，不改夹具；觉得夹具错了就停下交回。
- **评测配置测试已经有了**：spec 工单表本工单那一行的"评测配置测试"和 spec `:21`"纳入 `test_chat_eval_unchanged.py`"，118 已经落在 `test_charts_switch.py` 的守护测试里（那个文件开头写明不许改，118 Comments 有说明）。本工单不改 `test_chat_eval_unchanged.py`，这不是 spec 矛盾，不用停下。
- **spec"验收"一节（`:89-95`）不归本工单**：网页对话里问画图问题、明暗主题、`web-export` 到站点，要等 120 合并后由主会话做。本工单只做下面 Evidence required 里的 CLI 真实运行。
- **并行工单**：112、113（可观测性，`.scratch/observability/spec.md:116-117`）和 116（外部 MCP，`.scratch/mcp/spec.md:87`）也会改 `agent.py` 的 `build_options`、`cli.py`，116 还会改网页两条命令行。后合并的一方 rebase；新参数加在参数表末尾、单独成行，减少冲突。
- **待人定**：下面三条是人 2026-11-23 说"以后再定"的（118 Comments"留给人定或 119 处理的事项"）。规则：合入的测试定了的照测试；没定的照 spec 字面或最保守的做法实现，写成一处就能改的形式（一个常量或一个条件），在最终回复的"待人定"里逐条写明现在的做法、理由、改成另一种要动哪一行，由主会话在合并前拿给人定。**做的过程中不停下来问。**
  1. **`SANDBOX_RULE` 和画图规则冲突**。`SANDBOX_RULE`（`agent.py:197-200`）在查数提示词 `QUERY_PROMPT`（`agent.py:217-229`）里，叫 agent 把 "plotting" 放进 `sandbox_exec`；DAB 的提示词（`evals/dab.py:111`）也用它，评测提示词不能变。本工单的做法：`SANDBOX_RULE` 不动；只在 `--charts` 下，查数提示词换用一句新的常量（去掉 "plotting"，或者指向 `display_chart`），不带 `--charts` 时查数提示词逐字不变。补测试：不带 `--charts` 的查数提示词、DAB 提示词逐字不变；带 `--charts` 时提示词里不再叫 agent 在沙箱里画图。
  2. **网页表单起的 dbt 运行带不带 `--charts`**。spec `:20` 写"网页的表单运行和对话"带，没区分任务类型，和 `--network` 的传法一样（`--network` 对两种类型都传）；118 的测试只要求查数运行和对话带，dbt 不测。本工单照 spec 字面：表单运行不分类型都带（118 的参考实现只给查数带，见上面"参考实现"）。补一个单元测试钉住现在的做法。注意：dbt 运行有校验子 agent（`uses_verifiers`，`agent.py:1341-1343`），dbt 带 `--charts` 时它们会不会也拿到 `display_chart`（未确认），实现时核对并写进最终回复；会拿到的话，最终回复里写明，供人定时参考。
  3. **除 `query_database` 外哪些工具分配 id**。spec `:26` 要求"返回表格结果的其他数据工具，实现时列清"；118 只测 `query_database`。现在返回表格的还有 `query_labels`（`mcp_server.py:519`，`format_rows` 输出）；沙箱下它现在也不分配 id。本工单只给 `query_database` 分配（最保守，和现在沙箱下的范围一致）。最终回复列出所有返回表格结果的工具（逐个看 `register_base_tools` 和多库注册里的工具），写明加进来要改哪里。

## Acceptance

- [ ] `test_charts_server.py`、`test_charts_switch.py` 里所有 `TICKET_119` 用例去掉标记后通过；守护测试照旧通过；这两个文件的 diff 只有删标记和删因此没用的定义
- [ ] `tests/fixtures/charts/` 没有改动；回放测试通过
- [ ] 不带 `--charts` 时：`run` 和 `mcp-server` 的工具清单、`system_prompt`、查数提示词、`query_database` 结果文本都和现在一样（已有测试加上新补的单元测试）
- [ ] 带 `--charts`、不开沙箱时的 id 说明行不提 `sandbox_exec`；带 `--charts` 又开沙箱时，`sandbox_exec` 的 `data_files` 和 `display_chart` 用同一套 id（单元测试或 118 已有用例）
- [ ] 画图规则覆盖 spec `:53-57` 的三点，点名 `display_chart`，不含任何题目相关内容
- [ ] 待人定 1：带 `--charts` 时查数提示词不再叫 agent 在沙箱里画图；DAB 提示词、不带 `--charts` 的查数提示词逐字不变（单元测试）
- [ ] 待人定 2：网页表单的 dbt 运行的命令行带不带 `--charts`，按 Context 的做法有单元测试钉住
- [ ] `test_chat_eval_unchanged.py` 照旧通过（评测入口的工具和提示词不变）
- [ ] `make check` 通过

## Evidence required

- `make check` 的输出（存成文件再看）。
- `uv run pytest tests/acceptance/test_charts_server.py tests/acceptance/test_charts_switch.py -v` 的输出：每个用例 pass。
- 工单 agent 能做的真实运行（不调模型）：用 stdio 起 `data-agent mcp-server --charts`（不带沙箱），对 `tests/fixtures/shop.sql` 建的 DuckDB 先 `query_database` 一次按月汇总，再 `display_chart` 引用它，贴出 id 行和返回的 JSON；再发一次会被拒的调用，贴出错误文本。
- **真实模型运行由主会话跑**（工单 agent 拿不到模型环境变量）。最终回复写好可以直接粘贴的命令，形如：

  ```bash
  mkdir -p runs/119-real && duckdb runs/119-real/shop.duckdb < tests/fixtures/shop.sql
  uv run data-agent run --type query --config full --charts --sandbox docker \
    --duckdb runs/119-real/shop.duckdb --workspace runs/119-real/work \
    --events-dir runs/119-real/events-1 --task "<一个按月看趋势或按类别比较的问题>"
  grep '"mcp__data__display_chart"' runs/119-real/events-1/events.jsonl | grep tool_end
  ```

  （建库命令和其他参数以本仓库现在的 CLI 为准，工单 agent 核对后写准；本地没有 docker 就写 `--sandbox local` 以外的可行做法并说明。）再给一条单个数字的问题，确认不画图。主会话核对：`tool_end.content` 是 dict 的紧凑 JSON `{"chart":{…},"data":[…]}`，不套 `{"result": …}`，形状和 `tests/fixtures/charts/run/events.jsonl` 里的成功调用一致；图的数据和查询结果对得上；文字答案完整。不一致就退回本工单，不改夹具。
- 最终回复的"待人定"一节：三条各写现在的做法、理由、改成另一种要改哪一行。

## Review

第 5 步的 `da-review` 现在用不了：改为派一个只读的 `model: fable` 子 agent 审本分支已提交的 diff（对照 spec、118 的契约和本工单验收标准，只报问题、带 `file:line`，不改文件），结果当 Codex 的意见处理；然后开新上下文跑 `eng:code-review main`。每条要么修（修完提交），要么写明不修的理由。

## Stop conditions

- 某个 `TICKET_119` 用例只有改断言、夹具或输入才能通过，或者实现和 `tests/fixtures/charts/` 对不上而夹具看起来是错的。
- 不改 Authority 以外的文件（`evals/`、`SANDBOX_RULE` 本身、`test_chat_eval_unchanged.py` 等）就做不到"评测提示词和工具不变"。
- spec 自相矛盾，或缺一个需要人定、又不属于上面三条"待人定"的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2 (Fable: 4 fixed; eng:code-review: 1 nit fixed)
- Model spend: $0
- Outcome: in-review, branch `worktree-119-charts-backend`, commits e4eb6ed, 9753c8e, fb43b35

## Comments

- 2026-11-23（工单 agent 最终回复，主会话照抄）：

**What was done**
- **Switches:** `run --charts` and `mcp-server --charts` (single database and `--source`); both default to off.
- **Query ids:** whenever charts or the sandbox is on, `query_database` results get `query_N` ids from one store that `sandbox_exec` and `display_chart` share.
- **The tool:** `display_chart` and its checks live in the new `src/data_agent/charts.py`; `mcp_server.py` only registers it.
- **Prompts:** with `--charts`, the system prompt gets `CHARTS_PROMPT` and the query prompt uses `CHARTS_QUERY_PROMPT`.
- **Web:** form runs and conversation turns pass `--charts`.
- **Tests:** in the two acceptance files I only deleted the `TICKET_119` marks and their definitions (`git diff --stat`: −5 / −7 lines). The extra unit tests are in `tests/test_charts.py` (29 tests).
- **Untouched:** `tests/fixtures/charts/`, `evals/`, `SANDBOX_RULE` and `ANSWER_RULE`.

**Evidence**
- **`make check` on `fb43b35`:** exit 0 (saved in `runs/119/make-check-final.txt`).
  - ruff: "All checks passed!"
  - basedpyright: "0 errors, 0 warnings, 0 notes"
  - pytest: "2666 passed, 2 skipped, 79 deselected, 73 xfailed"
  - web: "Tests 322 passed | 52 expected fail"
- **Acceptance:** `uv run pytest tests/acceptance/test_charts_server.py tests/acceptance/test_charts_switch.py -v` gave "29 passed in 48.10s", every case PASSED, including the fixture replay (`runs/119/acceptance.txt`).
- **Model-free real run:** `uv run python runs/119-real/stdio_run.py runs/119-real/shop.duckdb` starts `data-agent mcp-server --config no-context --duckdb … --charts` over stdio. The database was built from `tests/fixtures/shop.sql`. Output (`runs/119-real/stdio-output.txt`):
  - Monthly revenue query: 7 rows, last line `[result id: query_1; give it to display_chart as query_id to chart it]`.
  - `display_chart(query_1, line, x=month, series=[revenue "Revenue"])`: `is_error=False`, `{"chart":{"query_id":"query_1","chart_type":"line","x":"month","series":[{"column":"revenue","label":"Revenue"}],"stacked":false,"title":"Revenue by month"},"data":[{"month":"2025-03-01","revenue":227.25}, … 7 rows]}`.
  - Pie with 2 series: `is_error=True`, "a pie chart takes one series, not 2".
  - x=region: `is_error=True`, "column region is not in query_1; its columns: month, orders, revenue".

**Reviews**
- **Fable read-only review (stands in for `da-review`):** nothing severe. All four findings fixed in `9753c8e`, each with tests:
  - inf/NaN values passed the checks and produced invalid JSON (`Infinity`). They are now refused with a message saying to filter them out or make them NULL.
  - `Decimal("NaN")` crashed the pie check. The same non-finite check covers it.
  - A result with a repeated column name charted the wrong column. It is now refused with a message saying to alias the columns.
  - The same series column given twice is now refused.
- **`eng:code-review main`:**
  - Spec axis: nothing missing, no conflict with the spec. The round-1 refusals are stricter than spec lines 41-46 but consistent with contract 5.
  - Standards axis: no hard violations. Fixed: the pyright ignore is gone because `is_number` now narrows the type (`fb43b35`). Not fixed:
    - "dead `results is not None` check" (`mcp_server.py`): it is there so the type checker knows `results` is set.
    - "two duplicate-name checks look alike": two lines with different messages; a helper would not make it clearer.
    - "the `charts` flag is threaded through many functions": it follows the existing `network`/`effort` pattern.
    - "make the return value a TypedDict": the contract's types live in `web/src/chart/model.ts` and the acceptance tests.
  - Spec note, not fixed: `CHARTS_SANDBOX_RULE` names `sandbox_exec` even in a run without a sandbox. `SANDBOX_RULE` already did that, and it is not the id line.

**待人定 (decisions left for the human)**
1. **`SANDBOX_RULE` vs the charting rule.**
   - Now: `SANDBOX_RULE` is untouched. Only with `--charts` does the query prompt use `CHARTS_QUERY_PROMPT = QUERY_PROMPT.replace(SANDBOX_RULE, CHARTS_SANDBOX_RULE)` (`agent.py`, near `QUERY_PROMPT`). The new line drops "plotting" and adds "Show a chart with `display_chart`, not by plotting in `sandbox_exec`." Without `--charts`, and in DAB, the prompt is byte-identical (`tests/test_charts.py` and `test_task_file.py`).
   - Why: this is the ticket's prescribed approach.
   - To change: edit the `CHARTS_SANDBOX_RULE` constant, or drop the `query_prompt = …` line in `run` to keep the old wording.
2. **Do dbt runs started from the web form get `--charts`?**
   - Now: yes. Every form run passes `"--charts"` (`web/launcher.py`, after `--network`), pinned by `test_a_dbt_run_from_the_run_form_has_the_switch`.
   - Verifier subagents do not get `display_chart`: their tools are an explicit list (`verifiers.py:102`, `DATA_TOOLS`), pinned by `test_the_verifier_subagents_never_get_display_chart`.
   - To give it to query runs only: change that line to `*(("--charts",) if task_type == "query" else ())` and flip that test.
3. **Which tools get ids.**
   - Now: only `query_database`.
   - Other tools that return table results: `query_labels` (`mcp_server.py`, `format_rows(db.labels.query(...))`). The rest (`list_tables`, `describe_table`, `explain_query`, `classify_text`, context and verifier tools) return catalogs, plans or reports, not query rows.
   - To add `query_labels`: use the same `results.add` / `results.note` pattern as `query_database`, passing `results` into the `query_labels` closure in `register_base_tools`.

**Real-model run (for you to run).** Docker is present here. The database sits outside the workspace, which docker runs require:
```bash
mkdir -p runs/119-real/work && uv run python -c "import duckdb; duckdb.connect('runs/119-real/shop.duckdb').execute(open('tests/fixtures/shop.sql').read())"
uv run data-agent run --type query --config full --charts --sandbox docker \
  --duckdb runs/119-real/shop.duckdb --workspace runs/119-real/work \
  --events-dir runs/119-real/events-1 --task "How did order revenue change month by month in 2025?"
grep '"mcp__data__display_chart"' runs/119-real/events-1/events.jsonl | grep tool_end
uv run data-agent run --type query --config full --charts --sandbox docker \
  --duckdb runs/119-real/shop.duckdb --workspace runs/119-real/work \
  --events-dir runs/119-real/events-2 --task "How many orders are there in total?"
grep -c display_chart runs/119-real/events-2/events.jsonl   # expect 0
```
For run 1, check that `tool_end.content` is `{"chart":…,"data":…}` with no `{"result":…}` wrapper, and that the data matches the query rows.

- 2026-11-23（主会话）：PR CI 绿；Codex GitHub app 审查完成，无意见。合并前：真实运行（`runs/batch2/live-checks.sh`）和"待人定"交人定。
