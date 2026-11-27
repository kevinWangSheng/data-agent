# 152 — 实测：data-agent 在容器里挂 Docker socket 起沙箱，工作区路径能挂进沙箱

Status: in-review
Blocked by: —
Spec: ../spec.md（Implementation Decisions"沙箱"）
Claimed by: claude-opus-5-5（worktree 152-spike-sandbox-in-container）
Branch: worktree-152-spike-sandbox-in-container

## Goal

回答 cloud-deploy 部署前提的几个是/否问题，各带证据，供 153、155 照着做：

1. data-agent 跑在容器里、挂 `/var/run/docker.sock` 时，`DockerSandbox`（`src/data_agent/sandbox.py`）能起沙箱容器，`sandbox_exec` 能执行。
2. 工作区、Skill 目录（`SKILLS_MOUNT`）、运行目录能挂进沙箱：data-agent 容器里的路径和宿主机路径不同（socket 挂载时 `docker run -v` 的源路径按宿主机解析），哪种做法可行——同名路径挂载（宿主机与容器用同一个绝对路径）、命名卷、还是别的。
3. 沙箱拿不到 data-agent 容器的环境变量和凭据（ADR-0010），默认无网络。
4. `dbt_execute`（宿主侧执行）、值字典、只读网关在 data-agent 容器里照常工作（DuckDB 文件、Postgres 连接）。

## Authority

- May edit：新建 `scripts/spikes/sandbox_in_container/`（Dockerfile 草稿、compose 草稿、探针脚本、说明）、`docs/research/2026-11-27-sandbox-in-container-spike.md`。
- Must not edit：`src/`、`evals/`、`skills/`、`tests/acceptance/`、`Makefile`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access：none（本机 Docker/colima）。
- Spend cap：$0。探针不调模型：直接构造 `DockerSandbox` 调 `sandbox_exec`、直接调网关，不起 agent 会话。

## Context

- 沙箱：`src/data_agent/sandbox.py:39`（镜像 `data-agent-sandbox`）、`:44-45`（`--cpus 2`、`--memory 4g`）、`:76-82`（`local` 后端透传的环境变量）、`SKILLS_MOUNT`；ADR-0010（沙箱拿不到写库凭据、产品要装 Docker）。
- 本机 Docker：colima（AGENTS.md 提到测试不依赖 docker 时选 `--sandbox local`）；先 `docker info` 确认可用，不可用就停下交回。
- 不要停止、重启或改动本机的 Docker/colima 守护进程配置；只起、删本工单自己的容器和卷（按名字前缀 `da-spike-152-`）。

## Acceptance

- [ ] 探针一条命令跑完四个问题，打印每个问题的是/否和证据（容器 id、挂载列表、沙箱里 `env`、`ls`、网络探测、网关查询结果）；跑完删掉自己起的容器和卷。
- [ ] 研究笔记写清四个答案、问题 2 推荐的路径方案及理由、给 153/155 的注意事项（socket 权限、colima 与 Linux 宿主的差别）。
- [ ] `make check` 通过（不应受影响）。

## Evidence required

探针的真实输出（本机 Docker，$0）；`make check` 输出。

## Stop conditions

- 本机 Docker 不可用。
- 问题 1 或 2 走不通且没有可行替代（写明试过什么）。
- 要改 `src/` 才能探到。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0
- Outcome: 四个问题都有答案；推荐同名路径挂载；发现 155 的阻塞点（Skill 目录在包里）
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：cloud-deploy 的第一张实测工单；跑不通就停下回对齐（decisions.md Q7 最后一条）。
**结论**（详见 `docs/research/2026-11-27-sandbox-in-container-spike.md`）

| 问题 | 答案 | 证据 |
|---|---|---|
| 1. 容器里挂 socket 能起沙箱、`sandbox_exec` 能执行 | 是 | 容器以非 root 运行（`--user 10001:10001` 一次、`501:20` 一次，`--group-add 991`），两次都是 `exit_code: 0` |
| 2. 工作区和 Skill 目录能挂进沙箱 | 用同名路径挂载才行 | 见下 |
| 3. 沙箱拿不到环境变量和凭据，默认无网络 | 是 | 金丝雀变量一个都没进沙箱；挂载只有工作区（可写）和 `/skills`（只读）；`NetworkMode=none`；沙箱里没有 socket。对照：同样的网络探测在 data-agent 容器里能连通 Postgres 和外网 |
| 4. `dbt_execute`、值字典、只读网关照常工作 | 是 | DuckDB 和 Postgres 都能查，`DELETE` 都被 `QueryBlocked` 拦下；值字典 9/9 张表；`dbt run` 两种库都 `exit_code: 0`、`PASS=3` |

