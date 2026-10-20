# 17 — da-remote 跑评测、看状态、取结果

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

在 Mac 上用 `da-remote` 一条命令把服务器仓库更新到指定 commit 并后台启动一次评测（`python -m evals ablate` / `dab`），随时看进度，跑完把 `runs/` 里的结果取回 Mac；全程输出不露服务器地址。

## Authority

- May edit: `scripts/da-remote`、`tests/`（除 `tests/acceptance/`）、`docs/setup.md`（da-remote 一行与用法）
- Must not edit: `tests/acceptance/` 下的验收测试、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: `da-remote check` 与本工单新增的子命令；服务器上只允许 clone/fetch 本仓库、`uv sync`、在 `$DIR/runs/` 下跑评测与读结果
- Spend cap: $1（服务器上 1 题冒烟）

## Context

plan §7"服务器与并发""结果怎么拿回来"、§9 第 8 项（"工单 12 全量、13 DAB 前补 start-eval / status / pull-results"）；`docs/setup.md` 第 3 行（服务器 6 核 / 7 GB，不能升配，仓库未 clone）。`scripts/da-remote` 已有 `check`、`mask`、`remote`；装好的副本在 `~/.local/bin/da-remote`。

## Acceptance

- [ ] `da-remote setup`：服务器上没有仓库就 clone，有就 fetch；切到指定 commit（默认 `origin/main`），拒绝脏工作区；`uv sync`
- [ ] `da-remote start <name> -- <evals 子命令与参数>`：在服务器 `$DIR` 里用 `nohup`（或 tmux）后台运行，加载服务器自己的 `~/.config/data-agent/env`，日志写 `runs/<name>/run.log`；同名已在跑就拒绝；打印运行的 commit
- [ ] `da-remote status [<name>]`：是否还在跑、日志末尾若干行、`evals spend` 的当前花费
- [ ] `da-remote pull <name>`：把服务器 `runs/<name>/` 取回 Mac 的 `runs/<name>/`（经 `remote` 走主机别名，不直接写 `ssh`/`rsync` 命令给人或 agent 用）
- [ ] 所有子命令输出都过 `mask`；参数里的 `<name>` 只允许 `[A-Za-z0-9._-]`
- [ ] `check` 的门槛改成 plan §7 的现行口径（6 核 / 7 GB 机器是合格的；不再要求 ≥8 核 / ≥32 GB）
- [ ] 单元测试覆盖参数校验、mask、命令拼装（不连服务器）
- [ ] 真实运行：`setup` → `start` 一次 1 题 × 1 配置 × 1 次的 `ablate`（holdout 只放一道开发集题目）→ `status` → `pull`，贴输出
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实 `setup / start / status / pull` 的命令与输出（已 mask）。

## Stop conditions

服务器需要 sudo、装系统包或改配置；需要人决定的事（例如服务器跑不动）；花费到上限；第 2 轮审查仍不过。

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

用户确认一期剩余准备项（17 da-remote、18 上榜轨迹、19 Postgres 方言）。本工单改的是 `scripts/da-remote`（仓库里的源）；合并后由主会话把它装到 `~/.local/bin/da-remote`。
