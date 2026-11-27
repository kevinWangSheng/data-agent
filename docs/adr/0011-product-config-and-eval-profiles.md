# 产品配置分层；评测是一份配置文件

日期：2026-11-27。人在对齐会话里逐条确认（`.scratch/product-config/decisions.md`）。依据：`docs/research/2026-11-27-product-config-audit.md`（审计：产品路径上写死、临时、为评测而设的东西）、`docs/research/2026-11-27-config-layering-local-cloud.md`（上游与同类产品的配置分层、本地与云端、跑榜做法）。

## 背景

一期为了跑榜，产品和评测共用一套写死的做法：Skill 全文写进提示词、SDK 的 Skill 工具关掉（`skills=[]`）、`strict_mcp_config=True`、不加载插件；`--config` 只收包内 6 个消融配置名；花费 $1、轮数、超时、行数、DuckDB 内存、沙箱资源、模型价格和上下文窗口都是常量；DAB 判分用的答题规则在对话里也生效；`data-agent run` 的 dbt 任务默认写生产库。使用者没有地方放自己的 Skill、业务规则和 MCP，也不能部署到服务器上给 IM bot 用。上游的产品版（SignalPilot 聊天：`setting_sources=["user"]`、本地插件、`skills="all"`）是打开的，只有它的评测 runner 才写死；同类产品（Claude Code、nao、LibreChat、Onyx、Dify）都是分层配置，跑榜（SWE-agent、Aider、Harbor 跑 Claude Code）走产品自己的配置机制。

## 决定

1. **分层配置**：代码默认值 < 配置文件 < 环境变量（只放密钥、部署模式、配置文件路径）< 命令行参数；最上面是运维锁定 `[policy]`，覆盖不了。一个 `config.toml`：用户级 `~/.data-agent/config.toml`、项目级 `<工作区>/.data-agent/config.toml`；显式 `--config <路径>` 或 `DATA_AGENT_CONFIG` 时只读这一个文件。有类型、未知键报错；密钥只写 `${VAR}` 引用；`data-agent config show` 显示每个键的值和来源。
2. **`full` 是产品默认**：全部启用、按需加载、少数常驻。Skill 打包成本地插件，经 SDK 的 Skill 工具按需读全文；`preload` 的照旧进系统提示词，默认只有工作流 Skill。使用者的 Skill 目录、MCP 配置、本地插件都能加载。产品默认配置从 `no-verifiers` 改回 `full`（推翻 ADR-0004 Update 2026-11-08 记的产品默认）。agent 不读或不照做 Skill 算我们的问题，按轨迹调。
3. **评测是配置文件**：每个榜单一份基础配置 `evals/profiles/<榜单>.toml`，格式和使用者的一样；消融 = 基础配置 + `evals/profiles/layers/*.toml` 层开关，名字（`full`、`baseline`、`no-skill`、`no-verifiers`、`no-context`、`full-old-skill`）不变。评测不加载使用者的 MCP、插件、Skill 目录。每次运行在事件文件里记生效配置全文和哈希。
4. **迁移分两步**：先只换机制不换行为（运行参数快照逐项相同才切换），再把评测改成产品新默认、当作一次普通的 Skill 改动在开发集上量。
5. **一期云端只做 Docker 部署**：`[server].mode = "local" | "cloud"`。cloud 下网页要访问令牌、缺密钥拒绝启动、默认不联网、强制临时 schema、单次运行不能改模型端点、沙箱必须是 Docker（挂 Docker socket）；上下文可从 git 拉。登录、多用户、管理界面加数据库仍不做。
6. **不变**：禁用内置 Bash、沙箱和 agent 拿不到写库凭据（ADR-0010）；不读本仓库 `.claude/`（ADR-0001）；Skill 里不写基准专属内容（ADR-0005）——评测配置文件的 `[prompts]` 复现榜单官方答题格式，不在 Skill 里，不算违反。
7. **例外**：使用者自己的 MCP 服务器可能带写库凭据；产品允许，文档写明它们不在只读保证之内；评测一律不加载。

## Consequences

- 改 `src/data_agent/run_config.py`、`agent.py`（选项装配、提示词、Skill、模型设置）、`cli.py`、`web/`、`im/`、`gateway.py`、`sandbox.py`、`dbt_execute.py`、`evals/`（评测入口改读配置文件）。spec：`.scratch/product-config/spec.md`、`.scratch/cloud-deploy/spec.md`。
- 迁移第一步之前的结果（消融、榜单、LiveSQLBench D1）和之后的结果可比；第二步之后的结果要和第二步之前的配对比较才能下结论。
- 挂 Docker socket 等于给 data-agent 宿主机的 Docker 权限，部署文档写明。
- `docs/plan.md` §1 的"不做公网部署"改为"一期只做 Docker 部署，不做登录、多用户"。
- 见 ADR-0001、ADR-0004、ADR-0010 各自的 Update 2026-11-27。

## Update 2026-11-27：设置界面与平台身份

见 ADR-0012。第 5 条"登录、多用户、管理界面加数据库仍不做"被取代；第 1 条的配置文件不再是唯一来源——设置类的键首次启动由文件播种、之后以数据库为准，部署层的键仍只认文件和环境变量。其余决定不变。
