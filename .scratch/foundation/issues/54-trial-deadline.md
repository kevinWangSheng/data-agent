# 54 — trial 截止时间：剩余不足时不再派验证器，到点自己停下

Status: done
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

- May edit: `evals/ablate.py`（只加 `--agent-kwarg KEY=VALUE` 透传成 Harbor `--ak`，主会话 2026-11-05 补）、`evals/harbor_agent.py`、`src/data_agent/agent.py`（主循环、CLI 参数）、`src/data_agent/cli.py`（若 CLI 参数在这里）、`src/data_agent/verifier_v2/hooks.py`、`src/data_agent/verifier_v2/dispatch.py`、`src/data_agent/verifier_v2/stop.py`、`evals/process.py`（只认 `terminal_reason: deadline` 和 `ended_unverified`）、`tests/`（`tests/acceptance/` 除外）
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

- Attempts: 2
- Review rounds: 3 (round 1: da-review + eng:code-review → 02899cf; round 2: da-review on the ablate change → aed2b62; round 3: confirming da-review, clean)
- Model spend: $0 (no model calls)
- Outcome: implemented; unit tests, local deadline run and ablate CLI check pass; server real run pending (main session)
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（142f041、02899cf）

- 2026-11-05 (claude, worktree 54-trial-deadline): implemented; commits 142f041 and 02899cf on worktree-54-trial-deadline.
  - What changed:
    - Harbor kwarg `agent_timeout_sec` (default 4000, must be > 0) is passed to the CLI as `--agent-timeout-sec`. The CLI turns it into a monotonic deadline at startup.
    - `verifier_v2/hooks.py`: with less than `VERIFY_MIN_REMAINING_S = 1000` left, PreToolUse denies the dispatch of `verifier` and `value-verifier`. The reason says "Only N s are left before the trial deadline… Do not verify again: finish with the results you have". Other subagents are unaffected. In the same window, Stop lets the run end and emits `ended_unverified` (reason, reasons, remaining_s), before the blocks counter is touched, so deny and block can't loop.
    - `agent.py`: an `asyncio.timeout` set to the deadline minus `DEADLINE_MARGIN_S` (taken from the gateway's `TIMEOUT_SECONDS`, 150) wraps the stream. When it fires, the SDK stream is closed and `result` is written with `subtype: error_deadline`, `terminal_reason: deadline`, price-table spend and `open_tool_calls` (tool calls started but not answered). The outcome is "unfinished", so the CLI exits 1 and the Harbor adapter turns that into 0. A budget stop still takes priority, and a ResultMessage received before the timer fires keeps the SDK's own result.
    - `evals/process.py`: a trial row gets `stopped: "deadline"` and `ended_unverified`. Per-task counts `stopped_by_deadline` and `ended_unverified` are added. The trial has no Harbor exception, so `is_infra_error` is false (tested).
  - Tests: `make check` gives 1740 passed, 1 skipped, 1 xfailed; ruff, format and basedpyright are clean. The baseline before the change was 1725 passed. New tests are in `tests/test_trial_deadline.py` (hooks with an injected clock; the main loop with a stream that sleeps past the deadline, which fails before the fix; CLI deadline). `tests/test_harbor_agent.py` gains the kwarg default, an override passed as the string "1500", and refusal of values ≤ 0. `tests/test_eval_process.py` gains the deadline trial and `ended_unverified`. `tests/test_eval_diverge.py` gets the two new per-task keys in its exact dict.
  - Local real run, no model spend (the endpoint is a dead local port, so the SDK keeps retrying until the stop): `bash /tmp/54-real/run.sh`, which runs `data-agent run --type query … --agent-timeout-sec 160` and so should stop about 10 s after start.
    - Output: `exit=1 wall=17s` / `error: the run stopped itself at the trial deadline`.
    - Events: version → dictionary → status(started) → `result{subtype: error_deadline, terminal_reason: deadline, cost_basis: price_table, open_tool_calls: []}`.
    - No CLI or mcp-server process was left afterwards. About 6 s of the wall time is the SDK's own close: 5 s grace after stdin EOF, then SIGTERM (`subprocess_cli.py` close).
  - Not yet done: the server run in Evidence required (`pos-operations` × 1, full, `--ak agent_timeout_sec=1500`) is for the main session.
  - Review 1, `da-review main` (Codex, completed):
    - [P1] "Setup (snapshot, dictionary) is not covered by the deadline." Not fixed: the timer is computed from the absolute deadline, so setup time is already subtracted. The dictionary build has its own 60 s cap (`context_tools.py:89`) and the snapshot only copies logic files. Setup can only reach Harbor's timeout with `agent_timeout_sec` under about 210 s, which is not a supported use.
  - Review 2, `eng:code-review main` (Standards and Spec sub-agents):
    - Standards: no hard violations. Fixed in 02899cf: the margin now comes from `gateway.TIMEOUT_SECONDS`; the duplicated result shape is now the shared `Spend.stopped_fields`; `remaining_s` is renamed `short_on_time_s`.
    - Standards, not fixed: the "deadline" string literals follow the existing `STOPS` style in process.py, and agent.py and evals stay decoupled. Reading the tool fields twice in the PreToolUse hook is minor. The `now` parameter is the clock-injection seam the ticket asks for.
    - Spec, every Acceptance item is implemented and tested. Not fixed:
      - (1) A verifier still running at the stop is not priced (`Spend` only reads finished transcripts); recorded as a known limitation.
      - (2) The 1000 s check measures to the deadline, not to T−150, so a verifier sent with 1000–1120 s left gets 850–970 s; this follows the ticket's wording.
      - (5) The SDK close has no timer of its own, but the SDK bounds it (about 20 s worst case, per its docstring), well inside the 150 s margin.
      - (6) If the SDK turns the cancel into an ordinary exception, an extra `error(source=sdk)` event can appear; nothing counts it as infrastructure.
    - Spec, scope: the positive-value checks are small and inside Authority. No files outside Authority were edited.
  - Known limitations: no rule against starting a new `dbt run` close to the deadline (as agreed; `open_tool_calls` shows what was running). DAB (`evals/dab.py`) has no deadline (follow-up). Spend of a verifier still running at the stop is not counted.

### 2026-11-05 主会话（补一处 Authority）

- Evidence required 的服务器运行需要 `--ak agent_timeout_sec=1500`，但 `evals ablate` 不透传 agent kwarg（`evals/ablate.py:383-394` 的 `harbor_run_args` 只固定传 `run_config`），`da-remote start` 又只能跑 `python -m evals`，所以这次运行起不来。pos-operations 在新 main 上 1418 s 就跑完（t52-real），用默认 4000 s 触发不了截止。
- 主会话补一行 Authority：允许改 `evals/ablate.py`，只加一个可重复的 `--agent-kwarg KEY=VALUE`，原样转成 Harbor 的 `--ak KEY=VALUE`，不得覆盖 `run_config`，并配测试。这是可回退的小扩展，在最终汇总里报给人。
- 状态改回 `ready-for-agent`，在原分支上补。

### 2026-11-05 claude (worktree 54-trial-deadline): evals ablate --agent-kwarg

- Merged origin/main into the branch (c7f4461; tickets 51, 52 and 53 included) with no conflicts.
- fadacd6: `evals/ablate.py` adds a repeatable `--agent-kwarg KEY=VALUE` (argparse type `agent_kwarg`). `harbor_run_args` passes each one unchanged as `--ak KEY=VALUE` after `--ak run_config=<config>`, so it reaches both the smoke run and the jobs. A key of `run_config` (also with surrounding spaces) is refused with "--config sets run_config; an agent kwarg cannot", and a value without a key or `=` with "expected KEY=VALUE"; both exit 2 before any Harbor call.
- aed2b62 (fix for the Codex P1, see below): `check_request` records the kwargs in `request.json` as `agent_kwargs` when there are any. Rerunning the same `--out` with different kwargs (or none, or new ones added) stops with "use another --out". Without kwargs, `request.json` is written as before (`{"model"}` only), so an existing OUT stays reusable.
- Tests (tests/test_eval_ablate.py):
  - kwargs reach the smoke run and the job in order (`run_config=full`, `agent_timeout_sec=1500`, `note=a=b`);
  - without kwargs only `run_config` is passed;
  - 4 refusal cases (exit 2, message checked, no Harbor call);
  - 3 kwarg-mismatch cases on the same `--out` stop and the same request is still reused;
  - an old-style `request.json` stays valid.
- `make check`: ruff, format and basedpyright clean; 1759 passed, 1 skipped, 1 xfailed.
- Real CLI check:
  - `uv run python -m evals ablate --help` shows `--agent-kwarg KEY=VALUE  repeatable: passed to Harbor as --ak KEY=VALUE (e.g. agent_timeout_sec=1500); run_config comes from --config`.
  - `uv run python -m evals ablate --tasks-dir t --manifest m --out o --budget 1 --agent-kwarg run_config=baseline` prints `python -m evals ablate: error: argument --agent-kwarg: --config sets run_config; an agent kwarg cannot`.
  - No real Harbor job was run.
- Review:
  - `da-review main` (Codex, completed, no capacity problem) on fadacd6: [P1] "Include agent kwargs in the ablation reuse key": rerunning an existing `--out` with another `agent_timeout_sec` would reuse jobs run under a different timeout. True; fixed in aed2b62. It goes slightly beyond "只加透传", but it is part of the flag's correctness and stays inside ablate.py.
  - Confirming `da-review main` on aed2b62: "No discrete, actionable correctness issues were found."
- Next step (main session): server run, e.g. `python -m evals ablate --split dev --task pos-operations --config full --trials 1 --agent-kwarg agent_timeout_sec=1500 ...` via `da-remote start`. Expected: `result(deadline)` at about 1350 s, no `AgentTimeoutError`, spend recorded, reward scored normally.

### 2026-11-05 主会话（服务器真实运行，aed2b62）

- `t54-real`：`ablate … --task pos-operations --out runs/t54-real --budget 2 --config full --trials 1 --agent-kwarg agent_timeout_sec=1500`。1/1，$0.1686，Harbor 0 异常。568 s 时两次派验证器被拒（"Only 929 s are left before the trial deadline … finish with the results you have and end the run."），588 s 记 `ended_unverified`，随后 `result`（terminal_reason completed），正常退出，花费已记。没走到 T−150 s。
- `t54-real2`：同上，`agent_timeout_sec=600`，专门验到点停止。455 s 写出 `result`（subtype `error_deadline`，terminal_reason `deadline`，cost_usd 0.1247），Harbor 0 异常、无 `AgentTimeoutError`，花费已记。reward 0（600 s 做不完这题，符合预期）。
