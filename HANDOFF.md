# HANDOFF — 通用 data agent 求职项目

> **2026-11-01 交接：分成两个会话，各管一摊。**
> - **编排会话**（主会话）：只按 plan §10 的依赖推进。负责派工单（`claude -p -w`）、把工单会话的结果抄进工单、开 PR、核对审查意见、合并、在服务器上跑评测。它是唯一做这些事的会话。不讨论"怎么构建"。
> - **对齐会话**：只讨论还没定的话题，一次一个，只写人确认过的 spec、工单、ADR 和文档。不派工、不合并、不碰服务器。每次提交前先 `git pull --rebase`，写好的工单交给编排会话去派。
> - 两个会话都从干净的上下文开始。2026-11-01 的旧会话已经停下，它的结论都写在 ADR、plan、工单和本文件里。
>
> 先读：`AGENTS.md`；`docs/plan.md` §10（一期剩余项和它们的依赖）；ADR-0008 Update 2026-11-01（分别初筛、合并验收，旧版用 dev-3）。
>
> **默认放行规则**（plan §10）：
> - 小额运行直接跑，跑完汇报：单题真实运行、初筛、回归、开发集 30 题的合并验收。
> - 大规模评测先和人确认范围和花费：全部题目、超过 30 题、留出集消融、全量、DAB 正式评测。
> - 开发集迭代累计实际花费到 ¥420 就止损（2026-11-03 人从 ¥300 提到 ¥420，已充值；ADR-0008 Update 2026-11-03）。
> - 实现一律派到工单会话（`claude -p -w`），主会话只做编排。
>
> **编排会话进度（2026-11-02 更新）：**
> - 已合并、`done`：工单 34（PR #32）、35（PR #33）、32（PR #31，初筛 16/18）、33（PR #34，初筛 6/6）、36（PR #35，imdb 从 96 分钟降到 8 分钟）。
> - plan §10 第 9 项 DAB 试跑已完成（`runs/dabtrial2-*`，commit 56d5127）：34 次全部 exit 0；Pass@1（17 个数据集 × query1 × 1 次）baseline 10/17、no-context 8/17，样本太小，不作结论；实际价共 $1.59，约 $0.05/次；内存峰值约 1.7 GB。第一遍 `runs/dabtrial-*` 中途 Mongo 容器停了，9 次 infra_error，重启后重跑没复现。
> - 第 13 项合并验收 `accept-1` 跑完（commit e9facce，30 题 × 3，full，实际价 $8.90，全在闲时）：
>   - ADR-0008 三条都过：总分 52/90 → 60/90（+8.9 分）；目标题（工单 32、33 的 8 题）15/24 → 20/24；回归集 12 题全 3/3。`evals accept` 判 `rerun-old`（dev-3 没有运行记录），按 ADR-0008 Update 一次性豁免，算通过。
>   - 回归集：dev-3 full 与 accept-1 都 3/3 的 12 题，存于 `runs/accept-1/accept/regression.json`（另一会话也写了同样内容的 `evals/regression.json`，未提交）。
>   - **最终消融前检查没过**：accept-1 full 60/90 低于 dev-3 baseline 62/90（−2.2 分，任务级配对 bootstrap 95% 区间 −11.1 到 +6.7，不显著）。
>   - 开发集迭代累计实际花费约 ¥271，离 ¥300 止损线约剩 ¥29，不够 13b 的一次重跑（约 ¥65）。
> - 工单 37 合入（PR #36，de30bf3）：服务器 `t37-real` imdb query1 1/1，真实 DAB 能跑 EXPLAIN 和 `to_regclass`；计划显示 imdb 慢在相关 EXISTS 子查询逐行全表扫 `movie_keyword`，不是 `regexp_replace` 连接。agent 没主动调 `explain_query`。
> - **人已决定（2026-11-03）**：止损线从 ¥300 提到 ¥420，已充值。跑第 13b 项：在当前 origin/main（含工单 37）上开发集 30 题 × 3，full 与 baseline 一起跑（`runs/accept-2`），尽量放在 DeepSeek 闲时；跑完用 `evals accept` 判 full 对 accept-1 full（ADR-0008 三条，回归集 `evals/regression.json`）和 full 对 baseline。full 高于 baseline 才建议冻结进第 15 项；持平或略低就用 `evals diverge` 读分歧题交人定；明显更低就停下。冻结由人决定。回归集已提交为 `evals/regression.json`。
> - **13b `accept-2` 跑完（2026-11-04，commit 0b5597d，与 de30bf3 代码相同）**：full 56/90，baseline 62/90。实际价 $15.74（约 ¥115；115 个事件文件在闲时，67 个跨进高峰）。开发集迭代累计约 ¥386，离 ¥420 约剩 ¥34。
>   - full 对 accept-1 full：`evals accept` 判 fail：总分 60→56，目标题 20/24→17/24；回归集 cart-abandonment、pos-operations 各 2/3，要补跑。
>   - full 对 baseline：−6.7 分（区间 −16.7 到 +4.4，不显著），属于情况 b。baseline 两轮都是 62；full 三轮是 52、60、56。
>   - 分歧题读完：`runs/accept-2/reading/summary.md`。full 输的四类原因：① `dictionary_search` 没有查询超时，挂满 1800s，后面常跟着 DuckDB 文件锁（只有 full 有这个工具，accept-2 有 5 个 trial 挂住、4 个输）；② 验证者看到了问题，却放行，或者主 agent 不理会它报的 FAIL；③ spec 把误读固化下来（division-by-zero 三次都一样）；④ 题面和评分器不一致（marketing-attribution 一直 0/3）。full 稳定赢的只有 test-orders-filter（Skill 规则）。
>   - **等人决定**：修一轮还是冻结。没有自动冻结，也没有进第 15 项。
> - ~~等人决定~~（已决定，见上）：编排会话建议把止损线提到约 ¥420，在合入 37 后的 main 上一起跑 full 和 baseline（各 30 × 3，约 ¥130），同时完成 13b 和 full ≥ baseline 检查；仍低于 baseline 就不再修，如实记录，交给留出集消融。不提线的话，跳过 13b，把 2 分差距记为已知风险。
> - 余额：accept-1 开跑前 ¥123.92，跑后约 ¥59（未直接核实）。正式评测前要充值。
>
> **对齐会话的话题（按 plan §10，一次一个）：**
> - 第 5 项网页界面。从 `docs/research/2026-11-01-upstream-web-ui.md` 开始，先定三点：
>   - SignalPilot 其实没用 assistant-ui；
>   - 可复制的组件清单；
>   - Memory 和澄清是否进一期（澄清需要 agent 先加事件）。
> - ~~工单 37 triage~~：2026-11-02 完成，`ready-for-agent`；范围扩成基础工具层给数据库事实（ADR-0003 Update 2026-11-02），合入后重跑合并验收（plan §10 第 13b 项）。
> - 之后的话题：第 15 项正式评测总花费（等合并验收和 DAB 试跑的实测）；第 19–23 项交付（报告、README、转公开）。
> - 新发现，待对齐会话定是否开工单（2026-11-02）：
>   - `dab-score` 每个数据集只有 1 道题时，区间退化成一个点并标"significant"（试跑输出 "-11.76 points (95% CI -11.76 to -11.76; significant)"），应改成报"题数不够估计区间"。正式评测（全部题）不受影响。
>   - DAB 的 Mongo 容器在试跑中途停止，原因查不到：`da-remote` 看不到容器退出状态，容器也没有重启策略。
>   - DAB clone 现在有 104 道题（§5 写的是 54），第 15 项花费估算按 104 算。
> - **accept-2 根因审查（2026-11-04，对齐会话）**：读完全部 180 个 trial，按工具、验证层、harness 三块和上游对照。完整报告（带 file:line 和实测数）在 `runs/accept-2/reading/design-audit/`，**只在本机，不进 git**；结论以本节为准。
>   - 三类共同根因：
>     - 抄了上游接口，丢了上游控制规模的设计（输出上限、预建字典、表目录缓存、改动前快照），在约 2400 个模型的 dbt 库上就挂住、输出超长或变慢；
>     - 能算出来的事实（主键 NULL 和重复、改动前后的差异）交给了模型判断；
>     - 本地测试环境和容器不一样（依赖、工作目录、dbt 增量解析）。
>   - 问题清单（按对评测的影响排序）：
>     1. `dictionary_search` 挂满 1800s：调用时遍历所有表的所有文本列现扫，没有总时限（`src/data_agent/context_tools.py:219-235`）；工具是同步函数，在线程里跑，客户端放弃后服务端仍在读库，之后 `dbt run` 报 "Conflicting lock"（取消有没有传到服务端未确认）。ktx@49a4ae6 是在 scan 阶段预建采样字典，搜索时只读字典（`packages/cli/src/context/scan/data-dictionary.ts:9-34`、`sl/dictionary-search.ts`）。full 有 5 个 trial 挂住，只过 1 个；同题的其他 trial 过 16/18；约占 6.7 分差距的一半。
>     2. 验证层花费大、信号弱：
>        - 全部 PASS 的 trial 成功率 63%，full 整体是 62%；34 个失败 trial 里只有 10 个收到过 FAIL；验证器占 full 运行时间的 1/3，花费约占 35–55%（估算，未确认）。
>        - CHECK 5/6 拿主 agent 自己的说法当"原样"，会让 agent 删掉原有过滤条件（division-by-zero），或驳回本来正确的推导（inventory-turnover）。
>        - 主键 NULL 由模型判断，曾给 256 行 NULL 判 PASS（pos-operations）。
>        - 派单消息带着主 agent 自己的假设；FAIL 只靠提示词约束；Skill 和系统提示词对 FAIL 之后怎么做说法不一致。
>        - 上游 SignalPilot 的 benchmark 版有 `prebuild_state.md` 和主键、NULL 子检查，我们没带。
>     3. 输出超长，模型读不到：
>        - `list_tables` 一次返回 66.5 万字符，两种配置共 125/180 个 trial 遇到，210 次调用只拿回报错。SignalPilot 是最多 200 张表、约 3.6 万字符，有 `schema=` 和 `columns=false`，结尾说明还有多少没显示（`signalpilot/gateway/gateway/mcp/tools/schema/catalog.py:204` 起）。
>        - scan 输出 758KB，模型只看到 2KB 预览，Skill 却要求 "Read the ENTIRE output"。
>        - `query_database` 只限行数、不限字符数。
>        - Claude Code 把超长输出存成文件，workspace 守卫又不让读（41 次）。
>     4. 表目录不缓存：每次调用都重读 `information_schema`，每条语句新开一个 DuckDB 连接；`describe_table` 中位数 14 秒、最慢 81 秒；验证工具累计约 49 个工具小时。SignalPilot 缓存 300 秒。
>     5. 工作目录和 dbt 解析：守卫不许 `cd`，profile 里的相对路径 `../database` 在 97/180 个 trial 里失效，agent 只好改 `profiles.yml`；镜像用 `--target dev` 构建，`vars_hash` 里含 target，所以每个 trial 第一次跑 dbt 都完整重新解析，要 60–75 秒，validate 的 60 秒上限超时 18/91 次（本地复现过）。
>     6. 守卫误拦：dbt_utils 自带的样例 `.env` 让整个 dbt 项目目录不能 Grep（205 次）；`echo $DB_TYPE` 在 180 个 trial 里全被拦，103 道题里有 94 道要求这样做。拦截共 757 次，和得分的相关很弱，代价主要是间接的。
>     7. 运行时缺 `pytz`：只通过开发依赖（dbt）装进来，容器安装用 `--no-dev`（`evals/harbor_agent.py:153`），带时区的时间戳列查询会报错，共 38 次，pos-operations 占 26 次。
>     8. 同一轮的多个 MCP 调用串行执行（21 个调用耗时 340 秒），原因未确认，可能和工具没标 `readOnlyHint` 有关。
>   - 修 agent 救不回来的（记成已知限制，按 ADR-0005 不追）：6 道两种配置都 0 分的题里，5 道主要卡在题面和评分器不一致，分别是 cohort-retention、cltv-forecasting、inventory-turnover、fifo-inventory-cogs、promotional-lift；fifo 的 DuckDB 期望值看起来自相矛盾，参考解答本地跑不了，未确认。tier-migration 主要是 agent 自己用了 `count(distinct)`。marketing-attribution 同理，之前已经定过不修。
>   - 分类：
>     - 已经可以定、上游有先例：7、3（`list_tables` 和 `query_database` 的输出上限）、4、6 的 Grep 规则。
>     - 要先讨论：1（照 ktx 预建字典，还是从 full 里拿掉）、5 和 6 的 `echo` 白名单（会改 command_guard 的安全边界）、3 的 scan 输出摘要（要改 Skill）、2（验证层重新设计，ADR 级别）。
>     - 先查清原因：8；1 里取消有没有传到服务端。
>   - 建议顺序：1 → 3 → 4 → 5/6/7 → 2 → 8。3 到 7 对 baseline 也有影响，修完 full 和 baseline 要一起重跑才能比较。
>   - **等人决定**：
>     - 修一轮还是冻结（上面 13b 的决定还没做）；
>     - 修完怎么验证：止损线还剩约 ¥34，只够在少量题上初筛；完整对照约 ¥115，超出止损线。
>
> **服务器同一时间只跑一批**：Harbor 和 DAB 不能同时跑，内存不够。
>
> 以下第 0–10 节是 2026-09-20 的历史记录。

