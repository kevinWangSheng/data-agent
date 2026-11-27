# 产品配置分层；评测是一份配置文件

日期：2026-11-27。决定见 `docs/adr/0011-product-config-and-eval-profiles.md` 和对齐记录 `.scratch/product-config/decisions.md`（方向 1–9、Q1–Q6，人逐条确认）。取证：

- `docs/research/2026-11-27-product-config-audit.md`：审计，按条目编号引用（如"审计 3.2"）
- `docs/research/2026-11-27-config-layering-local-cloud.md`：上游与同类产品的分层、跑榜做法（"调研第 N 条"）

云端部署另见 `.scratch/cloud-deploy/spec.md`（依赖本 spec 的配置层）。先行工单 140（三处安全默认）不在本 spec 里。

## Problem Statement

产品和评测共用一套为跑榜写死的做法（ADR-0011"背景"）。使用者改不了上限、模型设置、提示词，没有地方放自己的 Skill、业务规则、MCP、插件；产品入口用的是消融配置名，默认 `full` 和人定的 `no-verifiers` 不一致；DAB 的答题规则、data-eng-bench 的 `DB_TYPE` 指令在产品提示词里。

## Solution

- 一个分层的 `config.toml`，所有产品入口（`data-agent run`、`data-agent web`、对话、IM）读它；`data-agent config show` 看生效配置。
- 产品默认 `full`：全部启用，Skill 按需加载、工作流 Skill 常驻；使用者的 Skill 目录、MCP、本地插件、说明文件都能加载。
- 评测入口改读 `evals/profiles/` 里的配置文件，消融名不变；每次运行记生效配置和哈希。
- 迁移分两步：先快照证明行为不变再切换；再单独量"评测改用产品新默认"。

## Implementation Decisions

### 配置文件与分层（Q1）

- 位置：用户级 `~/.data-agent/config.toml`；项目级 `<工作区>/.data-agent/config.toml`。显式 `--config <路径>` 或 `DATA_AGENT_CONFIG` 时只读这一个文件。
- 优先级：代码默认值 < 用户级 < 项目级（或显式文件）< 环境变量 < 命令行参数；`[policy]` 最上，任何层覆盖不了。
- 合并：按节按键合并，同一个键后面的层整个替换，列表不拼接。
- 环境变量只放密钥、`DATA_AGENT_CONFIG`、部署模式；现有的 `DATA_AGENT_MODEL`、`DATA_AGENT_BASE_URL` 作为 `[roles].main` / 主模型端点的覆盖保留。
- 密钥只能写 `${VAR}`；看起来像明文密钥（复用 IM 配置现有的判定与 pre-commit 的 gitleaks 规则之外，至少：`api_key`、`token`、`password`、`secret` 结尾的键不是 `${...}`）就拒绝加载。
- `--data-sources`、`--mcp-servers`、`--im` 三个参数去掉，内容并入 `[sources.*]`、`[mcp]`、`[im.*]`；不做兼容。`--config` 改成配置文件路径；消融名只在评测入口认。
- 有类型（pydantic 或 dataclass 加校验），未知键报错，报错指出文件和键。

### 分节（Q2）

见 `decisions.md` Q2 的清单。补充：

