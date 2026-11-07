# 04 — 固化 Stage 1/Stage 2 闸门和结果合并

Status: ready-for-agent
Blocked by: 01 — 固定评测生命周期的验收接缝
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

让开发集评测明确区分正确性门槛、基础设施异常和效率告警，并且只合并同一运行契约下的 Stage 1/Stage 2 结果，形成可解释的 `full`、`baseline`、`no-verifiers` 配置选择报告。

## Authority

- May edit: `evals/process.py`、`evals/report.py`、评测合并/比较 helpers、对应测试文件、必要的 ADR/report wording
- Must not edit: acceptance tests from ticket 01 after they are merged、agent runtime、verifier implementation、holdout sampling、其他工单文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0 for implementation tests; no development or holdout batch

## Context

- Spec: `../spec.md`
- Stage 1 currently reports target success, verifier wall time, dispatch model counts, disputed/unverified outcomes, guard denials, and oversize outputs.
- Stage 2 is valid only when its code, Skill, benchmark, model, machine, split, task set, and request parameters match Stage 1.
- The current `pos-operations` redispatch line is an efficiency warning for Stage 2, while infrastructure errors and correctness failures remain blocking conditions.
- `no-verifiers` is retained for product selection, not for per-component holdout attribution.

## Acceptance

- [ ] Reports distinguish correctness success/failure, infrastructure exclusions, and efficiency warnings.
- [ ] The `pos-operations` redispatch warning is visible as a failed efficiency line without being relabeled as a correctness failure.
- [ ] `full_redispatches` 按人定规则计数（2026-11-07）：对同一 trial，后续 dispatch（n≥2）若 `source="fallback_all"`，直接记为全量重派；若 `source="again"`，只有当它的模型集合与首次 dispatch 的模型集合相同才记为全量重派。集合比较，模型顺序不影响；数量相同但模型名不同不算全量。新测试覆盖：fallback_all 计入；again 且集合相同计入；again 集合相同但顺序不同计入；again 数量相同但模型不同不计入；首次 dispatch 不计入。
- [ ] Stage 1 and Stage 2 results with any contract mismatch are rejected rather than merged.
- [ ] Matching partial jobs merge into the expected per-task trial counts without mixing configurations or conditions.
- [ ] The three development configurations produce a report sufficient to choose the product default using success, cost, and process evidence.
- [ ] Holdout reports remain aggregate-only and do not expose per-task holdout details.
- [ ] Tests from ticket 01 pass unchanged.
- [ ] `make check` passes.

## Evidence required

Fixture-based process/report output for passed gates, efficiency warnings, infrastructure exclusions, mismatched Stage 1/Stage 2 requests, and valid merges. No paid run is required.

## Stop conditions

- The existing event schema cannot support the required distinction without changing agent runtime events outside this authority.
- A proposed merge rule would allow different commits, models, or task sets to be combined.
- The requested product-selection report would require holdout per-task data.
- The second review round still finds a gate-classification or cross-condition merge defect.

## Failure handling

Set `Status: ready-for-human` and add a Comment with the fixture, the mismatched fields or misclassified gate, and the unresolved ADR/spec conflict.

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
