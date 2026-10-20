# 20 — 装 agent 不在容器里联网下载依赖

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

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

- [ ] 锁定依赖的 wheel 按容器的平台（架构、Python 版本、glibc）在本机下载一次，逐个核对 `uv.lock`/`requirements.txt` 里的哈希，缓存到仓库外的本机目录；再次运行命中缓存不联网；哈希不符就报错，不用该文件
- [ ] 容器里用 `--no-index --find-links`（或等价方式）离线安装，`--require-hashes` 保留；安装步骤不访问 PyPI（测试里断言命令不含联网安装、或在无网络的容器里能装上）
- [ ] 安装输出（stdout/stderr）写进 trial 的 `agent/setup/` 日志，并记下用时
- [ ] 单元测试覆盖：平台选择、哈希不符拒绝、缓存命中不下载、安装命令拼装
- [ ] 真实运行：开发集 6 题 × `full` × 1，`--n-concurrent 2`，在 Mac colima 上；贴每个 trial 的 agent setup 用时（全部明显低于 360 秒）和 `agent/setup/` 日志示例
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令、每个 trial 的 setup 用时表、一份安装日志。

## Stop conditions

要改 Harbor、任务配置的超时或资源，或改依赖；某个依赖没有容器平台可用的 wheel 必须源码构建且离线装不上；需要人决定的事；花费到上限；第 2 轮审查仍不过。

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

开发集检查 `runs/dev-1` 因 24 个 setup 超时没法出表（`ablate: not ready to score (full: 15 infrastructure errors …; baseline: 9 …)`），花费约 $4–5。本工单合并后开发集用新的空 `--out` 重跑。用户同意现在修。