- `[layers]` 默认 `skills = verifiers = context = true`。
- `[models.<名字>]`：`base_url`、`api_key`、`input_price`/`cache_read_price`/`output_price`（每百万 token，可省；省了花费记"未知"、没有实扣上限、`classify_text` 照常可用，审计 3.3）、`context_window`、`compact_window`（省了用代码默认 1M/400k，审计 3.1）、`efforts`（可用取值，省了不校验，审计 3.4）。现有价目表（`agent.py:404-413`）变成代码默认的 `[models.deepseek-flash]`、`[models.deepseek-v4-pro]`。
- `[roles]`：`main`（必填，默认 `deepseek-flash`）、`small`、`classify`、`verifier`，省了都等于 `main`（审计 3.5）。
- `[limits]`：`max_budget_usd`（默认 1.0）、`max_turns.dbt/query/large`、`agent_timeout_sec`、`effort`。
- `[sandbox]`：`backend`、`image`、`cpus`、`memory`、`network`、`exec_max_seconds`（审计 4.4、4.6）。
- `[sources.<名字>]`：现有 `kind`、`path`/`env`、`dbt_project`，加 `description`、`docs`（目录，复制或只读挂进工作区，审计 5.2）、`schemas.include`/`schemas.exclude`（审计 1.3 之外的通用版）、`query_timeout_seconds`、`max_rows`、`duckdb.memory_limit`、`duckdb.max_temp_directory_size`、`dbt_timeout_seconds`（审计 4.2、4.3、4.6）。
- `[skills]`：`dirs`、`disabled`、`preload`；`[instructions]`：`files`；`[mcp]`：`servers`（内联，或 `file` 指向 Claude Code 格式的 JSON）、`claude_code_config`；`[plugins]`：`dirs`、`enabled`。
- `[prompts]`：可覆盖的提示词片段，至少 `answer_rule`（审计 2.3）。产品默认用产品版答题规则（允许表格、可以写假设），DAB 的版本写进 `evals/profiles/dab.toml`。`DB_TYPE` 那句（审计 2.4）只写进 `evals/profiles/data-eng-bench.toml`。任务文件提示（审计 2.5）产品运行总是加，评测配置文件照现在的条件。
- `[im.<平台>]`、`[trace]`、`[server]`（`host`、`port`、`runs_dir`、`mode`；`mode` 的行为见 cloud-deploy spec）、`[policy]`（`disabled_models`、`network`、`dbt_target`）。
- 内部调参常量（剩余轮数提醒点等，审计 7）不开放。

### Skill、MCP、插件（方向 1、3，Q3）

- `skills/` 加插件清单，作为本地插件 `data-agent` 传给 SDK `plugins=[{"type": "local", "path": ...}]`；`[skills].dirs` 里每个目录运行时包成一个本地插件（生成到本次运行的临时目录）。
- SDK `skills` = 发现的全部减 `disabled`；`preload` 的全文照旧进系统提示词（`load_skills`）。默认 `preload` = 本任务类型的工作流 Skill（`sql-workflow` 或 `dbt-workflow`）；方言 Skill、`dbt-write`、`dbt-debugging` 按需。dbt-workflow 第 2 步"先加载辅助 Skill"（`agent.py:112` 的注释"as there is no Skill tool"）随之改成按需。
- `${CLAUDE_SKILL_DIR}`：按需加载时 SDK 填宿主路径；Skill 目录按原路径挂进 Docker 沙箱，或 Skill 里统一写挂载路径——实测（工单 142）后定。
- Skill 工具每次调用写一条事件（Skill 名、来源插件）。
- MCP：`[mcp].servers` 挂给 agent，沿用工单 116 的放行清单和环境变量规则；`claude_code_config = true` 时另读使用者的 Claude Code MCP 配置（见"已定"1）。产品运行不再设 `strict_mcp_config`，但只加载这两处来源，不让 SDK 自己去找本仓库的 `.mcp.json`。
- 插件：`[plugins].dirs` 每个作为本地插件加载，`enabled` 为空表示全部。
- 禁用内置 Bash、不读本仓库 `.claude/`（`setting_sources` 不含 `project`）不变。

### 产品默认与入口

- 所有产品入口的默认层开关是 `full`（方向 7）；网页表单不再列消融配置（审计 2.2），只显示生效配置的摘要。
- 网页和 IM 的 effort、agent 超时从配置来（审计 4.5）。
- 值字典按数据源缓存，键为目录哈希；产品运行复用、评测运行照旧每次重建（审计 5.3）。

### 评测配置文件（Q4）

