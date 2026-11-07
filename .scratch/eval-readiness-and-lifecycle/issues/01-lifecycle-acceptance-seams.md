# 01 — 固定评测生命周期的验收接缝

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 01-lifecycle-acceptance-seams)
Branch: worktree-01-lifecycle-acceptance-seams

## Goal

在 FakeHarbor、process/report 和 CLI acceptance 接缝上固定评测生命周期的外部契约，供后续实现工单共同遵守。此工单只建立验收测试，不实现生产逻辑。

## Authority

- May edit: `tests/test_eval_ablate.py`、`tests/test_eval_process.py`、`tests/test_eval_report.py`、`tests/acceptance/test_seam_c_eval_cli.py`，以及为测试提供最小 fixture 的新测试文件
- Must not edit: `src/`、`evals/`、`docs/`、`.claude/`、`CODING_STANDARDS.md`、其他工单文件
- Server access: none
- Spend cap: none; no model calls

## Context

- Spec: `../spec.md`
- Existing Harbor replacement seam: `tests/test_eval_ablate.py`
- Existing CLI contract seam: `tests/acceptance/test_seam_c_eval_cli.py`
- Existing process and report metrics: `tests/test_eval_process.py`、`tests/test_eval_report.py`
- The current lifecycle must distinguish infrastructure errors from logical agent failures, retain dropped-trial spend, and keep Stage 1/Stage 2 evidence tied to one run contract.

## Acceptance

- [ ] Tests cover a readiness failure that prevents a real job from starting.
- [ ] Tests cover a bounded readiness warning that is reported without pretending the run is fully clean.
- [ ] Tests cover reuse of completed trials and resume of interrupted or infrastructure-error trials.
- [ ] Tests prove logical agent failures remain scored failures and are not silently retried.
- [ ] Tests cover dropped-trial spend accounting and exclusion from scored results.
- [ ] Tests cover rejection of Stage 1/Stage 2 merges with mismatched commit, Skill, benchmark, machine, configuration, task set, or run parameters.
- [ ] Tests cover separate reporting of correctness gates and efficiency warnings, including the `pos-operations` redispatch warning.
- [ ] Existing holdout privacy and CLI acceptance behavior remains covered.
- [ ] Tests are reviewed and merged before implementation tickets 02–05 start.

## Evidence required

Test commands and output, including the new tests failing against the current implementation where applicable, plus the final passing output after the acceptance contract is reviewed.

## Stop conditions

- The agreed behavior cannot be expressed at the existing FakeHarbor or CLI seams without changing the production contract.
- A proposed test requires a real model call or holdout task detail.
- The test contract conflicts with ADR-0004, ADR-0008, or the confirmed spec.
- The second review round still finds an acceptance-contract defect.

## Failure handling

Set `Status: ready-for-human` and add a Comment describing the missing seam, the failing test, and the branch. Do not change production code to make the tests pass.

## Ledger

- Attempts: 2
- Review rounds: 2
- Model spend: $0
- Outcome: 退回已修（deadline 冒烟改告警），等人审后合并；合并后 02–05 才能开工
- Human review minutes: —
- Merged: PR #68 (a25b3b5), CI green, Codex GitHub review no findings

## Comments

- 实现：只改测试。tests/test_eval_ablate.py、test_eval_process.py、test_eval_report.py、tests/acceptance/test_seam_c_eval_cli.py，新增 tests/lifecycle_events.py（共用的 verifier_dispatch 事件 fixture）。分支 worktree-01-lifecycle-acceptance-seams，提交 04649ad、45518f2。
- 验收项对照：
  - readiness 失败阻断：由已有的 `test_a_failed_smoke_stops_before_any_job` 和 smoke scrub 不完整用例覆盖（退出 2、不起 job，通过）；deadline 自停归入告警（见退回修复）。
  - 有界 readiness 告警：冒烟里全量重派 → 跑完，ablation.json 的 `readiness` 为 {clean: false, warnings: [{check, reason}]}，md 和输出写明；每个配置只 1 个冒烟。xfail 02。另有"没有发现时 clean"一条，xfail 02。
  - 复用与续跑：完成的复用、中断的续跑、infra 错误 -f 续跑。已有测试覆盖，新增 infra 与逻辑失败同在一个 job 的情况。
  - 逻辑失败（reward 0、AgentTimeoutError）计分、不重试：新测试，现有代码已通过。
  - 丢弃 trial 的花费：两轮 infra 各算一次，新测试已通过。中断 trial 的花费在 resume 后仍计入，xfail 03（Harbor job.py:286-288 删掉没有 result.json 的 trial 目录，keep_spend 只拷 infra 错误；现在 spend 看到 $6 而不是 $7）。ablation 每个配置行的 `infra_excluded` {trials, cost_usd}，xfail 04。
  - Stage 1/2 合并：一张参数表，commit、Skill、config、benchmark、machine、model、每题 3 次已拒绝，通过；request.json 的 agent_kwargs 不同或缺失、split 不同，xfail 04。各 OUT 的 tasks 列表不同时照样合并（正例已通过）。
  - 正确性与效率分开：dev report.json 的 `gates.correctness` {successes, trials}，`gates.efficiency` {passed, warnings[{line, task, trial}]}，用 pos-operations [21,6,5]/[21,17,3,1,21]/[21,21,21] 当 fixture；process 每个 trial 的 `full_redispatches`。xfail 04。
  - 留出集隐私：seam C 新增一条，留出集 trial 带全量重派和 deadline 事件时报告仍只有汇总，通过。
