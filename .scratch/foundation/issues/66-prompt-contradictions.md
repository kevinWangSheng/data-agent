# 66 — 提示词里的矛盾：`profiles.yml`、已删的 `verify_model_values`

Status: done
Blocked by: 59, 64
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06 的补充）
Claimed by: claude (opus-5-5) 2026-11-06 (filled in by the main session; the worktree session could not write the main checkout)
Branch: worktree-66-prompt-contradictions

## Goal

模型读到的提示词不再自相矛盾：题面要求新建 `profiles.yml` 时，系统提示词不再说"不要改 `profiles.yml`"；Skill 不再介绍已经不存在的 `verify_model_values` 工具。顺手删掉"until ticket 47"的过时注释。

## 已和用户定下的（2026-11-06，grilling Q5）

- `DBT_PROMPT`（`src/data_agent/agent.py:139`）的 "Do not edit `profiles.yml`." 改成：不改已有的 `profiles.yml`；题面要求时可以新建。
- `skills/dbt-workflow/SKILL.md`：删掉 Tools 里的 `### verify_model_values` 一节（约 :77-84），和开头说明里提到它的那句（约 :23-25）。其他内容不动。
- 删除或改写过时注释：`src/data_agent/verifier_v2/dispatch.py:12-13`、`src/data_agent/verifier_v2/hooks.py:165-166` 的 "until ticket 47 removes it"（工单 47 已删掉值验证器）。只改注释，不改代码。
- 不写针对某道题的内容（ADR-0005）。

## Authority

- May edit: `src/data_agent/agent.py`（只改 `DBT_PROMPT` 那一句）、`skills/dbt-workflow/SKILL.md`（只改上面两处）、`src/data_agent/verifier_v2/dispatch.py` 和 `src/data_agent/verifier_v2/hooks.py`（只改注释）、相关测试
- Must not edit: `tests/acceptance/`、`src/data_agent/agents/verifier.md`、`src/data_agent/verifiers.py`、`src/data_agent/verifier_tools.py`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- A1 证据（2026-11-06）：约 86 道题的题面写着 "Create a `profiles.yml` in the dbt project directory…"；有评分断言查这个文件是否存在的题只在留出集或非样本题里。t47-screen3 `dbt-customer-cross-sell-insights` 3 个 trial 都自己建了 `/app/dbt_project/profiles.yml`、过了 3/3，但每个 trial 在思考里纠结 6–16 次"题面要我建、提示词不让我改"。
- `verify_model_values` 在 ADR-0009 后已不注册（`src/data_agent/verifier_tools.py:4`），Skill 仍在 :23、:77 介绍它。
- 为什么等 59、64：64 改 `dispatch.py`、`hooks.py`、Skill 第 8 步，59 改 `hooks.py`，先合它们避免冲突。
- 改 Skill 会改变 `skill_version`；这一轮全部合入后才跑统一评估第 1 段，所以没有影响。

## Acceptance

- [ ] 新测试：`DBT_PROMPT` 不再含 "Do not edit `profiles.yml`."，含"已有的不改、题面要求时可以新建"的意思（修复前会失败）。
- [ ] 新测试：`skills/` 下的 SKILL.md 不再出现 `verify_model_values`（修复前会失败）。
- [ ] `grep -rn "until ticket 47" src` 无结果。
- [ ] 现有测试和 `tests/acceptance` 全部通过；`make check` 通过。

## Evidence required

测试输出和上面的 grep 输出。行为由统一评估第 1 段检验，不单独真实运行。

## Stop conditions

- 有测试或代码依赖被删的 Skill 段落。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: done (PR #66 → d8eb6a9)
- Human review minutes: —

## Comments

- 2026-11-06 ticket agent (claude-opus-5-5), branch `worktree-66-prompt-contradictions`: 0efaa31, 5449562
  - Changes:
    - DBT_PROMPT (`agent.py:139`) now reads "Do not edit an existing `profiles.yml`; create one when the task asks for it." The `DBT_PROMPT_BEFORE` copy in `tests/test_task_file.py` matches.
    - SKILL.md: the `### verify_model_values` section is removed. The modification note at the top (the model sees it) now says "the Tools section lists only tools data-agent registers", naming no removed tool, and still records the upstream modification.
    - `dispatch.py:12-13` and `hooks.py:279-280`: comment wording only ("verifier" is the only name left since ticket 47).
  - Tests: 2 new in `tests/test_dbt_skills.py`, both failing before the change; after it, test_dbt_skills + test_task_file give 65 passed. `grep -rn "until ticket 47" src` and `grep -rn verify_model_values skills` return nothing. `make check`: 1934 passed, 1 skipped, 38 deselected. No real run, per the ticket (covered by unified eval stage 1).
  - `da-review main`: no findings.
  - `eng:code-review`, Spec: the header sentence is reworded (5449562); otherwise it matches Acceptance and Authority, with no ADR-0005 issue.
  - `eng:code-review`, Standards: "tools (map_columns)" fixed. Not fixing: the duplicated `DBT_PROMPT.format` in two tests (the existing pattern).
  - Follow-ups outside Authority: `agent_type == REWRITTEN_SUBAGENT` at `hooks.py:286/:290` is always true now that only "verifier" is left (dead guard); the `verify_model_values` code in `verifier_tools.py:720+` is dead (already a known limitation in ADR-0008 Update 2026-11-06: "`verify_values` 死代码"). Tidy-up comes after the eval.

- 2026-11-06 main session: PR #66 CI green; Codex GitHub app on 5449562: no findings. Squash-merged to main as d8eb6a9.
