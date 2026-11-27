# product-config：对齐会话已定的决定

写 spec 之前的对齐记录（2026-11-27，人逐条确认）。依据：`docs/research/2026-11-27-product-config-audit.md`、`docs/research/2026-11-27-config-layering-local-cloud.md`。spec 写成后以 spec 为准。

## 方向（已定）

1. 产品与评测分开。产品打开 Claude Code 的默认能力：Skill、使用者的 MCP 配置、本地插件。禁用 Bash（ADR-0010）、不读本仓库 `.claude/`（ADR-0001）保留。
2. 不写死：审计查出的写死、临时、为评测而设的东西统一改成可配置。
3. `full` = 全部启用、按需加载、少数常驻（`preload`）。agent 不读或不照做 Skill 是我们的问题，之后按轨迹调；接受这个风险。
4. 跑榜照使用者的方式配置，走同一套配置机制，不走专门代码路径。
5. 分层：代码默认值 < 配置文件 < 环境变量（只放密钥、部署模式、配置文件路径）< 命令行参数；最上面是运维锁定；加 `data-agent config show`。
6. 一期云端只做"能用 Docker 部署到服务器给 IM bot 和网页用"：部署模式开关、cloud 下默认收紧、监听地址可配、上下文可从 git 拉。登录、多用户、管理界面加数据库不做。改 `docs/plan.md:38`、`:349`。
7. 产品默认从 `no-verifiers` 改回 `full`（推翻 2026-11-08）。
8. 使用者自己的 MCP 服务器可带写库凭据：产品允许，文档写明不在只读保证之内；评测一律不加载。
9. 迁移：6 个配置改成评测配置文件后，先在小样本上验证新旧跑法一致再切换。

## 先行工单

- 140 产品路径三处安全默认（CLI dbt 默认 `scratch`、服务器级联网开关、隐藏别的运行的 `da_run_*`），不等 spec。

## Q1 配置文件放哪（已定）

- 一个文件 `config.toml`，把现有 `--data-sources`（TOML）、`--mcp-servers`（JSON）、`--im`（TOML）并成其中的节；旧的三个参数去掉，不做兼容。
- 用户级 `~/.data-agent/config.toml`；项目级 `<工作区>/.data-agent/config.toml`（不含密钥，可进 git）；不加 local 层。不放 `~/.config/data-agent/`（密钥目录，coding agent 不能读）。
- 显式 `--config <路径>` 或 `DATA_AGENT_CONFIG` 时只读这一个文件，不找用户级、项目级（云端挂载、评测配置文件都用这种）；否则 代码默认值 < 用户级 < 项目级 合并；命令行参数最后覆盖。
- 合并：按节按键合并，同一个键后面的层整个替换，列表不拼接。
- `--config` 改成配置文件路径；现有 6 个消融配置名变成 `evals/profiles/*.toml`（Q4）。
- 密钥只写 `${VAR}` 引用；出现明文密钥拒绝加载。

## Q2 配置文件分节（已定）

- 节：`[layers]`（skills/verifiers/context，默认全开）、`[models.<名字>]`（base_url、`api_key = "${VAR}"`、单价、context_window、compact_window、effort 取值）、`[roles]`（main/small/classify/verifier，只填 main 时都用它）、`[limits]`（花费、轮数、agent 超时、effort）、`[sandbox]`（后端、镜像、CPU、内存、network、sandbox_exec 最长时间）、`[sources.<名字>]`（现有字段 + description、docs、schemas.include/exclude、查询超时、行数上限、DuckDB 内存/溢写、dbt 超时）、`[skills]`（dirs、disabled、preload）、`[instructions]`（files）、`[mcp]`（servers 内联或 file；claude_code_config）、`[plugins]`（dirs、enabled）、`[prompts]`（answer_rule 等片段；评测配置文件写评测版）、`[im.<平台>]`、`[trace]`、`[server]`（host、port、runs_dir、mode）、`[policy]`（disabled_models、network、dbt_target，覆盖不了）。
- 有类型，未知键报错；`data-agent config show` 打印每个键的值和来源；内部调参常量不开放。
- `[prompts]` 在评测配置文件里写榜单的官方答题格式，不算违反 ADR-0005（复现官方提示、不在 Skill 里、产品默认不受影响）。
- 模型没填价格：没有实扣上限，`classify_text` 照常可用，花费记"未知"。

## Q3 Skill 按需加载（已定）

- 内置 `skills/` 打包成本地插件 `data-agent`（Skill 名 `data-agent:sql-workflow` 等），照 SignalPilot 产品版。
- 使用者 `[skills].dirs` 每个目录运行时包成本地插件；本地、云端、对话（各自的 `CLAUDE_CONFIG_DIR`）同一条路，不依赖 `~/.claude`。
- SDK `skills` = 发现的全部减 `disabled`，模型见名字和描述，经 Skill 工具按需读全文；`preload` 的全文照旧进系统提示词。
- 默认常驻只有工作流 Skill（`sql-workflow`、`dbt-workflow`）；方言 Skill、`dbt-write`、`dbt-debugging` 按需；dbt-workflow 第 2 步"先加载辅助 Skill"随之改。
- Skill 脚本路径：`${CLAUDE_SKILL_DIR}` 由 SDK 填宿主路径，沙箱看不到；Skill 目录按原路径挂进沙箱或统一写挂载路径，先实测再定。
- Skill 工具每次调用记进事件文件，供按轨迹调照做率。
- 评测配置文件写死 Skill 列表和 `preload`，Skill 版本照旧记哈希。
- 本块第一张工单是实测：DeepSeek 经 Anthropic 兼容接口能否调 Skill 工具、本地插件里的 Skill 能否被发现、脚本路径；任一不行就停下回对齐。

## Q4 评测配置文件（已定）

- 每个榜单一份基础配置文件，格式同使用者的 `config.toml`：`evals/profiles/data-eng-bench.toml`、`dab.toml`、`livesqlbench.toml`。写死模型、上限、`network = false`、沙箱、Skill 列表和 `preload`、不加载使用者的 MCP 和插件、该榜单官方的 `[prompts]`；现在分散在 `evals/configs/*.yaml`、`evals/dab.py` 里的值收进来。
- 消融 = 基础配置 + 层开关覆盖，名字不变（`full`、`baseline`、`no-skill`、`no-verifiers`、`no-context`、`full-old-skill`），结果目录、报告、`evals compare` 照旧用这些名字，历史结果能对上。
- 层开关写成 `evals/profiles/layers/*.toml`（只有 `[layers]` 几行，`full-old-skill` 另指 `skills-frozen/`），评测入口叠加在基础配置上。
- `data-agent run --config` 只接配置文件路径；消融名只有评测入口认。

## Q5 迁移判定（已定）

- 第一步只换机制不换行为：评测配置文件完全复现现在的行为（现有 Skill 全文进提示词、Skill 工具关、上限与提示词片段不变）。判定：每个榜单几道样本题，新旧方式各生成一次完整运行参数做快照测试，系统提示词全文、工具清单（允许/禁止）、MCP 服务器、Skill 版本哈希、轮数与花费上限、传给 CLI 的环境变量逐项完全相同；$0。另每个榜单真跑 1 题冒烟。
- 第二步评测改用产品新默认（按需加载、只常驻工作流 Skill），当作一次普通 Skill 改动：先在开发集上量、和第一步配对比较，再定跑榜用哪种；不和第一步混在一起。
