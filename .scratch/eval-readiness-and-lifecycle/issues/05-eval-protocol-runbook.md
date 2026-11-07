# 05 — 对齐评测协议和后续运行手册

Status: done
Blocked by: 02 — 增加轻量 readiness 检查; 03 — 固化断点续传和费用记账行为; 04 — 固化 Stage 1/Stage 2 闸门和结果合并
Spec: ../spec.md
Claimed by: claude (opus-5-5) 2026-11-07
Branch: worktree-05-eval-protocol-runbook

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
- [ ] ADR-0008:142 的"一票否决：infra_error 不为 0"改为只按未解决的 infra 错误判；已重跑出结果的只报数量和花费（人定 2026-11-07，实现见 04）。措辞写"闸门通过 = 没有未解决的 infra 错误"，不写"没有 infra 错误"；并写明 ADR 布局下未解决的错误会让 `check_complete` 直接拒绝出报告（见 04 已知限制）。
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

- Attempts: 1
- Review rounds: 2（da-review，然后 eng:code-review）
- Model spend: $0
- Outcome: in-review；提交 48a089e、c91d16e、126175a
- Human review minutes: —

## Comments

- 改动（只改文档）：`docs/adr/0008-change-acceptance.md`（第 142 行原地改 + 新增"补充 2026-11-07"）、`docs/plan.md` §10、`docs/setup.md`（`ablate` 的 preflight、续跑、合并与 select）。不改代码和测试；已有测试覆盖这些行为，所以没加文档契约测试。
  - 第 142 行：一票否决改为"未解决的 infra 错误"；"闸门通过 = 没有未解决的 infra 错误"；pos-operations 那条线改为效率告警、不再一票否决，Stage 2 仍要人签字。
  - 补充 2026-11-07：readiness 范围和成本上限（不调模型、不加 Harbor 运行，一次 `docker info` + 至多一次磁盘检查 + 每个配置已有的冒烟 trial）；续跑边界和花费只计一次；三道闸门分开报；ADR 布局下 `check_complete` 拒绝有未解决错误的报告；Stage 1/Stage 2 合并条件、改代码后作废；Stage 1 = 正确性/infra/验证器墙钟线通过、效率线不过（告警，不是全部通过）；三配置选择和六配置留出集消融分开；四个人工决策点（Stage 2、留出集、data-eng-bench 全量、DAB）。
- 真实运行（第 3 步，$0）：`PYTHONPATH=. uv run python runs/05/stage1_gates.py <主 checkout>/runs/u1-target/jobs/full <主 checkout>/runs/u1-full-rest/jobs/full`（调用 evals.report.gates / unresolved_infra / excluded_trials）。
  - u1-target：正确性 14/15；infra 通过，unresolved 0；效率 passed=false，4 条告警（pos-operations NvPFUAo ×1、p8TpFk3 ×2；dbt-fix-division-by-zero tzXXxtE ×9、xFESH3u ×4）。
  - u1-full-rest：14/25；infra 通过；效率 passed=false，6 条告警（6 道题各 ×1）。已写进 ADR。
  - `evals report runs/u1-target/jobs/full ... --split dev` → "no trial results for 25 of 30 dev tasks"，单独的 Stage 1 出不了报告（已写进 ADR）。
  - `evals report --help`、`evals select --help` 与 setup.md 里的命令示例一致。
  - 另外更正：trial 7cGbUhG 的 21 模型派单是首次派单，不是重派。
- make check：All checks passed!，2033 passed、1 skipped（基线相同）。
- 审查：
  - da-review main（Codex），两条 P2，都是文档措辞，c91d16e 修复：(1) `--skip-smoke` 时四项冒烟告警检查不跑（ADR 和 setup 已写明）；(2) 完全没有 result.json 的 job 续跑时不核对代码 commit / Skill 版本（`check_conditions` 没有版本头可比）。在代码里修会改评测器行为，超出本工单 Authority，记为已知限制：改完代码一律用新的 `--out`。
  - eng:code-review main，Spec，都是措辞，126175a 修复：第 142 行注明 pos 线不再一票否决；被丢弃 trial 的 result.json 是复制、只有事件文件是移动；合并时忽略 request.json 里的 `tasks`（ADR 和 plan 不再把题目集合列为必须一致的字段）；`check_complete` 拒绝报告的真实条件（合并后各 job 计划的 trial 数加起来为 3）；版本范围 "01–04"。
  - eng:code-review main，Standards，无硬性违规。已修：版本范围不一致；¥47 标明是 Stage 1 实际花费。不修：原地改第 142 行破坏只追加的历史（本工单验收标准明确要求）；阈值和续跑边界在三份文档里都写（验收标准要求 ADR、plan、setup 各写一遍）；ADR 里有运行细节、带日期的状态、引用 HANDOFF（和本 ADR 已有的补充节一致，状态行带日期）。
- 已知限制：(1) 没有 result.json 的续跑不核对代码版本（见上面 Codex 第 2 条；要修需另开代码工单）；(2) Stage 2 在 b7c6071 上跑还是先重跑 Stage 1，仍待人定。
- PR #72 Codex GitHub 意见（P2，ADR-0008 第 163 行"未解决的 infra 错误会让 check_complete 直接拒绝出报告"不对）：不属实。ADR 原文限定"按本 ADR 的布局（每道题在合并的各 job 里计划的 trial 数加起来正好是 3）"。一个 job 某题带结果的 trial 不会多于它计划的数（Harbor n_attempts），所以未解决的丢弃 trial 让该 job 少一个、合并总数 <3，`check_complete` 拒绝（evals/report.py:166-172）。Codex 的反例要另一个 job 补上第三个 trial，即各 job 计划数加起来超过 3，不在所述布局内；这正是 04 测试里的 "fill" 情形，报告能写出且 `unresolved: 1` 使闸门不过，与 ADR 不矛盾。
- 合并（2026-11-07）：PR #72 squash 为 5d8b1c2。CI 两项全绿；Codex GitHub app 一条 P2 判为不属实（理由见上）。