写于 2026-09-20。给下一个会话（新目录、新 agent）用。本文件是会话结论的可移植记录，不是规范；规范由后续 `CONTEXT.md` / ADR / spec 承接。

## 0. 一句话

做一个开源、可自托管的**通用 data agent**，对标 **SignalPilot**，主语是 agent（skills 方法、验证子 agent、问/做策略、上下文策略、失败归因自改进），工具层复用现成，在四个公开榜单上出分并做消融。目的是求职作品集，目标岗位 **agent 应用开发为主**。

## 1. 目标与约束（用户原话归纳，已锁定）

- 求职用项目；面试官看的是 agent 能力项（抽查 2026 年 JD 关键词：任务规划、工具调用、上下文管理、长期记忆、多 Agent 协作、Agent 评测体系；无一提 text-to-SQL）。
- 领域：data agent，但要**通用**，不要小领域单点。
- 必须是 agent；agent 循环/记忆/上下文管理用第三方 SDK，不自造。
- **主语必须是 agent 的行为**，不是网关/执行链/后端能力；不要拿"后端出身适合"当论据（用户三次纠正同一漂移）。
- 要快：尽量复用对标项目代码；有现成 benchmark 或易自建的参考标准。
- 非 demo 判据：公开榜单分数 + 可复现 eval 与消融 + 系统规模与工程完整度。
- 只看项目是否过时/是否还新/是否可用，不看市场与商业。
- 考虑 MCP / skills 接企业已有资产。
- 资源：个人几台 Linux 服务器，无 GPU，无云数仓账号。
- **不设工期维度**：项目由 coding agent 实现，不问不估工期。
- eval/tracing/benchmark 是每个 agent 项目的标准配套流程，不是产品卖点。

