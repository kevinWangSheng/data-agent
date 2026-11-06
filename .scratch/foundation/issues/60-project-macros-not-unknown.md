# 60 — 项目自己的宏不再让模型"依赖未知"，重派列表不再退成全量

Status: done
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）
Claimed by: claude-opus-5-5 (worktree 60-project-macros-not-unknown)
Branch: worktree-60-project-macros-not-unknown

## Goal

主 agent 在项目 `macros/` 里写的、不会引用其他模型的宏，不再让调用它的模型被当成"refs 未知、是所有改动模型的下游"。重派只验真正需要再验的模型。

## 已和用户定下的（2026-11-06，grilling 第 2 轮 Q2）

- 只看项目自己的 `macros/` 目录（`dbt_project.yml` 的 `macro-paths`，默认 `macros`），不看 `dbt_packages/`。
- 一个项目宏算"不会引用模型"，要同时满足：
  - 宏体去掉 Jinja 注释后不含 `ref`、`adapter`、`dispatch`、`run_query`、`graph`；
  - 宏体里调用的其他宏也都满足这两条（不动点迭代；调用了包宏或未知宏就不满足）。
- 模型的 Jinja 里调用的只有这样的宏（加上现在已允许的 ref/source/var/config/is_incremental），`referenced_models` 返回它的 ref 集合而不是 None。
- 包宏（如 `dbt_utils`）仍算未知；`downstream_of` 对 None 的保守处理不变。
- 宏解析按工作区读，跟模型文件一样受工单 52 的缓存约束（目录 mtime）或每次重读，由实现定，但不能让单次 Stop/派单判定明显变慢。

## Authority

- May edit: `src/data_agent/verifier_v2/project.py`、`src/data_agent/verifier_v2/stop.py`、`tests/test_verifier_v2_redispatch.py`、新的测试文件
- Must not edit: `tests/acceptance/`、`hooks.py`、`dispatch.py`、`src/data_agent/agents/`、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- 证据（2026-11-06，读 `runs/t47-screen3/jobs/full/pos-operations__*`）：pos 每次重派都是 21 个模型，单次验证调用 535–684 s。按事件里的 Write/Edit 重建 SQL 后重算：20/21 个模型调用了主 agent 自己写的宏（`parse_pos_amount`、`pos_clean_money` 等），`project.py:299-306` 遇到宏就返回 None，`stop.py:131-133` 把 None 当成每个种子的下游，所以种子 1 个、6 个、8 个时列表都是 21。复算脚本：`/private/tmp/claude-501/-Users-shenghuikevin-dev-AI-data-agent/2ea30d9e-8846-4eab-b489-eb8d7ee12b9f/scratchpad/refs.py`（本机临时目录，可能已清理；思路是按事件顺序回放 Write/Edit 得到最终模型 SQL）。
- 现有代码：`referenced_models`（`project.py:299`）、`LITERAL_CALL_RE`、`redispatch_models` 与 `downstream_of`（`stop.py:100-137`）。

## Acceptance

- [ ] 新测试：模型调用一个纯 SQL 项目宏，`referenced_models` 返回它的 ref 集合（修复前返回 None，会失败）。
- [ ] 新测试：宏体含 `ref`、`adapter.dispatch`、`run_query`，或调用包宏/未知宏、或间接调用这样的宏，仍返回 None。
- [ ] 新测试：`redispatch_models` 在"种子 1 个 + 其余模型都只调纯 SQL 项目宏"的工作区上，只返回种子和真正 ref 它的下游。
- [ ] 离线复算：用 t47-screen3 的 pos trial（`HGcLuCF` 或 `qpLnwaW`）回放出最终工作区，重派列表少于 21 个模型；把前后数字贴进 Comments。
- [ ] `make check` 通过。

## Evidence required

测试输出和离线复算的前后数字。

## Stop conditions

