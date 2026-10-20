# 21 — 服务器上备好两个榜单的数据与 DAB 数据库

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

一条 `da-remote` 命令把服务器备成能跑全量评测：data-eng-bench 题目、DAB clone 与数据、DAB 的 Docker 镜像、DAB 要的 Postgres 与 MongoDB；并在服务器上真实跑通 DAB 三类数据源（文件库、Postgres、MongoDB）各一题，量出内存占用。

## Authority

- May edit: `scripts/da-remote`、`evals/dab.py`、`evals/dab_server.py`（只限修 Postgres/MongoDB 数据集真实运行暴露的问题）、`tests/`（除 `tests/acceptance/`）、`docs/setup.md`
- Must not edit: `tests/acceptance/` 下的验收测试、`evals/harbor_agent.py`（工单 20 在改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: `da-remote` 现有子命令与本工单新增的子命令。服务器上允许：clone 公开仓库到 `$DIR` 旁的目录、跑 DAB 自带的数据下载脚本、`docker build`/`docker run`（容器只绑 127.0.0.1、用户级、可删）、在 `$DIR/runs/` 下跑评测。不允许 sudo、装系统包、改系统配置。
- Spend cap: $2

## Context

- 现状：服务器（6 核 / 7 GB / 98 GB 空闲）已有本仓库、`uv sync`、`da-remote setup/start/status/push/pull`（工单 17）；只推过一道题的副本做冒烟，没有完整题目，没有 DAB。
- data-eng-bench：公开，钉 commit `a3278ad`（plan §7 与 NOTICE）；`ablate`/全量要 `--tasks-dir <clone>/tasks`。
- DAB：钉 commit `6ad9d68`（本机 clone 在 `.claude/worktrees/13-dab-runner/runs/13/DataAgentBench`，只读参考）；没有许可证，只在服务器 clone 运行，不复制进本仓库。数据用 DAB 自带下载脚本，按 `dataset_manifest.tsv` 校验（`evals/dab.py:260-290`）。`execute_python` 要 `python-data:3.12` 镜像（`evals/dab.py:14-16`）。Postgres/MongoDB 连接经 `PG_HOST`、`PG_PASSWORD`、`MONGO_URI` 等环境变量（`evals/dab.py:78`），这些值放服务器 env 文件或运行时环境，不进仓库、不进输出（已在遮盖范围）。
- 工单 13 已知：Postgres 和 MongoDB 数据集没有真实跑过（13 的 Comments）；`ExecTool` 构造失败时已加载的 PG/Mongo 数据不清理。
- plan §7："DAB 要起 Postgres、MongoDB 和约 5 GB 数据，用量未测，放一台机器上"；服务器只有 7 GB 内存，和 Harbor 并发 3 不同时跑。

## Acceptance

- [ ] 新增 `da-remote` 子命令（如 `benchmarks`）：在服务器上 clone 或更新 data-eng-bench（`a3278ad`）与 DAB（`6ad9d68`），跑 DAB 数据下载并校验，构建 `python-data:3.12`，起（或确认已在跑）Postgres 与 MongoDB 容器（只绑 127.0.0.1、数据卷可删）；可重复执行，已就绪的步骤跳过；输出过 mask，不打印密码
- [ ] `da-remote check` 增加这些就绪项（题目目录、DAB 数据校验、镜像、两个数据库可连）
- [ ] 真实运行（经 `da-remote start`/`status`/`pull`）：DAB 文件库类一题、Postgres 类一题、MongoDB 类一题，各 `--config no-context --runs 1`，贴结果与 submission；跑时用 `docker stats`/`free` 记服务器内存峰值
- [ ] 真实运行暴露的 `evals/dab.py`/`evals/dab_server.py` 问题：修并配修复前失败的测试；修不了的写成已知限制
- [ ] data-eng-bench：服务器上 `--tasks-dir` 指向 clone 的 `tasks/`，用 `da-remote start` 跑开发集 1 题 × `baseline` × 1 证明可用（如工单 20 已合并，在其之后跑）
- [ ] 单元测试覆盖新子命令的参数校验与命令拼装（不连服务器）；`docs/setup.md` 更新
- [ ] `make check` 通过

## Evidence required

单元测试输出；`benchmarks`、`check` 的真实输出（已 mask）；三个 DAB 数据源各一题和 data-eng-bench 一题的命令、结果与内存峰值。

## Stop conditions

服务器要 sudo、装系统包或改系统配置；内存不够同时放下 DAB 数据库（需要人决定加机器或分批）；DAB 数据下载或校验失败且原因在 DAB 侧；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

用户确认：全量运行前把剩余准备做完，真正跑全量前找用户。本工单与工单 20（`evals/harbor_agent.py`）不重叠，可并行。
