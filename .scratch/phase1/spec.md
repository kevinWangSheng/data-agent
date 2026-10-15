# 一期：通用 data agent 与评测管线

Status: ready-for-agent

依据：`docs/plan.md` 第 1–7 节、`CONTEXT.md`、ADR-0001～0007。测试接缝经人确认（2026-10-15）。本 spec 不含网页界面、Memory、产品内澄清。

## Problem Statement

分析师和数据工程师手里有 dbt 项目和若干数据库（DuckDB、Postgres），常见的活是两类：按业务问题跨库查数，和改 dbt 模型（新建、修 bug、补字段）。通用编码 agent 能写 SQL，但不懂"先探索到什么程度、何时动手、怎么自检"，容易取错表、写错口径、改完不验证；直接给它数据库写权限又不安全。

作为求职作品，作者还需要拿出可信的证据：这个 agent 在公开榜单（data-eng-bench、DAB）上比同模型同工具的基线强，并且能用消融说明每一层 agent 设计各贡献了多少，而这些数字可复现、没有在留出集上偷看答案。

## Solution

一个可自托管的命令行 data agent：给它一个任务和一个工作区（dbt 项目、数据库连接），它按 Skill 规定的工作流探索、执行、由验证子 agent 自检，然后交付答案或改好的 dbt 项目。所有对数据库的 SQL 都经过只读网关；唯一的写方式是改 dbt 项目后 `dbt run`。每次运行留下一份结构化事件文件，记录过程、版本与花费。

围绕它有一套评测管线：通过 Harbor 跑 data-eng-bench，用自写 runner 跑 DAB，用 jaffle-shop + Postgres 验证"接任意 Postgres + dbt"。开发集用于失败归因和改 Skill；留出集只出汇总分数，用来报告基线对比和 6 个消融配置，差值带配对 bootstrap 置信区间。

## User Stories

### 用 agent 做事

1. As a 数据工程师, I want to run `data-agent run` with a task description and a dbt project directory, so that the agent builds or fixes the models for me.
2. As a 分析师, I want to ask a business question against a DuckDB file or a Postgres database, so that I get a numeric answer without writing SQL myself.
3. As a 数据工程师, I want the agent to explore the schema before acting, so that it does not guess table or column names.
4. As a 数据工程师, I want the agent to follow a fixed dbt workflow (understand → plan → write → run → verify), so that its behavior is predictable.
5. As a 分析师, I want the agent to use the correct SQL dialect for my database, so that queries do not fail on syntax differences.
6. As a 数据工程师, I want the agent to look up real column values when keys or enums look dirty, so that joins and filters match the data.
7. As a 数据工程师, I want schema search to return short references first and full detail only on request, so that wide databases do not flood the agent's context.
8. As a 数据工程师, I want the agent to find likely join keys across tables, so that cross-table work starts from real evidence.
9. As a 数据工程师, I want a structural verifier to check that the models I asked for exist with the right columns, grain and sources, so that obvious mistakes are caught before delivery.
10. As a 数据工程师, I want a value verifier to check row counts and key values against the source data, so that silently wrong numbers are caught.
11. As a 数据工程师, I want the agent to reproduce a verifier's failure before fixing it and re-verify the changed models, so that fixes are targeted and confirmed.
12. As a 数据工程师, I want the agent to stop when every check passes or is not applicable, or when its turn budget runs out, so that runs end in bounded time and cost.
13. As a 分析师, I want the agent to state the assumptions it made when the question was ambiguous, so that I can judge the answer.
14. As a 数据平台负责人, I want every SQL statement from the agent to be parsed and blocked if it is DDL, DML or a dangerous function, so that the agent can never change my data directly.
15. As a 数据平台负责人, I want the only write path to be dbt project edits materialized by `dbt run`, so that every change is reviewable as code.
16. As a 数据平台负责人, I want blocked statements to return a clear error to the agent, so that it can recover instead of looping.
17. As a 自托管用户, I want to point the agent at any Anthropic-compatible endpoint and model through `DATA_AGENT_*` variables, so that I can choose a cheaper provider such as DeepSeek.
18. As a 自托管用户, I want the agent's model settings to apply only to its own process, so that my own Claude Code setup is not affected.
19. As a 自托管用户, I want each run to have a spend cap, so that one bad run cannot burn my budget.
20. As a 自托管用户, I want clear exit codes and a final answer on stdout, so that I can script the agent.

### 运行记录

21. As a 项目作者, I want every run to write an append-only JSONL event file with sequence numbers, so that the run can be replayed, scored and later shown in a UI.
22. As a 项目作者, I want the first event to be a version header (git commit, Skill version, config name, model, SDK version), so that every result can be traced to exactly what produced it.
23. As a 项目作者, I want tool calls, SQL statements, verifier start and end, errors and the final result recorded as events, so that failure attribution has the full trace.
24. As a 项目作者, I want the final event to carry uncached input, cached input and output tokens plus dollar cost, so that cost is reported per task.
25. As a 项目作者, I want event files to contain no API keys, private endpoints or server addresses, so that trajectories can be published.

