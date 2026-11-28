# 调研：容器里起沙箱时的 Skill 挂载与孤儿沙箱清理

日期：2026-11-27。起因：工单 152 的实测（`docs/research/2026-11-27-sandbox-in-container-spike.md`，"155 的阻塞点"和注意事项第 4 条）。本文只调研，不改代码，不定方案；方案归对齐会话。上游代码都钉了提交（见"来源"）。本机实测只有一项：`docker cp` 的属主与可写性（colima，Docker server 29.2.1，client 29.10.1，`alpine`，`--rm` 自删的临时容器）。

## 结论摘要

- **问题 1（Skill 目录挂不进沙箱）是我们的实现选择造成的，不是 DooD 本身做不到。** 事实：bind 的源路径由守护进程在它自己的主机上解析。我们做了两个选择：内置 Skill 放在包里（所以在镜像文件系统里），并且用 bind 交给沙箱。两者一起，cloud 下就起不来。147 打算改成"按原路径挂"，这同样要求源路径在宿主机上，所以问题不会因此消失。只有"内容只存在于镜像文件系统里，又要 bind 给另一个容器"这件事从原理上做不到。可行的路有五条：把 Skill 落到同名宿主目录、做宿主路径换算、用命名卷、用 tar 流复制进沙箱（`docker cp` / `PUT /containers/{id}/archive`）、把 Skill 烤进沙箱镜像。上游的做法：Harbor、E2B、Daytona 复制文件进沙箱；OpenHands（1.0.0）和 SignalPilot 用"宿主路径环境变量 + bind"；OpenHands 还把自带 Skill 烤进运行时镜像。从证据看，**复制进沙箱**最符合"镜像自包含 + 路径和 agent 侧一致"。只要 Skill 必须在运行中实时可见，或者 Skill 很大，结论就会变（见问题 1 C）。
- **问题 2（孤儿清理误判）也是实现选择造成的。** 用 pid 判断所有者，只在"清理者与所有者在同一 PID 命名空间、同一次启动"时成立。我们的 `labelled(OWNER_LABEL)` 却列出整个守护进程上的容器，范围对不上。可行的修法：按部署实例与启动代次加标签、只清自己的；用租约或运行记录判断所有权（SignalPilot、DockerSpawner 的思路）；用 reaper 旁车按连接存活判断（Testcontainers Ryuk）；让容器自带寿命上限作兜底。光靠"每台机器只跑一个实例"，修不了重启后 pid 被复用的问题。

## 问题 1：Skill 目录挂不进沙箱

### A. 根因

查实的事实：

- `DockerSandbox.start` 把 Skill 目录用 bind 挂到 `/skills`（`src/data_agent/sandbox.py:278`），工作区也用 bind（`:277`）。
- 传进去的 Skill 目录是 `config.skills_dir`（`src/data_agent/cli.py:154`、`:157`），来自 `skills_path()`：装好的 wheel 里用包内的 `data_agent/skills`，checkout 里用仓库根的 `skills/`（`src/data_agent/run_config.py:18-22`）。wheel 靠 force-include 把 `skills`、`skills-frozen` 打进包（`pyproject.toml:69-72`）。
- Docker 官方文档："Bind mounts are created to the Docker daemon host, not the client"；`--mount` 的源路径在守护进程上不存在就报错。152 实测的报错是 `bind source path does not exist: /baked/skills`，整个沙箱起不来。
- 142 推荐、147 计划实现的"方案 A"是：每个 Skill 目录各挂一次，`source` 用真实路径，`target` 用 SDK 填进 `${CLAUDE_SKILL_DIR}` 的路径（`docs/research/2026-11-27-skill-tool-spike.md:98`、`:137`；`.scratch/product-config/issues/147-skills-mcp-plugins.md:21`、`:44`）。147 不改 Skill 文本，所以 Skill 里的脚本路径必须和 agent 侧的路径一致。
- 评测入口也用这个挂载：`evals/dab.py:314` 用 `SKILLS_MOUNT` 生成 Skill 文本。

推断：

- 问题出在"镜像里的文件"和"bind"这一组合上。147 落地以后，`source` 还是 agent 侧的真实路径，cloud 下内置 Skill 仍然只在镜像里，所以照样失败。Skill 分两种来源，结局不同：
  - **内置 Skill**（包里、镜像里）：只要用 bind 就一定失败，同名挂载救不了，因为宿主机上没有这个目录。
  - **使用者的 `[skills].dirs` 和 154 从 git 克隆的目录**：放在同名宿主目录下，bind 就能工作。147 每次运行用符号链接生成的临时插件目录（`/tmp` 下）也不碍事，因为 `source` 取的是解析后的真实路径，`target` 只需在沙箱里存在，挂载时会自动创建。但如果克隆到容器自己的文件系统或命名卷里，就会和内置 Skill 一样失败。
