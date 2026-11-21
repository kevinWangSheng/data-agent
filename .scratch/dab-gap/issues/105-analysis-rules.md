# 105 — sql-workflow 补通用分析规则

Status: in-review
Blocked by: —
Spec: ../spec.md（"通用分析规则（105）"）
Claimed by: claude (105 worktree)
Branch: worktree-105-analysis-rules

## Goal

`skills/sql-workflow/SKILL.md` 有 spec 列的 7 条通用分析规则，第 37 行和第 50 行不再矛盾；有一个测试保证 `skills/` 里不出现 DAB 数据集名。

## Authority

- May edit: `skills/sql-workflow/SKILL.md`（含文件头的改动说明）、`tests/`（新测试）
- Must not edit: 其他 Skill、`src/`（含 `ANSWER_RULE`）、`evals/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（验证运行由主会话做）
- Spend cap: $0

## Context

- 规则清单和写法要求：spec "通用分析规则（105）" 的 1–7 条。每条一个不点名基准的例子（照现有第 7 节的 orders/customers 风格）。
- 思路来源（只读，不复制原文）：DUO 的 `benchmark.md` 通用契约、Stela 的 `analysis-verification` skill；摘要在 `docs/research/2026-11-21-dab-same-model-gap.md`。DAB fork 没有许可证，Stela 的 skill 没有声明许可，AGENTS.md 禁止从无许可证仓库复制。
- 这个 Skill 是从 signalpilot-plugin 复制的（`NOTICE`），文件头已有改动说明，照格式补一句。
- ADR-0005：不点名数据集或题目，不写针对评分脚本行为的规则（例如"评分只认其中一个"）。
- DAB 的 12 个数据集：agnews、bookreview、crmarenapro、DEPS_DEV_V1、GITHUB_REPOS、googlelocal、music_brainz_20k、PANCANCER_ATLAS、PATENTS、stockindex、stockmarket、yelp。
- 注意 Skill 长度：现在 135 行；补完保持在约 200 行以内，规则写短。

## Acceptance

- [ ] 7 条规则都在，各带一个通用例子；第 37、50 行的并列矛盾消除。
- [ ] 新测试：`skills/` 下所有文件不含上面 12 个名字（大小写不敏感）；故意放一个名字进临时文件时测试会失败（测试自己验证这一点）。
- [ ] `tests/test_skills.py` 通过；`make check` 通过。

## Evidence required

`make check` 输出；Skill 改动前后的 diff 摘要（每条规则一行）。效果验证由主会话按 spec "验证"第 2 步做。

## Stop conditions

- 某条规则写不出不点名基准的版本。
- `tests/acceptance/` 里的测试因 Skill 文本变化变红。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1 (fixes in a4508cc; no second round needed)
- Model spend: $0
- Outcome: 7 rules added, top-N contradiction removed, dataset-name test added; verification run pending in the main session.

## Comments

**What changed in the Skill** (`skills/sql-workflow/SKILL.md`)
- Lines 37 and 50 (section 2): "top N" no longer says `LIMIT N` or "at most N rows". It now says to use `RANK()` in a subquery, then `WHERE r <= N`, and to check that any rows beyond N are tied with the Nth row.
- New section 8, "Analysis Rules", one line per rule, each with an `orders`/`customers` example:
  - 8.1 Top N keeps ties: use `RANK()`, keep rows tied with the Nth, and don't add a tie-breaker the question didn't give (example SQL included).
  - 8.2 Bound words: "at least" means `>=`, "more than" means `>`; every constraint must hold on every final row and is checked before answering.
  - 8.3 Time windows: filter on the date of the event the question names (`order_date`, not `ship_date`); a duration runs to the event that completes it.
  - 8.4 Join keys: sample both sides, normalize them, then check the match rate with `COUNT(DISTINCT order_id)` and `FILTER`; a low rate gets investigated first.
  - 8.5 Values stored as text: parse number-, list- and dict-shaped strings; no substring matching (`'["giftcard"]'` with `LIKE '%gift%'`).
  - 8.6 Column choice: use the code or label column the question literally names; names in the answer are copied from the data as stored.
  - 8.7 Recompute: compute the final number a second way; if the two differ, find out why. The turn budget in section 6 still applies, so at 3 turns left give the best-supported answer.
- The header note gained one sentence describing section 8 and the section 2 change.

**Tests** (`tests/test_skills.py`)
- `test_skills_name_no_dab_dataset`: no file under `skills/` contains any of the 12 dataset names (case-insensitive).
- `test_the_dataset_name_check_catches_a_planted_name`: copies `skills/` to a temp directory, plants a file mentioning "Yelp", and asserts the check reports `("sql-workflow/notes.md", "yelp")`.

**Checks**
- `make check` passes:
  - ruff: "All checks passed!"
  - basedpyright: "0 errors, 0 warnings, 0 notes"
  - pytest: "2541 passed, 2 skipped, 77 deselected"
  - web: "Test Files 20 passed / Tests 320 passed"
- No tests under `tests/acceptance/` went red.
- I ran the 8.4 match-rate SQL in DuckDB with a deliberately duplicated customer key and got `(3, 1)` as expected. The 8.1 query also runs in DuckDB (Fable checked it). Neither was run on Postgres; both are standard SQL.

**Real run (step 3)**: none. The only change is Skill text. The ticket's Authority gives no server access and a $0 spend cap, and its Evidence section hands the effect check to the main session (spec "验证" step 2).

**Review 1 (Fable in place of `da-review`; Codex returned "model not supported when using Codex with a ChatGPT account")**. Fixed in a4508cc:
1. 8.7's "answer only when both agree" contradicted section 6's "answer at 3 turns left", so an agent could run out of turns with no answer. 8.7 now defers to the turn budget, and the `orders` vs `order_items` revenue example (which can legitimately differ) became a count from a detail table vs a summary table.
2. The 8.5 example didn't show the failure it described. The value is now `'["giftcard"]'`.
3. Line 52's "has N rows" was wrong when fewer than N candidates exist.
4. "Tied at rank N" is wrong for `RANK()` (with counts 5,3,3,3 the ties have rank 2). It now says "tied with the Nth row". Line 39 also no longer shows a window function as a bare predicate.
5. The 8.4 match-rate query was inflated by duplicate keys and only normalized one side. It now uses distinct counts and normalizes both sides.

Not fixed:
- `duckdb-sql` and `postgres-sql` still show `ROW_NUMBER() <= 10`, which cuts ties. Those files are outside this ticket's Authority, so this is a candidate follow-up ticket.
- An agent might read tied extra rows as the "alternative answers" `ANSWER_RULE` forbids. The spec forbids changing `ANSWER_RULE`, so watch for this in the verification run.
- The name test only catches the 12 exact names. Spelling variants are not caught, and common words like "patents" or "yelp" could cause false positives. This matches the spec's literal wording.

**Review 2 (`eng:code-review main`)**
- Spec axis: line 52's wording was fixed in a4508cc. I removed `test_top_n_keeps_ties_throughout_sql_workflow`, because the spec's Testing Decisions says the rules get no unit tests. Not fixed: the planted-name test checks the helper rather than the test function itself (nit; both share one helper with a trivial `== []`). Also not fixed: `rglob` scans untracked files such as `__pycache__`, which is harmless and matches "skills/ 下所有文件".
- Standards axis: no hard violations. `NOTICE` already covers "modified as noted at the top of each file". Not fixed: the concern that 8.6 ("names copied as stored") is close to answer-format guidance, because spec rule 6 asks for it verbatim ("答案里出现的名字从数据里原样取"). Also not fixed: the overlap between 8.2 and section 7, a minor judgement call.