### 消融开关

26. As a 项目作者, I want each agent layer (Skill, verifier subagents, context strategy) to be switchable by a named run config, so that ablations change one layer at a time with everything else equal.
27. As a 项目作者, I want a baseline config with all layers off using the same model, tools and budget, so that "beats baseline" means the agent design, not the model.
28. As a 项目作者, I want to run the full config with an older Skill version, so that the failure-attribution loop's benefit can be measured.
29. As a 项目作者, I want a layer that is off to leave no trace in the run (no tools, no events), so that I can verify the switch from the event file.

### 跑榜与评测

30. As a 项目作者, I want a Harbor agent adapter, so that data-eng-bench runs our agent exactly as the leaderboard does.
31. As a 项目作者, I want the adapter to install the agent in its own environment and use the image's dbt, so that versions match the official setup.
32. As a 项目作者, I want web search and fetch disabled during benchmark runs, so that the agent cannot look up public answers.
33. As a 项目作者, I want transient model errors (rate limits, 5xx, disconnects) retried and timeouts or refusals not retried, so that infrastructure noise does not count as agent failure.
34. As a 项目作者, I want token counts and cost filled back into Harbor's results, so that the leaderboard report includes cost.
35. As a 项目作者, I want a DAB runner that uses DAB's own tools and official hint files from a local clone, so that our DAB score is comparable and no DAB code is copied into this repo.
36. As a 项目作者, I want DAB runs to produce the submission JSON and trajectories DAB requires, so that I can submit.
37. As a 项目作者, I want a reproducible holdout sample (stratified by difficulty, fixed seed committed to git), so that the holdout cannot drift.
38. As a 项目作者, I want the dev set to be the official `fast-30` list, so that failure attribution uses a known set.
39. As a 项目作者, I want scoring identical to the leaderboard's (accuracy ± per-task standard error, pass@k, errored runs count 0), so that local and public numbers agree.
40. As a 项目作者, I want paired per-task bootstrap 95% intervals for "config vs baseline" and for each layer's contribution, so that I only claim improvements the data supports.
41. As a 项目作者, I want an ablation runner that runs the 6 configs × 3 runs on the holdout, so that the ablation table is one command.
42. As a 项目作者, I want holdout reports to output aggregate scores only, never per-task outcomes or failure details, so that the holdout stays blind (ADR-0004).
43. As a 项目作者, I want dev-set reports to include per-task outcomes and trajectories, so that I can attribute failures.
44. As a 项目作者, I want infrastructure errors counted separately from agent failures in local ablations, so that reruns fix infrastructure without touching scores.
45. As a 项目作者, I want a scrub command that removes keys, private endpoints, IPs and hostnames from trajectories and must run before upload, so that public trajectories leak nothing.
46. As a 项目作者, I want a cumulative spend tracker that stops batch runs at 80% of the budget, so that I decide before overspending.
47. As a 项目作者, I want a failure-attribution command that reads dev-set failures and drafts a report (one FM1–FM4 label per failure, quoting the Skill text involved, proposing a general edit), so that Skill changes are evidence-based.
48. As a 项目作者, I want attribution proposals to be generic and reviewed by me before any Skill change, so that Skills never contain benchmark-specific content (ADR-0005).

### 接任意 Postgres + dbt

49. As a 自托管用户, I want the agent to work on dbt's `jaffle-shop` with a local Postgres, so that I can see it work outside benchmarks.
50. As a 项目作者, I want about 5 jaffle-shop cases (query cases with numeric tolerance, dbt cases checking rebuild, row count, grain and columns), so that "works on any Postgres + dbt" is tested.
51. As a 项目作者, I want 1–2 negative cases where the task asks for a direct write, so that the read-only gateway is shown to block it end to end.
52. As a 面试官, I want to reproduce a reported score from the version header and committed configs, so that I can trust the numbers.

## Implementation Decisions

