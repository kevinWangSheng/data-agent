# 99 — `dbt_execute`：连库执行 dbt 的唯一入口

Status: ready-for-agent
Blocked by: 97
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

dbt 任务带 `--sandbox` 时，agent 多一个 MCP 工具 `dbt_execute`：参数结构化、命令白名单照 SignalPilot，在宿主机上执行 dbt。`--dbt-target scratch` 时用 workspace 外生成的 profile，写到每次运行专用的临时 schema；`task`（CLI 默认）时照题目的 profile 写题目的库。网页 dbt 运行的 workspace 里不再有库文件和 `profiles.yml`。不带 `--sandbox` 时行为不变。

## Authority

- May edit: 新增 `src/data_agent/dbt_execute.py`；`src/data_agent/mcp_server.py`（注册工具）；`src/data_agent/cli.py`（`run` 和 `mcp-server` 的 `--dbt-target scratch|task`，CLI 默认 `task`）；`src/data_agent/agent.py`（只改把工具和参数加进选项与 MCP server 启动参数的部分）；`src/data_agent/web/launcher.py`（网页的 dbt 运行：拷项目时跳过库文件和 `profiles.yml`，库文件拷到 `<运行目录>/db/`，生成的 profile 写到 `<运行目录>/profiles/`）；`src/data_agent/web/sources.py`（只在布局检查需要跟着改时动，测试同步）；`src/data_agent/sandbox.py`（只加 stub profile 相关的接线）；`NOTICE`（复制 SignalPilot 代码时）；`tests/`（新增测试）；`tests/fixtures/`（新增小 dbt 项目夹具，可复用已有的）
- Must not edit: `command_guard.py`、`workspace_guard.py`、`skills/`、`evals/` 的评测行为、提示词文本、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "`dbt_execute`""凭据与只读"两节。ADR-0010 第 2、3 点。
- 上游（路径相对 `~/dev/AI/data-agent-refs/SignalPilot/signalpilot/gateway/gateway/`）：`mcp/tools/dbt_execute.py:1-67`（工具形状、临时 schema、凭据只在执行环境）；`standalone_chat/dbt_executor.py:103-109`（命令白名单、选择器正则、输出上限 20000、时限 900）、`:583-602`（`build_dbt_argv`）。SignalPilot 是 Apache-2.0：复制的代码保留版权头，登记 `NOTICE`。
- 我们是单机，workspace 本来就在宿主机上，不需要 SignalPilot 的 `sync_from_agent_sandbox`。
- 现在网页的 dbt 运行把整个项目连同 DuckDB 文件拷进 workspace（`launcher.py:390-402`），`profiles.yml` 里的相对路径靠文件在项目里的位置（`sources.py:144-148`）。移出后由生成的 profile 指向 `<运行目录>/db/` 里的库文件（spec "凭据与只读"一节）。
- 运行编号用 `--events-dir` 的目录名（和工单 97 一致），临时 schema 叫 `da_run_<运行编号>`。
- `--dbt-target` 的 CLI 默认是 `task`（现在的行为，Harbor 不用改）；网页后端传 `scratch` 是工单 100 的接线，本工单只提供选项和 `launcher.py` 的布局。
- 临时 schema 运行结束后保留（照 SignalPilot，spec Further Notes）。
- 现有 dbt 运行仍用 Bash 跑 dbt，本工单不动；切换到 `dbt_execute` 是工单 100 的事。
- 本机 Docker 是 colima，集成测试前先 `colima start`。colima 只挂 `$HOME`，`tmp_path` 下的目录挂进容器是空的；真实 Docker 的集成测试把 workspace 建在 `$HOME` 下（见工单 97 Context）。
- 依赖 97 的沙箱（stub profile 放在沙箱镜像里，dbt parse/compile 在沙箱里跑）。

## Acceptance

- [ ] 白名单外的命令（`show`、`run-operation`、`run; rm -rf /`）被拒绝；`select` 以 `-` 开头被拒绝；`threads` 超出 1–8 被拒绝（单元测试）
- [ ] argv 里 `--project-dir` 在 workspace 内，不经过 shell（单元测试）
- [ ] `--dbt-target scratch` 时生成的 profile 用 `da_run_<运行编号>` schema，`--profiles-dir` 在 workspace 外；`task` 时用题目的 profile（单元测试）
- [ ] 小 dbt 项目夹具上真实跑一次 `dbt run`：模型落在临时 schema，原 schema 不变（单元测试，用 dev 依赖组里的 dbt）
- [ ] 产品运行的 workspace 里没有库文件和带连接信息的 profile；沙箱里找不到它们（单元测试 + 一条真实 Docker 的集成测试）
- [ ] 不带 `--sandbox` 时工具清单和现在一字不变（单元测试）
- [ ] `make check` 通过

## Evidence required

`make check` 输出、集成测试输出。真实模型运行由主会话做：最终回复写好命令，用 `--sandbox docker` 和 `--dbt-target scratch` 跑一个本地 jaffle_shop 的 dbt 小任务，并在提示里要求 agent 用 `dbt_execute`；贴出工具调用、临时 schema 里的表、原 schema 没变的查询结果。

## Stop conditions

- 做不到"产品运行的 workspace 里没有凭据"而不改 Authority 以外的文件。
- spec 自相矛盾，或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: — (filled by the human at merge, optional)

## Comments
