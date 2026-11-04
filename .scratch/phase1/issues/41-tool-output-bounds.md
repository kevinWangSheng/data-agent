# 41 — 工具输出加上限：list_tables、query_database 照 SignalPilot 截断，scan 只打印摘要

Status: in-review
Blocked by: 39
Spec: ../spec.md
Claimed by: claude (worktree 41-tool-output-bounds)
Branch: 41-tool-output-bounds

## Goal

在约 2400 个模型的 dbt 库上，`list_tables`、`query_database` 和 `scan_project.py` 的输出都在模型读得下的范围内。截断时明确告诉模型还有多少没显示、怎么缩小范围，不会再出现只拿回一条"超长"报错、一行数据都没有的情况。

## 已和用户定下的（2026-11-04）

- 类别：**改变行为的工单**（ADR-0008 Update 2026-11-04），要跑初筛，见下面的 Acceptance。
- `list_tables` 和 `query_database` 的上限照 SignalPilot 抄，数值和截断顺序不要改：
  - **`list_tables`**（`signalpilot/gateway/gateway/mcp/tools/schema/catalog.py:109-110,143-210`）：
    - 新增参数 `schema`（不区分大小写）、`max_tables`（默认 200）、`columns`（默认 true）；保留 `name_contains`。
    - 表按 (schema, name) 排序后取前 `max_tables` 张。
    - 输出总长度上限 36,000 字符：超了先去掉列名，还超就从末尾去掉表。
    - 有表没显示时，最后一行写 `{n} more tables not shown; narrow with schema= or name_contains=`。
    - 每张表的行数估计照旧（工单 37）。
  - **`query_database`**（`signalpilot/gateway/gateway/mcp/tools/query.py:248-271`）：
    - 至少显示 50 行；超过 50 行后，按 12,000 字符的预算继续显示，最多 400 行。
    - 没显示完时，加一行 `[INCOMPLETE DISPLAY] ...`，照 SignalPilot 原文，提示加 WHERE 或 `OFFSET {已显示行数}`。
    - `row_limit` 参数的含义不变（数据库最多返回多少行），显示上限是另一层。
- **`scan_project.py`**：
  - 完整报告写到 workspace 里的 `.data-agent/scan/<项目目录名>.md`。
  - 标准输出只打印摘要，最多 20,000 字符。顺序是：Skill 第 1 步要求记录的 5 节（STUBS TO REWRITE、MODELS TO BUILD、DEPENDENCIES、REQUIRED COLUMNS、AVAILABLE MACROS）完整放在最前面，放得下就全放；其余各节按顺序，放不下的地方写 `… N more lines in <报告路径>`。
  - 最后一行给出完整报告的路径。
  - 这个文件是从 signalpilot-plugin 复制的，开头已经写了改动说明，在那里追加这次改了什么。
- **Skill**（只改 `skills/dbt-workflow/SKILL.md` 第 1 步和 Tools 一节，即第 37–55 行和第 91–107 行）：
  - "Read the ENTIRE output of BOTH" 改成：先读 scan 摘要，要看细节就 Grep 报告文件；`list_tables` 的结果有截断行时，用 `schema=` 或 `name_contains=` 缩小范围。
  - 其他步骤不改。第 8 步留给验证层的工单。
- 不改 DAB 包装层（`evals/dab_server.py`）的 `list_tables`：DAB 的库小，这次没有证据说明它有问题。

## Authority

