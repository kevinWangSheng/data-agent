# 03 — 固化断点续传和费用记账行为

Status: done
Blocked by: 01 — 固定评测生命周期的验收接缝
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 03-resume-spend-contract)
Branch: worktree-03-resume-spend-contract

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: in-review，提交 c2d3c78、455569c
- Human review minutes: —

## Comments

- 真实运行：Spend cap $0，只用 FakeHarbor。`uv run pytest tests/test_eval_ablate.py -v -k "interrupted or one_resume or logical or infrastructure or dropped or other_conditions or another_benchmark or another_model or agent_kwargs_on_the_same or another_split or usage_limit or streak or lack_versions"`：33 passed、1 xfailed（04 的）。覆盖中断、infra 错误、逻辑失败、条件不一致、花费记账、额度用尽、fail-fast。make check：1960 passed、1 skipped、14 xfailed（02、04）。
- 改动：
  - c2d3c78：Harbor `job resume` 删掉没有 result.json 的 trial 目录（harbor/job.py:280-288）。`ensure_job` 在续跑前把这些 trial 和 infra 错误 trial 一样保存到 `OUT/dropped/<config>/`。验收测试只删了 xfail 标记（主会话已核对 diff）。新增一条：同一次续跑里既有无事件的中断 trial 又有 infra 错误。
  - 455569c（审查第 1 轮）：Harbor 在删除前退出（续跑被拒或启动崩溃）时，trial 同时在 jobs/ 和 dropped/，下一次 spend guard 重复计数（花了 $6 记成 $8）；`keep_spend` 改为移动事件文件而不是复制，新测试修复前失败、修复后通过。新增 `evals.harbor.events_in(trial_dir)` 去掉重复的事件查找。`evals/accept.py` 改 docstring 并加一条 dropped/ 里有中断 trial 的测试。
- 审查：
  - da-review main（Codex）：第一次跑到 10 分钟上限被停、无输出；重跑结果 "No actionable correctness issues"。
  - eng:code-review main 第 1 轮：Standards 无违规，重复的事件查找已修；Spec 每条验收标准都对到通过的测试，重复计数路径和 accept 文案已修。
  - 不修：`interrupted()` 留在 ablate.py（挪到 harbor.py 只是取舍）；没有 result.json 的子目录都算 trial（正好是 Harbor 删除的集合）；`evals accept` 对丢了中断 trial 的一轮仍报"旧一轮有基础设施错误"——判定 rerun-old 是保守的、符合 spec 把中断和 infra 错误归一类，只是措辞不精确，改它要动已有 accept 测试断言。
  - 范围：`evals/accept.py`（docstring）、`evals/harbor.py`（events_in helper）在 Authority"评测 spend/resume helpers"之内（主会话核对：只有 docstring 和一个 spend 用的 helper）。
- 合并（2026-11-07）：PR #69 squash 为 29ea8c3。CI 两项全绿；Codex GitHub app 对 455569c 审查完成、无意见。
