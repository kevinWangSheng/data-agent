# agent 工具补全：shell 和代码放进沙箱，边界靠隔离和凭据

日期：2026-11-17。决策见 `docs/adr/0010-sandbox-and-credentials.md`（人 2026-11-17 逐条确认 6 点）。上游证据都在 `~/dev/AI/data-agent-refs/`，路径见文末。

## Problem Statement

agent 现在能做的事太少：

- **查数任务不能跑代码**：只有 Read、Glob、Grep 和 MCP 数据工具（`src/data_agent/agent.py:103-105`）。查出数据之后的计算、统计、跨库合并、画图都做不了。
- **dbt 任务的 Bash 只能跑 dbt**：`command_guard.py` 用白名单过滤命令，agent 要用的其他命令事先列不全。这个白名单是工单 04 自己加的，没有上游这样做。
- **任何运行都不能联网**：`WEB_TOOLS` 一律禁止（`agent.py:113-115`）。这是榜单的规矩（data-eng-bench 的 `disallowed_tools`），被写成了产品的规矩；ADR-0008 已知限制 14 已经说过"不取榜外数据"不是产品需求。
- **`execute_python` 只在 DAB 评测里有**（`evals/dab_server.py:485`），产品里没有。

上游的做法：shell 和代码完全放开，跑在隔离环境里，边界靠"隔离环境 + 拿不到写库凭据 + 数据库层只读"，不过滤命令。

## Solution

- **`sandbox_exec`**：一个 MCP 工具，在每次运行一个的隔离环境里执行任意 `bash -c`。产品里是 Docker 容器；跑 data-eng-bench 时 Harbor 的任务容器本身就是隔离环境，直接在本地执行。
- **沙箱没有数据库凭据，也看不到库文件**。代码要用的数据，由查询结果写成文件传进去。
- **`dbt_execute`**：连库执行 dbt 的唯一入口，参数结构化、命令白名单照 SignalPilot；只有它拿得到 profile。产品里只写到每次运行专用的临时 schema。
- **`network` 开关**：运行级选项，同时控制沙箱联网和 WebSearch、WebFetch。产品入口默认开，评测入口都不开。
- **文件工具**：Read、Glob、Grep、Write、Edit 在所有任务里都开，范围是 workspace，不碰凭据文件。
- **去掉** 内置 Bash 和 `command_guard.py`。

## Implementation Decisions

### 工具面（所有运行一样）

| 工具 | 说明 |
|---|---|
| MCP 数据工具 | 不变（`query_database`、`list_tables`、`describe_table`、`explain_query`、`classify_text`、`query_labels`，对话轮次另有 `clarification`）。只读网关作为查数工具保留，不再是安全边界 |
| `sandbox_exec` | 新增，见下 |
| `dbt_execute` | 新增，只在 dbt 任务里注册 |
| Read、Glob、Grep、Write、Edit | 所有任务都开，workspace guard 只保留"不出 workspace"和"不读写凭据文件" |
| WebSearch、WebFetch | 带 `--network` 时加载；不带时不加载，并列入 `disallowed_tools` |
| 内置 Bash | 不加载，列入 `disallowed_tools` |

工具面在产品、data-eng-bench、DAB 里一致，只有执行后端和开关不同，Skill 不用分版本。

### `sandbox_exec`

照 SignalPilot `sandbox_exec`（`gateway/mcp/tools/sandbox_vm.py:170-187`）和 nao `execute_sandboxed_code`（`shared/src/tools/execute-sandboxed-code.ts:42-52`）。

