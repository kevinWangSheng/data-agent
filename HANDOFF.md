# HANDOFF — 通用 data agent 求职项目

> **当前状态（2026-11-23）：一期做完，收尾的 1–4 项也做完；下一步是第 5 项的讨论。**
>
> 先读：`AGENTS.md`；`docs/plan.md` §10（一期各项和结果）；`docs/report.md`（分数、消融、失败归因）。
> 2026-11-01 到 11-22 的逐日进度记录已从本文件移出，原文见 `git show 9493448:HANDOFF.md`；各项结论以 ADR（尤其 ADR-0004、0005、0008 的各个 Update）、工单 Comments 和 plan §10 为准。

## 现状

- **plan §10 第 1–26 项**都已完成（第 23 项博客可选，没做）。工单全部 `done`，最后一批是 107、108、110（2026-11-22）。没有 `ready-for-agent` 的工单。
- **榜单提交**（都 open，对方没有评论）：
  - DAB：ucbepic/DataAgentBench #144，盲跑 0.7421（b7c6071），Tuned prompt 标否；轨迹在 release `dab-submission-2026-11-13`。换成盲跑的三步已做完（fork 分支在 d36cc71）。#144 合入后可删旧 release `dab-submission-2026-11-15`。
  - data-eng-bench：Snowflake-Labs/data-eng-bench #20，64.1% ± 1.6（ea740b5，no-verifiers，103 × 3）。
- **分数对应的代码版本**：README 和报告里的每个分数都标了 commit。DAB 在沙箱改造（工单 97–101）之后跑过一次全量（69a7e2b，0.7639，两次例外算 Tuned，不提交）；data-eng-bench 全量是改造前的 ea740b5，改造后的证据是工单 100 的跑分验收（开发集 full × 3 不低于 main，小样本不低于上次正式成绩）。110（1M 窗口、自动压缩）之后没跑过全量。
- **在线版**：`https://kevinwangsheng.github.io/data-agent/`，main 每次 push 由 Pages 工作流重新构建。
- **服务器**：在 71a5bcc，没有任务在跑。模型余额 ¥60.34（2026-11-23 收尾运行后 `da-remote check`）。

## 已定的规则（这一阶段新加的）

- 优化不再每轮跑全量（人 2026-11-23 定）：修复按 ADR-0008 只做单元测试、单题真实运行或目标题 × 3 初筛；开发集统一评估、DAB 全量、data-eng-bench 全量只在里程碑跑一次，且先经人批准。
- 不再针对 DAB 优化，榜上只留盲跑（ADR-0005 Update 2026-11-22）。
- 不再开"读 trace 再修"的一轮（2026-11-23 评估）：剩下的失败大头是题面和评分器不一致（ADR-0005 不允许针对题目写 Skill）和模型波动；前几轮修复准确率都在波动范围内（报告 Failure attribution）。重新考虑的条件：读到占比大（如 >10%）的新通用失败类型，或 DAB 改为可以提交新结果。

## 剩下的事

### 一期验收线对照（2026-11-23 核对）

| 验收线 | 现状 |
|---|---|
| DAB、data-eng-bench 超过裸基线（plan §4：配对 bootstrap 区间不含 0） | DAB 过（+4.09，+1.18 到 +6.88）。data-eng-bench 留出集没过：no-verifiers − baseline +5.6（−3.3 到 +15.6），full − baseline +1.1；103 题上 baseline 没跑过 |
| 消融拆出各层贡献 | 没过：5 个配置差值都不显著（报告"读不出方向"）；澄清层消融在二期；"失败归因自改进"成了开发流程，不是可开关的层。不补，照实报告 |
| 接任意 Postgres + dbt | 过：jaffle-shop 7/7（71a5bcc，full，$0.137，2026-11-23），写进 README Results 和报告 |

2026-11-23 讨论的结论：已跑过的全量都不重跑。103 题 baseline 不跑（混了调过 Skill 的开发集，估算还是不显著，未实测）。再开一轮优化也不建议：要显著需拉开约 10 分，前几轮修复都在波动范围内。唯一保留的判断入口（开发集配对分析）已做，没过开修复轮的线，data-eng-bench 和消融两条照实报告。

### 收尾的 1–4 项（2026-11-23 做完）

结果写在 plan §10 "2026-11-23 下一批的结果"、报告 Development set 一节和 Limitations、README Results。
1. 开发集配对分析：两个配置都系统性输给 baseline 的只有 2 题（6.7%），机制不同（rfm 内连接丢客户、tier-migration 题面歧义），不开修复轮。本机明细 `runs/dev-paired/summary.md`（不进 git）。
2. jaffle-shop：7/7，71a5bcc。本机 `runs/jaffle-main/`。
3. 110 在 dbt 上初筛：5/9 对 b7c6071 的 6/9，在波动内，单价持平；dbt trial 的上下文远没到压缩点，110 在 dbt 上不起作用。本机和服务器 `runs/t110-dbt-b/`（第一次运行在本机 `runs/t110-dbt-b.first/`）。
4. 收尾文档：本次提交。