- 测试证据：基线 173 passed（ablate/process/report/seam C/merge）。make check：1956 passed, 1 skipped, 15 xfailed。用 --runxfail 跑，15 条都在预期断言失败：KeyError readiness/gates/infra_excluded/full_redispatches；deadline 冒烟和 3 条合并用例 assert 0 == 2；中断 trial 的 spend 是 $6.0000 而不是 $7.0000。
- 真实运行：本工单不改生产代码，没有可运行的行为，$0。
- 审查：
  - da-review main（Codex）：没有可执行的问题。
  - eng:code-review main 第 1 轮修复（45518f2）：共用 dispatch fixture；readiness 和 efficiency 改成按键断言，给 02/04 留出加字段的余地；split 用例改成真实的 "holdout" 值；不再改 parametrize 的 dict；fixture 改名 dev_manifest；task-set 用例改名 task-set-trial-count。
  - 不修：合并参数表里与 test_eval_merge 重复的用例（为了在一处列出整份契约；test_eval_merge 不在本工单权限内）；spend 输出格式的字面量（已有测试同样写法）；correctness 不设 ≥12/15、墙钟等阈值（这些是每次运行定的，交 04 决定）；dev 报告不列 infra 排除（report 本来就拒绝含 infra 错误的 job，排除数放在 ablation 表）；资源快照和未缓存重复工作的检查没有 FakeHarbor 接缝，交 02 用自己的测试。
- 需要人定：
  1. 冒烟 deadline 自停判阻断还是告警（审查指出 ADR-0008 闸门口径是"列为异常、不直接判不过"）。
  2. 合并时各 OUT 的 request.tasks 可以不同（对 spec 里"task set"的解读）。
  3. 02 的重派告警和 04 的 full_redispatches 是同一规则：建议定先后，或让 02 Blocked by 04。
- 人定（2026-11-06）：
  1. 冒烟 deadline 自停改判告警，不阻断（ADR-0008:140"列为异常、不直接判不过"；spec"个别效率发现只记告警"）。
  2. 确认：合并时各 OUT 的 request.tasks 可以不同，task set 检查 = 每道 split 题恰好 3 个 trial。
  3. 02 改为 Blocked by 04；02 复用 04 在 process 里的 full_redispatches，不重复实现。
- 退回：冒烟 trial 在 deadline 自停目前被测成阻断（退出 2、不起 job），按人定第 1 条改成告警：批次照跑，`readiness` 为 {clean: false, warnings: [{check, reason}]}，reason 写明 deadline 自停；仍 xfail 02。其余测试不动。在原分支 worktree-01-lifecycle-acceptance-seams 上修，提交后照第 5 步两道审查。
- 退回修复（767236e，分支 worktree-01-lifecycle-acceptance-seams）：冒烟 trial 在 deadline 自停改为告警。测试改名 `test_readiness_a_smoke_trial_stopped_at_its_deadline_is_a_warning_the_table_carries`：退出 0，冒烟和 job 各 2 个，正确率是 trial 自己的；ablation.json 的 `readiness` 为 {clean: false, warnings: [{check, reason}]}，reason 含 "deadline" 和 config 名；输出和 ablation.md 写明 warning/deadline。仍 xfail 02。其余测试未动。
- 测试证据（第 2 轮）：`--runxfail -k deadline_is_a_warning` 在预期位置失败：`KeyError: 'readiness'`（test_eval_ablate.py:1480），退出码、冒烟/job 数、正确率断言已先通过。make check：ruff、format、basedpyright 0 errors，1956 passed、1 skipped、15 xfailed。pre-commit 全过。
- 真实运行：只改测试，$0。
- 审查第 2 轮：
  - da-review main（Codex），P2：`tests/lifecycle_events.py` 的 `dispatches()` 只发前缀列表、来源只有 first/again，测不出"同数量、不同模型"，也没测 `fallback_all`。不修：退回要求其余测试不动；"全量重派"的定义属 04。列为待人定。
  - eng:code-review main，Spec：退回要求全部满足；无范围外改动。提示 02 新增的系统性阻断检查要自带测试（round 1 已交 02）。
  - eng:code-review main，Standards：无硬性违规。不修：deadline 与 redispatch 告警测试约 12 行断言重复（抽 helper 会改 redispatch 测试）；seam C 内联 dispatch 事件（acceptance 目录导入不到 tests/lifecycle_events）；[21,6,5] 等 fixture 写在 4 处、参数成团、STAGE_REQUEST 用 `|=`（round 1 已审）。
- 需要人定：「全量重派」按模型身份还是数量判，`source=fallback_all` 是否计入。
- 人定（2026-11-07）：「全量重派」规则：对同一 trial，后续 dispatch（n≥2）若 `source="fallback_all"`，直接记为全量重派；若 `source="again"`，只有当它的模型集合与首次 dispatch 的模型集合相同才记为全量重派。集合比较，模型顺序不影响；数量相同但模型名不同不算全量。测试写进 04 的验收标准，本工单不再改，可以合并。
- 合并（2026-11-07）：PR #68 squash 为 a25b3b5。CI 两项全绿；Codex GitHub app 对 767236e 审查完成、无意见。