- 参数：`command`（`bash -c` 执行）、`timeout_seconds`（默认 300，上限 900，同 SignalPilot 的执行和 dbt 时限）、`data_files`（要放进沙箱的查询结果 id 列表）。
- 返回：退出码、stdout、stderr，合计有输出上限（同 `query_database` 的输出上限做法），超出部分存文件并告诉 agent 路径。
- 生命周期：每次 `data-agent run`（对话里就是每一轮）一个容器，第一次调用时创建，之后的调用复用（装过的包、进程状态保留），运行结束时删除。对话的 workspace 跨轮保留，所以文件跨轮还在，装过的包每轮要重装。
  - 运行编号：用 `--events-dir` 的目录名（网页后端的 `run_id` 就是这个目录名，`launcher.py:173-174`），CLI 不另加概念。容器名和标签带上它。
  - 删除由运行进程做（`agent.py` 的运行收尾），不靠 MCP server：MCP server 是 SDK 拉起的子进程，结束时会被直接杀掉（`agent.py:1508-1523`、`evals/dab.py:527-531`）。异常退出留下的容器，下次启动时按标签清理（照 `evals/dab.py:526`）。
  - `mcp-server` 子命令（`cli.py:142-165`）同样要接收 `--sandbox` 和运行编号。
- 后端（运行级选项 `--sandbox`，见"`network` 开关"一节）：
  - `docker`：产品默认。工作目录是 workspace 的 bind mount（可写）；网络关时 `--network none`；不传任何 `DATA_AGENT_*`、数据库连接串或 profile；限 CPU、内存（默认值由实现定，写成常量）。
  - `local`：直接在当前机器执行，只用于本身已经在隔离容器里的运行（Harbor 通过 CLI 传 `--sandbox local`）。网页后端只用 `docker`，不提供 `local`；CLI 的帮助文字写明 `local` 只能在隔离容器里用。
- 镜像：本仓库新增一个 Dockerfile，基于 `python:3.12-slim`，装 pandas、pyarrow、matplotlib、duckdb、dbt-core、dbt-duckdb（版本同 plan §5），和一份不含凭据的 stub duckdb profile，让 `dbt parse`、`dbt compile` 和 dbt-workflow 的两个脚本能在沙箱里跑（照 SignalPilot 的 stub profile，`sandbox_vm.py:9-12`）。
- Skill 目录：两个脚本在 `skills/dbt-workflow/` 下，不在 workspace 里。docker 后端把本次运行用的 Skill 目录（`RunConfig.skills_dir`）只读挂到 `/skills`，dbt-workflow 里的 `${CLAUDE_SKILL_DIR}` 换成 `/skills/dbt-workflow`；local 后端用真实路径（现在的替换方式，`agent.py:92`）。DAB 运行用 DAB 自己的 `python-data:3.12` 镜像，可配置。
- 查询结果传入：`query_database` 每次成功的结果带一个 id；`data_files` 里列出的结果在执行前写成 `data/<id>.csv`（照 nao 的 `data_files`，`execute-sandboxed-code.ts:47`）。DAB 现有的"结果存成变量"并入这套机制。
- 产出：沙箱写到 workspace 下的文件，宿主机和界面直接能看到（`outputs/` 目录约定写进工具说明）。

### 凭据与只读

- **库文件和凭据不放进产品的 workspace**：DuckDB 文件、带连接信息的 `profiles.yml` 放在 workspace 外，由 harness 管。
  - 现状：对话（查数）的数据源清单已经在 workspace 外（`conversations.py:130-131` 写在运行目录里）。网页的 dbt 运行把整个项目连同 DuckDB 文件拷进 workspace（`launcher.py:390-402`，`sources.py:144-148` 要求库文件在项目里）。
  - 改成：网页的 dbt 运行拷项目时跳过库文件和项目自带的 `profiles.yml`；库文件拷到 `<运行目录>/db/`，生成的 profile 写到 `<运行目录>/profiles/`，都在 workspace 外。`sources.py` 对数据源配置的要求可以不变（它管的是用户的源目录，不是 workspace）。沙箱只挂 workspace，所以既看不到库文件，也拿不到凭据（SignalPilot：`sandbox_vm.py:12` "NEVER holds warehouse credentials"）。
- **数据库层只读**：网关已经用只读方式打开连接（`gateway.py:141` DuckDB `read_only=True`，`:256` Postgres `read_only = True`），不变。
- **跑榜例外**：data-eng-bench 的库文件就在任务容器的项目里，`local` 后端能直接碰到它。接受，记为已知限制（SignalPilot 跑榜也是全开，`benchmark/agent/sdk_runner.py:52-59`）。

### `dbt_execute`