## 2. 否掉的方向（别再绕回去）

| 方向 | 否决理由 |
|---|---|
| 单轮 text-to-SQL（含"受治理 text-to-SQL + 网关"） | 已被模型吸收；复杂度在工具侧；2023 类项目（Vanna 已归档）；JD 不认 |
| 多轮澄清 text-to-SQL（BIRD-Interact 为主体） | 读起来仍是 text-to-SQL；榜单 2025-04 后无更新 → 降为可选子能力 |
| 多步分析报告 agent（KramaBench/DABstep 为主体） | ChatGPT/平台内置；数据清洗才是大头；面试难答 |
| AI DBA 诊断修复 agent 为主体 | DBA-Bench 仓库 "Coming Soon" 未开源；只能当一个 skill |
| 治理网关 / 多库联邦 / 语义层建模工具 / 微调 | 工具侧或资源不允许 |

## 3. 项目定义稿（用户已确认"确定做这个项目"）

**定位**：开源可自托管通用 data agent；MCP 接数据库、dbt 项目、文档；skills 承载方法；验证子 agent 把关；四个公开榜单出分；消融说明 agent 每层贡献。

**场景**：分析师/工程师给业务问题或数据任务，agent 探索多个数据源、必要时向人澄清、执行、自检、交付。三类任务：跨库查数与整合；分析工程（改 dbt 模型/修数据 bug）；SQL 与 CRUD 管理操作。

