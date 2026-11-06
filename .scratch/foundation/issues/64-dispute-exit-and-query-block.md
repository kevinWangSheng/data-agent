# 64 — 验证器与主 agent 争议的收场，钩子硬拦验证器查派单模型

Status: done
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）；../../verifier-v2/spec.md
Claimed by: claude-opus-5-5 (worktree 64-dispute-exit-and-query-block)
Branch: worktree-64-dispute-exit-and-query-block-r1 (rebased; was worktree-64-dispute-exit-and-query-block)

## Goal

验证器对同一个没改过的模型、在看过反证之后仍判同一项 FAIL 时，运行可以结束，争议记进事件和最终回复，不再一轮轮重派。验证器不能用 `query_database` 查本次派单的模型本身，由钩子强制，不只靠提示词。

## 已和用户定下的（2026-11-06，grilling 第 2 轮 Q3、Q5，第 3 轮 Q1、Q2）

- **争议的判定**：同一模型的同一 CHECK 第二次 FAIL，并且
  - 模型指纹和上一次 FAIL 时相同（中间没改过）；
  - 中间那次重派带了反证（派单里有 `dispatch.py` 的 `UNVERIFIED_HEADING` 一节，且内容非空）。
- **Stop**：争议项不再让 Stop 规则 3 拦；运行结束时发 `ended_disputed` 事件，列出争议项（模型、CHECK、两次 FAIL 的派单序号）。其余 FAIL 照旧拦，`MAX_BLOCKS` 不变。
- **主 agent 提示词**（`src/data_agent/verifiers.py` 的 VERIFIERS_PROMPT FAIL 段，约 :69-72，以及 :61 "still has a FAIL" 那句）：带反证重派一次后同一项仍 FAIL、你没有改模型时，可以结束，在最终回复里列出每个争议项和你的依据。Skill 第 8 步（`skills/dbt-workflow/SKILL.md` 约 :278-293）只引用系统提示词，不另写规则；若有冲突措辞就同步。
- **硬拦**：PreToolUse 钩子拦验证器（按 `agent_id` 认出是 `verifier` 子 agent）发出的 `query_database`，SQL 读到本次派单列表里的模型（按建成的表名和别名）就拒绝，理由写"不要查本次要验证的模型；用 grain_facts / compare_by_key"。SQL 解析不了就放行。主 agent 的查询不受影响。
- NULL key 规则（`agents/verifier.md` 约 :90）不动：accept-2 的 `pos-operations__JfH8WAC` 里 256 行源列自带的 NULL 主键被评分判错，放宽会放过它。

## Authority

- May edit: `src/data_agent/verifier_v2/hooks.py`、`src/data_agent/verifier_v2/stop.py`、`src/data_agent/verifier_v2/dispatch.py`（只读出派单是否带反证，不改模板文本）、`src/data_agent/verifiers.py`（VERIFIERS_PROMPT）、`skills/dbt-workflow/SKILL.md` 第 8 步、`evals/process.py`（只为兼容新事件）、`tests/test_verifier_v2_hooks.py`、`tests/test_verifier_v2_redispatch.py`、`tests/test_verifiers.py`、新的测试文件
- Must not edit: `tests/acceptance/`、`src/data_agent/agents/verifier.md`（归工单 63）、`src/data_agent/verifier_v2/project.py`（归工单 60）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0（初筛由主会话在 59–64 全部合入后合跑）

## Context

- 证据（2026-11-06，读 `runs/t47-screen3/jobs/full/pos-operations__{JPS92zS,HGcLuCF,qpLnwaW}`）：
  - CHECK 3 FAIL 原文："167 NULL-key rows, 0 duplicates; no exemption (task does not make NULL order_source part of the grain)"；三个 trial 评分都 69/69，是假阳性。同样 167 行，不同调用在 PASS 和 FAIL 之间摇摆。
  - HGcLuCF 第 2 次调用后主 agent 没改模型、只带反证重派，第 3 次验证器才改判；qpLnwaW 第 3 次全 PASS。每次调用 535–684 s。screen3 里没有"循环到截止"，0 次 `ended_*` 事件。
  - 维持重派的是 `stop.py` 的 `stop_decision`（:79-97，最后一份报告有 FAIL 就拦）和 `verifiers.py:69-72,79-80`（FAIL 复现不了只能带反证再派，结束条件不允许有 FAIL）。现有出口：验证器改判、`MAX_BLOCKS=2` 后记 `ended_with_unresolved`（`hooks.py:49,210-214`）、剩余时间不足记 `ended_unverified`。
- 硬拦的事实：
  - 子 agent 里触发的 PreToolUse 输入带 `agent_id`；`agent.py:397-434` 已按 SubagentStart/SubagentStop 跟踪运行中的子 agent，可照此建 `agent_id → agent_type`。
  - 表名解析：`verifier_v2/sql.py:144` 的 `table_references`；模型建成的名字和别名：`compare.py:217` 的 `_forbidden`（`compare_by_key` 已用同样规则拒绝读改动模型的参考 SQL）。
  - 工单 47 的 (c)：t47-screen3 验证器仍有"看模型输出"约 73 次、"干跑参考 SQL"约 90 次（按 SQL 文本粗分）。
  - 拒绝的写法照 `dispatch.py` 的 `deny_dispatch`（`permissionDecision: deny` + reason）。