- 必须改 `downstream_of` 对 None 的保守语义，或包宏的处理。
- 离线复算后列表仍是 21 且原因不在宏（交回并写明原因）。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0
- Outcome: done (PR #64 → 4628e58); pos redispatch lists drop from 21 to 6–11 models (offline recompute)
- Human review minutes: —

## Comments

- 2026-11-06 ticket agent (claude-opus-5-5), branch `worktree-60-project-macros-not-unknown`: ef8a9a5, 8741fde, ba056ca, 064fa11
  - `project.py`: `project_macros(project, config)` reads only the project's own `macro-paths` (default `macros`) and never `dbt_packages/`.
    - `.pure()` is the largest set that passes repeated elimination: no `ref`/`adapter`/`dispatch`/`run_query`/`graph` in the arguments or body (Jinja comments removed), no calls in argument defaults, and the body calls only other pure project macros by bare name. A duplicated macro name counts as pure only if every definition is.
    - `referenced_models(sql, pure)` keeps a model's refs when its Jinja calls only `ref`/`source`/`var`/`config`/`is_incremental` and pure macros.
  - `stop.py`: `redispatch_models` checks refs per project against that project's pure macros. `model_hashes` also hashes the text of the project macros a model calls, directly or indirectly, and the rule-2 text names macro edits.
  - Unchanged: unknown refs (None) still count as downstream of every seed, and package macros stay unknown.
  - Tests: `tests/test_verifier_v2_project_macros.py` has 22 cases. The first fails before the fix, at import. `make check` on 064fa11: lint, format and types clean; 1874 passed, 1 skipped, 38 deselected.
  - Offline recompute of pos-operations from t47-screen3 (the agent's scratch script replays Write/Edit events; seeds = models edited since the last dispatch + FAIL/NEEDS_INFO + never reported):
    - HGcLuCF seq 1083: 21/21 → 8/21 (the 8 seeds; 0 unknown refs, down from 20)
    - HGcLuCF seq 1540: 21/21 → 6/21
    - qpLnwaW seq 1338: 21/21 → 11/21 (0 unknown refs, down from 19)
    - qpLnwaW seq 2015: 21/21 → 10/21
  - Codex round 1:
    - P1, callers of an edited pure macro were not re-dispatched: fixed in 8741fde (macros in the fingerprint).
    - P2, `macro-paths` pointing into `dbt_packages` was read: fixed.
  - `eng:code-review`, Standards: fixed in ba056ca (naming, inlined pass-through helpers). Not fixing: a new `macros.py` would be outside Authority; macro files are read twice per redispatch, which the ticket allows.
  - `eng:code-review`, Spec: stale rule-2 text and docstrings fixed in ba056ca. Known limitations, all erring toward unknown refs: `return(...)`, `range(`, `if (` or a parenthesis inside a string counts as not pure; argument defaults nested two levels are not parsed; a pure macro called with a filter that takes arguments is still unknown. An existing gap: a project that overrides `ref`/`source`/`var`/`config` with its own macro is still trusted.
  - Codex round 2:
    - P1, argument defaults ignored when checking purity: fixed in 064fa11.
    - P2, a caller of a macro-only edit is missed: not fixed. Rule 2 (fingerprint) or rule 1 covers it, except for a caller whose file still matches the snapshot. That is a `model_diff` gap (it compares model files only) that existed before this branch; `model_diff.py` is outside Authority.
- 2026-11-06 main session: accepted the fingerprint change (8741fde) as a review fix, not a new policy. Before this ticket, a model that calls a project macro had unknown refs and was re-dispatched every time. Now that its refs are known, the macro text in the fingerprint keeps callers of an edited macro under Stop rule 2. Its effect on block counts is measured in the stage 1 gate. Follow-up: the `model_diff` macro-only gap goes into the acceptance criteria of a later ticket (not 59–67).

- 2026-11-06 main session: PR #64 CI green. The Codex GitHub app on 064fa11 had 1 finding (P2, `project.py:386`): parameter names such as `graph` or `ref` are searched for reaching symbols, which makes the macro opaque. I checked it against the code and it holds, but I am not fixing it.
  - It errs toward unknown refs, which is the behaviour from before this ticket, so the cost is efficiency, not correctness.
  - The body check would flag any use of that parameter anyway, so only a reaching-named parameter the macro never uses would gain.
  - Recorded with the known limitations above.
  - Squash-merged to main as 4628e58.
