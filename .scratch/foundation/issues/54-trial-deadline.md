# 54 — trial 截止时间：剩余不足时不再派验证器，到点自己停下

Status: in-progress
Blocked by: 51
Spec: ../../../docs/research/2026-11-05-foundation-latency-audit.md（§2）
Claimed by: claude (worktree 54-trial-deadline, 2026-11-05)
Branch: worktree-54-trial-deadline

## Goal

agent 知道这个 trial 的截止时间：剩余时间不够跑完一次验证时，不再派验证器，拿现有结果收尾；到点前自己干净地停下，写出 `result` 事件，正常退出。Harbor 不再报 `AgentTimeoutError`，超时的 trial 也有花费记录，进程不会在评分时还在跑。

## 已和用户定下的（2026-11-05，grilling Q5、Q6、Q7、Q16、Q18、Q20）

- 截止时间来源：照 Harbor 的 Cline 先例，加普通 kwarg `agent_timeout_sec`，默认 4000，由 Harbor agent 传给 CLI，CLI 用 monotonic 时钟换算成截止时间。
- 剩余时间不到 **1000 s** 时：派验证器的 Agent 调用被 PreToolUse 拒绝，理由写明剩余秒数和"不再验证，用现有结果收尾"；Stop 钩子同时放行，并记 `ended_unverified` 事件。两处一起改，否则 Stop 要求重派、派单又被拒，会循环。
- **T − 150 s（默认 3850 s）**：主循环自己停，关掉 SDK，写 `result` 事件并标 `terminal_reason: "deadline"`，按正常结束退出（Harbor 不报超时，花费照常记录）。
- 不加"剩余时间不足时拒绝新 `dbt run`"的规则：记为已知限制；事件里记下停下那一刻有没有未结束的工具调用。
- 只处理 data-eng-bench（Harbor）。DAB（`evals/dab.py`）的会话时限这次不动，记为后续项。

## Authority

- May edit: `evals/harbor_agent.py`、`src/data_agent/agent.py`（主循环、CLI 参数）、`src/data_agent/cli.py`（若 CLI 参数在这里）、`src/data_agent/verifier_v2/hooks.py`、`src/data_agent/verifier_v2/dispatch.py`、`src/data_agent/verifier_v2/stop.py`、`evals/process.py`（只认 `terminal_reason: deadline` 和 `ended_unverified`）、`tests/`（`tests/acceptance/` 除外）
- Must not edit: `src/data_agent/agents/`、`src/data_agent/verifiers.py`、`skills/`、`src/data_agent/mcp_server.py`、`evals/dab.py`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话跑）
- Spend cap: $0.5（本地）

## Context

- 现状：Harbor 用 `asyncio.wait_for(run(...), timeout)` 掐时（`harbor/trial.py:651-662`），`run()` 不收超时值；取消后容器里的进程没被杀（FMEvAdp 到 4009–4016 s 还在调工具，评分约 4002 s 开始）；没有 `result` 事件，`populate_context_post_run` 提前返回（`harbor_agent.py:372-373`），花费没记。
- 先例：Harbor Cline agent（`cline.py:83,396-415,1150`）。榜单静态检查只拦 `override_timeout_sec`、`max_timeout_sec` 和各类 multiplier（data-eng-bench `leaderboard/src/leaderboard/ci/static_analysis.py:64-70,128`），不拦普通 kwarg。
- 挂载点：主循环 `agent.py:957-963`（预算上限 `capped` 的写法在 `:502`，照着写 `result`）；派单 PreToolUse `verifier_v2/hooks.py:81-100`；Stop `hooks.py:148-168`。`AgentDefinition` 没有时长字段（SDK `types.py:108-126`），验证器只能靠主循环的截止时间。
- `harbor_agent.py:299` 把退出码 1 当正常结束；停下时的退出码按现有约定选，测试钉住。
- 阈值依据：t47-screen 14 个验证器窗口有 13 个 ≤970 s；150 s 对应网关单条语句上限（`gateway.py:21`）。两个数都做成常量或参数，便于测试注入。

## Acceptance

- [ ] 单元测试注入时钟（`now: Callable[[], float]`）：剩余 <1000 s 时派单被拒，理由含剩余秒数；同时 Stop 不拦截，并发出 `ended_unverified`；剩余 ≥1000 s 时行为和现在一样。
- [ ] 单元测试：假的消息流睡过截止时间，主循环写出 `result`（`terminal_reason: "deadline"`），记录未结束的工具调用，按约定的退出码退出（修复前会失败）。
- [ ] 单元测试：Harbor agent 把 `agent_timeout_sec`（默认 4000）传给 CLI；kwarg 改了，传的值跟着变。
- [ ] `evals/process.py` 把 `deadline` 结束的 trial 计入报告，不算基础设施错误。
- [ ] `make check` 通过。

## Evidence required

- 测试输出。
- 主会话的真实运行：服务器 `pos-operations` × 1（full），`--ak agent_timeout_sec=1500`（Harbor 自己的 4000 s 不变）。约 1350 s 出现 `result(deadline)`；没有 `AgentTimeoutError`；花费有记录；reward 正常打分。命令和输出贴到 Comments。

## Stop conditions

- 停下的写法必须改验证器提示词或 Skill。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
