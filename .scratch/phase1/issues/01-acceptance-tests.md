# 01 — 三个接缝的验收测试与夹具

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-code (opus-5-5)
Branch: worktree-01-acceptance-tests

## Goal

spec 约定的三个接缝（A：`data-agent run` CLI；B：MCP server；C：评测 CLI）各有一组验收测试和所需夹具，并在测试里定下各接缝的外部接口。测试全部标 `xfail(strict=True)`，`make check` 保持绿。

## Authority

- May edit: `tests/acceptance/`、`tests/fixtures/`、`pyproject.toml`（只加 pytest 标记配置）
- Must not edit: 其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none（本工单不跑 integration）

## Context

spec「Testing Decisions」三个接缝的核验项；spec「Implementation Decisions」里的 CLI 接口、MCP 工具分组、评测 CLI 功能；CONTEXT 的 Read-only gateway、Discover→hydrate、Dictionary search、Evaluation holdout 词条。夹具：小 DuckDB 文件（几张有脏键和枚举值的表）、迷你 dbt 项目（2–3 个模型）、伪造的 Harbor 结果目录与事件文件（含开发集与留出集各几题、含出错运行）。

## Acceptance

- [x] 接缝 A（`integration` 标记）约 4 个：查数答案在容差内；dbt 模型建成且数值正确；事件文件版本头在第一条、序号连续、结果事件带 token 与花费；基线配置下无验证者事件和工具；要求写库的任务被拦且运行不崩溃
- [x] 接缝 B 约 12–15 个：工具列表随配置变化；SELECT 可执行；DDL、DML、危险函数各若干条被拦并返回可读错误；discover 只回引用与 ≤200 字摘要；hydrate 回全文；dictionary search 回真实取值；6 个验证工具在 DuckDB 夹具上各一个通过、一个失败用例；Postgres 版用例标 `integration`
- [x] 接缝 C 约 9 个：同一种子抽样相同且按难度分层；算分与 `metrics.py` 一致；出错记 0；bootstrap 区间在已知数据上符合预期；留出集报告不含任何逐题结果或题号；开发集报告含逐题结果；scrub 移除 key、私有端点、IP、主机名；累计花费到 80% 停止
- [x] 所有测试标 `xfail(strict=True)`；`make check` 通过
- [x] 测试只测外部行为（CLI 输入输出、MCP 客户端看到的结果、评测 CLI 输出文件），不引用任何内部函数

## Evidence required

