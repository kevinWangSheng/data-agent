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

## Q6 记录生效配置（已定）

- 事件文件开头一条配置事件：合并后生效的完整配置、它的 sha256、参与合并的文件列表；密钥只记 `${VAR}` 引用，不写解析后的值；照 harbor `lock.json` 记 Skill、说明文件、插件目录里每个文件的哈希。
- 每个键的来源只在 `config show` 里显示，事件文件不记。
- `ablate` 的 `check_request`（`evals/ablate.py:413`）加生效配置哈希：同一 `--out` 哈希不同就拒绝续跑。
- 网页运行详情显示配置哈希和配置文件名，可展开看完整配置。

## Q7 云端模式（已定）

`[server].mode = "cloud"` 时：
- 网页：不登录，但要访问令牌 `DATA_AGENT_WEB_TOKEN`（bearer）；没设令牌就拒绝监听非本机地址（照 ktx）。
- IM：照旧用允许用户名单，名单为空拒绝启动。
- 密钥：配置引用的环境变量缺一个就拒绝启动。
- 联网：默认关，可配置打开（本地模式照 ADR-0010 默认开）。
- dbt：强制临时 schema（`policy.dbt_target = "scratch"`）。
- 模型端点：不能在单次运行里覆盖，只用配置文件里的。
- 沙箱：Docker 后端，Compose 挂 Docker socket 由 data-agent 起沙箱容器；沙箱不挂凭据、默认不联网；不许 `local` 后端；文档写明挂 socket 等于宿主机 Docker 权限。
- 上下文从 git：`[skills].dirs`、`[instructions].files`、`[sources.*].docs` 可写 git 地址加分支或提交，启动拉一次，按 `refresh` 间隔再拉（照 nao）。
- `runs_dir` 放持久卷；加 `/healthz`；交付镜像和 `docker-compose.yml` 示例，文档写清每个环境变量。
- 本块第一张工单是实测：容器里挂 Docker socket 起沙箱、工作区路径能否挂进沙箱（容器内外路径不同）；跑不通就停下回对齐。

## Q8 建票时发现的问题（2026-11-27）

### 人定的 5 个

1. 已合入的验收测试钉住旧接口（24 个 `tests/acceptance/` 文件用 `--config <消融名>`、`--data-sources`、`--mcp-servers`、`--im`、`load_config`、`CONFIG_NAMES`）：授权 141 把它们改成新接口，只改调用方式、断言意思不变；和已定决定直接冲突的断言（如 `test_web_api.py:354-357` 表单能选 `baseline`）按新决定改；141 逐条列出，人审后合并。
2. dbt-workflow 第 2 步改写、以及任何 Skill 文本改动，推到迁移第二步，和"评测改用产品新默认"一起在开发集上量；插件清单文件不计入 Skill 版本哈希。
3. 云端网页令牌：新增前端工单 156。打开网页先输一次令牌，服务器回 HttpOnly Cookie；接口接受 Cookie 或 bearer 请求头。前端工单由 Opus 审（不用 Codex）。
4. 云端联网：默认关，配置文件可打开；单次运行不能超过配置（配置关了联网，请求联网拒绝 409，同 140）。表单的联网勾选默认值跟服务器配置走（放进 156）。
5. 云端认环境变量 `DATA_AGENT_BASE_URL`（部署层）；"不能改端点"只针对单次运行，现在单次运行本来就改不了，不单写测试。

### 主会话定的实现细节

- `--config` 可写多次，后面覆盖前面（照 harbor）；显式 `--config` 优先于 `DATA_AGENT_CONFIG`。评测入口传"基础配置 + 层文件"两次 `--config`。
- 过渡：143 期间 `--config` 同时接受路径和旧消融名（`data-agent run`、`mcp-server`）；146 把评测入口都改完后删掉旧名。
- `config` 事件是第二条（在 `version` 之后）；`version.config` 照旧写配置名：评测写层名，产品写配置文件名（无文件时 `default`）。
- 快照比较覆盖任务提示词（`ANSWER_RULE`、`DB_TYPE`、`TASK_FILE_NOTE` 在任务提示里，`agent.py:2292-2302`）。
- `[layers]` 的键用 `skill`（单数，和 `RunConfig`、`version.layers` 一致）、`verifiers`、`context`。`[skills].tool`（默认 true）补进 Q2 清单。
- 已有的 `bot_token_env`、`[sources.*].env = "WAREHOUSE_URL"` 写的是变量名，保留；"只写 `${VAR}`"指值本身是密钥的键。
- 迁移第一步：所有评测配置文件（含 LiveSQLBench、jaffle）保留现在的答题规则；只有产品用新版。
- `lsb-single-shot` 只从 `livesqlbench.toml` 读 `[models]`/`[roles]`（模型、价格），不套层文件。
- Harbor 容器里的配置文件：146 自选打进 wheel 或在 `install` 时上传。
- 模型没填价格：不传 SDK 的 `max_budget_usd`，开头给一条警告。
- `[server]`：host、port、runs_dir 归 144（`data-agent web` 读它们，`--runs-dir`/`--port` 作为覆盖保留）；mode 归 153；refresh 由 154 补进配置格式。`[trace]`（base_url、tier；密钥仍是环境变量）归 144。
- `[policy]` 和命令行参数冲突：报错拒绝，不悄悄改成锁定值。
- `max_turns.large` 保留在 `[limits]`（DAB 用）；150 不删。
- 云端：IM 名单为空、缺 `${VAR}` 在所有模式都拒绝，写守门测试；153 加 Blocked by 146；`/healthz` 返回 `{"ready": true}`，两种模式都有；部署模式环境变量 `DATA_AGENT_MODE`；运行子进程的环境去掉 `DATA_AGENT_WEB_TOKEN`；任何模式监听非本机地址都要令牌。
- git 来源：克隆放 `<runs_dir>/.cache/git`；服务器进程统一拉，运行读这份；刷新先克隆到新目录再原子切换，正在跑的运行用旧目录；令牌经 `GIT_ASKPASS`，不进 URL、`.git/config`；154 只负责"git 地址 → 本地路径"，接入由 147、148 用；154 加 Blocked by 152（克隆出的 Skill 目录要能挂进沙箱）。
- 镜像：Compose 同时构建产品镜像和沙箱镜像（`DockerSandbox` 用 `--pull never`）；产品镜像装 dbt。

