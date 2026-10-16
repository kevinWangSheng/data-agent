# 14 — 失败归因报告

Status: done
Blocked by: 03, 10
Spec: ../spec.md
Claimed by: claude (opus-5-5)
Branch: worktree-14-failure-attribution

## Goal

对开发集失败题，一条命令让 agent 起草报告：每题一个 FM1–FM4 标签、引用相关 Skill 原文、给出通用修改建议；不自动改 Skill。

## Authority

- May edit: `evals/`、`src/data_agent/`（只限归因入口）、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $2

## Context

CONTEXT 的 Failure mode、Failure attribution loop；plan §3「失败 → 改 Skill」；ADR-0005（只用 FM1–FM4 的分类思路；judge 提示自己写，不照抄 DAB）。只读开发集，绝不读留出集。

## Acceptance

- [ ] 对夹具或真实开发集失败题产出报告
- [ ] 建议不含任何题号、表名等榜单专属内容
- [ ] 只读开发集
- [ ] `make check` 通过

## Evidence required

一份真实报告；花费。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3
- Review rounds: 3（本轮 da-review × 1，无意见）
- Model spend: $0.1468（之前 $0.1281；本轮 judge $0.0187）
- Outcome: 退回第 2 轮 2 条全修，各配修复前会失败的测试；夹具 job 出真实报告；`make check` 绿；da-review 无意见
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `428df6f`、`7567624`、`ed1b3a3`）

**实现**：`python -m evals attribute JOB_DIR --manifest M --out DIR [--tasks-dir D] [--budget USD]`（`evals/attribute.py`，注册于 `evals/__main__.py`；`tests/test_eval_attribute.py` 15 个测试，用假 judge）。
- 只读开发集：读每个 trial 的 `result.json` 判定所属题；不在 `manifest["dev"]` 的只计数，不打开事件、验证输出、题面（测试把留出集事件文件 chmod 0 证明）。
- judge：每道开发集失败题一次无工具的 Claude Agent SDK 调用，读轨迹、全部 Skill、题面与验证输出；`DATA_AGENT_*` 配置；提示自写，不抄 DAB。
- 标签 FM1–FM4，措辞同 CONTEXT。引用必须逐字出现在所点名的 Skill 里。
- 建议不得含 manifest 题号、运行 SQL 里的表或列、轨迹/题面/验证输出里的 snake_case 或 `schema.table` 名；Skill 已用的词放行。不合格重试一次，仍点名具体对象则扣下建议。
- 按调用计费，到预算不再调用（含重试）；基础设施错误不归因；已知密钥打码；输出 `attribution.json` 与 `report.md`，不写 `skills/`。

**真实运行**：没有真实开发集 Harbor job，用 shop 夹具跑真 agent 4 次，包成 Harbor 形状的 job（`runs/14/`，不进 git）。agent 花费 $0.0346（`python -m evals spend runs/14/events --budget 2`）；4 题对 3 题，"How many customers are repeat buyers?" 答 10，期望 7。
```
$ python -m evals attribute runs/14/job --manifest runs/14/manifest.json --tasks-dir runs/14/tasks --out runs/14/report
attribute: 1 of 1 dev failures attributed, $0.0189 spent -> runs/14/report
```
报告标 FM4，逐字引用 `sql-workflow` 的 "**Case sensitivity**: Use the correct case-insensitive function for your backend"；找到真实原因（`LOWER(status)='completed'` 漏掉 `'COMPLETE'`）；建议不含表名与题号（列出状态列的不同取值、归并到规范状态、答前核对计数）。job 里另放一个带哨兵事件文件的留出集失败题，`grep HOLDOUT runs/14/report/*` 无结果。

**审查**：`da-review main` 4 条全修（`7567624`，各配测试）：重试可超预算；重试抛错时丢花费；只出现在 `list_tables` 输出里的表名未拦；报错文本未打码。`eng:code-review main` 修 5 条（`ed1b3a3`）：docstring 误称不打开留出集 `result.json`；FM2/FM4 措辞超出 CONTEXT；lazy judge 改用 `functools.cache`；两个魔数改常量；删多余的 `Failure` dataclass。

**不修的理由**：模块约 570 行，各节独立，拆分会打散一个功能；`mask` 不复用私有的 `EventLog._mask`；测试里 `sys.path` 插入（假 judge 无法经子进程注入，`pyproject` 不在 Authority）；`check_reply` 返回元组与重复的 entry dict 属小问题；名称检查不拦 Skill 也用的单词表名或只以裸名出现的表名——Skill 词汇按政策是通用的，其余靠提示规则与人审。