`make check` 输出；`uv run pytest tests/acceptance -q -rx` 输出（显示每个测试都是 xfail）。人审并合并后，后续工单才能开工。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2（Codex `da-review` 1 轮 + `eng:code-review` 1 轮，均已修完）
- Model spend: $0（本工单不调 data agent 模型）
- Outcome: accepted (PR #1, squash 04759a8)
- Human review minutes: —

## Comments

### 2026-10-15 claude-code（opus-5-5）：实现完成，交人审

**提交**（分支 `worktree-01-acceptance-tests`）：a4704a6 初版；7362794 修 Codex 审查；c09bf31 修 eng:code-review。

**产出**
- `tests/acceptance/`：接缝 A 5 个（`integration`）、B 16 个函数（含 DuckDB/Postgres 参数，Postgres 标 `integration`）、C 8 个；共 39 个默认用例 + 15 个 integration 用例，全部 `xfail(strict=True)`，reason 写明负责转绿的工单。xfail 标在每个函数上；Postgres 参数和"按配置开关"的每个配置各自带一个 xfail，免得一个工单转绿时别的用例 XPASS 让 `make check` 变红。
- `tests/fixtures/`：`shop.sql`（脏键 `C0001 / c0001 / ' C0001' / 'C-0001 '`、脏枚举 tier/status、40 列宽表、2 个故意建错的模型表；DuckDB 与 Postgres 通用）、`dbt_shop/` 迷你 dbt 项目（3 个模型）、`harbor_job/` 伪造 Harbor 结果（开发集 3 题 + 留出集 4 题 × 3 次，2 次出错运行，每次带 `agent/events.jsonl`）、`eval_manifest.json`。
- 各接缝的外部接口写在三个测试模块的 docstring 里（CLI 参数、6 个配置名、工具名与参数、事件字段、评测 CLI 子命令与输出文件）。

**需要人看的决定**
1. **没提交 DuckDB 二进制**：用 duckdb 1.2.2 建出来是 2.6 MB，超过 pre-commit 的 1000 KB 上限。改为每个测试从 `shop.sql` 现建到 `tmp_path`，同时满足"每个 worktree 用自己的 DuckDB 文件"。
2. **验证工具测"观察值"不测结论字段**：SignalPilot 的 6 个工具只输出观察（行数、比例、模型值），由验证者 agent 判断通过与否。所以失败用例检查的是缺陷能被看出来：例如 fan-out 表 74 行对源表 37 行、模型值 18 对正确的 9。代价是"通过"用例偏弱，尤其 `list_tables` 的失败用例只是过滤不到表（spec 审查也指出了）。如果要工具直接给出结论，需要另定字段，属于接口决定。
3. **工单 05 的一句和 strict xfail 冲突**：05 写"基线配置无验证者痕迹的测试，若验证层尚未实现，保持 xfail"。但验证层不存在时，基线自然没有验证痕迹，测试会 XPASS（strict 下算失败）。所以我把它的 reason 改成 ticket 05，由 05 去掉标记。请人确认，或改 05 的工单文字。
4. **02、03 要先认 `--config full`**：接缝 B 的用例都带 `--config full`，A 的版本头要求 `config == "full"`。02、03 在 05 之前，需要先接受这些配置名（最小桩即可）。

**真实运行证据**（测试本身就是这张工单的行为）

`make check`：
```
uv run ruff check .
All checks passed!
uv run ruff format --check .
47 files already formatted
uv run basedpyright
0 errors, 0 warnings, 0 notes
uv run pytest -q
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.                                 [100%]
1 passed, 15 deselected, 39 xfailed in 0.44s
```

`uv run pytest tests/acceptance -q -rx`（逐条见本地输出，这里只贴尾行）：
```
15 deselected, 39 xfailed in 0.45s
```
`uv run pytest tests/acceptance -q -rx -m integration`：
```
39 deselected, 15 xfailed in 0.46s
```

**测试本身的正确性**：只证明"现在全 xfail"不够，所以在仓库外用一次性参考实现跑过一遍（`--runxfail`），没有提交：
- 接缝 C：`/tmp` 下写了一个参考 `evals` CLI（算分照抄上游 metrics.py 的公式），8/8 通过。预期分数由上游 `data-eng-bench/leaderboard/src/leaderboard/core/metrics.py` 直接算出：全量 52.38 ± 9.52、pass@2 0.7143、pass@3 0.8571；开发集 44.44；留出集 58.33。改成不配对的 bootstrap 后，测试能抓到（`ci_low = -40`）。
- 接缝 B：一个参考 MCP server（mcp 2.3.0 + duckdb 1.2.2），DuckDB 部分 31/31 通过。另把 DDL/DML/危险函数的全部语句，在两种方言下交给 SignalPilot 真实的 `engine.validate_sql` 跑：都被拦，且提示里带网关措辞；两条合法 SELECT 都放行。
- 接缝 A：参考"agent"用 dbt-duckdb 1.10.0 真跑 `dbt run`，判定逻辑通过。把模型改成不处理脏 status 后，测试能抓到。
- Postgres：用 colima 起 `postgres:17`，`shop.sql` 连续加载两次都成功，关键数据（37 / 74 / 9 / 18 / 40 列）与 DuckDB 一致。用完已停掉容器和 colima。
- 测试期间临时往 `.venv` 装过依赖，事后已 `uv sync` 恢复；`uv.lock` 和 `pyproject.toml` 都没改。

**审查结果与处理**
- Codex `da-review main`（3 条，都修了，7362794）：答案解析丢了负号；dbt 测试没校验一行一客户；bootstrap 用例分不出配对和不配对。
- `eng:code-review main`：
  - 修了（c09bf31）：
    - 配置用例提前 XPASS → 按用例分配 xfail。
    - 基线测试在全文里搜工具名，会被文本增量误伤 → 改为只看 `tool_start` 的工具名。
    - `verify_model_values` 子串匹配太宽 → 改整数匹配，并确认正确模型的输出里不出现 18。
    - `dictionary_search` 不应断言不含 Platinum → 已删。
    - 工具名集合在两个文件里重复 → 并入 `seam_support`。
    - 小节标题误导、重复的 SQL → 已改。
  - 不修：
    - "解析失败时网关提示不含关键词"：已用真实 engine 跑过，全部语句都正常解析并给出 Blocked 提示。
    - "写库测试不要求事件里出现拦截记录"：故意的。安全性看的是数据有没有变，以及运行有没有走完。要求事件里一定有拦截，等于要求模型一定先去尝试写，测试就会依赖模型行为。
    - 事件文件不泄露密钥的断言（审查认为超出本工单范围）：spec 事件流一节要求遮盖，留着。
    - 37、12 这类数字没提成常量；`run_agent` 参数多；`query_run` 返回元组：测试里直接写数字更直观，不改。
    - `list_tables` 的失败用例偏弱：见上面"需要人看的决定"第 2 条。
