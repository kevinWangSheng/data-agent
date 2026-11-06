# 63 — 验证器：删除原有逻辑要题面点名，参考 SQL 沿用快照行集，报告再短一点

Status: done
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）；../../verifier-v2/spec.md
Claimed by: claude (opus-5-5)
Branch: worktree-63-verifier-row-logic-basis

## Goal

主 agent 删掉或放宽原有过滤、连接、去重而题面没有点名时，验证器判 FAIL；CHECK 7 的参考 SQL 不再跟着主 agent 的改动走。验证器少做提示词不需要的源表探查，报告每行更短。

## 已和用户定下的（2026-11-06，grilling 第 2 轮 Q4、Q5，第 3 轮 Q2）

- **CHECK 6（`src/data_agent/agents/verifier.md` Step 3，约 :113-118）**：删除或放宽原有的过滤、连接、DISTINCT/QUALIFY/去重，依据只能是题面点名这个条件，或点名它影响的那些行（引用原文）。从成功标准、行数、"题面没说要保留"推出来的不算依据，判 FAIL，并引用被删的那一行。
- **Step 4 参考 SQL（约 :125-140）**：题面没提到的行集规则（过滤、连接、去重），照快照模型原样写进参考，从 `model_diff` 的 removed 行取；题面提到的照题面写。这样被删掉的过滤会表现为 only-in-model。仍不读模型现在的 SQL。
- **源表探查**：Step 4 现有那句"`query_database` 只用于题面规则需要的源表事实"再压一次：写明每个模型通常不需要探查，需要时一次查完；不加次数上限。
- **报告**：CHECK 行除 FAIL 外只写状态和关键计数，每行细节不超过约 100 个字符；CHECK 5、6 只写有问题的改动（全部有依据就写 "all changes have a basis"）。Summary 规则不变。2026-11-06 量过 t47-screen3：每个模型的报告已经是 900–1900 字符，这项预期每次调用只省几秒到几十秒。
- 措辞是通用规则，不能出现具体题目、表名或列名（ADR-0005）。
- NULL key 规则（约 :90）不动。

## Authority

- May edit: `src/data_agent/agents/verifier.md`、`tests/test_verifiers.py`、新的测试文件
- Must not edit: `tests/acceptance/`、`src/data_agent/verifiers.py`（VERIFIERS_PROMPT 归工单 64）、`src/data_agent/verifier_v2/`、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0（初筛由主会话在 59–64 全部合入后合跑）

## Context

- 证据（2026-11-06，读 `runs/t47-screen3/jobs/full/dbt-fix-division-by-zero__{6uVCUp4,7f7yp8X,KkKSw8q}`）：
  - 三个 trial 都只挂 `component_3_row_count: 390 vs 382`（`verifier/test-stdout.txt`）：主 agent 有意删掉了原模型的 `where status != 'CANCELLED'`，理由是成功标准的字面意思；评分器、原模型和参考解答都保留这个过滤。
  - 验证器的 `model_diff` 清楚列出 `-    where status != 'CANCELLED'`（6uVCUp4 事件 seq 241），CHECK 6 却写"Removed … has a basis: success criterion 3 … the task names no status exclusion"判 PASS；三次参考 SQL 的 `src` CTE 都只有 `WHERE customer_id IS NOT NULL`，所以 CHECK 7 0 mismatch。两个历史通过的 trial（`runs/t47-screen/.../xMzmqsL`、`runs/t47-screen2/.../qLyePQj`）参考带着 status 过滤：参考的行集跟着 diff 走，不独立。
- 上游思路：SignalPilot plugin `agents/verifier.md:40,48,74`（"verbatim authorization" 限定改动范围；"FAIL an unrequested … change"）。本机路径 `/Users/shenghuikevin/dev/AI/data-agent-refs/SignalPilot/signalpilot-plugin/agents/verifier.md`。
- 墙钟根因：`docs/research/2026-11-05-verifier-wall-root-cause.md` §5 杠杆 1、3。

## Acceptance

- [ ] 新测试：`load_definition("verifier")` 的提示词含"删除原有逻辑须题面点名、推论不算"的规则（修复前会失败）。
- [ ] 新测试：Step 4 含"题面没提的行集规则沿用快照模型（来自 model_diff 的 removed 行）"。
- [ ] 新测试：报告模板与 `report.py` 的解析仍兼容（用模板渲染一份报告，`parse_report` 得到每个模型 7 个状态）。
- [ ] 提示词里没有具体题目、表名或列名。
- [ ] 现有 `tests/test_verifiers.py`、`tests/acceptance` 里的验证器测试全部通过。
- [ ] `make check` 通过。

## Evidence required

测试输出；改后提示词的相关段落原文贴进 Comments。初筛由主会话在 59–64 全部合入后合跑（5 题 × 3，过线见 ADR-0008 Update 2026-11-06）。

## Stop conditions

- 规则必须写到具体题目才能生效（违反 ADR-0005）。
- 需要改 `report.py` 的解析规则或 VERIFIERS_PROMPT。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1 (one fix round; both reviews clean after it)
- Model spend: $0
- Outcome: done (PR #61 → b49d12d); real effect measured in unified eval stage 1
- Human review minutes: —

## Comments

- 2026-11-06 ticket agent (claude-opus-5-5), branch `worktree-63-verifier-row-logic-basis`, commits b6d4204, c780059
  - Change (only `src/data_agent/agents/verifier.md`, `tests/test_verifiers.py`):
    - CHECK 6: removing or loosening a filter, join or dedup the snapshot model had needs the task to name that condition or the rows it affects. A reason taken from a success criterion, a row count, or "the task doesn't say to keep it" is a FAIL that quotes the removed line.
    - Step 4: a row-set rule the task does not mention, which the main agent removed or changed, goes into the reference as its `model_diff` removed line shows it. Those removed lines are the only model logic the reference may copy.
    - Source-table probing: "Most models need none; when one does, get all of it in one query."
    - Report: a PASS line gives status and key counts only; N/A and NEEDS_INFO keep their reason; CHECK 5 and 6 list only the changes at issue, or "all changes have a basis".
  - Tests: baseline `tests/test_verifiers.py` 27 passed. 6 new tests, 5 of them red before the change (the 6th is a template/`parse_report` compatibility guard). `make check`: lint, format and types clean; pytest 1858 passed, 1 skipped. After the fix round: `tests/test_verifiers.py` 33 passed; `tests/acceptance -k verifier` 48 passed.
  - Real run ($0, prompt-only): loaded the verifier through `agent.build_options(..., load_config("full"), ...)` and printed the changed CHECK 6, Step 4, Output Format and template passages, which match the text above.
  - ADR-0005: the old example "a status column the task names" became "a column the task names". The new text names no task, table or column (checked by the agent and the Spec reviewer).
  - `da-review main`: no actionable defect. Re-run after the fixes: none.
  - `eng:code-review main`, fixed in c780059:
    - "only status and counts" contradicted the N/A and NEEDS_INFO reasons.
    - "as the snapshot model had it" was ambiguous; now only removed lines may be copied.
    - CHECK 5/6 now have "or why N/A".
    - Shared `template_check_lines()` test helper.
  - Not fixing: the ADR-0005 test bans only the evidence task's words (a general check can't be automated); "about 100 characters" is soft, per the decided rule; "all changes have a basis" appears in both prose and template, on purpose.

- 2026-11-06 main session: PR #61 CI green; Codex GitHub app on c780059: no findings. Squash-merged to main as b49d12d. The real effect is measured in unified eval stage 1.
