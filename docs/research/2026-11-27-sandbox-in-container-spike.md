# 实测：data-agent 在容器里挂 Docker socket 起沙箱（工单 152）

日期：2026-11-27。本机 colima（macOS arm64，VM 为 Ubuntu 24.04，2 CPU、`MemTotal` 2054299648 B，virtiofs（`colima status`），Docker server 29.2.1，client 29.10.1）。没调模型（$0）。探针直接调 data-agent 自己的代码：`DockerSandbox`、`execute_tool`（`sandbox_exec`）、`DuckDBGateway`/`PostgresGateway`、`DictionaryBuild`（值字典）、`DbtExecutor`（`dbt_execute`），不起 agent 会话。

复现：`scripts/spikes/sandbox_in_container/probe.sh`，一条命令。容器默认以调用者的 uid:gid 运行，`SPIKE_UID=10001 SPIKE_GID=10001` 可以换成别的。每次运行有自己的标签 `da-spike-152-<tag>`，输出连同清理记录 tee 到 `runs/da-spike-152-<tag>-output.txt`，跑完只删这次起的容器、卷、网络和 `runs/da-spike-152-<tag>/`。下面引的都是最后几次运行的真实输出：`ec1b4f` 以 `10001:10001` 运行，`d839c0` 和 `39bb63` 以调用者 `501:20` 运行（`39bb63` 补测了命名卷的 bind 情形）。每次都是 `all as expected`，清理后都是 `left: containers=[] volumes=[]`。

## 结论

| # | 问题 | 答案 | 证据（探针输出） |
|---|---|---|---|
| 1 | 容器里挂 socket，`DockerSandbox` 能起沙箱、`sandbox_exec` 能执行 | **是** | `Q1: YES — container 3c8613593cf6 started via the mounted socket; exit_code: 0`（`ec1b4f`；`d839c0` 同样）。data-agent 容器不以 root 运行（`--user 10001:10001` / `501:20`），加 `--group-add 991`（socket 的组）；沙箱 `Config.User` 和它相同 |
| 2 | 工作区、Skill 目录、运行目录能挂进沙箱 | **是，前提是用同名路径挂载** | 见下节 |
| 3 | 沙箱拿不到 data-agent 容器的环境变量和凭据，默认无网络 | **是** | 沙箱 `env` 里没有任何金丝雀变量（`ANTHROPIC_API_KEY`、`DATA_AGENT_WEB_TOKEN`、Postgres URL，值每次随机生成）；`Mounts` 只有工作区（rw）和 `/skills`（ro）两项；`NetworkMode=none`；沙箱里没有 `/var/run/docker.sock`；`/workspace/..` 是容器根，没有运行目录里的 `profiles/`。网络探测在沙箱里对 Postgres 和 `pypi.org` 都是 `Temporary failure in name resolution`，同一段探测在 data-agent 容器里两个都 `REACHABLE`（对照组） |
| 4 | `dbt_execute`、值字典、只读网关在 data-agent 容器里照常 | **是** | DuckDB 和 Postgres 网关都返回分组结果，`DELETE` 都是 `QueryBlocked: ... (read-only mode)`；值字典两边都是 `Coverage: 9 of 9 base tables sampled`，`search('gold')` 命中 `raw.customers.tier: 'Gold' (2), 'GOLD' (1), 'gold_tier' (1)`；`dbt_execute run`（scratch）两边都是 `exit_code: 0`、`PASS=3`、`target_schema: da_run_spike152duckdb` / `da_run_spike152postgres` |

## 问题 2：路径方案

`DockerSandbox.start` 用 `--mount type=bind,source=<路径>`（`src/data_agent/sandbox.py`，工作区和 `SKILLS_MOUNT` 两处）。经 socket 发出的 `docker run` 由宿主机上的守护进程解析源路径，所以这个路径必须在宿主机上存在，并且就是 data-agent 容器里看到的那个目录。

