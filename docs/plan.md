# 大致规划

写于 2026-10-11。这是当前的工作方向，不是规则：每一条都可以随讨论或实测结果修改，改了就直接更新本文件。已讨论过的部分标"已讨论"，其余是待讨论的默认想法。

## 1. 产品与业务（已讨论，2026-10-11）

依据：HANDOFF §3，上游取证 `docs/research/2026-10-11-upstream-product-shape.md`。

- **定位**：开源、可自托管的通用 data agent，对标 SignalPilot，项目主体是 agent 的行为。
- **用户与场景**：分析师或数据工程师给出业务问题或数据任务，agent 探索数据源、必要时澄清、执行、自检、交付。
- **任务类型**：目前两类——dbt 分析工程、跨库查数。原来的"SQL 与 CRUD 管理操作"先不做：上游五个项目都默认只读、写操作都被隔离，和我们的只读网关 + dbt 写入一致。
- **形态**：agent 本体是命令行工具（ADR-0001），外面加网页界面（见第 2 节）。Slack、对外 MCP 暂不做。
- **数据源**：Postgres、DuckDB、dbt 项目；MongoDB 只在 DAB 评测里用（ADR-0005）。"文档"暂时指项目里已有的文件（dbt yml、README、榜单自带的库说明），用文件工具读；Notion/Confluence 之类的连接器以后再说。上游多数也是把这些先同步成文件再让 agent 搜。
- **一期大致算完成**：沿用 HANDOFF §3 的验收线——DAB 与 data-eng-bench 超过基线，消融能拆出各层贡献，能接任意 Postgres + dbt 跑。具体怎么测放到第 4 节。

## 2. 求职呈现（已讨论，2026-10-11）

- **做一个网页界面**，模仿上游，不追求轻量，合适就行。大致两块，照 SignalPilot 的"对话 + 评测"划分：
  - 运行页：提问后实时看到 agent 的过程（工具调用、SQL、验证结果、澄清提问），最后给答案、表格或图。
  - 评测页：基线与各层消融的分数、成本；点进单题可回放轨迹和失败归因。
- **暂定模仿 SignalPilot**（`signalpilot/web/app/chats/`、`app/evals/`，聊天组件用 `@assistant-ui/react`）：它是总对标，同时有对话和评测两块，它的对话 agent 也跑在 Claude Agent SDK 上（`notebook-server/signalpilot/_server/ai/standalone_chat_tools.py`）。澄清交互和图表可以参考 nao（`apps/frontend/src/components/chat-messages/`）。整体不 fork：两者都绑着各自的后端。真正动手做界面时再核一次、定下来。
- **时机**：agent 和评测跑通后再做界面。但 agent 一开始就把运行过程输出成结构化事件，让界面、评测回放、trace 共用（带到第 3 节）。
- **界面以外**：README（一句话说清楚 + 运行页动图）、榜单提交链接、消融与失败归因报告；博客可选。trace 先用自己的轨迹文件在界面里回放，要不要接 Langfuse 以后再看。

## 3. agent 设计（已讨论，2026-10-11）

依据：`docs/research/2026-10-11-upstream-agent-design.md`；之前已定的 ADR-0002（验证工具）、ADR-0003（上下文工具契约）、ADR-0005（FM1–FM4）和 CONTEXT 词条不变。大体照 SignalPilot 做：

