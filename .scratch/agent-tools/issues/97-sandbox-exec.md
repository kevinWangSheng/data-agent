# 97 — `sandbox_exec`：在隔离环境里执行任意命令

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-18（退回第 2 轮）
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

- Attempts: 3（退回第 2 轮）
- Review rounds: 4
- Model spend: $0.005 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: merged (PR #107 → 1a4ad9f)
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
- 2026-11-17：PR #107。CI 两个 check 都 pass。主会话把 origin/main（98，8b1bc19）merge 进本分支，解决 `agent.py`、`cli.py` 的冲突（`sandbox`、`network` 两个参数都保留，docstring 合并），`make check` exit 0（pytest 2493 passed、1 skipped；web 295 passed），提交 d3cd5a5 并 push。**接着修之前先 `git pull`**。
- 2026-11-17 **退回（第 1 轮，Codex GitHub app P2，审 868fd03，主会话对照代码核实属实）：超时后脱离进程组的子进程杀不掉。** `sandbox.py:52-56` 的 `SUPERVISOR` 在超时后只做 `kill -KILL -- -$t`，杀的是 `timeout` 那个进程组。命令里用 `setsid ... &` 或 Python 的 `Popen(..., start_new_session=True)` 起的子进程，在另一个会话和进程组里，不受影响（Codex 本地执行确认：supervisor 以 124 退出，子进程还活着）。docker 后端的容器在同一次运行里复用，这个进程会在之后的调用里继续占资源、改 workspace 文件，工具却报告"已杀掉"。
  - 要做的：超时后让这个容器里不再有本次命令留下的进程，不管它们属于哪个进程组。可选做法（由你定）：
    - 超时后在容器里以同一 uid 执行 `kill -KILL -1`（杀掉该 uid 能发信号的所有进程，PID 1 和自己除外）。`sandbox_exec` 没有 readOnlyHint，同一轮里的调用是串行的，不会误杀并发调用；装过的包也能保留。
    - 或者超时后删掉容器，下次调用重建（会丢掉装过的包，工具说明要写明）。
  - local 后端（Harbor）有同样的问题，一并处理，或者写明为什么不处理。
  - 测试：补一个测试，用 `setsid sleep 600 &` 这类命令，在旧代码下失败、修复后通过（单元测试或真实 Docker 的集成测试都可以，集成测试的 workspace 放在 `$HOME` 下）。
- 2026-11-18（工单会话，退回第 1 轮，主会话抄录）：提交 `d46c3cb`、`825b6c2`，已 push。
  - 做法：用一个只依赖标准库的 Python supervisor（新文件 `src/data_agent/sandbox_supervisor.py`）代替 bash 的 `timeout` 包装。docker 后端在容器里用 `python3 -I -c` 执行它，local 后端用 `sys.executable`。
    - Linux 上它把自己设成 child subreaper（`prctl(PR_SET_CHILD_SUBREAPER)`），所以 `setsid`、`start_new_session` 起的进程在父进程退出后会挂回它名下。
    - 到时限时，它对 `/proc` 里自己的所有子孙进程先发 TERM，过 `KILL_GRACE_S` 再发 KILL，一直发到一个不剩，然后以 124 退出。
    - `-I` 防止 workspace 里的文件（比如 `signal.py`）顶替 supervisor 自己的 import。
  - 退回里给的两个做法为什么不用：
    - `kill -KILL -1`：会杀掉之前的调用有意留着的后台进程。容器加了 `--init`，`sleep infinity` 和命令是同一个 uid，会被一起杀掉，容器随之停止。在 Harbor 里 local 后端和 agent 是同一个用户，会把 agent 自己也杀掉。
    - 超时后删容器：会丢掉装过的包。
  - local 后端：Linux（Harbor）上用同一个 supervisor 修好。macOS 没有 `/proc` 和 subreaper，退回到只杀命令的进程组。macOS 只用于开发，记为已知限制。
  - 行为变化：命令已按时退出、但后台进程还占着 stdout 时，`run_process` 在 `KILL_GRACE_S` 内返回命令的退出码，和 `docker exec` 的行为一致（已实测）。以前会一直等到时限，报超时，再杀掉那个后台进程。新增了一个测试覆盖这一点。
  - 真实 Docker 运行（`execute_tool`，colima）：
    - 第 1 次调用 `setsid sleep 702 &`：exit 0。
    - 第 2 次调用起了 `setsid sleep 600` 和 `start_new_session` 的 `sleep 601`，时限 2 s：报超时。
    - 第 3 次调用看 `/proc`：只剩 `docker-init`、`sleep infinity` 和 `sleep 702`。
  - 新测试：
    - 集成测试 `test_processes_that_left_the_commands_process_group_are_killed_at_the_timeout`：旧代码下 `sleep 600`、`sleep 601` 还活着。
    - local 后端的测试只在 Linux 上跑：CI（ubuntu）会跑，macOS 上跳过。工单会话用 `runs/linux-pytest.sh` 在 `python:3.12-slim` 容器里跑过，旧代码下以 `living(114)` 失败。
    - 另加几条行为测试：之前调用留下的后台进程能撑过之后的超时；退出码原样传出；被信号杀掉时报 128+n；忽略 TERM 的命令会挨 KILL；按时退出的命令不等后台进程。
  - 检查：
    - `make check` exit 0：pytest 2495 passed、2 skipped，web 295 passed。
    - 集成测试 12 passed。
    - Linux 容器里 `tests/test_sandbox.py`、`tests/test_sandbox_tool.py` 39 passed，没有跳过。
  - 审查：
    - da-review 没有意见。
    - eng:code-review 修了这几条：工具说明的措辞；等输出用一个共同截止时间；`TIMEOUT_EXIT` 只在一处定义；改名并拆开两段循环；supervisor 改用 `start_new_session`，不再需要 Python 3.11。
    - eng:code-review 不修的几条：
      - `/proc` 解析在测试里重复：每个测试单独可读。
      - `CLIENT_MARGIN_S` 一个值管两处：注释已写明。
      - supervisor 自己卡住时，兜底只能杀到 supervisor：记进了 docstring。
  - 已知限制（新增）：
    - macOS 的 local 后端只杀进程组。
    - local 后端里，按时返回但后台进程还占着输出时，会留下两个读线程，直到那个进程结束。
    - **给 100**：DAB 的 `python-data:3.12` 镜像里 Python 的版本未确认（refs 里没有 DAB 的 Dockerfile），换 DAB 沙箱时要核对 supervisor 能不能在里面跑。
  - 主会话判断：改动只涉及超时处理，之前那次真实模型运行仍然有效，不重跑。
- 2026-11-18 PR #107：CI 两个 check 都 pass（825b6c2）。
- 2026-11-18 **退回（第 2 轮，也是这个 PR 的最后一轮；Codex GitHub app P2，审 825b6c2，主会话对照代码核实属实）：local 后端里，读线程比 `run_process` 活得久，管道 fd 却被提前关掉并复用。**
  - 问题：`Capture` 只记 fd 号（`sandbox.py:100`）。`run_process` 返回前关掉 `proc.stdout`（`:159`），这时读线程可能还阻塞在 `os.read(fd)` 上（`:106`）。Linux 上关 fd 不会唤醒阻塞的读，fd 号会被下一次 `Popen` 的管道复用。后台进程再写一次，旧线程就醒过来，读走新命令的输出。
  - Codex 的复现：先跑 `(sleep 6; echo late) & echo first`，下一条命令打印 100 行，只收到 50 行。这是上一轮新加的"按时返回、不等后台进程"带出来的。
  - 要做的：读线程还活着时，不能让它的 fd 被关掉或复用。做法由你定，例如：
    - 管道对象交给读线程，读到 EOF 时由读线程自己关；`run_process` 不关还在读的管道，接受 fd 一直占到后台进程结束；
    - 或者在交还 fd 之前，用确定的办法先停掉读线程。
  - 测试：照 Codex 的场景补一个测试，旧代码下失败、修复后通过。只在 Linux 上能复现的话，照上一轮在 Linux 容器里跑一遍，写明结果。
  - 第二轮修完还不过，按 Stop conditions 交回人。
- 2026-11-18（工单会话，退回第 2 轮，主会话抄录）：提交 `190424e`、`8a422b0`、`9b9e8ab`，已 push。
  - 根因：`Capture` 只把 fd 号交给读线程。`run_process` 返回后，`Popen` 和它的管道对象被回收，fd 随之关闭，读线程却还阻塞在上面。下一次 `Popen` 拿到同一个 fd 号，旧线程醒来就读走新命令的输出。
  - 修法（`190424e`）：管道对象交给读线程，读到 EOF 时由它自己关；`run_process` 不再关任何管道。fd 会一直占到后台进程结束，这在第 1 轮记过的已知限制之内。
  - 测试 `test_a_reader_left_on_a_background_process_takes_no_output_of_the_next_command`，用的是 Codex 的场景：
    - Linux 容器（`python:3.12-slim`，`runs/linux-pytest.sh`）：旧代码下失败，缺 35 行（`runs/97-r2-linux-red.txt`）；修复后 `tests/test_sandbox.py`、`tests/test_sandbox_tool.py` 41 passed，没有跳过。
    - macOS 上关 fd 会结束阻塞的读，所以旧代码也能通过，docstring 写明了；回归由 CI（ubuntu）兜住。
  - da-review 第 1 轮一条，已修（`8a422b0`）：命令自己在时限前以 124 退出、后台进程又占着 stdout 时，会被误判成超时，因为耗时是读完输出才量的。现在 `run_process` 记录进程本身的运行时长 `Execution.ran_seconds`，`timeout_past_limit` 按它判断。新测试在旧代码下失败。
  - da-review 第 2 轮：没有意见。
  - eng:code-review：
    - 已修（`9b9e8ab`）：变量改名为 `ran_seconds`；测试的 docstring 写明只在 Linux 上失败。
    - 不修：docker 后端上，进程结束后，只要后台进程还占着输出流，`docker exec` 还会再停留约 2 s（审查者实测 2.08 s）。这期间命令自己的 124 仍会被判成超时。窗口原来是 5 s。要彻底解决，得给 supervisor 另开一条通道（例如在 stderr 里加标记），会带来剥离和 `KEEP_BYTES` 截断的边界情况；而触发它要同时满足三个条件：命令自己以 124 退出、留下往输出写的后台进程、结束时离时限不到 2 s。范围太窄，记为已知限制。2 s 的来源没有对照 moby 源码确认（未确认）。
  - `make check` exit 0：pytest 2497 passed、2 skipped，web 295 passed。集成测试 12 passed。
  - 已知限制（新增）：docker 后端上，命令自己以 124 退出、后台进程占着输出、离时限不到约 2 s 时，会被报成超时。
- 2026-11-18 PR #107：CI 两个 check 都 pass（9b9e8ab）；Codex GitHub app（审 9b9e8ab）："Didn't find any major issues"。squash 合入 main，1a4ad9f。