- DooD 加 bind 本身是能用的：152 实测同名挂载可行，OpenHands 和 SignalPilot 的生产部署也在用（见 B）。从原理上做不到的，只有"bind 一个只存在于另一个容器 rootfs 里的路径"。

### B. 上游怎么做

| 项目 | 做法 | 证据 |
|---|---|---|
| OpenHands 1.0.0（旧版 Python 运行时） | 在 Compose 里把工作区的**宿主路径**作为环境变量 `WORKSPACE_MOUNT_PATH` 传给应用容器；应用容器自己把它挂在别的路径（`/opt/workspace_base`），起运行时容器时用宿主路径做 bind。自带的 Skill 在构建运行时镜像时复制进镜像（`runtime_build.py` 复制 `skills/` 到构建上下文，`Dockerfile.j2` 里 `COPY ./code/skills`）。往运行时里放单个文件走运行时内的 HTTP 上传（zip，`copy_to`） | `docker-compose.yml:12`、`:17-20`；`runtime_build.py:285-286`；`Dockerfile.j2:339-341`；`action_execution_client.py:186-215` |
| OpenHands 现行（software-agent-sdk 的 `DockerWorkspace`） | `volumes=["/host/dir:/workspace"]`，原样传 `-v`，容器 `--rm`；agent-server 有 `file_upload` 接口。它在容器里跑时怎么处理宿主路径，文档里没写，**未确认** | `workspace.py:97-99`、`:136-140`、`:220-222`、`:240-247`；`remote/base.py:188` |
| SignalPilot（评测沙箱） | 网关容器挂 socket 后直接调 Engine API。本地仓库用 `SP_EVAL_PROJECTS_HOST_DIR` 换算成宿主路径再 bind（文档原文："Docker binds resolve on the host, not inside the gateway container"）；git 仓库则在沙箱里克隆到 tmpfs，不做 bind | `runner.py:582-591`；`backends.py:110-136`；`docker-compose.yml:144-147`、`:165-169`；`docs/docs/evals/deploying.mdx:134-138` |
| Harbor（docker 环境） | `upload_dir` 用 `docker compose cp <dir>/. main:<target>`，失败时退回 tar 流解包；文件头注释写明 tar 能保留权限位、符号链接和空目录。Agent 的 `skills_dir` 是"环境里的路径"，也就是说 Skill 先传进环境 | `docker_unix.py:122-160`；`tar_transfer.py:1-18`；`agents/base.py:145` |
| E2B / Daytona（只看了文档） | 靠文件上传 API：E2B 用 `files.write` / `write_files`（文档示例不递归子目录），Daytona 用 `fs.upload_file` / `upload_files` | 文档链接见"来源" |
| Jenkins docker-workflow | agent 自己在容器里时（从 `/proc/self/cgroup` 或 `/proc/1/mountinfo` 找出容器 id），对工作区所在的卷加 `--volumes-from <自己>`；找不到对应卷时退回同名 bind | `WithContainerStep.java:168-196`；`DockerClient.java:136`、`:352-366` |

没查：Dify sandbox、GitHub Actions runner（含 ARC 的 dind 共享卷），**未确认**。

### C. 可选方案

共同约束：`local` 模式保持 bind 不变，改 Skill 立即生效，`skills-frozen` 照常可用。`${CLAUDE_SKILL_DIR}` 在沙箱里必须指向 agent 侧的同一路径（147 不改 Skill 文本）。Skill 在沙箱里只读。