- `evals/profiles/data-eng-bench.toml`、`dab.toml`、`livesqlbench.toml`：基础配置；`evals/profiles/layers/{full,baseline,no-skill,no-verifiers,no-context,full-old-skill}.toml`：只有 `[layers]`（`full-old-skill` 另设 `[skills]` 指向 `skills-frozen/`）。
- `ablate`、`board`、`dab`、`livesqlbench`、`lsb-single-shot`、`jaffle`、Harbor agent 都改成"基础配置 + 层文件"，经 `--config <路径>` 传给 `data-agent run`。现有 `evals/configs/*.yaml` 里属于 agent 行为的值并入配置文件，Harbor 自己的 job 字段（并发、超时、retry）留在 yaml。
- 评测配置文件写 `[mcp]`、`[plugins]`、`[skills].dirs` 为空；`[policy]` 锁 `network = false`。
- 结果目录、报告、`evals compare` 照旧用消融名。

### 生效配置的记录（Q6）

- 事件文件开头一条 `config` 事件：生效配置全文（密钥是 `${VAR}` 原文）、sha256、参与合并的文件列表，以及 Skill、说明文件、插件目录里每个文件的哈希。
- `ablate` 的 `check_request`（`evals/ablate.py:413`）把生效配置哈希算进请求。
- 网页运行详情显示哈希和配置文件名，可展开看完整配置。

### 迁移（Q5）

- 第一步：评测配置文件复现现在的行为：`[skills].preload` = 现在注入的全部 Skill（查数：`sql-workflow` + 方言；dbt：4 个），并关掉 Skill 工具（`[skills].tool = false`，只评测用，产品默认开）；上限、提示词片段与现在相同。验收见 Testing Decisions"快照"。
- 第二步（不是工单，主会话做）：评测配置文件改用产品新默认，在 LiveSQLBench、data-eng-bench 开发集上和第一步配对比较，结果写回本 spec，由人定跑榜用哪种。

## 工单（人 2026-11-27 确认）

| # | 工单 | Blocked by |
|---|---|---|
| 141 | 验收测试（下面"验收测试接缝"，人审后合入） | — |
| 142 | 实测：DeepSeek 经 Anthropic 兼容接口调 Skill 工具、本地插件 Skill 被发现、`${CLAUDE_SKILL_DIR}` 在沙箱里的路径；不行就停下回对齐 | — |
| 143 | 配置加载：模型、分层合并、`${VAR}`、未知键、明文密钥、`config show`、`--config` 路径、三个旧参数并入 | 141 |
| 144 | 配置接到运行上：`[limits]`、`[sandbox]`、`[sources.*]` 新字段、`[policy]`、网页和 IM 的 effort/超时、产品默认 `full`、表单不列消融配置、值字典缓存 | 143 |
| 145 | 模型：`[models.*]`、`[roles]`、价格可省、上下文窗口、effort 取值、小模型/分类/验证器模型 | 143 |
| 146 | 评测配置文件与迁移第一步：`evals/profiles/`、所有评测入口改读配置文件、快照逐项相同、`config` 事件与哈希、`check_request` | 143 |
| 147 | Skill、MCP、插件：插件打包、按需加载与 `preload`、使用者 Skill 目录、`[mcp]` 与 `claude_code_config`、`[plugins]`、Skill 工具事件、dbt-workflow 第 2 步 | 142、143、146 |
| 148 | 提示词与业务上下文：`[prompts]`（产品版答题规则）、`[instructions]`、`[sources.*].docs`/`description`、`DB_TYPE` 与任务文件提示挪进评测配置 | 143、146 |
| 149 | 网页：运行详情显示生效配置（前端，照前端工单审查） | 146 |
| 150 | 清理：审计第 6 节 | — |

## Testing Decisions

### 验收测试接缝（人 2026-11-27 确认）