- 上游：SignalPilot plugin 只派一轮、没有 Stop 硬门（`/Users/shenghuikevin/dev/AI/data-agent-refs/signalpilot-plugin/skills/dbt-workflow/SKILL.md:255-276`）；未见"意见不一致怎么收场"的条款。

## Acceptance

- [ ] 新测试：同一模型同一 CHECK，模型未改、中间派单带反证，第二次 FAIL 后 Stop 不拦，发 `ended_disputed` 并列出该项（修复前会失败）。
- [ ] 新测试：模型改过、或中间派单没带反证，第二次 FAIL 仍照旧拦。
- [ ] 新测试：同一份报告里争议项和非争议 FAIL 并存时，仍因非争议项拦。
- [ ] 新测试：验证器的 `query_database` 读派单模型（裸名、带 schema、别名）被拒；读源表、读未派单的表放行；SQL 解析失败放行；主 agent 的同一查询放行。
- [ ] 新测试：VERIFIERS_PROMPT 含争议收场的规则；Skill 第 8 步与之不冲突。
- [ ] `evals/process.py` 读到 `ended_disputed` 不报错。
- [ ] 现有钩子、Stop、验证器测试和 `tests/acceptance` 全部通过。
- [ ] `make check` 通过。

## Evidence required

测试输出；用 t47-screen3 `pos-operations__HGcLuCF` 的验证器报告序列离线回放 Stop 判定，贴出第 2 次 FAIL 后的判定（前后对比）。初筛由主会话在 59–64 全部合入后合跑。

## Stop conditions

