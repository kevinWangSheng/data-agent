# HANDOFF — 通用 data agent 求职项目

> **2026-10-15 状态**：尚未写业务代码。规划第 1–7 节已讨论定稿（`docs/plan.md`）；第 6 节开发流程已定；2026-10-15 去掉宿主机沙箱（挡住了安装、Codex、Docker）；下一步是第 9 节第 5 项 `/to-spec`。资源清单见 `docs/setup.md`。`.scratch/phase1/` 旧草稿已于 2026-10-15 删除，第 8 节用 `/to-spec`、`/to-tickets` 重写。环境剩余项见 `docs/plan.md` 第 6 节末尾。以下第 0–10 节是 2026-09-20 的历史记录。

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