**已知限制**：引用对照当前 Skill（运行用的版本不同时报告会注明）；预算在重试中途耗尽时丢弃首次回复；真实开发集报告要等有 Harbor 开发集 job（09/12 之后）。

### 2026-10-16 第 7 步：PR #7 退回第 1 轮（主会话）

退回：原因如下，在原分支 `worktree-14-failure-attribution` 上修，修完提交，不 push。

1. **CI 失败（合并后）**：PR 的 merge 检查 basedpyright 报 `evals/attribute.py:422:26 Argument missing for parameter "skills_dir"`。工单 05（PR #6）已合并，`skill_version()` 现在要 `skills_dir`。先 `git rebase origin/main`，再按 05 的运行配置取 Skill 目录。
2. **Codex P1（`evals/attribute.py:222`）属实**：`specific_names` 把 Skill 里出现过的词从全部名字里减掉，连运行 SQL 和 schema 输出里观测到的表名、列名也一起放行（例：Skill 含 `orders`，"always query orders" 不会被拦）。改为：从 SQL 与 schema 工具输出观测到的源标识符一律拦，不受 Skill 词表豁免；Skill 词表只豁免从自由文本里正则抓到的 snake_case。配一个修复前会失败的测试。
3. **Codex P1（`evals/attribute.py:329`）属实**：运行的版本头 `skill_version` 与当前 Skill 不同时，judge 看到的、引用核对用的都是当前 Skill。改为：版本不一致且拿不到该版本 Skill 时拒绝归因（记 error，不出标签），不只是报告里加提示。配测试。
4. **Codex P2（`evals/attribute.py:363`）属实**：最后一次重试后引用仍未逐字核对通过（`verified` 为假、`problems` 为空）时，条目被当成完成的归因保存。改为：最终仍未核实的引用使该条归因无效（`failure_mode=None` 并写 error）。配测试。
5. **Codex P2（`evals/attribute.py:301`）属实**：预算表只在调用前看已花费，单次调用不受剩余额度约束（两次 $1.20 在 $2 预算下都放行），SDK judge 还拿到整笔预算当单次上限。改为把剩余额度作为每次调用的上限传给 judge。配测试。

修完重跑 `make check`、真实运行一次 `python -m evals attribute`（同之前的夹具 job），再跑一轮 `da-review main`；最终回复给出要追加的 Comments 与 Ledger（Attempts 2）。

### 2026-10-16 Ticket agent: returned round 1 fixed (commits `742f761`, `3199da0`, `df6afb9`; after `git rebase origin/main`)

**Fixes, one per returned item (each has a test that failed before the fix):**
1. **CI**: rebased onto `origin/main`. `skill_sets()` computes `skill_version(d)` for `SKILLS_DIR` and for every run config's `load_config(name).skills_dir`. The report header no longer calls `skill_version()` without the Skill directory.
2. **Name filter**: names come from two places now.
   - Names the run actually saw are always blocked, even if a Skill uses the same word. These are manifest task ids, tables and columns parsed from the SQL, and tables and columns from `list_tables` output (`schema.table (kind): cols`).
   - Snake_case and `a.b` names guessed from free text are still let through when a Skill uses them.
   - Tests: `test_names_the_run_observed_are_specific_even_if_a_skill_uses_them` (`orders`/`customers`) and `test_a_skill_word_seen_only_in_free_text_is_allowed`.
3. **Skill version**: the run's version header `skill_version` is looked up in `skill_sets()`, and the judge reads and the quote check uses that version's Skills. A no-Skill run (`"none"`) is judged with no Skills. If the version isn't available, the failure is not attributed and no judge call is made.
   - Tests: `test_a_run_on_an_unavailable_skill_version_is_not_attributed` and `test_the_judge_sees_and_quotes_the_skills_of_the_run_version`.
4. **Unverified quote**: if the quote still doesn't match the Skill word for word after the last retry, the entry gets `failure_mode=None` and the error "skill_quote is not verbatim …". The `quote_verified` field and the report's "NOT found verbatim" branch are removed.
   - Test: `test_quote_never_found_in_the_skill_voids_the_attribution`.
