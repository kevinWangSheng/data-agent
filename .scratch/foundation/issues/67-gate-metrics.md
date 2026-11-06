# 67 — `process.py` 统计第 1 段闸门要的指标

Status: ready-for-agent
Blocked by: 59, 64
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06 的补充，"闸门的口径"）
Claimed by: —
Branch: —

## Goal

统一评估第 1 段的闸门要按固定口径比较机械指标。`evals/process.py` 现在只统计其中一部分；补齐后，编排会话只跑命令读数，不再临时写脚本数。

## 已和用户定下的（2026-11-06，grilling Q1）

- 每个 trial 新增统计：
  - `ended_with_unresolved`、`ended_disputed`（64 新加）是否发生；
  - 守卫拒绝次数：`tool_end` 里被 PreToolUse 拒绝的调用（内容含 Denied 的那类，按现有事件的实际写法认）；
  - 超长输出次数：`tool_end` 内容以 "Error: result (… characters) exceeds maximum allowed tokens" 开头的；
  - 验证器派单：次数，以及每次派单的模型数（读 59 的 `verifier_dispatch` 事件：n、models、source）。
- 这些数进 `trial_metrics`、`task_metrics`、`job_metrics`（每 trial 平均），以及 `evals report` 的 dev `process` 段。
- 旧的事件文件（没有 59/64 的事件）照常读：派单、争议那几项记为 None（不是 0），其余照数。accept-2 的事件文件要能算出守卫拒绝和超长输出。

## Authority

- May edit: `evals/process.py`、`evals/report.py`（只为把新字段放进 dev 报告）、`tests/test_eval_process.py`（或现有的 process 测试文件）、新的测试文件、`tests/fixtures/` 下的小样本事件
- Must not edit: `src/`、`skills/`、`evals/ablate.py`、`evals/compare.py`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- `evals/process.py`：`trial_metrics` 在 :111 起，现在只认 `ended_unverified`（:149）和截止自停；`task_metrics` :311、`job_metrics` :331、`timing_summary` :211。
- 事件名：`ended_with_unresolved` 在 `src/data_agent/verifier_v2/hooks.py` 的 Stop 段（约 :194-222）；`ended_disputed`、`verifier_dispatch` 由 64、59 新加，以合入后的代码为准。
- 超长输出的原文例（accept-2 事件文件）："Error: result (665,331 characters) exceeds maximum allowed tokens. Output has been saved to …"。注意别把内容里正常出现的 "truncated" 当成超长。
- 本地取证数据在主 checkout：`/Users/shenghuikevin/dev/AI/data-agent/runs/accept-2`、`/Users/shenghuikevin/dev/AI/data-agent/runs/t47-screen3`（`runs/` 不进 git，worktree 里没有，用绝对路径）。只用开发集 job；不看留出集逐题细节（ADR-0004）。

## Acceptance

- [ ] 新测试：一个含 `ended_with_unresolved`、`ended_disputed`、两次守卫拒绝、一次超长输出、两次 `verifier_dispatch`（模型数 3 和 1）的小样本 trial，各字段数对（修复前会失败）。
- [ ] 新测试：没有 59/64 事件的旧事件文件，派单和争议字段为 None，其他字段照数，不报错。
- [ ] 新测试：内容里出现普通 "truncated" 字样的 `tool_end` 不算超长输出。
- [ ] `job_metrics` 给出新字段的每 trial 平均（None 不参与平均）；dev 报告里有这些字段。
- [ ] 现有 process、report 测试全部通过；`make check` 通过。

## Evidence required

测试输出；在主 checkout 的 `runs/accept-2/jobs/full`、`runs/accept-2/jobs/baseline`、`runs/t47-screen3/jobs/full` 上各跑一次新统计，贴每 trial 平均（守卫拒绝、超长输出、`ended_*`）。

## Stop conditions

- 守卫拒绝在事件里没有稳定的写法，分不出被拒的调用。
- 必须改 `src/` 才能统计。
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