| 做法 | 结果 | 证据 |
|---|---|---|
| 同名路径挂载：宿主机目录 X 挂到容器里同一个 X（`-v X:X`），工作区和 Skill 目录在 X 下 | **可行** | agent 侧写的 `data/query_1.csv` 在沙箱里读得到；沙箱写的 `outputs/report.txt` 在 agent 侧读到 `from-sandbox`，并出现在回复的 `output_files:`；`/skills` 只读（`touch: cannot touch '/skills/x': Read-only file system`） |
| 容器内路径 ≠ 宿主机路径（宿主机 `runs/da-spike-152-<tag>/diff` 挂到容器 `/srv/da-spike-152-<tag>-diff`） | **不可行** | `SandboxError: ... invalid mount config for type "bind": bind source path does not exist: /srv/da-spike-152-ec1b4f-diff/workspace` |
| Skill 目录只在 data-agent 容器自己的文件系统里（镜像里自带，或 git 克隆到容器内路径；探针用容器的 tmpfs 模拟） | **不可行**（整个沙箱起不来，不只是 Skill 缺失）。**产品现在就是这样**，见下方"155 的阻塞点" | `SandboxError: ... bind source path does not exist: /baked/skills` |
| 命名卷挂到容器 `/vol`，工作区就是卷里的目录 | **不可行** | VM 磁盘满，写不进卷（`[Errno 28] No space left on device`），所以用卷的根 `/vol` 当工作区：`bind source path does not exist: /vol`（`39bb63`）。容器里看到卷的真实位置是 `/docker/volumes/da-spike-152-ec1b4f-vol/_data`（mountinfo）。和上面"路径不同"是同一个原因 |
| 命名卷 + `type=volume,source=<卷>,volume-subpath=<子目录>`（Docker API ≥ 1.45） | **未实测**（同上），且要改 `src/` | `DockerSandbox.start` 现在只发 bind |

**推荐：同名路径挂载。** 理由：

- 现有 `DockerSandbox` 不用改就能用，四个问题都实测通过。
- 沙箱挂的只有两样：工作区和 Skill 目录（`SKILLS_MOUNT`）。两样都放在同名挂载下，路径在 agent 侧和守护进程一侧就一致。运行目录里的其他东西（`dbt_execute` 的 `profiles/`、事件记录）只有 agent 侧读写，只要能持久保存就行，放在同一个挂载下最省事。
- 命名卷方案要改 `DockerSandbox.start`（改成 `type=volume` + `volume-subpath`），这是 153/155 范围外的 `src/` 改动；它也没被实测。

**155 的阻塞点：产品的 Skill 目录在包里。** `DockerSandbox` 拿到的是 `config.skills_dir`（`src/data_agent/cli.py:157`），来自 `run_config.skills_path()`：装好的 wheel 里是包内的 `data_agent/skills`（`pyproject.toml` 用 force-include 打进包），checkout 里是仓库根的 `skills/`（`src/data_agent/run_config.py:18-22`）。产品镜像里这个目录在镜像自己的文件系统里，宿主机上没有，所以按现在的代码，cloud 模式第一次调 `sandbox_exec` 就会因为 `bind source path does not exist` 失败。要么改 `src/`（Skill 目录可配置，或者每次运行把 Skill 复制到运行目录里），要么在部署层绕开：把包装到一个同名挂载的宿主机目录里（例如 venv 放在 `/srv/data-agent/venv`），镜像就不再自包含。哪种做法、归哪张工单，需要回对齐会话定；product-config 的 `[skills].dirs` 也许已经覆盖了这一点，**未确认**。

落到部署上就是：`[server].runs_dir`、`[skills].dirs`（包括 154 从 git 克隆的目录）、说明文件、数据源文档，凡是沙箱要挂的目录，都放在一个宿主机目录下（例如 `/srv/data-agent`），并在 Compose 里写 `- /srv/data-agent:/srv/data-agent`。154 的"持久卷"也要用这个同名 bind，不能用命名卷，也不能克隆到镜像里的路径。只在 agent 侧读的文件（DuckDB 数据文件、模型配置）不受这个限制。

## 给 153 / 155 的注意事项

