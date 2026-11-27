# 实测：data-agent 在容器里挂 Docker socket 起沙箱（工单 152）

日期：2026-11-27。本机 colima（macOS arm64，VM 为 Ubuntu 24.04，2 CPU、2 GiB 内存，virtiofs，Docker server 29.2.1，client 29.10.1）。没调模型（$0）。探针直接调 data-agent 自己的代码：`DockerSandbox`、`execute_tool`（`sandbox_exec`）、`DuckDBGateway`/`PostgresGateway`、`DictionaryBuild`（值字典）、`DbtExecutor`（`dbt_execute`），不起 agent 会话。

复现：`scripts/spikes/sandbox_in_container/probe.sh`（一条命令。输出 tee 到 `runs/spike-152-output.txt`，每次运行有自己的标签 `da-spike-152-<tag>`，跑完只删这次起的容器、卷、网络和 `runs/da-spike-152-<tag>/`）。下面引的都是真实输出：最后一次运行（标签 `629a3f`，容器以调用者 uid `501:20` 运行），加上之前一次以 `--user 10001:10001` 运行的结果（同样全部通过，这次改成调用者 uid 是为了 Linux 上清理时删得掉，见 Codex 审查）。

## 结论

| # | 问题 | 答案 | 证据（探针输出） |
|---|---|---|---|
| 1 | 容器里挂 socket，`DockerSandbox` 能起沙箱、`sandbox_exec` 能执行 | **是** | `Q1: YES — container d0677da30330 started via the mounted socket; exit_code 0`。data-agent 容器不以 root 运行（`--user 501:20`，之前一次 `10001:10001`），加 `--group-add 991`（socket 的组）；沙箱 `Config.User` 和它相同 |
| 2 | 工作区、Skill 目录、运行目录能挂进沙箱 | **是，前提是用同名路径挂载** | 见下节 |
| 3 | 沙箱拿不到 data-agent 容器的环境变量和凭据，默认无网络 | **是** | 沙箱 `env` 里没有任何金丝雀变量（`ANTHROPIC_API_KEY`、`DATA_AGENT_WEB_TOKEN`、Postgres URL，值每次随机生成）；`Mounts` 只有工作区（rw）和 `/skills`（ro）两项；`NetworkMode=none`；沙箱里没有 `/var/run/docker.sock`；`/workspace/..` 是容器根，没有运行目录里的 `profiles/`。网络探测在沙箱里对 Postgres 和 `pypi.org` 都是 `Temporary failure in name resolution`，同一段探测在 data-agent 容器里两个都 `REACHABLE`（对照组） |
| 4 | `dbt_execute`、值字典、只读网关在 data-agent 容器里照常 | **是** | DuckDB 和 Postgres 网关都返回分组结果，`DELETE` 都是 `QueryBlocked: ... (read-only mode)`；值字典两边都是 `Coverage: 9 of 9 base tables sampled`，`search('gold')` 命中 `raw.customers.tier: 'Gold' (2), 'GOLD' (1), 'gold_tier' (1)`；`dbt_execute run`（scratch）两边都是 `exit_code: 0`、`PASS=3`、`target_schema: da_run_spike152duckdb` / `da_run_spike152postgres` |

## 问题 2：路径方案

`DockerSandbox.start` 用 `--mount type=bind,source=<路径>`（`src/data_agent/sandbox.py`，工作区和 `SKILLS_MOUNT` 两处）。经 socket 发出的 `docker run` 由宿主机上的守护进程解析源路径，所以这个路径必须在宿主机上存在，并且就是 data-agent 容器里看到的那个目录。