**agent 侧五块（全部工作量所在）**
1. skills：每类任务的做事方法（探索到什么程度、何时动手、何时停）。
2. 验证子 agent 协议：审哪几项、只改 FAIL、改完再审、停止判据。
3. 问与做的策略：何时澄清、问什么、预算怎么花。
4. 宽 schema 上下文策略：先取什么、跨步骤记什么，不整库塞上下文。
5. 失败归因驱动的自改进：按失败类型改 skills/策略；成功口径沉淀回记忆。

**复用不做**：agent 循环与记忆 = Claude Agent SDK；治理网关、MCP server、沙箱 = SignalPilot 现成实现；上下文层参考 ktx。

**交付物**：仓库（agent、skills、验证协议、Harbor 接入、eval 与消融脚本、trace 进 Langfuse）；四榜分数与提交记录；失败归因与消融报告。

**验收线**：DAB 与 data-eng-bench 上超过 Claude Code 裸基线；消融表能拆出五层各自贡献；agent 能脱离榜单接任意 Postgres + dbt 项目跑。

**不做**：语义层建模工具、多库联邦引擎、微调、治理网关产品化。

## 4. 对标与参考代码（2026-09-20 核过）

| 项目 | 许可 | 状态 | 拿来干什么 |
|---|---|---|---|
| SignalPilot 主仓库 https://github.com/SignalPilot-Labs/SignalPilot | Apache-2.0 | 2026-03-31 创建，2026-09-17 推送，Spider2-DBT 官方榜 #1 65.6（#2 JetBrains Databao 60.29） | 治理网关、40 个 MCP 工具、11 连接器、`benchmark/`（Claude Agent SDK 写的评测管线）直接复用 |
| SignalPilot 插件 https://github.com/SignalPilot-Labs/signalpilot-plugin | Apache-2.0 | 25 个 SKILL.md（约 2,800 行）+ `agents/verifier.md`、`value-verifier.md`、`notion-verify.md` + dbt-workflow 的 scan/validate 脚本 | skills 结构与 8 步 dbt 工作流照抄；验证协议（表存在/列完整/行数与 fan-out/基数；数值交叉核对）逐条可抄。注意验证 agent 依赖其 MCP 工具（map_columns、audit_model_sources） |
| ktx https://github.com/Kaelio/ktx | Apache-2.0 | 2026-05-10 创建，1,600★，2026-09-11 推送 | 上下文层（join 图、指标定义、文档矛盾标记）→ MCP 的做法；它无任何公开分数、无 eval |
| nao https://github.com/getnao/nao | 主体 Apache-2.0 + 文件级企业条款 | 1,670★，2026-09-18 推送 | `nao test`（yaml 问题+期望 SQL）eval 骨架、chat UI、Slack |
| agno/dash https://github.com/agno-agi/dash | Apache-2.0 | 2,268★，2026-07-10 推送（2 个月未动） | 五类 eval 骨架（accuracy/routing/security/governance）|
| Wren AI https://github.com/Canner/WrenAI | 核心 Apache-2.0 | 17.7k★ | 语义层、LangGraph/Pydantic AI SDK；evals 目录空壳 |
| BIRD-Interact-ADK https://github.com/bird-bench/BIRD-Interact/tree/main/BIRD-Interact-ADK | MIT | 三微服务（agent/user_simulator/db_env） | 澄清策略参考：一次 ask 只解一个歧义；模拟器拒答 schema 问题；预算 6+2×歧义+2×耐心；ask=2/submit=3/探针 0.5–1 |