### 改票后主会话再定的（2026-11-27）

- 单独用 `data-agent run`、配置里有 git 地址：启动时拉一次到 `~/.data-agent/cache/git`，不定时刷新。
- 网页 Cookie：值是签过名的会话值，不是令牌原文；cloud 默认带 `Secure`，local 不带。
- 151 的监听地址测试标 `TICKET_153`（153 已依赖 144），151 不加依赖。
- `NewRun.tsx` 的合并顺序：144、149 先，156 最后。
- 146 加 Blocked by 144（146 删 `CONFIG_NAMES`，网页 `app.py:113`、`launcher.py:256` 由 144 先改掉）。

### 留给下一个会话（派工前审票时处理）

1. 141 合入后到 143/146 合入前，被改成新接口的旧验收测试是 XFAIL，这段时间它们守的行为没人守；派工前定是否接受，或让 143 尽快跟上。
2. 144 写了"非本机 host 在 153 加令牌前拒绝"——起草 agent 的保守解读，审票时确认。
3. git 地址由 154 转成本地路径，147、148 不依赖 154，也没写遇到 git 地址怎么办；154 合入后再补接入。
4. 141 自己提、人审：快照里 MCP 服务器的 `--config` 参数（名字→路径）怎么比；LiveSQLBench、jaffle 的快照怎么在没有 Postgres 时生成。

## 设置界面 + Slack 权限（2026-11-27 续，人逐条确认；提案见 `proposal-settings-ui.md`）

- S1 频道层谁能改：按设置的风险分。模型、数据源、MCP 连接、Skill 与插件、联网、上限（访问和花费类）只有 Slack 工作区 Owner/Admin 能改；频道说明、effort、是否要 @ 才回复（行为类）频道成员能改；部署的人可在 `[policy]` 把行为类也收紧到只有管理员。频道里任何设置被改，bot 在频道发一条"谁改了什么"，同时记审计。不用 Slack 的"频道管理者"角色（企业版才有，API 可得性未确认），先只分工作区管理员和频道成员两档。
- S2 一条消息用哪套设置：频道里 = 工作区 + 频道（个人层不参与，同频道同口径）；一对一私聊 = 工作区 + 个人；`[policy]` 对所有层生效。频道里用本人连接器做本人任务（Claude in Slack 的例外）排在后面（见"后续"）。
- 不再分一期、二期（人 2026-11-27）：只有"现在做"和"排在后面"。排在后面的记进下面的"后续"清单，之后按依赖排进工单。
- S3 界面里新增数据源连接：要做，排在后面。先做的版本里设置页只选用部署层登记的连接，显示名字、类型、是否只读，不显示连接串和密码，并提示新增找部署管理员。排到时照 SignalPilot/Dify：环境变量里的主密钥加密存进数据库，加测试连接、只读校验（ADR-0010）、密钥轮换、删除清理。

- S4 单条消息或线程里的临时覆盖（照上游：Claude in Slack、Cursor、Dust、Codex、Devin 都允许）：现在做。频道或线程里任何人都能用。模型和 effort 可选 `[policy]` 允许范围内的任何值，对整个线程生效直到再换（照 Claude in Slack）；数据源和 MCP 连接只能从本频道已开放的里挑，不能借一条消息新开访问（S1 不变）；Skill 和说明在线程开始时固定。越界的值 bot 回复说明不允许，不悄悄替换；用了什么覆盖记进运行事件。

### 后续（排在后面，要做）

- 界面里新增数据源连接（S3）。
- 频道里用本人自己的连接器和凭据做本人任务（S2 的例外）。