| 做法 | 结果 | 证据 |
|---|---|---|
| 同名路径挂载：宿主机目录 X 挂到容器里同一个 X（`-v X:X`），工作区、Skill 目录、`profiles/` 都在 X 下 | **可行** | agent 侧写的 `data/query_1.csv` 在沙箱里读得到；沙箱写的 `outputs/report.txt` 在 agent 侧读到 `from-sandbox`，并出现在回复的 `output_files:`；`/skills` 只读（`touch: cannot touch '/skills/x': Read-only file system`） |
| 容器内路径 ≠ 宿主机路径（宿主机 `runs/da-spike-152-<tag>/diff` 挂到容器 `/srv/da-spike-152-<tag>-diff`） | **不可行** | `SandboxError: ... invalid mount config for type "bind": bind source path does not exist: /srv/da-spike-152-629a3f-diff/workspace` |
| Skill 目录只在 data-agent 容器自己的文件系统里（镜像里自带，或 git 克隆到容器内路径；探针用容器的 tmpfs 模拟） | **不可行**（整个沙箱起不来，不只是 Skill 缺失） | `SandboxError: ... bind source path does not exist: /baked/skills` |
| 命名卷挂到容器 `/vol`，工作区放 `/vol/...` | **未实测**（VM 磁盘满，见"本次环境问题"），按机制推断不可行 | 写卷时 `[Errno 28] No space left on device`。容器里看到卷的真实位置是 `/docker/volumes/da-spike-152-vol/_data`（mountinfo），bind 的源 `/vol/...` 在宿主机上不存在，和上面"路径不同"是同一个原因 |
| 命名卷 + `type=volume,source=<卷>,volume-subpath=<子目录>`（Docker API ≥ 1.45） | **未实测**（同上），且要改 `src/` | `DockerSandbox.start` 现在只发 bind |

**推荐：同名路径挂载。** 理由：

- 现有 `DockerSandbox` 不用改就能用，四个问题都实测通过。
- 覆盖面完整：工作区、Skill 目录（`SKILLS_MOUNT`）、`dbt_execute` 的 `profiles/`（在运行目录里、工作区外）都在同一个挂载下，路径在 agent 侧和守护进程一侧一致。
- 命名卷方案要改 `DockerSandbox.start`（改成 `type=volume` + `volume-subpath`），这是 153/155 范围外的 `src/` 改动；它也没被实测。

落到部署上就是：`[server].runs_dir`、`[skills].dirs`（包括 154 从 git 克隆的目录）、说明文件、数据源文档，凡是沙箱要挂的目录，都放在一个宿主机目录下（例如 `/srv/data-agent`），并在 Compose 里写 `- /srv/data-agent:/srv/data-agent`。154 的"持久卷"也要用这个同名 bind，不能用命名卷，也不能克隆到镜像里的路径。只在 agent 侧读的文件（DuckDB 数据文件、模型配置）不受这个限制。

## 给 153 / 155 的注意事项