- **主循环**：一个主 agent，一次 `query()`，先加载工作流 skill——dbt 任务用 dbt-workflow（8 步），查数任务用 sql-workflow + 方言 skill。最大轮数暂按 SP：dbt 200、查数 50（大库 75）。验证者用 SDK 的 `agents=` 定义（内容照抄 SP 的 md），这样每次运行按配置开关各层，方便消融。
- **先抄的 skills**：dbt-workflow、dbt-write、dbt-debugging、sql-workflow、duckdb-sql、sqlite-sql、verifier、value-verifier，外加自补的 postgres-sql。其余（snapshots、versioning、testing、domain-*、write-report）等失败归因说明需要时再加。
- **验证**：照 dbt-workflow 第 8 步——结构、数值两个只读验证者并行，逐项给"通过 / 失败 / 需更多信息 / 不适用"；主 agent 先复现失败再做最小修改，改过的模型重审；全部通过或不适用为止，受最大轮数约束。只用于 dbt 任务；查数任务用 sql-workflow 自带的验证步骤。
- **澄清**：产品里用 nao 的工具形式（一个问题 + 2–5 个可点选项，问完本轮结束等回答），规则照 SP"先探索、只在歧义会改变答案时问；不问就在答案里写明假设"。跑榜时移除该工具。
- **Memory**：照 SP 的知识库——agent 提议、人在界面里批准后才生效，按连接分开存。评测里沿用 CONTEXT 的口径：每题从空开始、题后丢弃，避免题与题、次与次之间串信息。
- **事件流**：照 SP 聊天，每次运行一份只追加、带序号的事件日志；事件类型取与 agent 过程有关的一部分（状态、文本增量、工具开始/完成、SQL、澄清请求、错误、结果含花费与 token），验证者的开始结束用 hooks 记。界面按序号续读，评测回放和 Harbor 轨迹都从这份日志转出。
- **失败 → 改 Skill**：照 SP 赛后自诊断，只看开发集失败题；agent 写报告（FM1–FM4 分类、引用出问题的 skill 原文、给具体修改），建议必须通用、不针对单题；人审后再提交。不做全自动改写。

## 4. 评测设计（已讨论，2026-10-12）

依据：`docs/research/2026-10-12-upstream-eval-design.md`；ADR-0004（开发集/留出集、6 个消融配置、主指标）、ADR-0005（DAB 接入）、CONTEXT 的 Baseline 定义不变。

- **算分**：照 data-eng-bench 榜单代码（`leaderboard/src/leaderboard/core/metrics.py`）——准确率 ± 按题标准误，外加 pass@k；出错的运行记 0。DAB 用它自带的统计脚本。本地数字和榜上同一算法。
- **超过基线**：留出集上按题配对做 bootstrap，差值 95% 区间不含 0 才算超过；每一层的消融贡献也这样报，未显著或负贡献照实写。上游没人做显著性检验，这是按学术惯例补的。
- **防看答案**：data-eng-bench 由 Harbor 在 agent 跑完后才拷入测试；我们照官方配置关掉网页搜索与抓取（题和答案在 GitHub 公开）。
- **成本**：每次运行记未缓存输入、缓存输入、输出 token 和美元花费（取自 SDK `ResultMessage`），经 Harbor `populate_context_post_run` 回填；报告给逐题明细和总量。
- **接任意 Postgres + dbt**：dbt 官方 `jaffle-shop` + 本地 Postgres，约 5 个用例，格式照 SP `e2e-eval/eval-set/eval.json`（查数核数值带容差；改 dbt 核模型重建、行数、粒度、列），再加 1–2 个越权反例确认只读网关能拦住。
- **DAB 基线**：和最终配置一样每题 5 次；预算不够怎么削减放到第 7 节。DAB 自带 ReAct 的榜上分数只作榜单对照。
- LiveSQLBench（二期）不做 CRUD，只跑 180 道 SELECT 题。

## 5. 技术栈（待确认）

草稿：ADR-0001 Update（模型可配置，默认 DeepSeek）、ADR-0007（Python 3.12 + uv + Claude Agent SDK + MCP Python SDK）。跟着上游走，主要是了解和确认。

## 6. 开发环境（待讨论）

编码规范、检查命令、测试策略、权限配置、人和 agent 的分工。

## 7. 运行与评测环境（大体已有）

见 `docs/setup.md`。还缺一个能接受的总花费上限。

## 8. 实施计划（最后做）

前面几节定了以后，再用 `/to-spec`、`/to-tickets` 重写或修改 `.scratch/phase1/` 的草稿。
