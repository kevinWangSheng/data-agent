# 25 — 按开发集失败归因改 Skill（第一轮）

Status: done
Blocked by: —（只读工单 24 的报告，不依赖其代码）
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 25-skill-edits-dev-attribution)
Branch: worktree-25-skill-edits-dev-attribution

## Goal

把开发集 `full` 的 12 个失败归因（工单 24 的报告）里反复出现的 5 类问题写成通用的 Skill 规则，改进 `full`；之后主会话在开发集上 k=3 复查 `full` vs `baseline`。

## Authority

- May edit: `skills/`（`skills/*/SKILL.md` 与其附带文件）、`NOTICE`（只限说明本次修改）、`tests/`（除 `tests/acceptance/`；只限 Skill 相关测试，如 Skill 版本、ADR-0005 检查）
- Must not edit: `skills-frozen/`（旧版 Skill 保持 `fa6028eef62b`，作为"归因前"的对照）、`src/`、`evals/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $2

## Context

- 开发集 `runs/dev-2`（Skill `6ee2d4432631`，k=1）：`full` 60.0%（$5.09），`baseline` 66.7%（$2.39）；有差异的 6 题 full 胜 2、baseline 胜 4。
- 归因报告（只含开发集，可读）：`/Users/shenghuikevin/dev/AI/data-agent/.claude/worktrees/24-attribute-event-files/runs/24-attribution/report/report.md` 与 `attribution.json`。FM4 ×11、FM3 ×1。
- 用户确认的 5 类修改（全部要通用，ADR-0005：不点名题目、表、列、数值；报告里因含 benchmark 名称被扣下的 8 条建议不用）：
  1. **自证式核对**（5 题）：value 检查只比总量或重用模型自己的公式。改：在输出粒度上抽样逐键对照源数据重算；派生/加权列按书面规则独立重算并逐行比较（覆盖阈值、封顶、NULL 等分支）；自查不能重用模型的表达式。主要在 `dbt-workflow` 的 value CHECK 2 与 verifier/value-verifier。
  2. **行集合不对**（4 题）：加了没要求的过滤、改写时丢了原有 WHERE、排除条件多选了一个字段。改：加任何过滤前先量不加过滤的期望行数并说明每个谓词去掉多少行；改已有模型后把谓词与原模型逐条对比；排除条件只用任务点名的类别，逐个量其单独与重叠的影响。日期界定的指标不计日期为 NULL 的行（与"保留 NULL 行"规则的关系写清楚）。
  3. **百分比尺度**（1 题，baseline 通过）：`skills/dbt-write/SKILL.md:292` 无依据时默认 0-100（来自 SignalPilot）。改：无依据时默认 0-1 比例，且自查用同一尺度。
  4. **类型**（1 题）：时间戳被截成日期，schema 检查只比列名。改：schema 检查比类型；不在没要求时降精度（timestamp→date 等）。
  5. **完整性**（1 题）：最后只核对了 30 个要求模型中的 3 个。改：最终核对覆盖任务要求的每个模型/关系的列契约。
- Skill 文字越长 token 越多：`full` 已是 `baseline` 的约 2 倍花费。能并进已有规则就不新增段落；每条新规则配一句原因。
- 上游头注释：`skills/dbt-write/SKILL.md:10-15` 等记录了相对上游的修改，改了就同步更新。

## Acceptance

- [ ] 5 类修改落在合适的 Skill 里，措辞通用、不含任何 benchmark 标识（跑仓库已有的 ADR-0005 检查/测试；人工再过一遍）
- [ ] 与已有规则冲突的地方（如"保留 NULL 行"、"不加 WHERE"）一并理顺，不留互相矛盾的句子
- [ ] Skill 版本变化，写进 Comments（新旧版本号）；`skills-frozen/` 不动
- [ ] 真实运行（Mac colima，照 `runs/dev-2/run.sh` 的写法，新的 `--out`；从本 worktree 跑）：从 12 个失败里挑 3 题（各覆盖一类：1、2、3）× `full` × 1，贴结果与花费；不要求转绿，只证明 Skill 能被加载和遵循（引用事件里的相关步骤）
- [ ] `make check` 通过
- [ ] Comments 附一张表：每类修改 → 改了哪个文件哪几行 → 对应哪几个失败

## Evidence required

Skill diff 摘要、ADR-0005 检查输出、`make check`、3 题真实运行的命令与结果。

## Stop conditions

某条修改只能写成针对具体题目的内容；与上游规则冲突到需要人取舍；需要改 `src/` 或 `evals/`；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger
- Attempts: 2
- Review rounds: 1 (Codex local unavailable ×4; eng:code-review 1 round) + PR return round 1 fixed
- Model spend: $0.3987 (real run; no new run this round)
- Outcome: done，PR #26 squash 合并（1 轮退回）; 5 kinds of edit; PR return 1 (predicate correction wording) fixed in a5c1d2f; Skill f0805b10e5ae
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

用户选择"全部 5 类，然后复查"。合并后主会话在服务器从固定 commit 跑开发集 30 题 × `full`+`baseline` × k=3（约 $22），再找用户决定全量。

### 2026-10-20 工单 agent：实现（edab985、83b715c，主会话抄录）
Branch `worktree-25-skill-edits-dev-attribution`, commits `edab985` (edits) and `83b715c` (review round 1).

**Skill version:** `6ee2d4432631` → `2bcd45f17d00` (`edab985`, used for the real run) → `aa439e6eac46` (`83b715c`, final). `skills-frozen/` is unchanged (`git status` is empty there). `NOTICE` needs no change, because it already says "modified as noted at the top of each file" and I updated those top notes.

**Edits by class:**

| Class | File and lines (final version) | Failures it covers |
|---|---|---|
| 1 Self-verifying checks | `dbt-workflow/SKILL.md` Step 7.5 (196-200): one expression per factor, each cap applied to its own measure. Step 8 (241-251): a key-level check, run whether or not the verifiers ran: 3-5 keys at the output grain per model, recomputed independently from the written rules, never copying the model's expression, covering each branch, with the same rounding allowance. Step 8.3 (254-256): a verifier PASS can be overridden by a key-level difference. 8.4 (276): STOP also needs the key-level check to match | cohort-retention-matrix, inventory-turnover, fifo-inventory-cogs, web-session-quality-scoring, promotional-lift |
| 2 Wrong row set | `dbt-write/SKILL.md` §5 (185-191): count rows with and without each predicate and record it in the spec; use only the categories the task names; count each predicate alone and the overlap. §5 (223-227): rows with a NULL date are left out of date-bounded metrics. `dbt-workflow` 176 (the spec records rows removed per filter), 202 (after an edit, compare with the original predicate by predicate), 334-335 (points back to §5) | division-by-zero, multi-warehouse-rebalance, test-orders-filter, customer-cltv-forecasting |
| 3 Percentage scale | `dbt-write` §13 (307): covers every column holding a share, whatever its name; default is a 0-1 ratio; self-checks use the same scale | fix-daily-cohorts |
| 4 Types | `dbt-write` §1 (37-39): no precision loss the task doesn't ask for (timestamp is not cast to DATE); a reference table or sibling still wins. `dbt-workflow` Step 8 CHECK 2 (232-234): compares column types | marketing-campaigns-harbor |
| 5 Completeness | `dbt-workflow` Step 8 (225-226, 231): the verifiers and the self-checks cover every model the task asks for | workforce-analytics |

**How the new rules fit the existing ones:**
- "A row with some NULL columns is real data - keep it" now names the date-bounded exception right next to it. The `not_null` line in "What to Trust in YML" points to it.
- "Do NOT add WHERE" is unchanged. The date bound is itself an explicit requirement, so the two don't clash.
- "accept PASS / verifiers are the final authority" now includes the key-level check.
- The new type rule gives way to the existing rule that the reference table or sibling decides the type.

**Known gap (follow-up ticket needed):** the ticket asked for class 1 to go mainly into verifier/value-verifier. Those prompts are in `src/data_agent/agents/`, which this ticket may not edit, so the value-verifier still only reconciles totals. The independent key-level check is now done by the main agent, the one that wrote the model. Improving the verifier side needs a separate ticket that can edit `src/`.

**ADR-0005 checks:**
- `tests/test_dbt_skills.py::test_dbt_skills_say_nothing_about_how_results_are_graded`, `test_dbt_skills_carry_no_project_specific_names` and `tests/test_skills.py` all pass.
- A grep of the added lines for task, table and column words (benchmark, harbor, cohort, cltv, warehouse, fifo, promotion, workforce, engagement, session, sku, day_0, early_churn, effective_date, testing, sampling, internal use, division, evaluat, tolerance) found nothing.
- A manual read found no task names, tables, columns or values.
- The subagent review also confirmed ADR-0005 is clean.

**New tests:** 6 new wording tests in `tests/test_dbt_skills.py`. They failed before the edits and pass now.

**make check:** `1152 passed, 1 skipped, 25 deselected` (on `83b715c`; log in `runs/25/check-2.log`).

**Real run:**
- Mac colima, from this worktree. Command: `./runs/25/run.sh --manifest runs/25/manifest-dev.json --out runs/25/out --budget 2 --config full --trials 1 --n-concurrent 3`
- `run.sh` copies `runs/dev-2/run.sh`. The manifest puts the 3 tasks under "holdout" so `ablate` runs them.
- Event header: `git_commit edab98530fa7…` (clean), `skill_version 2bcd45f17d00`, `config full`.

| Task (class) | dev-2 | This run | Cost |
|---|---|---|---|
| web-session-quality-scoring (1) | 0 | **1.0** | $0.2023 |
| dbt-test-orders-filter (2) | 0 | **1.0** | $0.1054 |
| dbt-fix-daily-cohorts (3) | 0 | **1.0** | $0.0911 |
| Total | | 100.0% (3/3) | **$0.3987** |

This is k=1 on 3 tasks, so it only shows the Skill was loaded and followed. It is not evidence of a real improvement.

**Steps in the events that show the new rules being followed:**
- **daily-cohorts:**
  - #172 spec: "Scale = 0-1 ratio: the project uses 0-1 for rate columns…".
  - #273: "Both verifiers passed. Now I'll run my own independent key-level check."
  - #285: "independent key-level recomputation (5 cohorts including the largest, smallest…) matched every metric row for row".
- **test-orders-filter:**
  - #150 spec lists each of the three named flags alone (22 / 22 / 44 rows).
  - It then says: "Union of the three = 87 distinct orders (overlap between flags)… Rows without any flag = 2037 - 87 = **1950**".
  - In dev-2 it had used a fourth flag and got 1930.
- **web-session:**
  - #373: "consistent in my own key-level recomputation".
  - #287/#290 recompute 4 sessions directly from the source tables.
  - #639 spec: "Key-level recomputation on 3-5 sessions/visitors per model, including … a bounce session and an orphan session".

**Reviews:**
- `da-review main` (Codex): **did not run.** It failed 3 times with `ERROR: Selected model is at capacity` (logs in `runs/25/da-review-{1,2,3}.log`). The model `gpt-6-astra` is hardcoded in the installed script, so I didn't switch. **The main session should rerun it before the PR.**
- `eng:code-review main` (fresh-context subagent, Standards + Spec). Fixed in `83b715c`:
  - (1) The key-level check had no rounding allowance and gated STOP. It now uses the same allowance as above.
  - (2) Its scope was "each built model". It is now "every model the task asks for".
  - (3) The fix route now says: update the spec first, then edit the SQL.
  - (4) The timestamp rule now gives way to a reference table or sibling.
  - (5) "0-100 only when asked" overlapped with the authorities in the same sentence and was removed.
  - Reasons were added for the per-factor rule, the 0-1 default and the overlap counting.
  - The duplicate `step_8` in the tests was merged.
- Not fixed, with reasons:
  - Possible confusion between §5 "a column that sounds related" and §14's `is_<event>` flag: the two cover different cases (excluding categories vs. confirming an event happened).
  - The tension between "descriptions are not literal computation instructions" and "recompute from the written rules" already existed. The new rules only apply where the YML states the rule explicitly.
  - The tests only check wording: that is how the rest of the file is written.
  - The 0-1 default and NULL-date exclusion may lean toward the expected answers. Both are general rules, but watch the holdout aggregate in the k=3 recheck.
  - Skill size grew about 8-9% (dbt-workflow +291 words, dbt-write +245). Everything was merged into existing items with no new sections.

### 2026-10-21 主会话：PR #26 Codex 意见（第 1 轮退回）

CI 绿（83b715c）。Codex 本地 `da-review` 因容量不足没跑成，PR 上请求 `@codex review` 后有 1 条 P2（`skills/dbt-workflow/SKILL.md:203`）：新句子"each stays unless the task names it for removal"在修 bug 的题里会保留任务要修的那个错误谓词（任务说某条件语义错了、要改，但没说"删除"）。属实：措辞只放行"删除"，没放行"改正"。

退回：把该句改成每个谓词保持不变，除非任务点名要删除或改正它（被点名的谓词按任务改，其余逐条保持）；配一条措辞测试（修复前失败）；其余不变，不需要再真实运行。

### 2026-10-21 工单 agent：退回第 1 轮修复（a5c1d2f，主会话抄录）

**Skill 版本：** `aa439e6eac46` → `f0805b10e5ae`（`a5c1d2f`）。`skills-frozen/` 未动。

**修复（Codex P2，`skills/dbt-workflow/SKILL.md:203`）：** 编辑规则末尾由"each stays unless the task names it for removal"改为"each stays unchanged unless the task names it for removal or correction. Change a named predicate as the task says and keep every other predicate as it was."——被任务点名要删或改正的谓词按任务改，其余逐条保持。文件头上游修改说明（22-23 行"an edit is compared with the original predicate by predicate"）仍准确，未改。

**测试：** 新增 `tests/test_dbt_skills.py::test_edit_changes_a_predicate_the_task_names_as_wrong`（断言编辑规则段含 "removal or correction" 与 "keep every other predicate"）；修复前失败（`1 failed`），修复后通过。

**make check：** `1153 passed, 1 skipped, 25 deselected`（日志 `runs/25/check-3.log`）；pre-commit 全部 Passed。

**真实运行：** 按退回要求不重跑（只改一句措辞）。

**审查：** `da-review main` 第 4 次仍 `ERROR: Selected model is at capacity`（`runs/25/da-review-4.log`），没跑成；依赖 PR 上 `@codex review` 复核本次修改。

**与 origin/main：** 未 merge；`git merge-tree` 可干净合并，origin/main 的改动只在 `evals/`、`docs/setup.md` 和 3 个评测测试，与本分支不重叠。

### 2026-10-21 主会话：合并 PR #26

CI 绿（a5c1d2f）。Codex 对 83b715c 的审查只有 1 条 P2，已在 a5c1d2f 修复。对 a5c1d2f 的 Codex 审查 3 次都失败（status failed、两次 "Something went wrong"），本地 da-review 4 次容量不足；修复只改一句措辞加一条测试，主会话逐行核对 diff：只放行被任务点名删除或改正的谓词，其余保持，与意见一致、无其他改动。已 `gh pr merge 26 --squash`。
已知缺口（后续工单）：verifier/value-verifier 提示词（`src/data_agent/agents/`）仍只核对总量。