- **骨架**（ADR-0001）：Python 3.12 命令行，主循环是 Claude Agent SDK 一次 `query()`，SDK 版本锁精确。模型经 `DATA_AGENT_BASE_URL` / `DATA_AGENT_API_KEY` / `DATA_AGENT_MODEL` 配置，只传给 SDK 子进程（映射成它需要的 `ANTHROPIC_*`），并以 `setting_sources=["user"]` 启动，不读本仓库的 `.claude/`。默认 DeepSeek `deepseek-flash`。
- **CLI 接口**：`data-agent run` 接收任务（文本或文件）、任务类型（`dbt` 或 `query`，由调用方给出，不靠推断）、工作区目录、连接（DuckDB 文件路径或 Postgres 连接串，后者从环境变量读）、运行配置名、事件文件输出目录。最终答案写 stdout；退出码区分成功、agent 未完成、配置或基础设施错误。
- **运行配置**：一个命名配置决定各层开关、Skill 版本、最大轮数（dbt 200、查数 50、大库 75，照 SignalPilot）和单次 `max_budget_usd`（flash 先设 $1）。至少有 6 个：基线（全关）、全开、关 Skill、关验证子 agent、关上下文策略、全开旧版 Skill。配置文件进 git。
- **Skill**：从 signalpilot-plugin 复制 dbt-workflow、dbt-write、dbt-debugging、sql-workflow、duckdb-sql、sqlite-sql，保留版权头并登记 `NOTICE`；按同样格式自写 postgres-sql。dbt 任务先加载 dbt-workflow，查数任务加载 sql-workflow 加方言 Skill。Skill 版本取 Skill 目录内容的哈希。Skill 中不得出现任何榜单专属内容。
- **验证子 agent**（ADR-0002，plan §3）：结构、数值两个只读验证者，用 SDK `agents=` 定义，内容照 signalpilot-plugin 的 verifier / value-verifier，逐项给"通过 / 失败 / 需更多信息 / 不适用"。只用于 dbt 任务；查数任务用 sql-workflow 自带的验证步骤。开始与结束经 hooks 记成事件。
- **只读网关**：原样复制 SignalPilot `engine/`（sqlglot，含其测试），对 DuckDB 与 Postgres 方言生效；被拦截的语句返回可读错误。数据库驱动 `duckdb`、`psycopg` 3，连接以只读方式打开。
- **MCP server**（stdio，官方 `mcp` SDK 2.x）：
  - 基础工具（所有配置都有）：执行查询（经网关）、列出表。
  - 上下文策略工具（ADR-0003，"关上下文策略"时不暴露）：discover 搜索只回引用加至多 200 字摘要；hydrate 按引用读单个对象全文；dictionary search 按关键词查列的真实取值；join 候选先按 `_id` 列匹配。
  - 验证工具（"关验证子 agent"时不暴露）：照 ADR-0002 移植 6 个（list_tables、map_columns、check_model_schema、audit_model_sources、verify_model_values、query_database），去掉存储与审计外壳，并改掉 DuckDB 专属写法使其在 Postgres 上可用。
  - 暴露哪些工具由运行配置决定。
- **事件流**（plan §3、§5）：每次运行一个只追加、带连续序号的 JSONL 文件，是唯一原始记录。第一条是版本头（git commit、Skill 版本、配置名、模型、SDK 版本）。事件类型：状态、文本增量、工具开始与完成、SQL、验证者开始与结束、错误、结果（未缓存输入、缓存输入、输出 token 与美元花费，取自 SDK `ResultMessage`）。澄清请求类型预留给二期界面。写入前对已知密钥值与 `DATA_AGENT_BASE_URL` 做遮盖。
- **Harbor 适配器**：继承 Harbor 的 `BaseInstalledAgent`，实现 `install()` 与 `run()`；agent 装进独立虚拟环境，dbt 用镜像自带的版本；照官方配置关闭网页搜索与抓取；经 `populate_context_post_run` 回填 token 与花费；只对模型接口的临时错误开重试，排除列表照 Harbor 默认。
- **DAB runner**：从本地 clone 按路径 import DAB 的脚手架并把它的 `common_scaffold/tools` 包成本项目的 MCP 工具（ADR-0005），不向本仓库复制任何 DAB 代码或文本；使用官方提示文件；每题 5 次；输出 DAB 要求的提交 JSON 与轨迹。
- **评测 CLI**：
  - 抽样：开发集 = data-eng-bench 官方 `fast-30`；留出集 = 其余 73 题按 `difficulty` 分层抽 30 题，种子与结果清单进 git。
  - 算分：复用 data-eng-bench 的 `metrics.py`（Apache-2.0，复制并登记 `NOTICE`）：准确率 ± 按题标准误、pass@k，出错记 0。DAB 用它自带的统计。
  - 比较：按题配对 bootstrap，报差值与 95% 区间，区间不含 0 才算显著。
  - 消融：一条命令在留出集上跑 6 个配置 × 3 次，基础设施报错与 agent 失败分开统计，前者重跑不进分。
  - 报告：留出集只输出汇总；开发集可输出逐题结果与轨迹位置。
  - 清理：上传前强制运行的 scrub，移除 API key、私有端点、IP 与主机名。
  - 花费：跨运行累计花费，到上限 80% 停止批量运行。