照 SignalPilot `dbt_execute`（`gateway/mcp/tools/dbt_execute.py:1-67`、`standalone_chat/dbt_executor.py:103-109,583-602`）。

- 参数：`command`（只能是 run、test、build、seed、snapshot、compile、`docs generate`）、`select`、`exclude`（只接受节点选择器语法，开头不能是 `-`）、`full_refresh`、`threads`（1–8）。
- 由参数拼成 argv 执行，不经过 shell。输出有上限（SignalPilot 20000 字符），时限 900 秒。
- 在宿主机上 agent 这一侧执行，`--project-dir` 是 workspace 里的项目，`--profiles-dir` 是 workspace 外持有凭据的目录。沙箱和 agent 都碰不到这个目录。不需要同步：workspace 本来就在宿主机上。
- 写到哪里（运行级选项 `--dbt-target scratch|task`）：
  - `scratch`：网页后端传这个。profile 的 schema 设成每次运行专用的 `da_run_<运行编号>`，不写生产 schema（SignalPilot：`dbt_execute.py:57-58`）；`--profiles-dir` 在 workspace 外。
  - `task`：CLI 默认值，也就是现在的行为。照题目自带的 profile 写进题目的库；Harbor 不用另传。这时 profile 在题目项目里（Harbor 的 `/app`），"profiles-dir 在 workspace 外"不适用。
- dbt 其他子命令（parse、ls、deps、debug）在沙箱里用 stub profile 跑，不进 `dbt_execute`。

### `network` 开关

- 运行级选项 `network`（`data-agent run --network`），不放进 `configs/*.toml`：那些层配置是产品和评测共用的（`--config` 必填，`cli.py:177`；产品默认配置 no-verifiers 也在里面）。开时 Web 工具加载、沙箱联网；关时都关（data-eng-bench：`configs/*.claude-code.yaml` 的 `disallowed_tools`；nao 的沙箱 `networkEnabled: true`，`execute-sandboxed-code.ts:169`；nao 的 Web 搜索由 `webSearch.enabled` 控制，`backend/src/services/agent.ts:402`）。
- CLI 不带 `--network` 时关，这样漏传只会少功能、不会让评测联网。产品入口（网页后端的 `launcher.py` 和对话的 `conversations.py`）默认带上，界面设置里能关（开关在工单 101）。
- 评测入口：Harbor 通过 `evals/harbor_agent.py:310` 拼 `data-agent run`，不带 `--network`；`evals/ablate.py`、`evals/board.py` 都是启动 Harbor。DAB 不启动 `data-agent run`，而是在进程内调用 `build_options`，再把 MCP server 换成 `dab_server`（`evals/dab.py:403-421`），所以 DAB 的检查对象是 `build_options` 的结果和 `dab_server` 的沙箱参数。
- 沙箱后端同样是运行级选项 `--sandbox docker|local`：网页后端和对话用 `docker`；Harbor 用 `local`。工单 100 之后 CLI 默认 `docker`，这样没有 Bash 的 dbt 运行也总有沙箱；没有 Docker 时第一次调用报错，和"dbt 不在 PATH"一样退出码 2。DAB 的沙箱在 `dab_server` 里，调用同一个沙箱模块，docker 后端、断网、DAB 的镜像。
- 2026-11-17 实测：用项目的 `ModelSettings.sdk_env()` 走 DeepSeek 端点（`deepseek-flash`），WebSearch 返回了带链接的结果，WebFetch 抓 `https://pypi.org/pypi/duckdb/json` 得到 `info.version`，两次都 `error=None`。

### 提示词与 Skill

- `agent.py` dbt 提示词里"Bash runs only dbt commands"一段（202-216 行）改成：shell 和代码用 `sandbox_exec`，连库执行 dbt 用 `dbt_execute`。
- 查数提示词加一句：查询结果需要计算、合并或画图时，用 `sandbox_exec` 并通过 `data_files` 传入结果。
- `skills/dbt-workflow`、`skills/dbt-write` 里"在 Bash 里跑 dbt"改成 `dbt_execute`；两个脚本改成在 `sandbox_exec` 里跑。文件头注明改动，更新 `NOTICE`。