| # | 做法 | 要改的代码 | 内置 Skill | `[skills].dirs`、git 克隆 | 代价与风险 | 上游 |
|---|---|---|---|---|---|---|
| 1 | **同名宿主目录，启动时落盘**：镜像照旧自带 Skill；cloud 模式启动时把包内 Skill 复制到同名挂载的宿主目录（如 `/srv/data-agent/skills/<版本>`），之后 `skills_dir` 指向那里，bind 不变 | 内置 Skill 目录要能被覆盖（`run_config.skills_path` 或 143/147 的配置），外加一个启动步骤 | 行 | 行（放在同一目录下） | 镜像仍然自包含，但部署必须有同名 bind（152 本来就要求工作区这样做）。升级时要按版本分目录，否则旧文件残留。`skill_version` 哈希的是落盘后的副本，**未确认**是否和包内一致 | 152 的推荐；OpenHands、SignalPilot 用宿主目录 |
| 2 | **宿主路径换算**：配置里给"容器内前缀 → 宿主前缀"（如 `DATA_AGENT_HOST_ROOT`），`DockerSandbox` 把 `source` 换成宿主路径，`target` 不变 | `DockerSandbox.start` 的挂载参数，加一个部署层设置 | 仍需先落到宿主目录（同方案 1） | 行 | 比同名挂载灵活，但内置 Skill 的问题没解决。多一个必须配对的变量，配错就起不来 | OpenHands `WORKSPACE_MOUNT_PATH`，SignalPilot `SP_EVAL_PROJECTS_HOST_DIR` |
| 3 | **命名卷**：data-agent 容器把卷挂在 P，启动时把 Skill 写进卷；沙箱用 `type=volume,source=<卷名>,target=<同一路径>,readonly`，需要时加 `volume-subpath` | `DockerSandbox.start` 改用 `type=volume`，加卷名配置 | 行（靠启动时复制） | 行（克隆进卷） | 不要依赖"空卷挂上时自动复制镜像内容"：只在卷为空时发生一次，升级后内容会过期。Compose 会给卷名加项目前缀，要写 `name:`。`volume-subpath` 要求 API ≥ 1.45，且子目录必须先存在。152 因磁盘满**没测成**。只在 cloud 下用，local 不变 | Docker 文档；DockerSpawner 用命名卷放用户数据 |
| 4 | **复制进沙箱**：`docker run -d` 之后、第一条命令之前，把每个 Skill 目录打成 tar 流，解到沙箱里 agent 侧的同一路径（`docker cp - <容器>:/`，或 Engine API `PUT /containers/{id}/archive?path=/`） | `DockerSandbox.start` 多一步复制；只读要靠属主和权限位（见下） | 行 | 行 | 不依赖宿主路径，镜像自包含，路径天然一致。代价见表下说明 | Harbor `upload_dir`；OpenHands `copy_to`；E2B / Daytona 上传 API |
| 5 | **烤进沙箱镜像**：构建沙箱镜像时把内置 Skill 复制到 agent 侧的同一路径 | 沙箱镜像的 Dockerfile，并保证两个镜像版本一致 | 行（路径要和产品镜像一致） | 不行（动态目录） | 只解决内置 Skill。两个镜像必须同版本构建；`--pull never` 下版本错配不会报错 | OpenHands 运行时镜像 `COPY ./code/skills` |
| 6 | **把 venv 装到同名宿主目录**（部署层绕开） | 无（只改 Dockerfile、Compose） | 行 | 行 | 镜像不再自包含，升级要重装宿主目录 | 152 提过 |
| 7 | **`--volumes-from <data-agent 容器>`** | `DockerSandbox.start` | 不行（镜像层不是卷） | 行 | 会把 data-agent 的全部挂载带进沙箱，包括 socket 和放凭据的卷，违反 ADR-0010。**不可取** | Jenkins |

方案 4 的代价和本机实测：

- 每次运行都要复制。现在 `skills/` 是 168K、10 个文件，量很小；`[skills].dirs` 如果很大，**未测**。
- 复制得到的是快照，运行中改了 Skill 也看不到。这和 ADR-0012 S4"Skill 在线程开始时固定"一致。
- 目标的父目录必须存在。本机实测：`docker cp <dir> <容器>:/opt/da/skills/sk` 报 `Could not find the file /opt/da/skills`；把完整路径打进 tar、解到 `/` 就成功了（守护进程以 root 写入）。在 macOS 上打包时要关掉扩展属性（`--no-xattrs --no-mac-metadata`），否则报 `lsetxattr ... com.apple.provenance: operation not supported`。
- **只读不能靠默认属主保证。** Docker 文档说复制进容器的文件属 root，但本机两种方式实测都是 `501:0`（tar 头里的 uid）。沙箱 `--user` 是 `10001` 时，覆盖和新建都 `Permission denied`，脚本照样能执行（权限位保留）。但在 cloud 下，沙箱的 uid 就是 data-agent 的 uid（`sandbox.py:274`），tar 头里也是这个 uid，文件就**可写**了。所以要在 tar 头里显式把 uid/gid 设为 0，或者去掉写权限位。这一点在 Linux 宿主机上**未测**。
- Engine API 对只读 rootfs 返回 403，也不能复制进 tmpfs 或使用者自己挂的目录（Docker 文档）。现在的沙箱不是只读 rootfs（`sandbox.py:268-280`）。

