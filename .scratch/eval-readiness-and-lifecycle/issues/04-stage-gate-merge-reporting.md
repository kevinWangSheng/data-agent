# 04 — 固化 Stage 1/Stage 2 闸门和结果合并

Status: done
Blocked by: 01 — 固定评测生命周期的验收接缝
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 04-stage-gate-merge-reporting)
Branch: worktree-04-stage-gate-merge-reporting

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

- Attempts: 2
- Review rounds: 4（第一次：eng:code-review 加 rebase，然后 Codex；退回后：Codex，然后 eng:code-review）
- Model spend: $0
- Outcome: infra 闸门按 2026-11-07 人定只判未解决的 infra 错误；被退回 1 次（infra 闸门口径）。make check 2007 passed、3 xfailed（02 的）
- Human review minutes: —

## Comments

- 实现（7c450ea、1ecaaba、286bf91；03 合并后 rebase 到 4bf0b3d）：
  - `evals/process.py`：`full_redispatches` 按 2026-11-07 人定规则计数，按题汇总、进闸门指标；没有 dispatch 事件的旧事件文件记 None。
  - `evals/report.py`：dev 报告加 `gates`，三部分分开——正确性（成功、失败、trial 数，不设通过线，成功线由 ADR-0008 每次评测定）；基础设施（OUT/dropped 里有 infra 错误即不过，中断 trial 只报不判）；效率（每个全量重派 trial 一条 warning，有 warning 即不过；无 warning 时只有每个 trial 都有 dispatch 记录才算过，否则 unknown）。所有报告加 `infra_excluded`、`interrupted_excluded`（只有数量和 cost_usd，读 keep_spend 移走的事件文件）和 `run`（运行条件）；留出集报告仍不出现题目或 trial 名。
  - 合并 job：`request.json` 除 `tasks` 外必须一致（agent_kwargs、split、model）；缺 request 拒绝，dev request 没有非空任务列表也拒绝。
  - 新命令 `evals select REPORT_DIR... --out DIR`（`evals/selection.py`，在 `evals/__main__.py` 注册）：各配置 dev 报告并排（成功、花费、每次成功花费、三道闸门、过程均值，`full_redispatches` 记总数）。拒绝：留出集报告；commit、model、benchmark、machine、split、agent_kwargs 不同；带 Skill 的配置之间 Skill 版本不同；同一配置给两次；题目或每题 trial 数不同。`ablation.md` 同样出这张表。
  - Authority 列表外改动：`evals/ablate.py`（ablation.md 的闸门表，01 的 xfail 点名 04）；`evals/__main__.py`（注册 select）；`tests/test_eval_diverge.py`、`tests/test_verifier_dispatch_event.py`（精确字段列表加 `full_redispatches`）；`tests/test_eval_merge.py`（helper 写 request.json）。
  - 01 的测试只删了 04 的 xfail 标记（含不再用的 `NOT_YET_COMPARED`）；主会话核对 diff，被删行只有这些和 test_verifier_dispatch_event 的字段列表。
- 夹具运行（第 3 步，`runs/04/demo`，由 `runs/04/demo.py` 生成，无模型调用）：
  - `uv run python -m evals report runs/04/demo/s1/full/jobs/full runs/04/demo/s2/full/jobs/full --manifest runs/04/demo/manifest.json --split dev --out runs/04/demo/r/full` → `dev: accuracy 77.8% ± 11.1% over 9 trials`，每题 3 个 trial。正确性 7/9；基础设施 `{"passed": false, "trials": 1, "cost_usd": 0.05, "interrupted": {"trials": 0, "cost_usd": 0.0}}`；效率 `passed: false`，pos-operations__1（1 次）、pos-operations__2（2 次）各一条 warning。
  - Stage 2 用 `agent_timeout_sec=900` 跑同样合并 → `error: the requests of the 2 jobs differ in agent_kwargs; merge only jobs run under the same request`，退出 2，不写报告。
  - `evals select` 比 full、baseline、no-verifiers：full 77.8 ± 11.1，7/9，`failed (2 warnings)`；baseline 44.4 ± 19.2，4/9，`passed`；no-verifiers 66.7 ± 15.7，6/9，`passed`。
  - 留出集报告："h-secret" 出现 0 次，`infra_excluded {'trials': 1, 'cost_usd': 0.5}`；select 拒绝：`only dev reports can be compared to choose a config, got ['dev', 'holdout']`，退出 2。
- 审查：
  - da-review main（Codex）：第一次跑到 20 分钟后台上限无结论；重跑完成（期间 rebase 进 03）。286bf91 修复，均配修复前失败的测试：P2 只有部分 trial 有 dispatch 记录时效率判过 → 改为 unknown；P2 dev request 没有任务列表也能合并 → 改为拒绝。
  - eng:code-review main，Spec：base 过期 → rebase 到 03，按 03 的新布局读 OUT/dropped（1ecaaba）；select 没比 Skill 版本 → 带 Skill 的配置之间比较（1ecaaba）；续跑后的 infra 闸门 → 交人定；不修"新旧 trial 混合时的效率"（次要，后被 Codex 修复覆盖）。
  - eng:code-review main，Standards（无硬性违规）：1ecaaba 修了 `full_redispatches` 标成每 trial 均值（改为总数）、OUT 路径重复（`out_of()`）、"值一致"检查重复（`distinct()`）。不修：`source` 不收紧成 `DispatchSource`（事件文件来自多个旧版本，字符串比较正确）；`selection_row` 的 Feature Envy、report.py 体量、闸门记录加类型（暂无具体需要）；`job_dirs=()` 默认值（直接调用方没有 OUT，`run` 如实记 None）；默认 split `"holdout"`（已加注释，和 `ablate.check_request` 一致）。
