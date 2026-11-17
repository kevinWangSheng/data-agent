# 87 — 答案没点名查询时，结果表取答案之前最后一条成功的查询（标明是推断）

Status: in-review
Blocked by: 79（done）
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-16 (filled in by the main session)
Branch: worktree-87-answer-table-fallback

## Goal

新的查数运行在运行页的答案旁边也有结果表：`answer.sql_tool_use_id` 为 null 时，视图模型取答案之前最后一条成功的查询，并标明这是推断的；页面照这个标记写出来。

## Authority

- May edit: `web/src/run-view/`（除 `acceptance/` 外；`FinalAnswer` 可以加字段，不改、不删现有字段）、`web/src/run-page/`（只动答案和结果表的显示）、对应的单元测试
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/`、`tests/fixtures/web/`）、`src/data_agent/`（agent 不改，`answer` 事件照旧写 null）、`evals/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "旧事件文件缺新字段时的退路"新加的一条（2026-11-16 人定）。
- 起因：主会话在工单 79 的真实运行里发现（见 79 的 Comments）。agent 写 `answer` 事件时 `sql_tool_use_id` 总是 null（`src/data_agent/agent.py:1451`，工单 74 "不要猜"）；`buildRunView` 的答案（`web/src/run-view/model.ts` 第 534 行起）只在没有 `answer` 事件的旧文件里退到"之前最后一条成功的查询"，于是所有新的查数运行都没有结果表，违反 plan §2 硬约束"最终答案和结果表不用滚到底"。验收测试的 new-fields 夹具里答案点名了 `t-q1`，所以没测出来。
- 要做：
  - `answer` 事件的 `sql_tool_use_id` 为 null 时，用和旧文件同一条规则（答案之前最后一条成功的查询）；`FinalAnswer` 加一个字段说明查询是推断的（如 `sqlInferred: boolean`）：agent 点名的为 false；新文件 null 退回的、旧文件退回的都为 true。
  - 答案依据的查询改变后，阶段划分（查数运行"回答"阶段从答案依据的查询开始）跟着用同一条查询，保持一致。
  - 页面（`Side.tsx` 答案和结果表那里）：推断的写 "last query before the answer"（或同义的英文），点名的照旧 "from query #N"。
- 只用 pnpm。

## Acceptance

- [ ] 单元测试：`answer` 事件点名查询 → 用它，`sqlInferred` false；`answer` 事件 `sql_tool_use_id` null → 取之前最后一条成功的查询、有表、`sqlInferred` true；之前没有成功的查询 → `sqlStep` null、没有表；旧文件（没有 `answer` 事件）照旧且 `sqlInferred` true
- [ ] 前端验收测试不改且全过；现有单元测试只在需要加新字段的断言处改
- [ ] 主会话 79 那次真实运行的事件文件（`/Users/shenghuikevin/dev/AI/data-agent/.claude/worktrees/79-web-local-frontend/runs/t79-real/web/20261116T163248Z-8ea724/`，只读，不复制进仓库）喂给 `buildRunView`：答案依据最后一条 `query_database`，表是 4 行 × 3 列（Gold 710.00 在第一行）
- [ ] `make check` 通过

## Evidence required

`pnpm --dir web test` 输出；`make check` 输出；上面那份真实事件文件的输出（答案、依据的查询、表的行列数）；运行页答案区的截图（推断的标签可见）。

## Stop conditions

- 要改验收测试或 `src/data_agent/` 才做得到。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: in-review
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-16 主会话：人定"同一条退路、标明是推断"（不改 agent 的提示，不影响评测）。
- 2026-11-16 工单 agent（0bcb0e4、e119f1b）：
  - 视图模型：`answer` 事件 `sql_tool_use_id` 为 null 时取答案之前最后一条成功的查询（和旧文件同一条规则），"回答"阶段从同一条查询开始；`FinalAnswer.sqlInferred` 推断时为 true（新旧文件都是）。页面 `Side.tsx`：推断的在答案和结果表头写 "last query before the answer · #N"（带 tooltip），点名的照旧 "from query #N"。
  - 偏离工单措辞：`sqlInferred?: true`（推断为 true，否则不存在），不是 `boolean`——验收测试 `run-view.test.ts:325` 对点名答案 `toEqual({text, seq, sqlStep, table})`，带 `sqlInferred: false` 会失败，验收测试不能改。主会话认可（工单只是举例）。
  - 测试：220 → 224 passed（4 个新单元测试：点名 → 用它、没有 sqlInferred；null → q1、有表、sqlInferred true、阶段 orient/explore/answer[q1..]；之前无成功查询 → sqlStep、table 为 null；旧文件照旧、sqlInferred true）。验收测试未改、全过。make check exit 0：pytest 2307 passed, 1 skipped；web 224 passed。
  - 真实事件文件（79 的真实运行）：sqlStep = 最后一条 `query_database`（9 条里第 9 条），sqlInferred true，表 4 行 × 3 列，首行 Gold | 13 | 710.00；阶段 orient 1、explore 8、answer 1。截图（worktree 的 `.playwright-mcp/t87-run-page-answer.png`，主会话看过）：答案和结果表头都写 "last query before the answer · #10"，表 4 行。
  - da-review（Codex）：无问题。eng:code-review：Standards 无硬违规，修注释和参数名；不修两处按钮重复、三处构造对象。Spec 记一条：dbt 运行的新 `answer` 事件也是 null，"Final report" 会显示自查阶段最后一条查询的表并标为推断——spec 那条没限定查数运行，旧文件本来也这样，不改。