- PreToolUse 输入里拿不到可靠的 `agent_id`，分不出验证器和主 agent 的调用（只做争议收场，硬拦交回）。
- 争议判定必须改报告格式或派单模板文本。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 2 (send-back round 1)
- Model spend: $0
- Outcome: done (PR #65 → 7549361, after send-back 1); real effect measured in unified eval stage 1
- Human review minutes: —

## Comments

- 2026-11-06 ticket agent (claude-opus-5-5), branch `worktree-64-dispute-exit-and-query-block`: bce5e53, ccfe4cb, 0a78811 (rebased by the main session to 53f5e49, d8cd8b3, 40f00f6)
  - Change:
    - When the same CHECK of the same model FAILs again, the model is unchanged since the first FAIL, and the dispatch in between carried counter-evidence (a non-empty `UNVERIFIED_HEADING` section), Stop rule 3 no longer blocks. At the end of the run an `ended_disputed` event lists each disputed item (model, CHECK, the two dispatch numbers). Any other FAIL still blocks, and `MAX_BLOCKS` is unchanged.
    - A new PreToolUse hook denies the verifier's `query_database` call when its SQL reads a dispatched model by name or alias ("use grain_facts / compare_by_key"). Unparsed SQL passes, and so do the main agent's queries.
    - `VERIFIERS_PROMPT` has the dispute rule. Skill Step 8.4 refers to the system prompt.
  - Tests: `tests/test_verifier_v2_dispute.py` (19 new) and 1 new test in `tests/test_verifiers.py`. The dispute test fails before the fix. `make check` on 0a78811: lint and types clean; 1872 passed, 1 skipped, 38 deselected.
  - Offline replay of t47-screen3 `pos-operations__HGcLuCF` (`runs/t64/replay.py` in the worktree, which git ignores; real dispatch prompts and reports through `run_hooks`), Stop after report 2:
    - Before: blocks on 6 models (rule 3, CHECK 3).
    - After: blocks only on `fct_order_daily_performance`, the model edited before dispatch 2; the other 5 are disputed (dispatches 1 and 2).
  - How the verifier is recognised: `tests/acceptance/test_verifier_v2.py:682` requires `run_hooks` to register only PreToolUse/SubagentStop/Stop, and `agent.py` is outside Authority, so there is no SubagentStart map. The guard uses the PreToolUse input's `agent_id` plus `agent_type == "verifier"` (SDK `types.py:329-345`).
  - Known limitation: the hook doesn't know the dialect or schema, so a same-named source in another schema is also denied. `compare_by_key` can still read it.
  - Counter-evidence follows the ticket rule (a non-empty section), so any non-empty re-dispatch message counts.
  - `da-review main`:
    - [P2] RecursionError on deeply nested SQL escaped the guard: fixed in ccfe4cb, with a test.
    - [P1] "FAIL d1, FAIL d2 without evidence, FAIL d3 with evidence is a dispute (d2, d3)": not fixed. It matches the agreed rule (evidence carried, model unchanged since d2's FAIL, still FAIL).
  - `eng:code-review main`:
    - Fixed: the `last_dispatch_models` name, the Step 8.4 reflow, a test for a standing dispute.
    - Not fixing:
      - Reusing `agent.QUERY_TOOL` would make a circular import.
      - Catching RecursionError in `sql.table_references` too: sql.py is outside Authority; follow-up for compare_by_key.
      - A dataclass for dispute records: judgement call.
      - Schema-blind deny and counter-evidence breadth: covered above.
- 2026-11-06 main session:
  - Verifier recognition, live evidence: in `runs/t47-screen3/jobs/full/dbt-fix-division-by-zero__7f7yp8X`, the 8 `tool_timing` events for the verifier's `query_database` calls carry the same `agent_id` that `verifier_start` recorded from the SDK hook input; the 22 main-agent calls have `agent_id` null. Per the SDK, `agent_type` comes with `agent_id` inside a subagent, so the stop condition does not apply. Unified eval stage 1 confirms it live (guard rejection events).
  - Counter-evidence breadth: the ticket's agreed rule ("UNVERIFIED_HEADING section present and non-empty") is applied as written; no new decision is needed.
  - Rebased onto main after 59 (6380aa0), 63, 65 and 61 merged. The `hooks.py` conflict was positional only: both sides edited the dispatch lines. Kept 59's `models, source = dispatch_models()` plus the `verifier_dispatch` event, and 64's `last_dispatch_models` and `Dispatch` record. After the rebase: lint, format and types clean; 6 affected test files (dispute, dispatch event, hooks, verifiers, trial deadline, redispatch) 131 passed.

- 2026-11-06 main session: PR #63 CI green. The Codex GitHub app on 40f00f6 had 1 finding (P2, `stop.py:102`). I checked it against the code and it holds.
- **Send-back: with several verifier dispatches outstanding, a FAIL can be disputed with no counter-evidence dispatch after it.** `subagent_stop` (`hooks.py:282-284`) attributes each report to the oldest dispatch (`dispatched[0]`) but removes the newest (`pop()`). This is the conservative fingerprint pairing already on main (`hooks.py:177-179`). So with d2 (carries counter-evidence) and d3 both outstanding, both reports get `dispatch=2, counter_evidence=True`. If the first report records a new FAIL and the second report FAILs the same check on the same fingerprint, `record_checks` (`stop.py:96-102`) records a dispute `[2, 2]` and Stop lets the run end, although no counter-evidence dispatch came after that first FAIL.
  - Fix: a dispute needs two different dispatches, where the evidence-carrying dispatch comes strictly after the one whose report first recorded the FAIL (for example, require `before["dispatch"] < dispatch`). Keep the fingerprint pairing conservative as it is.
  - Add a test that fails before the fix: two outstanding dispatches (the oldest carries counter-evidence), both reports FAIL the same check on an unchanged model, and Stop still blocks with no `ended_disputed`.
- Status back to `ready-for-agent`. Fix it on the original branch `worktree-64-dispute-exit-and-query-block` (already rebased on main, now 40f00f6) in the existing worktree. This is send-back round 1.

- 2026-11-06 ticket agent (claude-opus-5-5), send-back round 1, commits f75dab8, 6de6a42
  - f75dab8: a dispute needs `before["dispatch"] < dispatch` (`stop.py:102`). The test `test_two_reports_of_one_outstanding_dispatch_are_no_dispute` returned `{}` from Stop before the fix; after it, Stop blocks and emits no `ended_disputed`.
  - 6de6a42 (gap found by the Spec review): each report took `counter_evidence` from the oldest outstanding dispatch. Now a report carries counter-evidence only if every outstanding dispatch did. The test `test_a_fail_counts_as_disputed_only_if_every_outstanding_dispatch_has_evidence` failed before the fix. The same commit makes the `redispatch_models` docstring say "undisputed FAIL" and reflows Step 8.4.
  - `make check` on 6de6a42: lint and types clean; 1909 passed, 1 skipped, 38 deselected. The HGcLuCF offline replay is identical to the earlier one (empty `diff`; its dispatches are sequential).
  - `da-review main`: [P1] "FAIL d1, FAIL d2 without evidence, FAIL d3 with evidence is a dispute (d2, d3)". Not fixed, same as round 1: it matches the agreed rule.
  - `eng:code-review`, not fixing:
    - `ended_disputed` can be emitted again when Stop fires again (`ended_with_unresolved` does the same).
    - The guard denies models from every outstanding dispatch, the conservative reading.
    - The modification note at SKILL.md:19-20 is outside Authority (Step 8 only); follow-up.
    - Judgement calls on `# type: ignore` and the double `model_hashes`.
- 2026-11-06 main session: rebased onto main after ticket 60 (4628e58). The `stop.py` conflicts were docstrings only, and both texts are kept (the fingerprint covers the macros a model calls, plus the dispute fields). There is no semantic conflict: a macro edit changes the fingerprint, so it rules out a dispute, as it should. After the rebase: lint, format and types clean; the verifier test files plus `tests/acceptance/test_verifier_v2.py` give 195 passed. AGENTS.md forbids force-push, so the rebased commits went to a new branch, `worktree-64-dispute-exit-and-query-block-r1` (head fea3815), as PR #65. #63 is closed as superseded.

- 2026-11-06 main session: PR #65 CI green; Codex GitHub app on fea3815: no findings. Squash-merged to main as 7549361.
