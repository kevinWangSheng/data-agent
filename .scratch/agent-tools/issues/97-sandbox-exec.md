# 97 — `sandbox_exec`：在隔离环境里执行任意命令

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-17
Branch: worktree-97-sandbox-exec

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
- colima 只把 `$HOME` 挂进 VM：`/var/folders/...`（pytest 的 `tmp_path`、`mktemp -d`）下的目录 bind mount 进容器后是空的，不报错（主会话 2026-11-17 实测；`$HOME` 下的目录正常，容器能联网）。真实 Docker 的集成测试把 workspace 建在 `$HOME` 下（例如 `tmp_path_factory` 换成 `$HOME/.cache/data-agent-tests/` 下的临时目录，测完删掉）。
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

- Attempts: 1
- Review rounds: 2
- Model spend: $0.005 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: implemented (`3151650`, `ca6af3d`, `868fd03`), real-model run done; PR #107 waiting for CI and the Codex PR review
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-17（工单会话，主会话抄录）：分支 `worktree-97-sandbox-exec`，三个提交 `3151650`、`ca6af3d`、`868fd03`。
  - 不调模型的真实运行：`uv run python runs/sandbox_demo.py`。用 stdio 起真实的 `data-agent mcp-server --sandbox docker --run-id demo-97`，先 `query_database`，再用 `sandbox_exec` 带 `data_files=["query_1"]` 跑 pandas。
    - `query_database` 返回 "(20 rows) … [result id: query_1 …]"，`sandbox_exec` 退出码 0，结果和手算一致。
    - server 运行时有一个容器 `data-agent-sandbox-demo-97`；server 被杀后容器还在；运行收尾后没有容器了。
    - 启动清理也用真实 Docker 验证过：owner 进程已退出的容器被 `remove_orphaned_containers()` 删掉。
  - `make check`：exit 0；ruff、format 通过；basedpyright 0 errors；pytest 2473 passed、1 skipped（基线 2424）；web 295 passed。
  - 集成测试 `uv run pytest tests/test_sandbox_docker.py -m integration`（colima，Docker 29.2.1）：10 passed。覆盖：
    - 网络开时 `pip install` 成功，关时失败；
    - 看不到 workspace 外的文件和宿主的环境变量；
    - `/skills` 只读；
    - 产出文件在宿主机上看得到；
    - stub profile 下 `dbt parse` 成功；
    - 超时，包括忽略 TERM 的子进程；
    - 容器被删除。
  - 偏离工单的决定（主会话已确认，理由见各条）：
    1. 镜像目录是 `sandbox-image/`（含 `Dockerfile` 和 `bash_env.sh`），不是 Authority 写的 `docker/sandbox/`。仓库根目录下的 `docker/` 会被 Python 当成命名空间包导入，顶替 Docker SDK，`evals/dab.py` 的 `importlib.import_module("docker")` 因此出错，曾挂掉 33 个测试。主会话接受：只改了目录名，范围不变。
    2. 运行编号是 `<events-dir 目录名>-<8 位 hex>`（`SandboxRun.of_run`），容器名和标签都带它。只用目录名的话，不同 worktree 里同名的并发运行会互删容器（Codex 和 Spec 审查都指出）。主会话接受。**给 99**：临时 schema `da_run_<运行编号>` 要不要带这个后缀、`-` 怎么处理，由 99 定，写进 Comments。
    3. 启动清理只删 owner 进程（MCP server pid 标签）已不在的容器，不会删掉并发运行的容器。
    4. 容器以宿主 uid:gid 运行（`--user`），镜像设 `HOME=/tmp`，pip 自动改成用户安装。这样 Linux 上 workspace 里不会出现属主为 root 的文件，容器里也做不了 mknod。
    5. stub profile 由 `BASH_ENV` 在每次 `bash -c` 前重写：只找两层以内的 `dbt_project.yml`，按项目当时用的 profile 名写到 `/tmp/stub-profiles`。entrypoint 的做法和第一次 `docker exec` 有竞态，没用。
    6. local 后端给命令一个白名单环境（PATH、HOME、LANG 等），不带 `DATA_AGENT_*`、模型 key、Postgres URL。
    7. `${CLAUDE_SKILL_DIR}` 的替换能力在 `sandbox.skill_text`，按工单留给 100 去接到提示词。
    8. `sandbox_exec` 的注解不带 readOnlyHint，所以同一轮里的多次调用串行执行。
  - 审查：
    - da-review 第 1 轮 4 条，都修了：
      - 宿主机内存无上限：每个输出流最多保留 10 MB，读线程把其余的读完丢掉；
      - 产出文件属主是 root：改用 `--user`；
      - `data/<id>.csv` 是 FIFO 时 server 会卡住：先写新文件再 rename 覆盖，从不打开已有路径；
      - `timeout -k` 升级成 KILL 后退出码是 137：也算作超时。
    - da-review 第 2 轮 2 条，都修了：
      - 同名运行互删容器：见偏离第 2 条；
      - 超时后忽略 TERM 的子进程还活着：容器里加一层 supervisor，杀掉 `timeout` 的整个进程组。新测试在旧 argv 下失败，修复后通过。
    - eng:code-review Spec：
      - 同名冲突：已修；
      - 命令自己以 124 退出时被当成超时：已修，超过时限才算超时；
      - stub 每次全量 glob：改成只找两层以内；
      - pid 复用、共享 daemon：记为已知限制；
      - local 环境白名单：不修。理由是 ADR-0010 第 2 点（沙箱拿不到凭据）；交给 100 在 Harbor 里核对。
    - eng:code-review Standards：
      - Authority 偏离：见偏离第 1 条；
      - 改了两处：`report` 改名为 `clean_up`；`QueryResults` 改由 `register_sandbox_tool` 创建并返回；
      - 其余不修，理由分别是：
        - `image` 参数：spec 要求 DAB 的镜像可配置；
        - `skill_text` 暂时没有调用方：100 会接入；
        - backend 分支出现在三处：分别属于 CLI 构造、生命周期、工具说明三件事；
        - Dockerfile 里的 `WORKDIR`、`CMD` 和 argv 重复：其他镜像没有这两项，所以 argv 写全；
        - `sandbox.py` 约 560 行：只有一个概念，比仓库里已有的模块小；
        - pid 和 `dropped` 元组的类型：改动比收益大。
  - 已知限制：
    - 孤儿容器的清理靠 pid 判断进程是否还在：pid 被复用时会暂时清不掉；用共享或远程 Docker daemon，或 data-agent 本身跑在容器里时，`os.kill(pid, 0)` 不可靠。
    - rootless Docker 下 `--user` 会被映射成 subuid。
    - 对话的每一轮里 `query_N` 都从 1 开始编号，会覆盖上一轮的 `data/query_N.csv`。
    - local 白名单环境可能缺 Harbor 题目要用的变量（`DB_TYPE`、`VIRTUAL_ENV`、`DBT_PROFILES_DIR`），**100 要核对**。
- 2026-11-17（主会话）真实模型运行，在工单分支 868fd03 上，deepseek-flash：先 `make sandbox-image`（exit 0），再 `uv run data-agent run --type query --config full --sandbox docker --duckdb runs/97-real/shop.duckdb --workspace runs/97-real/work --events-dir runs/97-real/events-1 --task "每位客户的订单总额是多少？查出结果后，用 sandbox_exec 通过 data_files 传入结果，用 pandas 算出总额的均值和中位数。"`（库由 `tests/fixtures/shop.sql` 建）。
  - exit 0。工具调用：`list_tables`×1、`query_database`×5、`sandbox_exec`×1。`sandbox_exec` 的输入是 `data_files=["query_5"]`，命令用 pandas 读 `data/query_5.csv`，算均值和中位数。
  - 答案："均值：178.27083333333334；中位数：150.75"。直接用只读方式查 `customer_orders.total_amount` 得到 12 行，均值 178.27、中位数 150.75，两者一致。
  - 运行结束后 `docker ps -a --filter label=data-agent.sandbox.run` 为空。$0.005（price_table）。
- 2026-11-17：PR #107。
