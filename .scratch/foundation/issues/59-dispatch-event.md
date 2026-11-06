# 59 — 验证器派单写进事件：第几次、模型列表、列表来源

Status: ready-for-agent
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
