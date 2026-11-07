# 02 — 增加轻量 readiness 检查

Status: ready-for-agent
Blocked by: 01 — 固定评测生命周期的验收接缝; 04 — 固化 Stage 1/Stage 2 闸门和结果合并
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

在大批量评测启动前，用低成本检查发现明显的环境、资源、超时、缓存和异常全量重派问题；检查保持浅而有界，不变成另一轮完整评测。

## Authority

- May edit: `evals/ablate.py`、相关 `evals/` helpers、对应测试文件；必要的本地运行说明
- Must not edit: acceptance tests from ticket 01 after they are merged、`src/data_agent/`、holdout sampling、其他工单文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none for implementation; any real evidence must be a bounded, human-approved local or single-task check
- Spend cap: $0 for implementation tests; no batch evaluation

## Context

- Spec: `../spec.md`
- Existing preflight checks identity, balance, wheels, one smoke task per configuration, scrub, and fail-fast infrastructure errors.
- The current smoke path uses one development task and therefore may miss task-specific long-tail behavior such as `pos-operations` redispatch.
- 重派告警复用 04 在 `evals/process.py` 里的 `full_redispatches` 计数，不另写一套规则（人定 2026-11-06）。
- 冒烟 trial 在 deadline 自停判告警，不阻断（人定 2026-11-06，ADR-0008:140）。
- Readiness must classify systemic environment/execution failures as blockers and isolated efficiency findings as warnings.

## Acceptance

- [ ] The preflight performs the existing identity, balance, smoke, scrub, and fast-fail checks without weakening them.
- [ ] The preflight adds bounded resource and timeout sanity checks that do not start a full evaluation batch.
- [ ] The preflight can inspect available evidence for obvious repeated uncached work and abnormal full-model redispatch without requiring a model call.
- [ ] A systemic readiness failure prevents the first real job from starting.
- [ ] An isolated efficiency warning is recorded with its reason and does not masquerade as a correctness failure.
- [ ] The checks have an explicit bounded time/cost policy and do not run one probe per task or per trial.
- [ ] Tests from ticket 01 pass unchanged.
- [ ] `make check` passes.

## Evidence required

Test output and a no-model demonstration of blocker versus warning behavior. If a real single-task check is needed, record the exact command and cost separately; do not start Stage 2.

## Stop conditions

- The proposed check requires broad profiling or a full model batch.
- A readiness check cannot distinguish a systemic failure from an isolated warning.
- The check needs to alter agent or verifier behavior rather than evaluator orchestration.
- The second review round still finds a contract or cost-bound violation.

## Failure handling

Set `Status: ready-for-human` and add a Comment with the check that was attempted, its observed output, and why the bounded readiness contract could not be implemented safely.

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