证据倾向与会改变结论的条件：

- 方案 4 是唯一同时满足"镜像自包含、两种 Skill 来源都行、路径一致、不碰宿主路径"的，并且 Harbor 和沙箱云服务都这样做。
- 方案 1 和 3 也能让镜像自包含，但都要多一个启动步骤和一个持久位置。152 已经要求工作区用同名宿主目录，所以方案 1 的增量其实不大。
- 结论会变的情形：Skill 必须在运行中实时可见（如开发时边改边跑），改选 bind 类（1、2）；`[skills].dirs` 很大、每次复制太慢，改选 1 或 3；部署机 Docker 低于 API 1.45，就排除方案 3 的 subpath 写法。不管选哪个，local 模式都可以保留现在的 bind。

## 问题 2：孤儿沙箱清理误判

### A. 根因

查实的事实：

- 沙箱带两个标签：运行 id，以及起沙箱的 MCP 服务器进程的 `os.getpid()`（`sandbox.py:57-59`、`:271`）。
- 清理在**运行进程**里做：每次运行开始前，`docker_lifecycle` 先调 `remove_orphaned_containers`，结束时删自己的容器（`sandbox.py:408-419`；调用点 `agent.py:2226`、`evals/dab.py:466`）。
- `remove_orphaned_containers` 列出守护进程上**所有**带 `OWNER_LABEL` 的容器（`labelled`，`sandbox.py:342-361`），用本进程的 `os.kill(pid, 0)` 判断所有者是否还活着（`alive`，`:371-385`）。

推断：

- 在"同一台机器、同一个 PID 命名空间、同一次启动"之内，这套判断是对的。今天的单元测试覆盖的也是这种情形（`tests/test_sandbox_run.py:108`、`:125-126`）。
- 出错是因为范围不一致：pid 只在一个命名空间里有意义，标签过滤却覆盖整个守护进程。所以 152 列的三种后果都成立：宿主机上 PID 1 永远活着，所以 owner=1 的沙箱永远不被清；容器里的实例会删掉别处正在用的沙箱；重启后 pid 被复用，死掉的运行看上去还活着。
- 要分开两种孤儿：(a) **实例还活着，某次运行被杀**：同一命名空间里，pid 判断够用；(b) **整个实例重启**：上一次启动留下的沙箱全是孤儿，pid 判断会被复用干扰，必须靠"启动代次"之类的信息区分。
- 用 pid 判断所有权这个模式本身没错，只要把清理范围限制在同一命名空间、同一次启动。并行的评测 worktree 共用一个守护进程，各自只能删自己的容器，这个要求不变。

### B. 上游怎么做

| 项目 | 做法 | 证据 |
|---|---|---|
| Testcontainers（Ryuk） | 每个进程生成一个 `SESSION_ID = uuid4()`，作为 `org.testcontainers.session-id` 标签打在每个容器上。另起一个 Ryuk 容器（挂 socket，`auto_remove`），客户端通过 TCP 发 `label=...session-id=<id>` 并保持连接；连接断开超过 `RYUK_RECONNECTION_TIMEOUT`（默认 10s）后，Ryuk 按标签删容器、网络、卷 | `labels.py:7`、`:11`、`:34`；`container.py:492-541`；moby-ryuk `README.md` |
| SignalPilot | 评测容器跑完即删（`finally` 里 `DELETE ...?force=1`）。所有权按"标签里的运行 id 是否对应本组织的运行目录"判断（`_owns`），但这只用于查看面板。运行有数据库租约（TTL 180s，每 45s 续），启动时和定期回收过期运行。回收的是分支、连接和密钥，**没有删容器的代码**；网关崩溃后留下的已退出容器由谁清，**未确认**（可能没人清） | `backends.py:110-171`；`sandboxes.py:190-213`；`runner.py:717-752`；`retention.py:82-119`、`:128` |
| JupyterHub DockerSpawner | 容器 id 存进 Hub 数据库（`get_state` / `load_state`）；Hub 重启后按名字或 id `docker inspect`，看 `State.Running` 判断（`poll`）。所有权来自持久化的状态，不来自 pid。有没有清理"数据库里不存在的容器"的逻辑，**未确认** | `dockerspawner.py:915-942`、`:972-1010` |
| OpenHands 1.0.0 | 按名字前缀 `openhands-runtime-` 识别；进程退出时通过关闭监听器停掉**所有**带这个前缀的容器（`stop_all_containers`），`close()` 默认只停自己的。多实例共用一个守护进程时会互相误伤，没有防护 | `docker_runtime.py:43`、`:105-108`、`:594-612`；`containers.py` |
| OpenHands software-agent-sdk | `--rm`，名字带 `uuid4`，退出时 `cleanup` | `workspace.py:240-251`、`:363-378` |

