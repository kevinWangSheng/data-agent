# 审计：产品路径上写死、临时、为评测而设的东西

日期：2026-11-27。仓库 `feefa9e`。只读审计，没有运行、没有改代码；不做设计决定。三份分区原表（core 18 条、surfaces 18 条、tools 16 条）由三个只读子 agent 按同一份说明产出，主会话抽查了标 ✓ 的条目。

起因：LiveSQLBench 诊断时发现 Skill 写死在提示词里、使用者没有地方加自己的 Skill 和业务规则（spec `.scratch/livesqlbench/spec.md` D4 补充）。人要求在"产品与评测分开、打开 Claude Code 默认的 Skill / MCP / 插件"之外，再审一遍类似的问题。

## 0. 已查清、这次不再审的五项

| 现状 | 位置 | 当初理由 | 证据强弱 |
|---|---|---|---|
| Skill 写进系统提示词，`skills=[]` 关掉 SDK 的 Skill 工具 | `src/data_agent/agent.py:1316`、`:1721` | 消融要整层开关（ADR-0001、`docs/plan.md:80`）；按次开关单个 Skill 当时"未确认"（`docs/research/2026-10-11-upstream-agent-design.md:59`） | 弱 |
| `strict_mcp_config=True` | `agent.py:1714` | 没有 ADR；116 之后改由我们自己的配置接外部 MCP（`.scratch/mcp/spec.md:11`） | 弱 |
| 不加载插件 | — | 没有记录 | 无 |
| `setting_sources=["user"]`，不读本仓库 `.claude/` | ADR-0001 | 本仓库 `.claude/` 是给开发用 coding agent 的 | 强，保留 |
| 禁用内置 Bash | ADR-0010 | 宿主机 Bash 能写库文件、读凭据 | 强，保留 |

上游对照：SignalPilot 产品聊天 `setting_sources=["user"]`、`plugins=[{type: local}]`、`skills="all"`（`docs/research/2026-10-11-upstream-agent-design.md:11`）；它的评测 runner 才固定 Skill（`:9`）。

## 1. 评测行为漏进产品，有安全或正确性影响

| # | 位置 | 现状 | 记录的理由 | 建议 |
|---|---|---|---|---|
| 1.1 ✓ | `src/data_agent/cli.py:344`、`:66-70` | `data-agent run` 的 dbt 任务默认 `--dbt-target task`：用项目自己的 profile，写进它指向的库（生产）；只有网页传 `scratch`（`web/launcher.py:178`） | `.scratch/agent-tools/spec.md:77`："CLI 默认值……Harbor 不用另传" | CLI 默认 `scratch`，评测入口显式传 `task`；违反 AGENTS.md"产品里写到每次运行的临时 schema"、ADR-0010 第 3 点 |
| 1.2 ✓ | `src/data_agent/im/adapter.py:302`、`web/conversations.py:97` | IM 从不传 `network`，默认开；服务器没有统一关掉的开关 | ADR-0010:32 写"设置里可以关"，网页每次运行有勾选框，IM 没有 | 服务器级默认（如 `data-agent web --no-network`），IM 跟随 |
| 1.3 | `dbt_execute.py:1-7`；`mcp_server.py:73-85`、`db_facts.py:34-44` | `da_run_*` 临时 schema 不删（有意，spec `:153`），目录过滤只排除 `information_schema`、`pg_catalog`；推断后续运行的 list_tables、字典会看到别的运行没审过的模型（未核实） | 不删有记录；不隐藏、没有 include/exclude 列表：无记录 | 每个数据源一份 schema include/exclude，默认排除 `da_run_*` |

## 2. 产品默认值和已定的不一致

| # | 位置 | 现状 | 记录 | 建议 |
|---|---|---|---|---|
| 2.1 ✓ | `web/conversations.py:45`、`web/src/local/NewRun.tsx:10` | 对话、IM、网页表单默认 `full` | 人定产品默认 `no-verifiers`（`docs/plan.md:301`，2026-11-08；ADR-0004:27），没有后来的工单推翻 | 一个产品默认配置，所有产品入口共用；查数时两者等价（验证器只在 dbt），dbt 表单运行每次成功的实付约 2 倍 |
| 2.2 | `web/launcher.py:47`、`:250-257` | 网页表单列出全部消融配置（`baseline`、`no-skill`、`full-old-skill`…） | `plan.md:37` 允许在表单选配置 | 产品只列产品配置，消融配置放评测 |
| 2.3 | `agent.py:212-218`、`:2293` | DAB 判分驱动的 `ANSWER_RULE`（不要表格、不列备选）在每个查询提示里，对话也有 | ADR-0005 Update 2026-11-14 | 评测入口保留，产品另给答案规则 |
| 2.4 | `agent.py:293-295` | 产品 dbt 提示里有 data-eng-bench 的 "`echo $DB_TYPE`" | 工单 100 | 挪到 Harbor 任务路径 |
| 2.5 | `agent.py:2301`、`:307-313` | 任务文件提示只在开验证器时加，"保持和旧运行可比" | 工单 32 | 产品运行总是加 |