- **失败归因**：只读开发集失败题的事件文件，由 agent 起草报告：每题一个 FM1–FM4 标签（CONTEXT 定义）、引用相关 Skill 原文、给出通用的修改建议。不自动修改 Skill，人审后再提交。
- **jaffle-shop 用例**：dbt 官方 jaffle-shop 加本地 Postgres，约 5 个用例，格式照 SignalPilot `e2e-eval` 的 eval.json：查数用例核数值并带容差；dbt 用例核模型重建、行数、粒度、列。另加 1–2 个要求直接写库的反例，网关必须拦住。
- **版本对齐**：本地 dbt 锁 1.10.x、`dbt-duckdb` 1.10.0、`duckdb` 1.2.2，与 data-eng-bench 镜像一致。

## Testing Decisions

- **好测试的标准**：只测外部行为，即 CLI 的输入输出、MCP 客户端看到的工具结果、评测 CLI 的输出文件，不测内部函数和实现细节。测试在 spec 约定的三个接缝上写，验收测试由工单 01 写出并经人审合并，之后的工单不得修改。
- **接缝 A：`data-agent run` CLI**（integration 标记，默认跳过，真调模型，花真钱，所以用例少）。夹具：一个小 DuckDB 文件加一个迷你 dbt 项目。核：dbt 模型建成且数值正确；查数答案在容差内；事件文件第一条是版本头、序号连续、结果事件带 token 与花费；关掉某层时该层不留事件也不暴露工具；越权写入被拦且运行不崩溃。
- **接缝 B：MCP server**（经 stdio 用真实 MCP 客户端调用，确定性，不调模型，进 `make check`）。核：工具列表随配置变化；执行查询拦截 DDL、DML、危险函数并返回可读错误；discover 只回引用和不超过 200 字的摘要，hydrate 回全文；dictionary search 回真实取值；6 个验证工具在夹具上给出预期结论，DuckDB 与 Postgres 方言都覆盖（Postgres 部分需要数据库时标 integration）。
- **接缝 C：评测 CLI**（确定性，进 `make check`）。夹具：伪造的 Harbor 结果目录与事件文件。核：同一种子抽样结果相同且按难度分层；算分与 `metrics.py` 一致、出错记 0；bootstrap 区间在已知数据上符合预期；**留出集报告不含任何逐题结果或失败细节**；scrub 能移除各类密钥、端点、IP 与主机名；累计花费到 80% 时停止。
- **不设验收测试、用真实运行证明的**：Harbor 适配器（单题经 Harbor 跑通）、DAB runner（单题出提交格式）、jaffle-shop 端到端用例、失败归因报告。各自工单把命令与真实输出贴进 Comments。
- **已有先例**：仓库内只有包导入的冒烟测试。上游可参考：SignalPilot `engine/` 自带的测试（随代码复制）、data-eng-bench `metrics.py` 的用法、SignalPilot `e2e-eval` 的用例格式。

## Out of Scope

- 网页界面（运行页、评测页）及其后端；agent 和评测跑通后另写 spec。
- Memory（需要界面审批；评测中每题从空开始，不受影响）。
- 产品内的澄清工具与交互；跑榜时本来就移除。
- 澄清层消融与 BIRD-Interact、LiveSQLBench（二期）。
- MongoDB 连接器（只在 DAB 评测里经 DAB 自带工具使用）。
- Langfuse 等 trace 看板导出。
- Slack、对外 MCP、Notion/Confluence 连接器。
- 写操作（CRUD/DML）能力；SQL 写入一律拦截。
- 自动改写 Skill。
- 服务器上的批量评测编排细节（`da-remote` 另做，见 plan §9）。

## Further Notes

- 许可：从 SignalPilot、signalpilot-plugin、data-eng-bench 复制的文件保留版权头并登记 `NOTICE`。DAB 没有许可证，只能从本地 clone 运行和 import，不复制它的代码或提示文本。失败归因用 CONTEXT 里 FM1–FM4 的分类思路，judge 提示自己写，不照抄 DAB 的 `failure_analysis/` 原文（ADR-0005 当时写的"采用其 judge prompt"与此冲突，以 AGENTS.md 的许可规则为准）。
- 留出集保密靠两点：报告代码只出汇总（接缝 C 测试覆盖）；服务器上的逐题结果只经 `da-remote` 取汇总（plan §9 第 8 项）。
- 成本与并发：单次成本、DeepSeek 并发与限流额度都未实测，第一批真实运行（5 题）后重算预算（plan §7）。
- 推荐的推进顺序：先打通一条最窄的路径（DuckDB + 一个 dbt 任务 + 事件文件 + 经 Harbor 跑 1 题），再加验证、上下文、消融与 DAB；具体拆分由 `/to-tickets` 决定。
- `docs/setup.md` 与 plan §7 里的"工单 01/08/09/10"是旧编号，拆工单时一并更新。