5. **Per-call budget**: the `Judge` signature is now `(prompt, max_budget_usd)`. `Meter` passes `budget - spent` to each call, and `sdk_judge` uses that as the call's `max_budget_usd`.
   - Test: `test_each_call_is_capped_at_the_remaining_budget` (two calls at $1.20 against $2 get caps of [2.0, 0.8]).

**Real run**: same fixture job as before. Commands and outputs are above; the earlier run on `742f761` cost $0.0248.
- The repeat-buyers failure was labelled FM3 in this run and FM4 in the earlier one. The judge varies on this borderline case (status-value mapping: is it the definition or the implementation?).
- The evidence cites the real cause: case variants of the status values ('COMPLETE' missed).
- The suggestion contains no table names or task ids.
- `grep HOLDOUT runs/14/report3/*` finds nothing.

**Review**: `da-review main`, 2 rounds, both findings confirmed against the code and fixed:
- **Round 1, P2**: `list_tables` content can come as a list of SDK text blocks, in which case no names were extracted. Fixed in `3199da0` with test `test_names_in_a_listing_given_as_sdk_text_blocks_are_specific`.
- **Round 2, P1**: when the SDK yields an error `ResultMessage` and then raises `ResultError`, `sdk_judge` lost the charge, so the budget was under-counted. Now the charge is kept the same way `agent.run` does it. Fixed in `df6afb9` with test `test_sdk_judge_keeps_the_cost_of_a_result_followed_by_an_sdk_error`.
- No third `da-review` was run after `df6afb9`, because that hit the 2-round limit. `eng:code-review` was not run this round, since the return only asked for `da-review`.

**Known limitations (new)**:
- The SDK's `max_budget_usd` is enforced by the CLI only after each API call, so one call can still go slightly over the remaining amount. `Meter` refuses the next call once the budget is used up.
- Observed column names that are common words (e.g. `status`, `amount`) are now always blocked, so the judge often needs one retry to reword. The run on `742f761` took 2 attempts.

**主会话**：分支已 rebase，推原 PR #7 需要 force push（AGENTS.md 禁止），改推 `worktree-14-failure-attribution-r1` 开新 PR，关闭 #7。

### 2026-10-16 第 7 步：PR #10 退回第 2 轮（主会话；最后一轮）

CI 两次全绿。退回：原因如下，在分支 `worktree-14-failure-attribution` 上修，修完提交，不 push、不 rebase（需要同步 main 时用 `git merge origin/main`）。主会话推到 `worktree-14-failure-attribution-r1`（PR #10）。

1. **Codex P2（`evals/attribute.py:262`）属实**：只经题面、验证输出或读本地 schema 文件（`Read` 结果）见到的表，若名字是普通单词（如 `orders`），既非 snake_case 也非带点名，不进 `guessed`，"Inspect orders first" 能通过；FM1 类失败常常没跑 SQL、没调 `list_tables`。改为：从 schema/DDL 形状的内容里收集标识符并一律拦（不受 Skill 词表豁免）——`Read` 到的 dbt YAML 里 `name:` 条目、`.sql` 文件与题面里 `CREATE TABLE/VIEW`、`from`/`join` 后的名字、题面与验证输出里反引号包起来的标识符。配修复前会失败的测试。
2. **Codex P2（`evals/attribute.py:595`）属实**：`--manifest` 是坏 JSON 时 `json.loads` 在 `try` 外抛出，CLI 打 traceback 而不是 `attribute: ...` 加退出码 2。把读 manifest 挪进受保护的段。配测试。

这是第 2 轮（最后一轮）。修完后名称过滤若还有启发式漏洞，记成已知限制：报告是草稿，每条建议由人审，工具不自动改 Skill。修完 `make check`、真实运行一次 `python -m evals attribute`（同夹具 job），再跑一轮 `da-review main`；最终回复给出要追加的 Comments 与 Ledger（Attempts 3）。

### 2026-10-16 工单 agent：退回第 2 轮已修（commit `c56dd0e`，未 rebase、未 push）

**修复（每条都配了修复前会失败的测试，4 个新测试在修复前全红）：**