## 3. 只适配 DeepSeek 的按模型设置

| # | 位置 | 现状 | 建议 |
|---|---|---|---|
| 3.1 ✓ | `agent.py:1275-1281` | 上下文窗口 1M、压缩窗口 400k，对所有模型 | 按模型配置；推断换 128k/200k 的模型压缩永远不触发（未核实） |
| 3.2 | `agent.py:404-413`、`:420-430` | 价目表只有 `deepseek-flash`、`deepseek-v4-pro`；别的模型没有实扣上限，SDK 按 Claude 价算 $1，约 1/30 就停（`plan.md:191`） | 使用者可填价格 |
| 3.3 | `classify.py:175-193` | 模型不在价目表里，`classify_text` 拒绝运行（工单 70 已知限制 1，待人定） | 同上；另可配单独的分类模型 |
| 3.4 | `agent.py:140` | effort 只收 `low/high/max`（DeepSeek 的取值） | 按提供方的取值 |
| 3.5 | `agent.py:1265-1270`、`verifiers.py:103` | 所有模型槽位（小模型、验证器）都是主模型 | 可配更便宜的小模型和验证器模型 |

## 4. 上限只能改源码

| # | 位置 | 现状 | 记录 |
|---|---|---|---|
| 4.1 ✓ | `configs/*.toml:6`、`run_config.py:14-15,42`、`cli.py:290` | `max_budget_usd = 1.0`、轮数 dbt 200 / 查数 50；`--config` 只收包内 6 个名字，没有覆盖参数 | "flash 先设 $1"（`.scratch/phase1/spec.md:92`），临时值成了产品上限 |
| 4.2 ✓ | `gateway.py:22-27` | DuckDB 内存 2GiB、溢写 4GiB，锁死 | 按 7.7 GB 评测机定，工单 56"不加环境变量开关" |
| 4.3 | `gateway.py:20-21`、`:253` | 查询超时 150 s、行数上限 10,000、Postgres 连接超时 10 s | 照 SignalPilot；没记录为什么不开放 |
| 4.4 | `sandbox.py:39,44-45` | 沙箱镜像 `data-agent-sandbox`、2 核、4g | 照 data-eng-bench 题目资源（`plan.md:203`） |
| 4.5 | `web/launcher.py:162-183` | 网页、IM 不传 effort、agent 超时，API 没有这两个字段 | 无记录 |
| 4.6 | `dbt_execute.py:46`、`sandbox.py:420-424` | dbt 超时 900 s；sandbox_exec 最长 900 s | 照 SignalPilot |

## 5. 没有接业务上下文的地方

| # | 位置 | 现状 | 记录 |
|---|---|---|---|
| 5.1 | `agent.py:147-153`、`:1360-1383` | 系统提示是常量，没有操作者说明文件或追加入口 | 无记录 |
| 5.2 | `web/launcher.py:411-413`、`web/sources.py:33` | 查数运行的 workspace 是空目录；数据源条目没有文档目录或描述字段 | 无记录（`plan.md:13` 的"文档"指项目里已有的文件） |
| 5.3 | `context_tools.py:89`、`agent.py:355-375` | 值字典每次运行重建，大库最多 60 s | 工单 42；产品可按目录缓存 |

## 6. 清理

- `verify_model_values` 已无提示词仍留约 350 行（`verifier_tools.py:4-5`、`im/progress.py:39`）。
- `VERIFIER_MAX_TURNS` 定义两次（`verifiers.py:35`、`verifier_v2/hooks.py:73`）。
- `classify.py:38` 的"provisional"注释已过时（工单 70 定 20,000）。
- 产品配置里的 `large = 75` 只给 DAB 用（`configs/*.toml`）。
- `trace-check` 只搜 `./runs`，没有 `--runs-dir`（`trace_check.py:30`）。

## 7. 审过、建议保留

数据库只支持 DuckDB 和 Postgres（`plan.md` §1 范围）；网页只绑 127.0.0.1（§1）；一次只跑一个运行（`plan.md:66`）；外部 MCP 只收 stdio/http（`.scratch/mcp/spec.md:51`）；DuckDB 关外部访问（ADR-0010）；dbt 子命令白名单（ADR-0010）；`data-agent run` 默认不联网（AGENTS.md 评测规矩，README 写明）；SDK 版本钉住、保留版本测试（`agent.py:52-53`、`:475-481`）。
