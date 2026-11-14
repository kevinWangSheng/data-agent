# 68 — 查询任务的最终答案只答所问、名称紧跟数值

Status: done
Blocked by: —
Spec: ../../../docs/adr/0005-dab-integration.md（Update 2026-11-14）
Claimed by: claude-code (68-answer-format worktree)
Branch: worktree-68-answer-format

## Goal

查询任务结尾那句提示改成一条明确的答案写法规则，产品和 DAB 两条路径用同一句。agent 的最终消息先给答案，答案里只有题目问的东西。

## 已和用户定下的（2026-11-14，grilling Q2、Q3）

- 改两处的同一句话：`src/data_agent/agent.py:120`（`QUERY_PROMPT`）和 `evals/dab.py:99`（`TASK_PROMPT`）。两处文字必须一致；用一个共用常量还是各写一遍由实现定，但要有测试保证一致。
- 规则内容（英文措辞由实现定，四点都要有，不加别的规则）：
  1. 只答所问，不附带题目没问的行、列或实体。
  2. 每个名称后面紧跟它的值，中间不夹别的列。
  3. 名称和文字照数据里的原样写，不截断、不加省略号。
  4. 假设保留，放在答案之后，只写短句；不放表格，不列备选答案或备选算法。
- 规则是通用的：不出现任何基准、数据集、题目或评分脚本的名字和内容（ADR-0005）。

## Authority

- May edit: `src/data_agent/agent.py`（只动查询任务的提示）、`evals/dab.py`（只动 `TASK_PROMPT`）、`tests/test_agent*.py`、`tests/test_dab.py`、提示相关的现有测试
- Must not edit: `skills/`、`tests/acceptance/`、dbt 任务的提示、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- 现在的句子：`End with a final message that gives the answer first, then the assumptions you made.`
- 诊断（ADR-0005 Update 2026-11-14）：no-context 270 次里有 15 次答对了却被判错，原因有三种：名称和数值之间夹了别的列；名称被截断或加了省略号；在答案旁用表格列出别的候选。
- 工单 69 也会改这两个提示的开头（加轮数上限），和本工单改的不是同一行；后合并的一方解决冲突。
- 验证者（`verifiers = true` 的配置）读的是最终答案；改提示前先 grep 有没有别处引用这句话或依赖"assumptions"字样（验证器提示、Skill、测试）。有就一并核对，写进最终回复。

## Acceptance

- [ ] 新测试：产品查询提示和 DAB 任务提示都包含这条规则，且两处的规则文字相同（修复前会失败）。
- [ ] 新测试：规则文字里没有基准专属的词（至少断言不含 `DAB`、`benchmark`、`validate`、`grader`）。
- [ ] dbt 任务的提示不变（现有测试不改语义，全部通过）。
- [ ] `make check` 通过。

## Evidence required

- 测试输出。
- 主会话在合入前做一次真实运行：本地用 `tests/fixtures/` 的库跑一道查询题，贴出最终消息，核对四点规则。

## Stop conditions

- 发现验证器或别的组件依赖旧句子的格式，改了会破坏它。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted (PR #76 → 6a3b53b); CI green, Codex PR review no findings
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-14 ticket agent (opus), branch `worktree-68-answer-format`, commits de5f573, fbbd910; PR #76
  - Change: new constant `ANSWER_RULE` in `src/data_agent/agent.py`; `QUERY_PROMPT` ends with it and `evals/dab.py` imports it for `TASK_PROMPT`. dbt prompt unchanged. Rule text: "End with a final message that gives the answer first. The answer holds only what the task asks for: no rows, columns or entities it did not ask about. Put each value right after its name, with no other column between them. Write names and text exactly as they appear in the data; do not shorten them or add an ellipsis. After the answer, state your assumptions in short sentences: no tables, no alternative answers or alternative methods."
  - Other uses of the old sentence: only `tests/test_task_file.py` (updated). Verifiers run on dbt tasks only (`src/data_agent/verifiers.py:6`); nothing parses the old format. No stop condition hit.
  - Tests: `tests/test_agent_prompts.py` (3 tests; the first fails on main). `make check`: 2084 passed before, 2087 passed, 1 skipped after.
  - `da-review main`: no findings (its sandbox could not run pytest). `eng:code-review main`: fixed in fbbd910 — snapshot test compared the rule with itself; a comment used grader wording; dbt test strengthened. Not fixed: phrase-by-phrase asserts break on rewording (deliberate); the test splits the prompt on blank lines (low risk).
- 2026-11-14 main session, real run on the branch: `data-agent run --task "Which three customers spent the most in total, and how much did each spend?" --type query --duckdb <db built from tests/fixtures/shop.sql> --config no-context`. Final message: "Avery Lane spent 285.25, Blake Moss spent 232.50, Emery Cole spent 185.75." then "Assumptions:" with two sentences; no table, no alternatives. All four rules hold.