没查：Jupyter Enterprise Gateway、GitHub Actions runner，**未确认**。

### C. 可选方案

| # | 做法 | 要改的代码 | 修了哪种孤儿 | 代价与风险 | 上游 |
|---|---|---|---|---|---|
| 1 | **实例标签 + 启动代次标签**：加 `data-agent.sandbox.instance=<部署实例 id>`（cloud 从配置或环境变量取，要稳定；local 用机器加用户，或者干脆不加，保持现状）和 `...boot=<进程启动时生成的 uuid>`。清理只看本实例：`boot` 不等于当前值的直接删（上一次启动的），等于当前值的才用 pid 判断 | `DockerSandbox.start` 的标签；`labelled` / `remove_orphaned_containers` 按实例过滤。启动代次要由长寿进程（cloud 的 web 服务器）生成，经参数传给 MCP 服务器 | (a)(b) 都修；不同实例互不误删 | 实例 id 必须稳定且唯一，配错（两台用了同一个 id）就会互删。local 模式要保证行为不变 | Testcontainers 的 session 标签思路 |
| 2 | **用运行记录或租约判断所有权**：标签只放运行 id；运行在自己的运行目录里写心跳（或一个租约文件），清理时只看本实例运行目录里存在的运行，租约过期就删 | `docker_lifecycle` 改判断依据；运行进程定期续租 | (a)(b) 都修（重启后租约过期） | 多一个心跳线程，以及"多久算过期"的阈值（太短会误删跑得慢的运行）。依赖运行目录持久保存（155 本来就要） | SignalPilot 的租约，DockerSpawner 的持久化 id |
| 3 | **reaper 旁车（Ryuk 式）**：一个挂 socket 的小容器，data-agent 和它保持连接，断开超时后按实例或运行标签删除 | 新组件（可以直接用 `testcontainers/ryuk` 镜像），Compose 加一个服务 | (a)(b) 都修，而且不依赖 data-agent 自己活着 | 多一个镜像、一份 socket 权限、一个端口。Ryuk 按会话设计，所有连接都断开后会自己退出，长驻的服务端怎么用要再看，**未确认** | Testcontainers |
| 4 | **容器自带寿命上限**：主进程从 `sleep infinity` 改成 `sleep <上限>`，再加 `--rm`，到点自删 | `DockerSandbox.start` 的命令和参数 | 只是兜底：(a)(b) 最终都会被清，但要等到上限 | 上限必须大于最长的运行（dbt 运行、多轮对话），否则会删掉还在用的沙箱。不能单独当方案用 | 沙箱云服务的超时机制（E2B 等），**未确认**，本次没查 |
| 5 | **`pid: host`**：data-agent 容器和宿主机共用 PID 命名空间，pid 判断就重新成立 | 只改 Compose | 修了"PID 1 永远活着"和"跨实例误删"；重启后 pid 被复用的问题仍然在 | 削弱容器隔离（在容器里能看到宿主机的全部进程）。它本来就挂了 socket，等于已有宿主机权限，增量风险不大，但和"每加一项控制都要有理由"相冲突 | Docker 文档里的 `--pid=host` 选项 |
| 6 | **规定每台机器只跑一个用 docker 后端的实例** | 只改文档 | 只修跨实例误删；PID 1 和 pid 复用的问题仍然在 | 并行评测 worktree 和 cloud 实例共用开发机的守护进程时仍然会出错 | 152 的建议 |

证据倾向与会改变结论的条件：方案 1 或 2 能修好两种孤儿，也不需要新组件；local 模式不加实例标签，就能保持现在的行为。方案 4 成本最低，适合和它们搭配作兜底。以后要支持"data-agent 崩溃、短时间内又不会重启"的长时间无人值守部署，方案 3（不依赖 data-agent 自己活着）就更有价值。

## 来源