- May edit：`src/data_agent/mcp_server.py`、`src/data_agent/db_facts.py`（只改格式化相关）、`skills/dbt-workflow/scan_project.py`、`skills/dbt-workflow/SKILL.md`（只能改第 37–55 行和第 91–107 行）、`NOTICE`、`tests/`（`tests/acceptance/` 除外）
- Must not edit：`src/data_agent/context_tools.py`、`src/data_agent/verifier_tools.py`、`src/data_agent/configs/`、`evals/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none（初筛和真实运行由主会话做）
- Spend cap：$0.3（单题真实运行）；初筛由主会话跑

## Context

- 证据（`runs/accept-2/reading/design-audit/audit-db-tools.md` 第 2、5 条，`audit-harness.md` 第 3 条）：
  - `list_tables` 677 次调用里有 210 次报 "exceeds maximum allowed tokens"，出现在 180 个 trial 里的 125 个，覆盖全部 30 道题。不带过滤时输出 66.5 万字符。
  - scan 输出 758KB，模型只看到 2KB 预览。
  - `query_database` 有 3 次超长，12 次超过 2.5 万字符。
- Claude Code 的 MCP 输出上限默认是 25,000 token（环境变量 `MAX_MCP_OUTPUT_TOKENS`）。超了会把结果存成文件；存文件失败才返回报错。工单 40 放开了读这些文件，但不能依赖这条路。
- 复制 SignalPilot 代码时，要保留版权头并登记 `NOTICE`（Apache-2.0）。
- 读轨迹的结论：输出超长不是哪道题失败的直接原因（`runs/accept-2/reading/design-audit/shared-*.md`），所以这张工单的初筛以过程指标为主。

## Acceptance

- [ ] 单元测试：
  - [ ] `list_tables`：用 300 张表的 DuckDB 夹具，默认显示 200 张并有 footer；`schema=` 能过滤；`columns=false` 不带列名；输出超过 36,000 字符时，先去掉列名再去掉表，结果 ≤36,000 字符。
  - [ ] `query_database`：1000 行的小行全部显示到 400 行为止并带 `[INCOMPLETE DISPLAY]`；宽行时 50 行之后按 12,000 字符截断；10 行以内不带提示。
  - [ ] `scan_project.py`：对一个大项目夹具（可以由测试生成），标准输出 ≤20,000 字符，前 5 节放在最前，最后一行是报告路径，报告文件内容完整。对现有的 `tests/fixtures/dbt_shop`，输出内容和之前一致，只多了最后一行路径。
- [ ] `tests/acceptance/` 原样全部通过。
- [ ] `make check` 通过。
- [ ] **初筛**（主会话，full，每题 3 次，DeepSeek 闲时；对照 accept-2 full，不用 dev-3）：
  - 目标题：accept-2 full 里输出超长次数最多的 3 道，排除题面和评分器不一致的题：
    - dbt-fix-customer-snapshot-and-build-dimension，25 次；
    - dbt-fraud-detection-model，13 次；
    - dbt-customer-churn-cohorts，13 次。
    - accept-2 full 在这 3 道题上都是 3/3。
  - 过关只看过程指标（2026-11-04 和用户定），从轨迹里机械地数，不靠人判断。9 个 trial 里下面两项都是 0：
    - "exceeds maximum allowed tokens" 报错的次数；
    - 工具输出被存成文件（"Output has been saved to" / `tool-results/`）的次数。
  - 成功次数只记录，不作为过关条件。按 ADR-0008 Update 2026-11-04，这类修复是"目标题没涨不合入"的例外；分数的影响到统一评估里看，退步由冒烟测试和统一评估的回归集兜底。

## Evidence required

单元测试输出；`make check` 输出；对 dbt_shop 夹具的 scan 输出前后对比。

## Stop conditions

- 按 SignalPilot 的上限做，单元测试里的大夹具仍然超过 25,000 token。
- scan 的 5 节本身就超过 20,000 字符：停下，贴出实际大小，交主会话决定。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1（Codex 1 条已修；eng:code-review 2 处清理已修，其余写明理由不修）
- Model spend: $0（工单会话无模型调用；初筛花费由主会话记）
- Outcome: in-review，branch `worktree-41-tool-output-bounds`（commits 3d2284a、0d18c9a、3de27a6）；初筛待主会话
- Human review minutes: —

## Comments

### 2026-11-04 工单会话（worktree 41-tool-output-bounds，commits 3d2284a、0d18c9a、3de27a6）

改动：`mcp_server.py` 的 `list_tables` 照 SignalPilot 限 200 张表、36,000 字符，加 `schema=`、`columns=`、`max_tables=`，结尾说明还有多少没显示；`query_database` 照 SignalPilot 限 400 行 / 50 行后 12,000 字符并带 `[INCOMPLETE DISPLAY]` 提示；`scan_project.py` 标准输出限 20,000 字符、前 5 节在前、最后一行是完整报告路径（`<project_directory>/.data-agent/scan/<name>.md`）；`SKILL.md` 对应三处改法；`NOTICE` 登记复制自 SignalPilot 的部分。

**Step 3 real run (2026-11-04).**

Scan on a copy of `tests/fixtures/dbt_shop`:

```
uv run python skills/dbt-workflow/scan_project.py /tmp/scan41/dbt_shop > /tmp/scan41_after.txt
diff /tmp/scan41_before.txt /tmp/scan41_after.txt
31a32
> Full report: /private/tmp/scan41/dbt_shop/.data-agent/scan/dbt_shop.md
diff /tmp/scan41_before.txt /tmp/scan41/dbt_shop/.data-agent/scan/dbt_shop.md   # identical
```

Scan on dbt_shop plus 800 orphan SQL files: stdout 19,9xx chars (20,749 bytes), report file 41,916 bytes. Heading order in stdout: title, STUBS TO REWRITE, MODELS TO BUILD, DEPENDENCIES, REQUIRED COLUMNS, then the deps note, COMPLETE MODELS, SQL FILES WITHOUT YML cut at `… 422 more lines in <path>`, last line `Full report: <path>`.

Tools over stdio (`data-agent mcp-server --config full --duckdb` on a 300-table file, 3 schemas):

```
list_tables {}                                   -> 7864 chars, 201 lines; last: "100 more tables not shown; narrow with schema= or name_contains="
list_tables {schema: B, columns: false, max_tables: 3} -> "b.t000 (base table, ~0 rows, 2 cols)" ... "97 more tables not shown; ..."
query_database SELECT range AS n FROM range(1000) -> 400 rows, "(1000 rows)", "[INCOMPLETE DISPLAY] 1000 rows total; only the first 400 are shown above. ... OFFSET 400 ..."
```

`make check`: ruff and format clean, basedpyright 0 errors, 1467 passed, 1 skipped (includes `tests/acceptance/`, untouched).

**Codex review (`da-review main`), 1 finding.** P1: when the five Step 1 sections alone exceed 20,000 chars the summary cut them silently. Fixed in 0d18c9a: the summary appends `WARNING: the Step 1 sections alone exceed the summary budget; cut or missing above: <sections>. Read them in the full report.` with a test (300 YML-only models). This goes beyond the ticket's stop condition, which was about the real project; the check-on-the-real-project still belongs to the main session's 初筛.

**eng:code-review main.** Standards: no hard violations. Fixed: test helper that shared the name of a production function, and a placeholder-then-filter construction in the empty-result message (3de27a6). Not fixed: the budget-fill loops in `format_rows` and `render_table_listing` are each copied verbatim from a different SignalPilot function with different shapes (floor and max rows vs tail reserve), so sharing a helper would change the copied code; the `(full, compact)` tuple and the six-argument `table_lines` are local to one module and small. Spec: no missing acceptance item; Authority respected (SKILL.md hunks at original lines 42, 52–54, 99). Decisions to record: a report that fits in 20,000 chars is printed whole, so the dbt_shop output is unchanged (reordering happens only when cutting); `list_tables` prints no `Tables: N` header (ticket fixes only numbers and truncation order); `max_tables < 1` is a tool error, as upstream's `list_tables` returns; the report is written under the directory passed to the scan, which the Skill calls `<project_directory>`. When the dbt project is a subdirectory of the workspace, that is the workspace; when the project is the argument, `.data-agent/scan/` lands inside the project tree. 未确认 whether the eval grader objects to an extra dot-directory there.
- 2026-11-04 主会话：PR #42 CI 绿。Codex app 一条 P1（`mcp_server.py:98`）：前 50 行不受 12,000 字符预算约束，`SELECT repeat('x', 1000000) FROM range(50)` 这类查询仍能返回约 50 MB。核对：工单"已和用户定下的"明确写了"至少显示 50 行"、"数值和截断顺序不要改"，照 SignalPilot `query.py:248-271` 抄，这是约定的设计，不算偏离，**不退回**。单个超宽值的风险属实，记为已知限制，建议对齐会话决定是否另加"单个值截断"（SignalPilot 是否有这层，未确认）。初筛 `t41-screen` 已在服务器开跑（commit 3de27a6，3 题 × 3，full，`--budget 6`）。
- 2026-11-04 主会话：初筛 `t41-screen`（commit 3de27a6；`ablate --split dev --config full --task dbt-fix-customer-snapshot-and-build-dimension --task dbt-fraud-detection-model --task dbt-customer-churn-cohorts --trials 3 --budget 6`）：成功 9/9（accept-2 full 也是 9/9）；连冒烟实际价 $0.9926（cost_usd $1.9853，10 个事件文件全在闲时）。过程指标（机械计数，`runs/t41-screen/jobs/full/*/agent/events/`）："Output has been saved to" 0 次、`tool-results/` 0 次；**"exceeds maximum allowed tokens" 2 次**，都在 trial dbt-fix-customer-snapshot-and-build-dimension__f9SPTjp，来源是内置 Read 读 `/app/dbt_models_duckdb/target/run_results.json`（dbt 自己生成的 1,049,294 token 单行 JSON，`limit: 60` 和 `limit: 20` 两次），不是 `list_tables`、`query_database`、scan 的输出。本工单约束的三个工具在 9 个 trial 里没有触发过 Claude Code 的超长报错。过关条件按字面是"次数为 0"，这两次不在 41 的改动范围内，**合不合入交人定**；若按字面判不过，可能的后续是守卫或 Skill 不让 Read `target/*.json`。PR #42 CI 绿，Codex 一条 P1 已按设计记录（见上）。
- 2026-11-04 主会话：**停止条件"scan 的 5 节本身就超过 20,000 字符"在真实项目上成立**。`t41-screen` 9 个 trial 的 scan 标准输出都是 19,930 字符，含 STUBS TO REWRITE、MODELS TO BUILD、DEPENDENCIES、REQUIRED COLUMNS 四节，末尾都带 `WARNING: the Step 1 sections alone exceed the summary budget; cut or missing above: AVAILABLE MACROS. Read them in the full report.`；9 个 trial 都打出了 `Full report:` 路径和 `… N more lines in` 的截断行。`list_tables` 的 "more tables not shown" footer出现 17 次，`[INCOMPLETE DISPLAY]` 0 次。完整报告文件留在容器里，Step 1 五节的实际总大小未取回（未确认）。要人定：按字面停下不合入；或接受"前四节完整 + 宏去报告里 Grep"合入；或把预算提到能放下五节（会超过 Claude Code 25,000 token 上限的约 100,000 字符，不可行）。
