# 152 — 实测：data-agent 在容器里挂 Docker socket 起沙箱，工作区路径能挂进沙箱

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md（Implementation Decisions"沙箱"）
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：cloud-deploy 的第一张实测工单；跑不通就停下回对齐（decisions.md Q7 最后一条）。