1. **挂 socket 等于宿主机 root 权限**：容器里的进程能用 socket 起任意容器、挂宿主机任意路径。`docs/deploy.md` 要写明（spec 已要求）。
2. **socket 权限**：data-agent 不必以 root 运行。探针用 `--user <非 root uid:gid> --group-add <socket 的 gid>` 跑通了（`10001:10001` 和 `501:20` 各一次）。gid 要在部署机上取：`stat -c %g /var/run/docker.sock`（colima VM 里是 991；Linux 宿主机上是 `docker` 组的 gid，各机器不同，**未确认**具体值）。Compose 里用 `group_add`。rootless Docker 的 socket 路径不同（`$XDG_RUNTIME_DIR/docker.sock`），**未实测**。
3. **沙箱的 uid 就是 data-agent 容器的 uid**（`--user os.getuid():os.getgid()`，实测 `10001:10001`、`501:20`）。Linux 宿主机上，同名挂载的宿主机目录要让这个 uid 可写（如 `chown 10001:10001 /srv/data-agent`）。colima/Docker Desktop 的 virtiofs 会映射所有权，所以本机测不出这个问题；Linux 上的行为**未确认**。
4. **孤儿清理在容器里会出错（要改 `src/`，归 153/155 判断）**：`OWNER_LABEL` 记的是 `os.getpid()`，也就是起沙箱的那个进程在容器 PID 命名空间里的 pid。探针里是 1（`data-agent.sandbox.owner: "1"`），因为 probe.py 就是容器的主进程；产品里是 MCP 服务器子进程的 pid，同样是容器命名空间里的小数字。`remove_orphaned_containers` 用本进程 PID 命名空间里的 `alive()` 判断，而它列的是守护进程上所有带标签的容器。两种后果：(a) 同一守护进程上另一个 data-agent（宿主机上的开发进程，或另一个容器）看到 owner=1 会当作活着（宿主机 PID 1 永远在），这些沙箱永远不会被清；(b) 容器里的 data-agent 看到别处起的沙箱时，owner PID 在它的命名空间里多半不存在，会把别人正在用的沙箱删掉。探针刻意没调这个函数。还有一种情形：data-agent 容器重启后，小 pid 会被复用，被杀掉的运行留下的沙箱可能看起来还活着，于是一直不被清理。建议：每台部署机只跑一个用 docker 后端的 data-agent，或者 owner 标签再加一个实例 id（加上容器启动时间）、只清自己实例的。
5. **`--pull never`**：沙箱镜像 `data-agent-sandbox` 必须事先在宿主机守护进程上 build 或 pull 好。155 的部署文档要有这一步（或者 Compose 里加一个只 build 的 service）。
6. **产品镜像要自带**：docker CLI（探针从 `docker:cli` 拷贝；client 29.10.1 对 server 29.2.1 能自动协商 API 版本）；dbt-core 和 dbt-duckdb、dbt-postgres（`pyproject.toml` 里它们是 dev 依赖，`dbt_execute` 在 agent 侧执行，要用到；探针用 uv.lock 的版本 1.10.23 / 1.10.0 / 1.10.2）。草稿见 `scripts/spikes/sandbox_in_container/Dockerfile`，**没有 build 成功过**（见下）。
7. **资源**：沙箱固定 `--cpus 2 --memory 4g`。2 CPU、约 2 GB 内存的 VM 上能起（memory 只是上限）。只有 1 vCPU 的机器上 Docker 会拒绝 `--cpus 2`，这一点**未实测**；选机器时至少 2 vCPU。
8. **沙箱网络**：`--network none` 时连 DNS 都没有。`--network`（联网）时沙箱走默认 bridge，不在 Compose 的网络里，所以到不了 Compose 里的 Postgres。这是好事，但 153 的联网开关别指望沙箱能连 Compose 内的服务（**未实测**）。
9. **colima 与 Linux 宿主机的差别**：colima 只把 `$HOME` 以同一路径共享进 VM。同名挂载的目录在 macOS 上要放在 `$HOME` 下（探针用的是 `$HOME` 下的 `runs/`；colima 默认挂载里没有 `/tmp`，但放 `/tmp` 会怎样**未实测**）。Linux 宿主机没有这层 VM，任意宿主机路径都行。virtiofs 上 uv 解压 wheel 时 `chmod` 失败（`failed to set permissions ... Operation not permitted`），探针把 uv 缓存放到 tmpfs 才绕开；这只影响在 Mac 上用 bind 目录装依赖，产品镜像不受影响。
10. Compose 草稿没写：探针用的是等价的 `docker run` 参数（`probe.sh` 末尾那段：`--group-add`、socket、同名 `-v`、`--user`），155 照着写 Compose 再实测。

## 本次环境问题（不影响结论，要人处理）

- colima VM 磁盘满（`df /` 显示 29.4G 已用 29.3G，可用 0）。第一次用 Dockerfile 构建时依赖装完了，但导出层失败（`no space left on device`）。所以探针不 build 镜像：用 `python:3.12-slim`，把代码、venv、docker CLI 放在同名挂载的宿主机目录里（这本身也是在验证同名挂载），Postgres 数据放 tmpfs。命名卷的两种情形因此没测成。
- 那次失败的构建留下了一个停止的中间容器 `jovial_dijkstra`（1.26 GB，`Cmd` 就是 Dockerfile 里的 `uv sync ... dbt-postgres==1.10.2`），还有 4 个无标签的中间镜像（`docker images -a --filter dangling=true` 里创建于 2026-11-27 07:28:02–07:28:05 的 `279e99c8cc08`、`1ce9b35fba76`、`16b32c86f809`、`52cbbf487b61`，各约 313 MB，大部分层和 `python:3.12-slim` 共用）。删除被自动审批拦了，没删。要人执行 `docker rm jovial_dijkstra && docker rmi 52cbbf487b61 16b32c86f809 1ce9b35fba76 279e99c8cc08`。本工单还 pull 了 `docker:cli`（212 MB）；探针还用到本机已有的 `alpine`、`postgres:16-alpine`、`python:3.12-slim`、`ghcr.io/astral-sh/uv:0.10.8`、`data-agent-sandbox`。探针只在 `data-agent-sandbox` 缺失时 build 它（本次没有 build），这个镜像不带工单前缀，也不会被清理，因为它就是产品要用的沙箱镜像。
