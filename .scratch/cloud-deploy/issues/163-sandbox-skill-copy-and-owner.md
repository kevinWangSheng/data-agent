# 163 — 沙箱：Skill 用 tar 流复制进沙箱；孤儿清理按实例 id + 开机 id

Status: ready-for-agent
Blocked by: product-config 147, 152
Spec: ../spec.md（Implementation Decisions"沙箱"）；决定：`.scratch/product-config/decisions.md`"调研后定"第 1、2 条
Claimed by: —
Branch: —

## Goal

data-agent 跑在容器里、经挂进来的 Docker socket 起沙箱时（cloud 模式），两件事要能用：
1. **Skill 进沙箱**：Docker 后端不再 bind mount Skill 目录，改成每次运行起沙箱后用 tar 流把 Skill（内置的和 `[skills].dirs` 的）复制进沙箱里约定的路径，只读。镜像仍然自包含，不依赖宿主机上有 Skill 目录。
2. **孤儿清理**：owner 标签不再只记 pid，加实例 id 和开机 id；每个实例只清自己起的沙箱；容器加存活上限兜底。

本机模式（`--sandbox local`）和现有行为不变；Docker 后端在本机的行为从调用方看也不变（Skill 路径、只读、`${CLAUDE_SKILL_DIR}`）。

## Authority

- May edit：
  - `src/data_agent/sandbox.py`：`DockerSandbox.start`（Skill 挂载 `:278`、owner 标签 `:271`）、复制 Skill 的新函数、`alive`/`remove_orphaned_containers`（`:371-396`）、存活上限。
  - `src/data_agent/agent.py`（`:2226` 调 `remove_orphaned_containers` 处）、`evals/dab.py`（`:466` 同上）：只为传实例信息或改调用签名。
  - `tests/`（除 `tests/acceptance/`）。
- Must not edit：`tests/acceptance/`；`skills/`（Skill 文本不改）；`sandbox-image/`；`Dockerfile`、`deploy/`、`docs/`（归 155）；147 的 Skill 发现与打包代码（只用它给出的目录列表）；其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none（真实运行交主会话）。
- Spend cap：$0。

## Context

- 取证：`docs/research/2026-11-27-sandbox-skill-mount-and-orphans.md`（根因、七个复制方案、六个清理方案、上游代码位置）；`docs/research/2026-11-27-sandbox-in-container-spike.md`（152："155 的阻塞点"、第 4 条孤儿清理）。
- **根因 1**：内置 Skill 经 force-include 打进包里（`pyproject.toml:69-72`、`run_config.py:18-22`），容器里只在镜像自己的文件系统上；bind mount 的源路径由守护进程在宿主机上解析，所以挂不进去。147 的"同名路径挂载"也解决不了内置 Skill。
- **复制的做法**（照 Harbor `upload_dir` 的 tar 回退；Docker `put_archive` / `docker cp`）：
  - 目标父目录不存在时 `docker cp` 失败：tar 里带完整路径、在 `/` 解开（调研本机实测）。macOS 上打 tar 要去掉 xattrs（`--no-xattrs` 或用 Python `tarfile` 自己写头）。
  - 复制进去的文件属于 tar 头里的 uid（实测 `501:0`），不是 root；沙箱以 data-agent 的 uid 跑（`sandbox.py:274`），所以要在 tar 头里写 uid 0 或去掉写权限，Skill 才只读。Linux 宿主上没测过，要测。
  - 沙箱里的路径与 147 合入后 `${CLAUDE_SKILL_DIR}` 解析到的路径一致（以 147 合入的代码为准）。
- **根因 2**：标签记 MCP 服务器的 `os.getpid()`（`:271`），`alive()` 在本进程的 pid 命名空间里判断（`:371-385`），而 `labelled(OWNER_LABEL)` 列的是守护进程上全部带标签的容器：范围不一致。容器里 pid 常是小数字、重启后复用。
- **清理的做法**（照 Testcontainers 的会话标签；DockerSpawner 按 id 认领）：标签加实例 id（如主机名/容器 id）和开机 id（进程或容器启动时生成）；只清"实例 id 是自己、开机 id 不是本次"或"实例 id 是自己、开机 id 是本次且 pid 已不在"的容器；别的实例的一概不碰。存活上限（如容器里 `sleep` 改成有上限的时长，或标签记截止时间）兜底。
- 改回挂载的条件（`decisions.md`）：运行中 Skill 必须实时更新，或 `[skills].dirs` 很大。实现时发现属于这种情况就停下。

## Acceptance

- [ ] 单元测试（替身 docker 调用）：起沙箱的参数里没有 Skill 的 bind mount；起来后有一次复制；复制的 tar 头 uid 为 0 或文件没有写权限。
- [ ] 单元测试：标签含实例 id 和开机 id；清理只删自己实例的孤儿；别的实例（含 owner pid 为 1 的）不删；本实例上次开机留下的删。
- [ ] 集成测试（`-m integration`，真 Docker）：Skill 在沙箱里可读、写入被拒；`${CLAUDE_SKILL_DIR}` 下的脚本能跑。
- [ ] 本机模式与已有测试不改就通过。
- [ ] `make check` 通过。

## Evidence required

贴到 Comments：`make check` 输出；集成测试输出。真实运行交主会话（在服务器上，Linux 宿主）：照 152 的探针在一个 data-agent 容器里挂 Docker socket 起沙箱，`sandbox_exec` 读 Skill、写 Skill 被拒；起两个实例，杀掉一个的运行，确认另一个不清它的沙箱、它自己重启后清掉。最终回复写出命令。

## Stop conditions

- 147 合入的 Skill 路径或 `${CLAUDE_SKILL_DIR}` 的解析方式和复制方案对不上，要改 147 的代码。
- 复制方案在 Linux 宿主上做不到只读。
- 要改验收测试或 `skills/`。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：依据 `decisions.md`"调研后定"第 1、2 条（人确认"按证据支持的方式"）。155 等本工单。