### 规则与文档（工单 D 一起改，和代码同时生效）

- AGENTS.md 硬规矩"所有对数据源的 SQL 走只读网关；唯一的写方式是改 dbt 项目后 `dbt run`"改成：
  - 沙箱和 agent 拿不到写库凭据；对数据源的写只走 `dbt_execute`（产品里写到每次运行的临时 schema）。
  - 跑榜和评测的入口一律不开 `--network`。
- `CODING_STANDARDS.md` 的"Never bypass the read-only gateway"按上面改写。
- `CONTEXT.md`：改"Read-only gateway""dbt write"词条，新增"Sandbox（沙箱）""dbt_execute"。
- ADR-0002、ADR-0005 末尾加一行"见 ADR-0010"。

### 网页

`sandbox_exec`、`dbt_execute` 是 MCP 工具，走现有的工具开始、完成事件。界面上的显示（命令、输出、产出文件，"守卫拒绝"分类去掉 Bash）和联网开关另开前端工单，在工单 D 之后。人 2026-11-17：界面基本做完，之后的调整统一放在这张工单里。

## 工单

| 工单 | 内容 | 依赖 |
|---|---|---|
| 97 A | `sandbox_exec`：docker 和 local 后端、镜像、Skill 目录挂载、查询结果传入、生命周期与清理（运行编号、运行收尾）；`run` 和 `mcp-server` 的 `--sandbox` 选项，不带时不注册（评测行为不变） | 无 |
| 98 C | `--network` 选项：Web 工具加载与禁止；产品入口（`launcher.py`、`conversations.py`）带上；评测入口不带的测试 | 无（和 A 并行） |
| 99 B | `dbt_execute`、`--dbt-target`、网页 dbt 运行的库文件和 profile 移出 workspace；不带 `--sandbox` 时不注册 | 97 |
| 100 D | 把 `--network` 接到沙箱网络；CLI 默认 `--sandbox docker`，产品入口带 `--sandbox docker`、dbt 带 `--dbt-target scratch`，Harbor 带 `--sandbox local`；DAB 的 `dab_server` 换成同一个沙箱模块；去掉内置 Bash 和 `command_guard.py`，改 workspace guard、提示词、Skill、规则和文档；重新生成评测快照（人 2026-11-17 批准覆盖）；跑分验收 | 97、98、99 |
| 101 | 前端：运行页和对话页显示两个新工具，"守卫拒绝"分类调整，联网开关 | 100 |

A、C 和对话界面的工单（89、91 等）并行；它们只在 `agent.py`、`mcp_server.py` 上可能有小冲突，后合入的 rebase。

## Testing Decisions

单元测试不调模型、不起 Docker（docker 后端测拼出来的命令行）；真实 Docker 和真实模型放进 `make test-integration`。

- **`sandbox_exec`**：
  - docker 后端的命令行：挂载只有 workspace；网络关时带 `--network none`，开时不带；环境变量里没有 `DATA_AGENT_*`、连接串、profile 路径；有 CPU 和内存限制。
  - 网页后端启动的运行只会用 `docker` 后端。
  - 超时：命令被杀掉，返回超时错误；输出超过上限时截断并给出完整输出的文件路径。
  - `data_files`：列出的结果写成 `data/<id>.csv`，内容和查询结果一致；未知 id 报错。
  - 同一次运行的两次调用复用同一个容器；运行结束后容器被删；启动时清理残留容器（docker 调用用替身）。
  - local 后端在临时目录里真实执行 `echo`、写文件、非零退出码。
- **`dbt_execute`**：
  - 白名单外的命令（`show`、`run-operation`、`run; rm -rf /`）被拒绝；`select` 以 `-` 开头被拒绝；`threads` 超出 1–8 被拒绝。
  - argv 里 `--project-dir` 在 workspace 内；不经过 shell。
  - `--dbt-target scratch` 时生成的 profile 用 `da_run_<运行编号>` schema、`--profiles-dir` 在 workspace 外；`task`（CLI 默认）时用题目的 profile。
  - 小 dbt 项目夹具上真实跑一次 `dbt run`，模型落在临时 schema，原 schema 不变。
