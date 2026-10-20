# 17 — da-remote 跑评测、看状态、取结果

Status: ready-for-human
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 17-da-remote-eval)
Branch: worktree-17-da-remote-eval

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

- Attempts: 1
- Review rounds: 2（da-review 1、eng:code-review 1）
- Model spend: $0
- Outcome: ready-for-human（服务器缺 GitHub deploy key）
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

用户确认一期剩余准备项（17 da-remote、18 上榜轨迹、19 Postgres 方言）。本工单改的是 `scripts/da-remote`（仓库里的源）；合并后由主会话把它装到 `~/.local/bin/da-remote`。

### 2026-10-20 工单 agent：实现完成，真实运行受阻，转人（主会话抄录）

分支 worktree-17-da-remote-eval：3b6f9c8、123c5c0、8ad0232。

改动：`scripts/da-remote` 新增 `setup [<commit>]` / `start <name> -- <evals 参数>` / `status [<name>]` / `push <name>` / `pull <name>`。参数放进 stdin 上的 `bash -s` 脚本、`%q` 引号，不上远端命令行；输出都过 mask；`<name>` 限 `[A-Za-z0-9._-]+` 且不能全是点。`start` 写 `runs/<name>/run.sh`、`run.log`、`run.pid`，结束写 `exit`；同名在跑拒绝；打印 commit（脏标 `-dirty`）。`setup` 在有 run 在跑时拒绝。`status` 显示状态、日志末尾 20 行，并按启动时 `--budget` 跑 `evals spend`。`check` 改用 plan §7 并发口径（6 核 / 7 GB → `--n-concurrent 3`），磁盘门槛 100→20 GB，工具列表加 rsync；`docs/setup.md` 同步。新增 `push`（Acceptance 没列，冒烟要把临时 manifest 和题目副本传上去）：只写服务器 `runs/<name>/`，在跑的 run 拒绝，控制文件不覆盖。

测试：`tests/test_da_remote.py`（假 ssh、rsync、uv，不连服务器）67 passed、1 skipped（shellcheck 未装；`uvx --from shellcheck-py shellcheck scripts/da-remote` 手动查 clean）。`make check`：1085 passed, 1 skipped；basedpyright 0 errors。

真实运行（本 worktree 的 `scripts/da-remote`）：
- `check` → exit 1：仅 "FAIL no git repo at …/data-agent (da-remote setup)"，其余 ok（"ok cores 6, memory 7 GB -> --n-concurrent 3"、"ok free disk 98 GB"、"ok rsync installed"）
- `setup`（https）→ "fatal: could not read Username for 'https://github.com'"；`setup`（ssh，现默认）→ "git@github.com: Permission denied (publickey)."
- `status` → "status: no runs"（exit 0）；`status smoke` → exit 1；`start smoke -- ablate --budget 1` → "no repo …; run da-remote setup first"（exit 1）；`pull smoke` → 经主机别名连上，远端目录不存在（exit 23）；`start ../x -- ablate` → 名字校验拒绝（exit 2）
- 受阻：服务器没有 GitHub 凭据，私有仓库拉不下来；加 deploy key 属于改服务器配置（停止条件）。冒烟未跑，花费 $0。GitHub 主机密钥已在服务器 known_hosts。

审查：
- `da-review main`：P1 默认 https clone 用不了 deploy key → 已修，改 SSH；P2 push 会覆盖在跑 run 的控制文件 → 已修。各配修复前失败的测试。
- `eng:code-review main` 已修：setup.md 段落打断表格；第 3 行仍写 100 GB；`--out` 与 `<name>` 一致写进用法。不修：push 的存活检查与 rsync 间竞态（单人工具）；push 用旧文件覆盖同名输出（只传输入、控制文件已排除）；mask 误伤版本号（只影响显示）；远端路径含空格（默认无空格）；本地报错不过 mask（不含地址）；重复代码。
- 待决定：`pull` 会把 holdout 逐题 trial 目录取回 Mac。

### 2026-10-20 主会话

- `pull` 的 holdout 问题由主会话定：按 `spec.md:141`"服务器上的逐题结果只经 `da-remote` 取汇总"与 ADR-0004，holdout 的 run（`ablate`）只取回汇总报告（reports、`ablation.json`/`ablation.md`、日志里不含逐题信息的部分），不取 trial 目录；开发集与上榜全量 run 照旧整目录取回。建票时 Acceptance 写"取回 `runs/<name>/`"有误，以此为准。接手时一并修，配测试。
- deploy key 交给用户决定；加好后原分支接着做冒烟。