剔除：pandas-ai（2025-10 后停更）、Chat2DB（转数据库客户端）、Metabase Metabot（商业许可目录）、DB-GPT（7 包太重，solo 对标成本高）。

## 5. 评测组合（全本地可跑，GT 可拿）

| 榜 | 测什么 | 接入 | GT | 现状 |
|---|---|---|---|---|
| DAB (UC Berkeley DataAgentBench) https://github.com/ucbepic/DataAgentBench | 54 题/12 数据集/PG+Mongo+SQLite+DuckDB；跨库整合、脏键 join、非结构化抽取；Pass@1 | `common_scaffold/` 自带 DataAgent.py 与只读工具；输出 JSON 走 validate；PR 附轨迹上榜 | 仓库内 | 最活跃 agent 榜：39 条，最新 2026-09-13，榜首 0.9467；需本地 PostgreSQL ≥17.5 + MongoDB 8；许可未确认 |
| LiveSQLBench-Lite https://github.com/bird-bench/livesqlbench | 270 题（180 SELECT + 90 CRUD），Postgres docker | LiveSQLBench-CLI（Harbor，`--agent-import-path` 接自定义 CLI agent，需自建 agent 镜像）或 LiveSQLBench-Agent（ADK，替换 system_agent 服务） | 邮件 bird.bench25@gmail.com 标题 `[livesqlbench-base-lite GT&Test Cases]` 自动回 | 榜首 48.0（2026-05），榜上有 Claude Code / OpenHands 条目可对比 |
| data-eng-bench (Snowflake Labs) https://github.com/Snowflake-Labs/data-eng-bench | 103 个 dbt 数据工程任务，pytest 逐行比对 | Harbor 原生，`DB_TYPE=duckdb` 完全本地，有 30 题快速子集 | 仓库内 | Harbor Hub 在线榜；Claude Code+Opus 5 69.6%，Cortex Code 73.8%，Codex 60.5% |
| DABstep https://huggingface.co/spaces/adyen/DABstep | 多步分析事实题，quasi exact match | 输出 JSONL 上传表单 | dev 公开，全集私有 | Validated 榜已关（v2 将出），只能上 Unvalidated；当 smoke test |