### 下一步（新会话从这里开始）

5. **之后再讨论（一次一个主题）**：项目终点（二期要不要做、做什么，先看 JD 覆盖的 Memory、多 Agent 协作）→ 冻结后再做展示：求职材料 → 报告和 README 中文版 → 视频 → 博客（给视频引流，没准备好就推迟）。根目录 `.playwright-mcp/` 待人定。
- 11-18 的 4 个清理分支（`cleanup-prune-features`、`test-cleanup`、`cleanup-prune-tests`、`cleanup-speed`）和 `chore/glossary-rename` 已由人定删除（2026-11-23，未合入；内容见 `6adb612`、`e84a615`、`0447ec4` 等提交，可能被 gc 回收）。
- **一期外的功能候选**（各 spec 的 Out of Scope，要做先在对齐会话讨论）：答案里出图表；查数任务读 dbt 模型说明当业务语义（现在只有验证者读 `target/manifest.json`）；在对话里做 dbt；对话总花费上限。其余（Memory、登录多用户、公网部署、MongoDB 连接器、Langfuse、LiveSQLBench、澄清层消融等）价值低或偏平台。

## 已知限制

- DAB 工具服务器偶发启动失败（全量 540 次里 2 次），原因未确认；107 之后每个 trial 留 `dab_server_stderr.log`，要等下次失败。服务器上启动约 7.4 s（导入 5.7 s）。这个文件也收 DAB `BaseTool` 的 INFO 日志，单 trial 约 172 KB。
- 110 之后 trial 不再在 177k 被拦，轨迹变长，单次花费上升（agnews q3 标价 $0.43 → $0.60）。抽样：stockmarket q2 3/5 → 5/5，agnews q3 仍 0/5（都答完了但全错，分类口径问题，未读轨迹）。压缩摘要由 deepseek-flash 写，可能丢任务细节。
- 服务器事件里 240 次 dbt `api_error` 全部出自 10-22 的 `dev-3`（共 450 个 trial），都是 `402 Insufficient Balance`，不是 bug。
- 本机没有集成测试用的 Postgres（`DATA_AGENT_TEST_POSTGRES_URL`），`make test-integration` 有 32 个用例因环境缺失失败；对话页面 e2e 的澄清用例偶发失败。
- `runs/f18c/par.py`（运行目录里的一次性脚本，不进 git）对上次 exit 非 0 的任务不记日志就跳过。
- 根目录未跟踪的 `.playwright-mcp/`，待人定是否删除。

## 操作提示

- 派工：`claude -p -w NN-<slug> --model opus --permission-mode auto "/implement <工单绝对路径>"`，输出写 `runs/dispatch/`；派工前本地 main 不能有未 push 的提交。工单会话 source 不了模型 env 文件，真实运行由主会话用 `scripts/da-remote` 做（用仓库里的版本，不用 `~/.local/bin` 的旧版）。
- `da-remote start` 不透传额外环境变量；要临时变量的运行用 `da-remote exec -- bash -c 'nohup env X=... ...'`（做法见工单 110 Comments）。连续多次 ssh 偶尔被断开，隔一会儿重试即可。
- 审查：`da-review`（Codex CLI）在这个账号上不能用，用 fable 子 agent 代替（见 memory）；Codex GitHub app 能用，没出审查时评论 `@codex review`。前端工单不用 Codex，见 AGENTS.md。
- 本机 Docker 是 colima，集成测试前先 `colima start`。
- 已合并的 worktree 和分支在 2026-11-23 清理完：本地和远端都只剩 `main`。

---

以下是立项时（2026-09）的背景，保留作参考；其中的榜单现状、对标数据是当时的快照，不代表现状。§6、§7、§9（当时的下一个决策点、待核实项、建议 skills）已经过时，已删除，原文见 git 历史。

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

## 8. 证据来源

- Data Agent 全景调研（2026-09-07，含 Codex 换模型复核）：https://claude.ai/artifact/LTjfDy1iujQvwAo6hdgsiE
- BIRD-Interact 论文 v3（2026-03-24）arXiv 2510.05318：>80% 失败源于歧义未解决；强 agent 先探索后提交、ask/submit 与探针均衡。
- DBA-Bench arXiv 2607.22165：Safe Pass 最佳 17.9% vs 人类 93.4%；仓库 https://github.com/TanJI-C/DBA-Bench "Coming Soon"。
- 两路子调研原文（开源项目扫描 20 仓库；benchmark 扫描 13 项）在原会话中，结论已并入第 4、5 节。

## 10. 协作方式（用户偏好）

- 规划一个决策点一个决策点推进，先证据后结论；别顺从式复述，要带审视。
- 决策阶段不写代码；口径、验收、分期确认后再执行。
- 简短优先；"最佳实践"类问题直接给一个答案。
- 读过 README ≠ 验证过；未核的一律标"未确认"。