- **凭据**：产品运行的 workspace 里没有库文件和带连接信息的 profile；Read、Write、Edit、Grep 仍然拒绝 `.env*` 和密钥文件（沿用现有测试）。
- **工具面**：所有运行的工具列表里没有内置 Bash，`disallowed_tools` 里有；查数任务有 Write、Edit；带 `--network` 时有 WebSearch、WebFetch，不带时没有且被禁止。
- **评测入口**：`evals/harbor_agent.py` 拼出的 `data-agent run` 命令行不含 `--network`；`evals/dab.py` 用的 `build_options` 结果里没有 Web 工具、`disallowed_tools` 有它们，`dab_server` 的沙箱断网；CLI 不带 `--network` 时 Web 工具被禁止、沙箱断网。
- **产品入口**：`launcher.py` 和 `conversations.py` 拼出的命令行带 `--network`、`--sandbox docker`；dbt 运行带 `--dbt-target scratch`；网页的 dbt 运行 workspace 里没有库文件和 `profiles.yml`。
- **集成测试**（真实 Docker；本机是 colima，先 `colima start`）：网络开时沙箱里 `pip install` 能装包，关时失败；沙箱里找不到库文件和 profile；产出文件在宿主机 workspace 里能看到。

## 验收

新增能力，不针对某个失败模式，不套 ADR-0008 的"目标题必须涨"，只要求不退步：

- 开发集 full 每题 3 次，不带 `--network`，判定通过率的点估计不低于当前 main（旧版结果按 ADR-0008 的条件复用）；回归集不退步。约 $7–15。
- data-eng-bench 和 DAB 各跑一次小范围验证，各 20 次以内（已预授权的小规模运行），不低于上一次正式结果在同一组题上的成绩。
- 产品入口（网页后端，带 `--network`）手动跑 2–3 个需要 Python 或联网的真实问题，用于演示，不计分。
- 工单 97–99 是新能力，单元测试 + 一次真实运行即可；跑分验收在 100 合入前做，因为提示词和 Skill 在 100 才改。

## Out of Scope

- micro-VM（nao 的 boxlite、SignalPilot 的 VM）：先用 Docker。
- 沙箱里直接连数据库（第 2 点：超大结果集的场景出现了再议）。
- agent 直接写生产 schema、把临时 schema 合进生产（由人走 dbt 的正常发布流程）。
- 产品里自动清理临时 schema（见 Further Notes）。
- 沙箱密钥注入（nao 的 sandbox secrets）、MCP 连接器。
- 新的 Skill 内容（只改已有 Skill 里 Bash 的写法）。

## Further Notes

- 临时 schema 运行结束后保留，方便人看结果：照 SignalPilot（`dbt_execute.py:57` 的 `sp_chat_...`，gateway 里没有删除它的代码）。
- 已知限制：跑 data-eng-bench 时 `local` 后端能直接写题目的 DuckDB 文件，"只经 `dbt_execute` 写"在跑榜时不强制。
- 上游证据：
  - SignalPilot 跑榜 agent：`SignalPilot/benchmark/agent/sdk_runner.py:52-59`（`bypassPermissions`，无工具白名单）；Skill 里 `allowed-tools: Bash(dbt *)` 只是免确认（`signalpilot-plugin/skills/dbt-workflow/SKILL.md:5`）。
  - SignalPilot 产品：`SignalPilot/signalpilot/gateway/gateway/mcp/tools/sandbox_vm.py`、`dbt_execute.py`、`standalone_chat/dbt_executor.py`。
  - nao：`nao/apps/backend/src/agents/tools/index.ts`、`execute-sandboxed-code.ts`、`backend/src/services/agent.ts:397-409`、`backend/src/types/agent-settings.ts`。
  - dash：`dash/dash/tools/build.py:23-35`（只读由 Postgres 的 `default_transaction_read_only` 保证）。
  - data-eng-bench：`configs/data-eng-bench-duckdb.claude-code.yaml:29`。
- DAB 的 `ExecTool` 来自没有许可证的仓库，不复制代码；`sandbox_exec` 是我们自己的实现，只用 DAB 的镜像。