可选：BIRD-Interact（澄清能力消融用；榜停在 2025-04）；ADE-bench（dbt Labs，Apache-2.0，接自定义 agent 要改 harness）。不能用：Spider2-Snow/Lite（云账号，Snowflake 账号 2026-08 中断）、DBA-Bench（未开源）、Data Intelligence Index（只评模型无 harness）。

Harbor = Terminal-Bench 团队的容器化 agent 评测框架（Apache-2.0，`uv tool install harbor`），同一份 agent 可复用于 LiveSQLBench-CLI 与 data-eng-bench。

## 6. 下一个决策点

**agent 骨架**：(a) Claude Agent SDK 做 CLI 形态，通过 Harbor 接榜；(b) 沿 SignalPilot 做成 Claude Code/Codex 插件（skills 只在宿主里生效）。影响：Harbor 需要自建 agent Docker 镜像；DAB 需要独立 scaffold。之后按决策点：写操作与 CRUD 的处理方式 → 三类任务 skill 的顺序 → 消融矩阵口径。

## 7. 待核实 / 未确认

- DAB、KramaBench 许可；DABstep 当前榜首；Spider2-DBT 条目日期；ADE-bench 精确题数。
- SignalPilot benchmark 管线能否脱离其 Cloud 运行；插件 skills 在非 Claude Code 宿主的可移植性。
- DAB 本地 PG17 + Mongo 8 的资源占用。
- LiveSQLBench Large-v1（1000 列/库）是否纳入第二阶段。

## 8. 证据来源

- Data Agent 全景调研（2026-09-07，含 Codex 换模型复核）：https://claude.ai/artifact/LTjfDy1iujQvwAo6hdgsiE
- BIRD-Interact 论文 v3（2026-03-24）arXiv 2510.05318：>80% 失败源于歧义未解决；强 agent 先探索后提交、ask/submit 与探针均衡。
- DBA-Bench arXiv 2607.22165：Safe Pass 最佳 17.9% vs 人类 93.4%；仓库 https://github.com/TanJI-C/DBA-Bench "Coming Soon"。
- 两路子调研原文（开源项目扫描 20 仓库；benchmark 扫描 13 项）在原会话中，结论已并入第 4、5 节。

## 9. 建议下一会话调用的 skills

- `eng:domain-modeling`：把第 3 节落成 `CONTEXT.md` + ADR（对标选择、agent 侧五块、评测组合、不做项）。
- `thinking:grilling`（或 `/grill-with-docs` 若已加载）：只补第 6、7 节的漏，已锁定项不重问。
- `thinking:research`：核第 7 节未确认项，尤其 Harbor 自定义 agent 镜像与 DAB scaffold。
- `claude-api`：Claude Agent SDK 的用法与模型 ID（先读再写代码）。
- `writing:writing-for-agents`：写 SKILL.md 与验证 agent 提示词时用。
- `eng:tdd` / `code-review`：实现阶段。
- 之后主流程：`/to-spec` → `/to-tickets` → `/implement`（若已加载）。

## 10. 协作方式（用户偏好）

- 规划一个决策点一个决策点推进，先证据后结论；别顺从式复述，要带审视。
- 决策阶段不写代码；口径、验收、分期确认后再执行。
- 简短优先；"最佳实践"类问题直接给一个答案。
- 读过 README ≠ 验证过；未核的一律标"未确认"。
