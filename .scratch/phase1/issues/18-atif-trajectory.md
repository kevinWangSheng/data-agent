# 18 — 上榜轨迹 trajectory.json（ATIF）

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

data-eng-bench 上每个 trial 都带一份 Harbor 认可的 ATIF 轨迹（由我们的事件文件转出），上传 Hub 后能过榜单"every rewarded trial has a Hub `trajectory_path` (ATIF)"检查；轨迹公开前已经过 scrub。

## Authority

- May edit: `evals/harbor_agent.py`、新建 `evals/atif.py`（名字可改）、`evals/scrub.py`、`tests/`（除 `tests/acceptance/`）、`NOTICE`（只限登记从 Harbor 复制或改写的代码）
- Must not edit: `tests/acceptance/` 下的验收测试、`src/data_agent/`（事件格式不改；缺字段就停下交回）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $1

## Context

- 榜单要求：`data-eng-bench/leaderboard/SUBMIT.md:123`（每个得分 trial 要有 ATIF `trajectory_path`，"agents that only upload custom logs fail here"）、`:148-159`（`/judge` 读轨迹）。
- Harbor@dc933be（Apache-2.0）：ATIF 模型与 Claude Code 会话转换 `src/harbor/agents/installed/claude_code.py:606`（`convert_trajectory`）、`:1124`（`_convert_events_to_trajectory`）；Hub 同步 `src/harbor/trial/sync_trajectory.py`；Harbor 在项目依赖里（`harbor==0.24.0`）。
- 我们的事件：plan §3"事件流"，每次运行一个 JSONL（版本头、文本增量、工具开始/完成、SQL、验证者开始结束、结果含花费与 token）。`evals/harbor_agent.py` 的 `populate_context_post_run()` 已回填 token 与花费。
- plan §7："上榜轨迹是公开的"，上传前强制 scrub。

## Acceptance

- [ ] 从一次运行的事件文件转出 ATIF `Trajectory`（用 Harbor 自己的 pydantic 模型构造和校验，不手写 schema）：用户提示、助手文本、每个工具调用及其结果、验证者子 agent 的步骤可区分、最终 token 与花费
- [ ] Harbor adapter 在 trial 结束时把它写到 Harbor 期望的位置，使 Harbor 记录该 trial 的 `trajectory_path`（照 Harbor 内置 agent 的做法，引用具体文件行）
- [ ] 轨迹文件经 `evals scrub` 处理（key、端点、IP、主机名、home 路径），单元测试覆盖轨迹里的这些字段
- [ ] 事件文件缺失或损坏时不让 trial 报错，只记录没有轨迹
- [ ] 真实运行：开发集 1 题 × `full` × 1 次，贴 `trajectory.json` 的步数、类型统计和 Harbor 模型校验通过的输出
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实 1 题运行的命令、轨迹校验输出与 scrub 结果。

## Stop conditions

事件文件缺 ATIF 必需信息、要改 `src/data_agent/` 的事件格式；Harbor 写轨迹的位置或方式需要改 Harbor；需要人决定的事；花费到上限；第 2 轮审查仍不过。

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

一期上榜缺口：仓库里 `evals/`、`src/` 都没有生成 `trajectory.json`。上传 Hub 本身不在本工单（由人做）。
