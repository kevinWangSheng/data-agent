# 18 — 上榜轨迹 trajectory.json（ATIF）

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: claude-code (opus-5-5) 2026-10-20
Branch: worktree-18-atif-trajectory

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

- [x] 从一次运行的事件文件转出 ATIF `Trajectory`（用 Harbor 自己的 pydantic 模型构造和校验，不手写 schema）：用户提示、助手文本、每个工具调用及其结果、验证者子 agent 的步骤可区分、最终 token 与花费
- [x] Harbor adapter 在 trial 结束时把它写到 Harbor 期望的位置，使 Harbor 记录该 trial 的 `trajectory_path`（照 Harbor 内置 agent 的做法，引用具体文件行）
- [x] 轨迹文件经 `evals scrub` 处理（key、端点、IP、主机名、home 路径），单元测试覆盖轨迹里的这些字段
- [x] 事件文件缺失或损坏时不让 trial 报错，只记录没有轨迹
- [x] 真实运行：开发集 1 题 × `full` × 1 次，贴 `trajectory.json` 的步数、类型统计和 Harbor 模型校验通过的输出
- [x] `make check` 通过

## Evidence required

单元测试输出；真实 1 题运行的命令、轨迹校验输出与 scrub 结果。

## Stop conditions

事件文件缺 ATIF 必需信息、要改 `src/data_agent/` 的事件格式；Harbor 写轨迹的位置或方式需要改 Harbor；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: 约 $0.27（两次真实运行 $0.114、$0.156）
- Outcome: in-review
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

一期上榜缺口：仓库里 `evals/`、`src/` 都没有生成 `trajectory.json`。上传 Hub 本身不在本工单（由人做）。

### 2026-10-20 工单 agent：实现（a66b5f7、fea3242、d26779f，主会话抄录）

- 新 `evals/atif.py`：`convert(events, instruction, session_id)` 用 Harbor 自己的 pydantic 模型构造轨迹：第一步是指令（user）；每轮一个 agent 步（thinking、文本、工具调用），工具结果挂到发起调用的那一步；每个 `error` 事件一个 system 步；token 与花费合计取自 `result` 事件。验证者期间的步骤标上当时在跑的验证者名；每个 `verifier_end` 另加一步放报告与检查结果。
- `evals/harbor_agent.py`：trial 结束后写 `logs_dir/trajectory.json`——Harbor 内置 agent 写的位置（`claude_code.py:1669`），Harbor 上传时据此记 `trajectory_path`（`upload/uploader.py:680`）。事件文件缺失、读不了、损坏或校验不过只记"no trajectory"，trial 不失败；损坏的 `result` 事件也不再弄坏已有的 token/花费回填。
- scrub：`evals/scrub.py` 不用改；新测试在轨迹各字段放 key、端点、IP、主机名、home 路径，scrub 后全清掉且仍通过校验。
- 真实运行（`dbt-fix-division-by-zero` × `full` × 1，colima，`-n 1`，`runs/18/jobs`，没动 dev-1）：

```
JOB=atif-real-2 ./runs/18/real.sh   # Trials 1, Exceptions 0, Mean 1.000
Harbor Trajectory.model_validate_json: OK (ATIF-v1.8, trajectory.json)
agent: data-agent version=d26779f… model=deepseek-flash
steps: 149  {user: 1, agent:main: 68, agent:inside-verifier: 77, agent:verifier-report: 3}
tool calls: 182, observation results: 182, steps with reasoning: 46
final_metrics: prompt 3540783, cached 3424384, completion 83485, cost $0.1556, total_steps 149
scrub: changed 5 file(s) of 15; redactions: key=0 endpoint=0 host=0 ip=0 home=7 user=0  (exit 0)
scrubbed trajectory.json → Harbor validation OK
```

  7 处 home 路径都在 Harbor 的 config/lock/result 里，轨迹本身只有容器路径。更早的 `atif-real-1`（reward 0，87 步）也校验通过，但版本带 `-dirty`，所以在提交后的代码上重跑。
- 审查：`da-review` P1（只接 `ValidationError`，非数字/`Infinity` token 数或写失败会让 trial 失败）→ fea3242、d26779f 修，各配先失败的测试。`eng:code-review` Standards：合并重复的 result 解析为 `atif.usage()`、注释占位 `step_id`、写明只读最后一个事件文件；不修：agent 上的 `instruction` 属性（Harbor 只在 `run()` 给指令）。Spec：修 `OverflowError`/`Infinity`，改成照 Harbor（`claude_code.py:1659`）兜底捕获；不修：验证者在 `verifier_end` 前被截停时标签留在之后的步骤上（那种运行之后没有主 agent 步，docstring 已写并行标签限制）；缺 `tool_use_id` 变成字符串 `"None"`（`record_message` 总会设 id）。
- 已知限制：并行验证者共用标签（事件文件里子 agent 没有父 id；要区分需改 `src/data_agent/` 事件格式）。Hub 上传未覆盖，由人做；证明的是文件内容与位置。
