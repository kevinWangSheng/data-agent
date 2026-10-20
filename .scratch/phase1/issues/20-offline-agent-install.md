# 20 — 装 agent 不在容器里联网下载依赖

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: Claude Code (claude-opus-5-5) 2026-10-20
Branch: worktree-20-offline-agent-install

## Goal

Harbor trial 里装 agent 不再从 PyPI 下载依赖：锁定的 wheel 在本机（Mac 或服务器）按容器平台下载一次、核对哈希后缓存，每个 trial 上传进容器离线安装；安装输出留在 trial 目录里。agent setup 稳定在 Harbor 的 360 秒上限以内。

## Authority

- May edit: `evals/harbor_agent.py`、`evals/` 下新建的辅助模块、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试、`evals/configs/`（超时和资源不能改，榜单静态检查会拒）、`pyproject.toml` 与 `uv.lock`（不改依赖）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $3

## Context

- 现状 `evals/harbor_agent.py:225-253` `install()`：上传本仓库 wheel、`requirements.txt`（带哈希）和 uv 压缩包，然后在容器里 `uv pip install --require-hashes -r requirements.txt` 从 PyPI 拉全部依赖。uv 本身已经是本机下载、核对 sha256、上传（`:83-190`），本工单把依赖也照这个做。
- 开发集运行 `runs/dev-1`（2026-10-20，Mac colima，并发 2）：58 个 trial 里 24 个 `AgentSetupTimeoutError: Agent setup timed out after 360.0 seconds`；成功的 setup 用时 21–289 秒，没有安装日志（`agent/setup/` 为空）。超时分布在整个运行时段。
- 主会话实测同一个 `claude_agent_sdk-0.2.168-py3-none-manylinux_2_17_aarch64.whl`（114 MB）：Mac 本机一次 59 秒卡住（0 B/s）、一次 196 秒（0.58 MB/s），容器内一次 50 秒。并发 trial 共享带宽，是超时的主因。
- Harbor 的 agent setup 上限 360 秒来自任务配置，不能改（`evals/configs/data-eng-bench-duckdb.data-agent.yaml:38`、`SUBMIT.md` 静态检查）。
- Mac 容器是 aarch64，服务器是 x86_64：缓存按平台分开。DAB 的运行（`evals/dab.py`）不经 Harbor，不在本工单范围。

## Acceptance

- [x] 锁定依赖的 wheel 按容器的平台（架构、Python 版本、glibc）在本机下载一次，逐个核对 `uv.lock`/`requirements.txt` 里的哈希，缓存到仓库外的本机目录；再次运行命中缓存不联网；哈希不符就报错，不用该文件
- [x] 容器里用 `--no-index --find-links`（或等价方式）离线安装，`--require-hashes` 保留；安装步骤不访问 PyPI（测试里断言命令不含联网安装、或在无网络的容器里能装上）
- [x] 安装输出（stdout/stderr）写进 trial 的 `agent/setup/` 日志，并记下用时
- [x] 单元测试覆盖：平台选择、哈希不符拒绝、缓存命中不下载、安装命令拼装
- [x] 真实运行：开发集 6 题 × `full` × 1，`--n-concurrent 2`，在 Mac colima 上；贴每个 trial 的 agent setup 用时（全部明显低于 360 秒）和 `agent/setup/` 日志示例
- [x] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令、每个 trial 的 setup 用时表、一份安装日志。

## Stop conditions

要改 Harbor、任务配置的超时或资源，或改依赖；某个依赖没有容器平台可用的 wheel 必须源码构建且离线装不上；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: 约 $0.70（6 个 trial）
- Outcome: in-review
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

开发集检查 `runs/dev-1` 因 24 个 setup 超时没法出表（`ablate: not ready to score (full: 15 infrastructure errors …; baseline: 9 …)`），花费约 $4–5。本工单合并后开发集用新的空 `--out` 重跑。用户同意现在修。

### 2026-10-20 工单 agent：实现（3003557、2ea0f3c，主会话抄录）

- 新 `evals/wheels.py`：探测容器（CPython 版本、glibc、架构），从 `uv.lock` 选每个包最合适的 wheel，按 `requirements.txt` 哈希核对后缓存到 `~/.cache/data-agent/wheels`；下载哈希不符报错不缓存，缓存哈希不符删掉重下、不使用；每平台每进程建一次硬链接目录供上传。`python -m evals.wheels <image>` 预热缓存。真实 `uv.lock` 34 个包在 aarch64、x86_64（CPython 3.12 / glibc 2.36）都有 wheel，无需源码构建。
- `install()`：探测容器（替代 `uname -m`），`upload_dir` 上传 wheel 目录，`uv pip install --offline --no-index --find-links /installed-agent/wheels --require-hashes`（本仓库 wheel 也 `--offline --no-index --no-deps`）；全部经 `tee` 写 `agent/setup/install.log`（首行主机准备与上传用时，之后每步命令与用时，末行 `installed in N s`）。
- 测试：`tests/test_eval_wheels.py` 10 个（平台选择、非 glibc/非 CPython 拒绝、缺 wheel、哈希不在 requirements、下载哈希不符、缓存命中不下载、缓存损坏重下）；`tests/test_harbor_agent.py` 用 bash + 假 uv 跑真实安装脚本，断言两次安装都 `--offline --no-index --find-links …/wheels`、无 index-url、保留 `--require-hashes`，日志有每步用时；安装失败让 setup 失败且输出留在日志。`make check`：1131 passed，1 skipped。
- 真实运行（Mac colima，开发集前 6 题 × `full` × 1，`-n 2`）：预热 `python -m evals.wheels hb__…:latest` → `CPython 3.12, glibc 2.41, aarch64: 34 wheels`（148 MB），再跑 1.1 s 无下载。`harbor run … -k 1 -n 2 -o runs/t20-setup --job-name dev6-full -i <6 题>` → 17m58s，reward 1.0 ×4、0.0 ×2，0 exceptions。agent setup：18.8 s、18.7 s（含首次构建本仓库 wheel 6.3 s 与上传 3.8 s）、5.8 s、5.9 s、5.8 s、4.8 s；容器内安装 3–7 s。
- 审查：`da-review` P1 冷缓存时下载仍在 Harbor setup 超时内 → 不修：每台机器每次改锁只发生一次，Harbor 取消 trial 后下载线程继续把缓存填满，可先 `python -m evals.wheels <image>` 预热；已知限制：冷缓存 + 慢带宽时最早 1–2 个并发 trial 可能超时，`ablate` 续跑重做。后续：`ablate` 先预热（`evals/ablate.py` 超出本工单 Authority）。`eng:code-review`：修了 `download` 过时注释、`host` 改名 `host_line`；不修：`evals.wheels.main` 懒加载（避免循环导入）、测试里重复的探测 JSON、`install()` 不拆、元组不包类型；已知：主机侧失败（探测、缺 wheel、哈希不符、冷缓存超时）不留 `install.log`，只在 trial 异常里；每进程的临时 wheel 目录不删（硬链接几乎不占空间，回落复制时约 148 MB/进程）。
- 过时信息（Authority 外）：`evals/configs/data-eng-bench-duckdb.data-agent.yaml` 注释"install() took 62-135 s"已过时。

### 2026-10-20 主会话

服务器全量与开发集重跑前，先在对应机器上 `python -m evals.wheels <image>` 预热缓存（冷缓存已知限制）。