本仓库（main，2026-11-27）：`src/data_agent/sandbox.py`、`run_config.py`、`cli.py`、`agent.py:2226`、`evals/dab.py:314`、`:466`、`pyproject.toml:69-72`、`tests/test_sandbox_run.py`、`docs/research/2026-11-27-sandbox-in-container-spike.md`、`docs/research/2026-11-27-skill-tool-spike.md`、`.scratch/product-config/issues/147-skills-mcp-plugins.md`、`.scratch/cloud-deploy/spec.md:26`、`docs/adr/0011-*.md`、`0012-*.md`。

Docker 官方文档：

- Bind mounts（源路径在守护进程主机上；`--mount` 遇到不存在的源路径就报错）：https://docs.docker.com/engine/storage/bind-mounts/
- Volumes（`readonly`、`volume-subpath`、空卷自动复制、`volume-nocopy`）：https://docs.docker.com/engine/storage/volumes/
- `docker cp`（复制进去的文件"属 root"；本机实测与此不符，见问题 1 C）：https://docs.docker.com/reference/cli/docker/container/cp/
- Engine API 1.45 `PutContainerArchive`（403 只读、404 路径不存在、`copyUIDGID`）：https://docs.docker.com/reference/api/engine/version/v1.45.yaml
- API 版本历史（`VolumeOptions.Subpath` 在 v1.45 加入）：https://docs.docker.com/reference/api/engine/version-history/

上游代码（钉了提交）：

- OpenHands `1.0.0`（`0cb27a4a`）：`openhands/runtime/impl/docker/docker_runtime.py`、`containers.py`、`openhands/runtime/impl/action_execution/action_execution_client.py`、`openhands/runtime/utils/runtime_build.py`、`openhands/runtime/utils/runtime_templates/Dockerfile.j2`、`docker-compose.yml`。https://github.com/OpenHands/OpenHands/tree/0cb27a4aa8c2656c37c00a8d52ad574c1c21649b
- OpenHands software-agent-sdk（`cf8158bc`）：`openhands-workspace/openhands/workspace/docker/workspace.py`、`openhands-sdk/openhands/sdk/workspace/remote/base.py`。https://github.com/OpenHands/software-agent-sdk/tree/cf8158bc25f5552c50ad131a5f0ce2dbc297789b
- OpenHands Docker runtime 文档（`SANDBOX_VOLUMES` 格式为 `host_path:container_path[:mode]`）：https://docs.openhands.dev/openhands/usage/runtimes/docker
- SignalPilot（本地克隆 `f5eee229`）：`signalpilot/gateway/gateway/evals/{backends,sandboxes,runner,retention}.py`、`docker-compose.yml`、`docs/docs/evals/deploying.mdx`。https://github.com/SignalPilot-Labs/SignalPilot
- Harbor（本地克隆 `dc933be7`）：`src/harbor/environments/docker/docker_unix.py`、`src/harbor/environments/tar_transfer.py`、`src/harbor/agents/base.py`
- testcontainers-python（`6146aa11`）：`src/testcontainers/core/labels.py`、`container.py`。https://github.com/testcontainers/testcontainers-python/tree/6146aa1170f415fcc825ed9bddc4eacbeb937a12
- moby-ryuk（`0509b379`）README：https://github.com/testcontainers/moby-ryuk/blob/0509b379b7c4a572c918d54079f9ede176045714/README.md
- DockerSpawner（`a73d0de6`）：`dockerspawner/dockerspawner.py`。https://github.com/jupyterhub/dockerspawner/tree/a73d0de667e43a6967d4c870c00d6b11c9a0dd17
- Jenkins docker-workflow-plugin（`8320297d`）：`WithContainerStep.java`、`client/DockerClient.java`。https://github.com/jenkinsci/docker-workflow-plugin/tree/8320297da7acc8378ede4bb9edba966ccfe539c5
- E2B 上传文档（只看了文档）：https://docs.e2b.dev/filesystem/upload
- Daytona 文件系统文档（只看了文档）：https://www.daytona.io/docs/en/file-system-operations/

没查：Dify sandbox、Jupyter Enterprise Gateway、GitHub Actions runner / ARC。

本机实测：`docker run --rm -d --network none --user 10001:10001 alpine sleep 90`，然后分别 `docker cp <dir> <id>:/opt/da/skills/sk`（失败：父目录不存在），以及 `COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -C stage -cf - opt | docker cp - <id>:/`（成功，文件属主 `501:0`，沙箱用户覆盖和新建都 `Permission denied`，脚本能执行）。容器靠 `--rm` 到期自删。
