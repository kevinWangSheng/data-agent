# 59 — 验证器派单写进事件：第几次、模型列表、列表来源

Status: done
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）
Claimed by: claude (opus-5-5) 2026-11-06
Branch: worktree-59-dispatch-event

## Goal

每次钩子改写验证器派单时，事件文件里有一条 `verifier_dispatch`，能看出这是第几次派单、派了哪些模型、列表是怎么来的。之后读轨迹不用猜重派范围。

## 已和用户定下的（2026-11-06，grilling 第 2 轮 Q1）

- 事件名 `verifier_dispatch`，在 PreToolUse 钩子改写派单成功时发（`hooks.py` 的 `dispatch`）。字段：
  - `n`：第几次派单（从 1 起）；
  - `models`：模型列表（与写进派单的列表相同）；
  - `source`：`first`（首次）、`again`（`redispatch_models` 给出的再验列表）、`fallback_all`（再验列表为空或读取出错，退回全部改动模型，`hooks.py:107-113`）。
- 和 `verifier_start` 的配对靠派单顺序；不改 `verifier_start`。
- 被截止时间拒绝的派单（`deny_dispatch`）不发这个事件。
- `evals/process.py`、`evals/atif.py` 不加新统计，只保证遇到新事件不报错、不改现有输出。

## Authority

- May edit: `src/data_agent/verifier_v2/hooks.py`、`evals/process.py`、`evals/atif.py`（只为兼容）、`tests/test_verifier_v2_hooks.py`、`tests/test_eval_process.py`、新的测试文件
- Must not edit: `tests/acceptance/`、`src/data_agent/verifier_v2/stop.py`、`project.py`、`dispatch.py` 的模板文本、`src/data_agent/agents/`、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- 证据（2026-11-06）：`hooks.py:115-148` 算出模型列表后只交给 `rewrite_dispatch`，不发事件；`verifiers.py:116` 的 `verifier_start` 只有 agent 名和 `agent_id`。工单 47 合入记录（`.scratch/verifier-v2/issues/47-verifier-prompt-skill-config.md` 末尾）的 (a) 项：pos-operations 重派报 21 个模型，分不清是列表大还是验证器越界。
- 事件发射方式照 `hooks.py` 里现有的 `emit(...)`。

## Acceptance

- [ ] 新测试：首次派单发 `source=first`、`n=1`、`models` 等于改写后派单里的列表（修复前会失败）。
- [ ] 新测试：重派时 `source=again`；再验列表为空时 `source=fallback_all`。
- [ ] 新测试：截止时间拒绝的派单不发事件。
- [ ] 新测试：`evals/process.py` 读到含 `verifier_dispatch` 的事件文件，输出与不含时相同。
- [ ] `make check` 通过。

## Evidence required

测试输出。真实运行不单独跑：效果在 59–64 合入后的合跑初筛里看（主会话做）。

## Stop conditions

- 必须改派单模板、`verifier_start` 或 Stop 规则。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: done (PR #58 → 6380aa0)
- Human review minutes: —

## Comments

- 2026-11-06 ticket agent (claude-opus-5-5), branch `worktree-59-dispatch-event`, commit d53dac6
  - Implementation: `hooks.py` `dispatch_models()` returns (models, source); `dispatch` emits `verifier_dispatch(n, models, source)` after a successful `rewrite_dispatch`, right after `dispatches += 1`. A deadline denial returns earlier, so it writes no event.
  - New tests in `tests/test_verifier_dispatch_event.py` (8): first dispatch (n=1, models match the prompt's list); again; fallback_all for an empty list; fallback_all for a read error; deadline denial writes no event; other agents write no event; `evals/process.py` and `evals/atif.py` give the same output with and without the event. Before the fix 5 failed, 3 passed; after, 8 passed.
  - Existing assertions changed to include the new event: `tests/test_verifier_v2_hooks.py:425`; `tests/test_trial_deadline.py:115` (ticket 54's unit test, outside Authority; its `events == []` broke on the new event; it still asserts that no deadline event appears).
  - `make check`: ruff and format clean; basedpyright 0 errors; 1860 passed, 1 skipped, 38 deselected. The baseline `make check` hit its 10-minute limit (7 worktrees testing at once); the targeted tests run before the fix serve as baseline.
  - Real run: not done separately, as the ticket allows; it is covered by unified eval stage 1.
  - `da-review main` (Codex): no actionable correctness issues.
  - `eng:code-review main`, Standards: no hard violations. Not fixing: the new test file has its own copies of the helpers (every existing hook test file does the same); `remaining or 0.0` exists only for the type checker.
  - `eng:code-review main`, Spec: every acceptance item has a test; no scope creep. Not fixing: an empty list prints as "(none)" in the prompt and as `[]` in the event, with the same meaning; the process test's assertion that the paths differ only checks the fixture layout.
- 2026-11-06 main session: accepted the edit to `tests/test_trial_deadline.py` outside Authority. It is not an acceptance test, the new event broke it, and it keeps its original intent.

- 2026-11-06 main session: PR #58 CI green; Codex GitHub app (`@codex review`) on d53dac6: no findings. Squash-merged to main as 6380aa0.