- **配置加载**：`load_effective_config(paths, environ, cli_overrides) -> EffectiveConfig`（名字由 141 定）：分层优先级、`[policy]` 覆盖不了、显式文件不读用户级/项目级、未知键报错、明文密钥拒绝、`${VAR}` 缺失报错、每个键的来源。
- **运行参数快照**：对一个任务和一份配置，构建出的 `ClaudeAgentOptions` 与系统提示词（`build_options`、`system_prompt`，`agent.py:1556`、`:1360`）序列化成快照。迁移第一步：每个榜单的样本题，新旧方式快照逐项相同。
- **产品默认**：不给任何配置文件时，`data-agent run`、网页、对话、IM 的层开关都是 `full`，Skill 工具开、`preload` 只有工作流 Skill。
- **评测隔离**：评测入口在使用者有 `~/.data-agent/config.toml`、Skill 目录、MCP、插件时，运行参数不变。
- **记录**：`config` 事件含生效配置、哈希、文件列表，且不含任何解析后的密钥值。

### 其他

- 单元测试不联网不调模型；142 和迁移冒烟的真实运行由主会话做（工单会话不能 source 模型的 env 文件）。

## 验收

- 141–150 合入；迁移第一步快照逐项相同；每个榜单真跑 1 题冒烟（主会话）。
- 文档：`docs/setup.md` 写配置文件、每一节、`config show`、评测配置文件；README 写产品默认与使用者的 MCP 不在只读保证之内。

## 已定（原"待确认"，人 2026-11-27 确认）

1. **使用者的 Claude Code MCP 配置**：读进来的服务器默认放行它的全部工具（照 Claude Code 自己的行为），可在 `[mcp]` 里按服务器名补 `allowedTools` 收窄；环境变量照 Claude Code 原样展开，不加 `DATA_AGENT_MCP_` 前缀限制；`config show` 和运行开头的事件列出这些服务器和工具。`[mcp].servers` 里写的服务器仍照工单 116 的规矩（必须有放行清单、前缀）。
2. 工单拆分（141–150）与"验收测试接缝"照上文。

## Update 2026-11-27（建票后，decisions.md Q8）

建票时发现的问题已定，细节以 `decisions.md` Q8 为准。改变本 spec 的几条：

- **141 同时改已合入的验收测试**：24 个 `tests/acceptance/` 文件钉着 `--config <消融名>`、`--data-sources`、`--mcp-servers`、`--im`；141 把它们改成新接口（只改调用，断言意思不变；和已定决定冲突的断言按新决定改），逐条列出给人审。
- **过渡**：143 期间 `--config` 同时接受路径和旧消融名；146 改完评测入口后删掉旧名。`--config` 可写多次，后面覆盖前面；显式 `--config` 优先于 `DATA_AGENT_CONFIG`。
- **Skill 文本不在迁移第一步改**：dbt-workflow 第 2 步等 Skill 改动推到迁移第二步一起量；147 只做机制。插件清单不计入 Skill 版本哈希。
- **键名**：`[layers]` 用 `skill`、`verifiers`、`context`；`[skills].tool`（默认 true）。
- **评测配置文件**：全部保留现在的答题规则（含 LiveSQLBench、jaffle）；只有产品换新版。`lsb-single-shot` 只读模型和价格。
- **模型没填价格**：不传 SDK 的 `max_budget_usd`，开头警告。
- **`[policy]` 和命令行参数冲突**：报错拒绝。
- **`config` 事件**：第二条事件；`version.config` 照旧写配置名。快照覆盖任务提示词。
- **`[server]` 的 host、port、runs_dir 和 `[trace]`** 归 144。

## Out of Scope

- 云端部署（cloud-deploy spec）。登录、多用户、管理界面加数据库。
- 新数据库类型（只支持 DuckDB、Postgres，`docs/plan.md` §1）。
- 改 Skill 内容提照做率（按轨迹另开）。

## Further Notes

- 第一步之前和之后的评测结果可比；第二步之后要配对比较。
- ADR-0001、ADR-0004、ADR-0010 各加 Update 2026-11-27 指向 ADR-0011。