问题 2 的各种做法：
- **同名路径挂载**（`-v X:X`）：可行，推荐。现有代码不用改，两个方向的文件都看得到。
- **容器内外路径不同**：失败，`bind source path does not exist: /srv/...`。
- **命名卷**：失败，`... does not exist: /vol`。
- **Skill 目录只在容器自己的文件系统里**：失败，整个沙箱起不来。
- **命名卷 + `volume-subpath`**：没测成（VM 磁盘满），而且要改 `src/`。

**155 的阻塞点（要对齐会话定）：** `DockerSandbox` 用的 Skill 目录是包里的 `data_agent/skills`（`src/data_agent/run_config.py:18-22`、`cli.py:157`）。在产品镜像里这个目录宿主机上没有，正好是上面失败的那种情况。两条路：
- 改 `src/`：Skill 目录做成可配置，或每次运行把 Skill 复制到运行目录。
- 部署层绕开：把包装到同名挂载的宿主机目录里。代价是镜像不再自包含。

product-config 的 `[skills].dirs` 是否已经覆盖这一点，我没核实。

**给 153/155 的其他注意事项**（note 里有完整列表）：
- **孤儿清理在容器里会误判**：沙箱上记的 owner pid 属于容器自己的 pid 命名空间，`remove_orphaned_containers` 可能删掉别的实例正在用的沙箱，也可能永远清不掉。修法要改 `src/`。
- **沙箱镜像要事先准备好**：启动参数是 `--pull never`，部署机上必须先有 `data-agent-sandbox`。
- **产品镜像要自带 dbt 和 docker CLI**：dbt 现在只是 dev 依赖。
- **socket 权限**：socket 的 gid 要在部署机上取，用 `group_add` 加进去。
- **Linux 宿主机上的所有权**：沙箱以 data-agent 容器的 uid 运行，挂载目录要对这个 uid 可写；colima 测不出这一点，未确认。
- **资源**：沙箱固定 `--cpus 2`，选机器至少 2 vCPU；1 vCPU 时会被拒绝，未实测。

**第 3 步：真实运行**
- 命令：`scripts/spikes/sandbox_in_container/probe.sh`（`SPIKE_UID=10001 SPIKE_GID=10001` 可以换 uid）。
- 输出：`runs/da-spike-152-{ec1b4f,d839c0,39bb63}-output.txt`。三次都是 `all as expected`，清理后都是 `left: containers=[] volumes=[]`。
- `make check`：exit 0。ruff 通过，basedpyright 0 errors，pytest 3461 passed / 3 skipped，web 448 passed。这次是在最后一个提交之前跑的，最后一个提交只改了 `scripts/` 和 `docs/`，pre-commit 的 ruff 也通过了。

**第 5 步：审查**
- **`da-review`**：两条，都修了（`400c327`）。
  - 同时跑两个探针会互相删容器：改成每次运行用自己的标签和 run id。
  - Linux 上清理会因文件属主删不掉：容器改用调用者的 uid。
- **`eng:code-review` Spec**：
  - note 声称有一次 10001 的运行通过，但没有留下记录：已用 10001 重跑并留了输出。
  - 修了：Q1 证据里写死的 exit code；note 里不成立的 `profiles/` 理由；内存数字补了出处；清理记录现在也进输出文件。
- **`eng:code-review` Standards**：
  - 修了：死返回值、重复的网络探测、问题 id 不一致、mountinfo 标签写错、`q4` 改名、输出文件按运行加标签、note 里过时的卷路径。
  - 不修三条，理由：dbt 版本号在 Dockerfile 草稿和探针里各写一次，这是一次性 spike；改 `sandbox.NAME_PREFIX` 是为了满足工单的命名前缀，注释里写了原因；沙箱镜像缺失时自动 build，因为那就是产品要用的镜像，本次也没触发。
- 2026-11-27（主会话）：抄入工单 agent 的回复（它写不了主 checkout）。155 的阻塞点（产品镜像里 Skill 目录在包内、宿主机上没有）留给下一个对齐会话。本机清理项（失败构建留下的容器和无标签镜像、`docker:cli` 镜像、`runs/spike-152-fast/`、`runs/spike-152-perm/`）由人处理，见 HANDOFF.md。
