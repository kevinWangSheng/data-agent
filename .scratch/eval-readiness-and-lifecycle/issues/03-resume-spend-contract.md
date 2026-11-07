# 03 — 固化断点续传和费用记账行为

Status: ready-for-agent
Blocked by: 01 — 固定评测生命周期的验收接缝
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

让评测在中断或基础设施失败后安全续跑：已完成 trial 不重复运行，基础设施错误 trial 重跑但不计分，丢弃的 trial 费用仍计入预算，普通逻辑失败不会被自动隐藏。

## Authority

- May edit: `evals/ablate.py`、评测 spend/resume helpers、对应测试文件和必要的 runbook 文本
- Must not edit: acceptance tests from ticket 01 after they are merged、agent runtime、Harbor task definitions、其他工单文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0 for implementation tests; use FakeHarbor fixtures only

## Context

- Spec: `../spec.md`
- The evaluator already stores jobs under `OUT/jobs`, dropped artifacts under `OUT/dropped`, and run identity under `OUT/records`.
- Resume must continue only under a matching benchmark commit, machine, model, configuration, task set, and request contract.
- Account exhaustion and infrastructure exceptions are not agent-quality scores.

## Acceptance

- [ ] A completed job is reused without rerunning completed trials.
- [ ] An interrupted job resumes only missing work.
- [ ] Infrastructure-error trials are retained under dropped artifacts, counted for spend, removed from scoring, and rerun under the same request.
- [ ] A logical agent failure remains in the scored table and is not automatically retried.
- [ ] A changed commit, benchmark, machine, model, configuration, task set, or request parameter refuses reuse/resume.
- [ ] Spend accounting does not count a dropped trial twice when the replacement trial runs.
- [ ] Resume and stop behavior remains bounded when the account is exhausted or infrastructure errors repeat.
- [ ] Tests from ticket 01 pass unchanged.
- [ ] `make check` passes.

## Evidence required

FakeHarbor test output covering interrupted, infrastructure-error, logical-failure, mismatch, and spend-accounting cases. No paid run is required.

## Stop conditions

- Harbor does not expose enough state to distinguish infrastructure errors from logical failures safely.
- Reuse would require ignoring a recorded condition or mixing different run contracts.
- A fix would change scoring semantics outside this spec.
- The second review round still finds a double-counting or silent-retry path.

## Failure handling

Set `Status: ready-for-human` and add a Comment with the reproduction fixture, the resume state, and the unresolved contract ambiguity. Do not delete or reset existing run artifacts.

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
