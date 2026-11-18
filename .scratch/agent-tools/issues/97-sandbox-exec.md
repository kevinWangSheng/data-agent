# 97 — `sandbox_exec`：在隔离环境里执行任意命令

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`data-agent run --sandbox docker|local` 时，agent 多一个 MCP 工具 `sandbox_exec`，能在每次运行一个的隔离环境里执行任意 `bash -c`，并能把查询结果作为文件传进去。不带 `--sandbox` 时工具不注册，现有运行和评测的行为一字不变。

## Authority

- May edit: 新增 `src/data_agent/sandbox.py`（或同名包）；`src/data_agent/mcp_server.py`（注册工具、给 `query_database` 的成功结果加 id）；`src/data_agent/cli.py`（`run` 和 `mcp-server` 两个子命令的 `--sandbox` 选项、把运行编号传给 MCP server）；`src/data_agent/agent.py`（只改：把工具和参数加进选项与 MCP server 的启动参数，`agent.py:1508-1523`；运行收尾时删容器、启动时清理残留）；`src/data_agent/tool_annotations.py`；新增 `docker/sandbox/Dockerfile` 和 stub profile；`NOTICE`（复制或改写了上游内容时）；`tests/`（新增测试）；`Makefile`（只加构建沙箱镜像的目标，可选）
- Must not edit: `src/data_agent/command_guard.py`、`workspace_guard.py`（工单 100 改）、`skills/`、`evals/`、提示词文本、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "`sandbox_exec`"一节和"工具面"表。ADR-0010 第 1、2 点。
- 上游（路径相对 `~/dev/AI/data-agent-refs/`）：SignalPilot `SignalPilot/signalpilot/gateway/gateway/mcp/tools/sandbox_vm.py:1-17,170-200`（工具形状、每次运行一个、没有凭据、stub profile）；nao `nao/apps/shared/src/tools/execute-sandboxed-code.ts:42-52`（`data_files` 把查询结果挂成 CSV、产出目录）、`nao/apps/backend/src/agents/tools/execute-sandboxed-code.ts:150-175`（每次复用、`networkEnabled`）。
- 我们现有的先例：`evals/dab.py:526`（按名字清理残留容器）、`evals/dab_server.py:237-290`（查询结果存成变量和文件）。DAB 的 `ExecTool` 来自没有许可证的仓库，只能参考思路，不能复制代码。
- 沙箱的 `network` 在本工单是构造参数，默认关（`--network none`）；接到 `--network` 选项是工单 100 的事。
- 生命周期、运行编号（`--events-dir` 的目录名）、由运行进程删容器的理由，见 spec "`sandbox_exec`"一节。MCP server 是子进程，结束时会被直接杀掉，不能靠它收尾（`evals/dab.py:527-531`）。
- Skill 目录只读挂到 `/skills`，`${CLAUDE_SKILL_DIR}` 在 docker 后端换成 `/skills/<skill>`（spec 同一节）。本工单只做挂载和路径替换的能力，提示词和 Skill 文本在工单 100 改。
- 网页后端和对话的接线（带 `--sandbox docker`）是工单 100 的事，本工单不碰 `web/`。
- 不传任何 `DATA_AGENT_*`、连接串、profile 路径进容器。
- 本机 Docker 是 colima（`default` profile，2026-11-17 是 Stopped）；跑集成测试前先 `colima start`，用完不用停。
- 和工单 98、对话界面工单都可能改 `agent.py`、`cli.py`、`mcp_server.py`，后合并的一方 rebase。

## Acceptance

- [ ] docker 后端拼出的命令行：只挂 workspace；网络默认 `--network none`，构造参数打开时不带；环境变量里没有 `DATA_AGENT_*`、连接串、profile 路径；有 CPU、内存限制（单元测试）
- [ ] CLI 帮助文字写明 `local` 只能在隔离容器里用；`run` 和 `mcp-server` 都接受 `--sandbox`（单元测试）
- [ ] docker 后端把 Skill 目录只读挂到 `/skills`，`${CLAUDE_SKILL_DIR}` 替换成容器内路径；local 后端用真实路径（单元测试）
- [ ] 容器名或标签带运行编号；运行进程收尾时删容器，MCP server 被杀掉也不留容器（docker 调用用替身，单元测试）
- [ ] 超时杀掉命令并返回超时错误；输出超过上限时截断，并给出完整输出的文件路径（单元测试）
- [ ] `data_files` 把列出的查询结果写成 `data/<id>.csv`，内容与结果一致；未知 id 报错（单元测试）
- [ ] 同一次运行的两次调用复用同一个容器；启动时按标签清理残留容器（docker 调用用替身，单元测试）
- [ ] local 后端在临时目录里真实执行 `echo`、写文件、非零退出码（单元测试）
- [ ] 不带 `--sandbox` 时工具清单和现在一字不变（单元测试）
- [ ] 集成测试（真实 Docker，放进 `make test-integration`）：网络打开时 `pip install` 成功、关闭时失败；沙箱里看不到 workspace 外的文件；产出文件在宿主机 workspace 里能看到
- [ ] `make check` 通过

## Evidence required

`make check` 输出；真实 Docker 的集成测试输出（工单 agent 能跑 Docker 就自己跑，`docker info` 不通就写明）。真实模型运行由主会话做：最终回复写好命令，用 `--sandbox docker` 跑一条快速上手的查数并要求 agent 用 `sandbox_exec` 对结果做一次 pandas 计算，贴出工具调用和答案。

## Stop conditions

- 本机没有 Docker 且无法安装。
- 不改 Authority 以外的文件就做不到"不带 `--sandbox` 时一字不变"。
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