- 已知限制：被丢弃的中断 trial 没有 result.json，无法按题过滤，计入其所在 job 的报告。
- 人定（2026-11-07）：infra 闸门只按"未解决"的 infra 错误判。已经重跑出结果的 infra 错误不判不过，只在 `infra_excluded` 里报数量和花费。理由：spec 的续跑设计（排出计分表、保留记账、重跑）和用户故事 8。ADR-0008:142"一票否决：infra_error 不为 0"的措辞由 05 对齐。
- 退回：`evals/report.py` `gates()` 的 infrastructure 现在只要 OUT/dropped 里有 infra 错误就判不过。按人定改为只在有未解决的 infra 错误时判不过：未解决 = 被丢弃的 infra 错误 trial，其 (task, trial 槽位) 在计分的 job 里没有带 result.json 的替补 trial。已解决的仍报进 `infra_excluded`（数量、cost_usd）。新测试：重跑成功 → passed 且 infra_excluded 计数为 1；没有替补 → failed。修复前前者会失败。在原分支 worktree-04-stage-gate-merge-reporting 上修，提交后照第 5 步两道审查。
- 退回修复（9a1a9c3、ad3dec8、cc52859）：
  - "槽位"口径：Harbor 续跑给重跑 trial 新的随机名，被丢弃的 trial 无法按名字对上替补，所以按 (job, task) 计槽位：计划数 = job 的 `config.json` 里的 `n_attempts`；空缺 = 计划数减同一 job 里该题带 result.json 的 trial 数；未解决 = Σ min(该题被丢弃的 infra 错误数, 空缺)。按 job 算的理由：ADR-0008 补充 2026-11-06 把 full 的 Stage 1 拆成几个 OUT（目标题 ×3、其余 ×1），每个 job 对每道题计划同样多的 trial，别的 job 里的 trial 只填那个 job 自己的槽位。
  - `gates.infrastructure` 加 `unresolved`，为 0 时通过；`infra_excluded` 不变。`evals select` 和 `ablation.md` 在 infra 排除数后面标 "(N unresolved)"，措辞改为"只有未解决的错误判不过"。
- 测试：重跑成功 → 通过，`infra_excluded` 1 个 trial、`unresolved` 0（修复前失败）；没有重跑（Stage 1 job 计划 3、有 2 个结果加 1 个被丢弃的错误，另一个计划 1 的 job 补足总数）→ `passed=False`、`unresolved=1`；同一槽位丢两次、重跑一次 → 已解决；select 表显示未解决数。
- 夹具运行（`runs/04/demo.py`，改成 ADR 的 Stage 1 布局）：`evals report s1t s1 s2` → `{"passed": true, "trials": 1, "cost_usd": 0.05, "unresolved": 0, ...}`；`evals report s1t s1-norerun s2 fill` → `{"passed": false, ..., "unresolved": 1}`；`evals select` 的 full 行 `| 1, 0.0500 (1 unresolved) | failed (2 warnings) |`。
- 退回后的审查：
  - da-review main（Codex），P2：`out_of()` 假设 `OUT/jobs/<config>` 布局，单独的 Harbor job 会读到上两层的 request、run record 和 dropped trial。ad3dec8 修复：只有父目录名为 `jobs` 才认 OUT；新测试修复前失败。
  - eng:code-review main，Spec（medium）：`kept_trials` 按传入路径名找 `dropped/<名字>`，用符号链接或 `.` 给出 job 时找不到 dropped trial，闸门悄悄通过。cc52859 改用真实目录名修复；新符号链接测试修复前失败。
  - eng:code-review main，Standards：无硬性违规。修了题目集合只建一次、一个看着像 OUT 目录其实装结果列表的变量改名。不修：`excluded_trials` 和 `unresolved_infra` 各扫一遍 `dropped/`（几个文件读两次，共用 helper `kept_trials` 已统一过滤）；`out_of() is None` 判断重复 3 处（可接受）。
- 已知限制：
  - ADR-0008 的布局下，未解决的 infra 错误会让 `check_complete` 直接拒绝出报告，所以写出来的报告 infra 闸门实际总是通过——比人定更严，不是更松。05 改 ADR-0008:142 时应写"闸门通过 = 没有未解决的 infra 错误"，不写"没有 infra 错误"。
  - `OUT/dropped/<config>` 只按 config 分。若有人手动删 `OUT/jobs/<config>` 再在同一 OUT 重跑，旧 job 的 dropped trial 会记到新 job 的槽位上。这是 ablate 的 OUT 设计，正常使用不删 job。
- 合并（2026-11-07）：PR #70 squash 为 d59e1f2。CI 两项全绿；Codex GitHub app 对 cc52859 审查完成、无意见。