1. **挂 socket 等于宿主机 root 权限**：容器里的进程能用 socket 起任意容器、挂宿主机任意路径。`docs/deploy.md` 要写明（spec 已要求）。
2. **socket 权限**：data-agent 不必以 root 运行。探针用 `--user <非 root uid:gid> --group-add <socket 的 gid>` 跑通了（`10001:10001` 和 `501:20` 各一次）。gid 要在部署机上取：`stat -c %g /var/run/docker.sock`（colima VM 里是 991；Linux 宿主机上是 `docker` 组的 gid，各机器不同，**未确认**具体值）。Compose 里用 `group_add`。rootless Docker 的 socket 路径不同（`$XDG_RUNTIME_DIR/docker.sock`），**未实测**。
3. **沙箱的 uid 就是 data-agent 容器的 uid**（`--user os.getuid():os.getgid()`，实测 `10001:10001`、`501:20`）。Linux 宿主机上，同名挂载的宿主机目录要让这个 uid 可写（如 `chown 10001:10001 /srv/data-agent`）。colima/Docker Desktop 的 virtiofs 会映射所有权，所以本机测不出这个问题；Linux 上的行为**未确认**。
4. **孤儿清理在容器里会出错（要改 `src/`，归 153/155 判断）**：`OWNER_LABEL` 记的是 `os.getpid()`，容器里 data-agent 是 PID 1（实测沙箱标签 `data-agent.sandbox.owner: "1"`）。`remove_orphaned_containers` 用本进程 PID 命名空间里的 `alive()` 判断，而它列的是守护进程上所有带标签的容器。两种后果：(a) 同一守护进程上另一个 data-agent（宿主机上的开发进程，或另一个容器）看到 owner=1 会当作活着（宿主机 PID 1 永远在），这些沙箱永远不会被清；(b) 容器里的 data-agent 看到别处起的沙箱时，owner PID 在它的命名空间里多半不存在，会把别人正在用的沙箱删掉。探针刻意没调这个函数。建议：每台部署机只跑一个用 docker 后端的 data-agent，或者 owner 标签再加一个实例 id、只清自己实例的。
5. **`--pull never`**：沙箱镜像 `data-agent-sandbox` 必须事先在宿主机守护进程上 build 或 pull 好。155 的部署文档要有这一步（或者 Compose 里加一个只 build 的 service）。
6. **产品镜像要自带**：docker CLI（探针从 `docker:cli` 拷贝；client 29.10.1 对 server 29.2.1 能自动协商 API 版本）；dbt-core 和 dbt-duckdb、dbt-postgres（`pyproject.toml` 里它们是 dev 依赖，`dbt_execute` 在 agent 侧执行，要用到；探针用 uv.lock 的版本 1.10.23 / 1.10.0 / 1.10.2）。草稿见 `scripts/spikes/sandbox_in_container/Dockerfile`，**没有 build 成功过**（见下）。
7. **资源**：沙箱固定 `--cpus 2 --memory 4g`。2 CPU、2 GiB 的 VM 上能起（memory 只是上限）。只有 1 vCPU 的机器上 Docker 会拒绝 `--cpus 2`，这一点**未实测**；选机器时至少 2 vCPU。
8. **沙箱网络**：`--network none` 时连 DNS 都没有。`--network`（联网）时沙箱走默认 bridge，不在 Compose 的网络里，所以到不了 Compose 里的 Postgres。这是好事，但 153 的联网开关别指望沙箱能连 Compose 内的服务（**未实测**）。
9. **colima 与 Linux 宿主机的差别**：colima 只把 `$HOME` 以同一路径共享进 VM。同名挂载的目录在 macOS 上要放在 `$HOME` 下，不能放 `/tmp`。Linux 宿主机没有这层 VM，任意宿主机路径都行。virtiofs 上 uv 解压 wheel 时 `chmod` 失败（`failed to set permissions ... Operation not permitted`），探针把 uv 缓存放到 tmpfs 才绕开；这只影响在 Mac 上用 bind 目录装依赖，产品镜像不受影响。
10. Compose 草稿没写：探针用的是等价的 `docker run` 参数（`probe.sh` 末尾那段：`--group-add`、socket、同名 `-v`、`--user`），155 照着写 Compose 再实测。

## 本次环境问题（不影响结论，要人处理）

- colima VM 磁盘满（`df /` 显示 29.4G 已用 29.3G，可用 0）。第一次用 Dockerfile 构建时依赖装完了，但导出层失败（`no space left on device`）。所以探针不 build 镜像：用 `python:3.12-slim`，把代码、venv、docker CLI 放在同名挂载的宿主机目录里（这本身也是在验证同名挂载），Postgres 数据放 tmpfs。命名卷的两种情形因此没测成。
- 那次失败的构建留下了一个停止的中间容器 `jovial_dijkstra`（1.26 GB，`Cmd` 就是 Dockerfile 里的 `uv sync ... dbt-postgres==1.10.2`），可能还有它的无标签父镜像 `52cbbf487b61`。删除被自动审批拦了，没删；要人执行 `docker rm jovial_dijkstra`。另外本工单 pull 了 `docker:cli`（212 MB）。
