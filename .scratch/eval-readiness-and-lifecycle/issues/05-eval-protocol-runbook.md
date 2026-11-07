# 05 — 对齐评测协议和后续运行手册

Status: ready-for-agent
Blocked by: 02 — 增加轻量 readiness 检查; 03 — 固化断点续传和费用记账行为; 04 — 固化 Stage 1/Stage 2 闸门和结果合并
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

把已经实现并验证的评测生命周期写回 ADR、计划和运行说明，使后续操作者知道何时可以启动 Stage 2、何时必须重做 Stage 1，以及何时才值得购买留出集和正式评测。

## Authority

- May edit: `docs/adr/0008-change-acceptance.md`、`docs/plan.md`、`docs/setup.md`、相关评测 runbook 文档和本工单自己的 tests only when needed for documentation contracts
- Must not edit: production evaluator behavior from tickets 02–04、acceptance tests from ticket 01 after they are merged、其他工单文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- Spec: `../spec.md`
- The confirmed policy is lightweight readiness, infrastructure-only automatic resume, separate correctness and efficiency reporting, same-commit Stage 1/Stage 2 merging, and deferred six-configuration holdout attribution.
- The current Stage 1 result must be described as correctness/infra lines passing with an efficiency warning, not as a clean pass of every ADR line.

## Acceptance

- [ ] ADR and plan state the lightweight readiness scope and its bounded cost.
- [ ] Setup documentation states the resume boundary and spend-accounting behavior.
- [ ] Stage 1/Stage 2 same-contract requirements and invalidation after code changes are documented.
- [ ] The `pos-operations` efficiency warning is reported without claiming that the efficiency line passed.
- [ ] The three-config development decision is separated from the later six-config holdout ablation and formal evaluations.
- [ ] Documentation names the human decision points before Stage 2, holdout, full benchmark, and DAB runs.
- [ ] Links and command examples remain consistent with the implemented evaluator behavior.
- [ ] `make check` passes.

## Evidence required

Documentation diff review plus the relevant unit/acceptance test output after tickets 02–04 are merged. No paid run is required.

## Stop conditions

- The implemented behavior and the ADR/plan cannot be reconciled without a new human decision.
- The documentation would imply a clean gate pass where an efficiency warning remains.
- A later-stage evaluation is presented as automatic rather than requiring the stated decision point.

## Failure handling

Set `Status: ready-for-human` and add a Comment identifying the contradictory behavior, document, or decision point. Do not silently change the policy to match the implementation.

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