1. **名称过滤漏掉只在 schema 内容里出现的普通单词**（`evals/attribute.py` 的 `specific_names`）。下面这些名字现在一律拦，Skill 也用这个词也不放行：
   - **Agent 用 `Read` 读到的 schema 文件**：`tool_end` 用 `tool_use_id` 对回 `tool_start` 的 `file_path`，先去掉 Read 输出里的行号前缀。
     - `.yml`/`.yaml`：取所有 `name:` 条目，包括模型和列。
     - `.sql`：取文件名本身、`CREATE TABLE/VIEW` 后的名字、`from`/`join` 后的名字、`ref()`/`source()` 里的名字。
   - **题面与验证输出**（`prose_names`）：
     - 反引号包起来的标识符；
     - `CREATE TABLE/VIEW` 后的名字；
     - 大写 `FROM`/`JOIN` 后的名字；
     - 代码块和行内代码里 SQL 形状的名字。
   - **不照工单字面做的一处**：普通文字里小写的 `from` 是英文介词（"comes from the …"），照字面取会把 "the" 当名字拦掉。所以普通文字里只认大写的 `FROM`/`JOIN`，小写的只在代码里认。测试里有断言确认 "the" 不会被拦。
   - 测试：
     - `test_names_in_a_dbt_yaml_the_agent_read_are_specific`（用真实 Read 输出的 `N\t` 行号格式）
     - `test_names_in_a_sql_file_the_agent_read_are_specific`
     - `test_names_the_instruction_and_verifier_show_are_specific`
2. **`--manifest` 是坏 JSON 时 CLI 打出 traceback**：读 manifest 挪进受保护的 `try` 段。另外顺手兜住两种情况，同样输出 `attribute: …` 并退出码 2：
   - 文件不存在（捕获 `OSError`）；
   - manifest 不是带 `dev` 列表的对象。

   测试：`test_cli_reports_an_unreadable_manifest_without_a_traceback`（真子进程，断言退出码 2、stderr 以 `attribute: ` 开头、没有 `Traceback`）。

**检查**：`make check` → `558 passed, 15 deselected, 15 xfailed`（输出存在 `runs/14/check-r2.txt`）。`tests/test_eval_attribute.py` 共 26 个测试全过。

**真实运行**（同一个夹具 job，脚本 `runs/14/attr3.sh`）：
```
$ python -m evals attribute runs/14/job --manifest runs/14/manifest.json --tasks-dir runs/14/tasks --out runs/14/report4
attribute: 1 of 1 dev failures attributed, $0.0187 spent -> runs/14/report4
exit=0
```
- 结果：repeat-buyers 这题这次标 FM2，前两次分别是 FM4、FM3，judge 在这道边界题上仍然摇摆。
- 证据找到了真实原因：状态列同一个值有多种大小写写法，agent 自己算出了两种结果，最后交了 10。
- 引用逐字核对通过：`sql-workflow` 的 "Metrics match the question's definition …"。
- judge 调了 2 次（第一次回复被拒后重试），最终建议没有点名任何名字（`specific_names` 为空）。
- `grep -c HOLDOUT runs/14/report4/*` 两个文件都是 0。

**审查**：`da-review main` 跑了 1 轮（输出在 `runs/14/da-review-4.txt`），结论 "No actionable regressions were found"，没有要修的。这一轮没跑 `eng:code-review`，因为退回只要求 `da-review`。

**已知限制**（工单要求记录；报告是草稿，每条建议由人审，工具不自动改 Skill）：
- 只解析 `Read` 读到的 `.yml`/`.yaml`/`.sql` 文件。经 `Bash`（如 `cat`）、`Grep` 或其他文件类型看到的普通单词表名不会进入一律拦截的名单。
- 题面普通文字里，小写 `from`/`join` 后面没加反引号的普通单词表名不会被拦。
- 正则只认 `name:` 形式的 YAML 条目和常见的 DDL、ref/source 写法。

### 2026-10-16 第 7 步：PR #10 复审（主会话）

`c56dd0e` CI 两次全绿。Codex 复审 2 条 P2，属实；退回已用满 2 轮，按流程记为已知限制，合并：
- `evals/attribute.py:562` 打码在序列化后的整段 JSON 上做替换：密钥很短（如 `DATA_AGENT_API_KEY=1`）时会改坏数字和键，`json.loads` 失败、报告写不出（judge 花费已发生）。应像 `EventLog._mask` 一样只递归处理字符串值。
- `evals/attribute.py:518` manifest 的 `dev` 列表含非字符串元素（如 `[[]]`）时 `set(...)` 抛 `TypeError`，CLI 打 traceback。应先校验每个 task id 是字符串。
两条都小，下次有工单改 `evals/attribute.py` 时顺手修。
