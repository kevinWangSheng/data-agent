# 106 — classify_text：未定结果、只重跑没标上的行、多列文本

Status: done
Blocked by: —
Spec: ../spec.md（"分类工具（106）"）
Claimed by: claude (worktree 106-classify-unresolved-resume)
Branch: worktree-106-classify-unresolved-resume

## Goal

`classify_text` 能把拿不准的行记成 `unresolved`；能只对上一次没标上的行再调模型并合并结果；能一次给几列文本。产品和 DAB 两个工具服务器行为一致。

## Authority

- May edit: `src/data_agent/classify.py`、`src/data_agent/mcp_server.py` 和 `evals/dab_server.py`（只限这个工具的参数和注册）、`tests/test_classify.py` 和新测试、`tests/fixtures/`（新的非基准小样本）
- 例外（人 2026-11-21 批准，照工单 100 的先例）：可以重新生成 `tests/fixtures/chat/eval-unchanged.json`，因为本工单有意给 `classify_text` 加参数；`tests/acceptance/test_chat_eval_unchanged.py` 的代码一字不改。快照差异只能是 `classify_text` 的参数变化，新旧差异贴进 Comments，合入前由人审。
- Must not edit: `skills/`、提示词（`agent.py` 里的任务提示）、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- 现状：`src/data_agent/classify.py`。结果类型 `LABELED`、`OUTSIDE`、`NO_LABEL`、`FAILED`、`EMPTY`（`:45-49`）；`ROW_LIMIT` 20,000，`BATCH_ROWS` 50，并发 8；模型调用关思考（工单 71）；预算和花费计入（工单 70、72）。
- 工单 70 的规则全部沿用：取数走只读网关、超限报错不截断、预算用完停下并报告、密钥不出现在事件和输出里、工具返回事实不返回猜测。
- 思路来源（只读，不复制原文）：Stela `semantic-analysis` 的 unresolved、`resume=`；摘要在 `docs/research/2026-11-21-dab-same-model-gap.md`。
- 设计要点见 spec：`unresolved` 用固定标记，写进系统提示，单独计数；重跑参数指向上次存下的结果，只对 `failed`、`no_label`（和可选的 `unresolved`）再调模型；多列按列名拼接。工具描述（`DESCRIPTION`）同步说明新参数。

## Acceptance

- [ ] 假模型对某行回固定的"拿不准"标记：该行 `unresolved`，不进任何类别计数，返回里单独计数（测试）。
- [ ] 重跑：上次结果里有 `failed`、`no_label` 的行，假模型只收到这些行；已标上的行原样保留；返回写明新调的行数（测试）。
- [ ] 重跑途中预算用完：照工单 70 停下，已有标签保留（测试）。
- [ ] 多列：两列文本按列名拼进同一行的提示（测试）。
- [ ] 不给新参数时，行为、提示和返回格式和现在一致（测试）。
- [ ] 产品和 DAB 两个服务器都注册了新参数（测试）。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出。最终回复写好主会话用的命令：`make test-integration` 里分类相关的那条，加一次带重跑参数的本地运行。

## Stop conditions

- 重跑没法从上次存下的结果可靠地找到没标上的行（例如结果没存键）。
- 需要改 Skill 或提示才能让新参数被用上（另开讨论）。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3
- Review rounds: 2
- Model spend: $0
- Outcome: Codex P2 return fixed (DAB resume bound to source db_name, test red before fix); round-2 reviews no bugs, one lock nit fixed; snapshot unchanged since dda39b1

## Comments

**第 1 轮（交回，Status: ready-for-human，分支 `worktree-106-classify-unresolved-resume` 3e89d44）**

**What was built** (files changed: `src/data_agent/classify.py`, `src/data_agent/mcp_server.py`, `evals/dab_server.py`, `tests/test_classify.py`):
- **`allow_unresolved`** (off by default): only when it is on, the system prompt tells the model it may answer the fixed mark `__unresolved__`. Such a row gets outcome `unresolved`, is counted on its own line and goes into no category. A category named like the mark is refused before any model call.
- **`resume_from`** (plus optional `retry_unresolved`): points at a label table from earlier in the run (`labels_<n>` on the product server, a result id on the DAB server).
  - The query is fetched again. The model only gets rows the earlier table left `failed` or `no_label` (and `unresolved` if asked), plus rows the table doesn't have, such as rows a budget stop left out. All other rows are kept exactly as they were.
  - The merged table is stored as a new one, and the reply says how many rows were sent and how many kept.
  - The call is refused before any model call if the table is unknown, isn't a label table, has different columns, or holds labels outside this call's categories.
  - The product server keeps each label table's original values in memory. I did this because `label_tables.py` is outside the ticket's Authority, so I couldn't read them back from there.
- **`text_column` takes a list**: each row's text is its non-empty columns, one `name: value` line each. No other hint is added.
- **Budget stop during a resume**: follows ticket 70's rule. Rows to retry that the budget left out keep their old outcome, and the reply says so. They are not reported as "not classified".
- **Without the new parameters**, the system prompt, user prompt and reply match the old ones word for word. That test's expected values came from the old code.
- Both servers register the same parameters, and the tool description explains them.

**Checks:**
- `uv run pytest tests/test_classify.py -q` → `57 passed, 1 deselected`. There are 14 new tests, one or more per acceptance item.
- `make check`: ruff `All checks passed!`, basedpyright `0 errors`, pytest `2 failed, 2550 passed, 2 skipped, 77 deselected`. Both failures are in `test_chat_eval_unchanged.py` (`test_harbor_dbt_run_gets_the_same_options`, `test_dab_query_session_gets_the_same_options`). The only difference is `mcp_tools` → `classify_text` parameters: the snapshot records 6 parameters on the DAB path and 5 on the Harbor path, all from before this ticket.
- An earlier full run showed 30 `test_dab` failures. That run overlapped my edits; `tests/test_dab.py` passed on its own afterwards and in the final run.

**Local run (step 3):** I used the real `data-agent mcp-server` process over stdio with the local fake endpoint, not a real model, because the worktree can't load the model env. Script: `/tmp/106-run/run.py`; full output: `/tmp/106-run/output.txt`.
- First call (two text columns, `allow_unresolved`): `20 rows fetched; 17 labeled`, `unresolved: 1`, `no_label: 2`. A text as sent: `'title: Item 1\nbody: Heavy rain is expected ...'`
- Resume (`resume_from=labels_1`, `retry_unresolved`): `Resumed from labels_1: 3 rows sent to the model (failed, no_label or unresolved there, or not in it); 17 rows kept as they were.` → `20 labeled`. The model received 3 texts.

**For the main session to run (real model):**
- `make test-integration` covers `tests/test_classify.py::test_the_run_model_classifies_the_samples`.
- A run with the resume parameter: `uv run python /tmp/106-run/run.py "$PWD" /tmp/106-run/out-real`, after pointing `DATA_AGENT_*` at the real model. As written the script uses the fake endpoint, so with a real model the first call's unlabeled rows come from the model rather than the script's rigged answers.

**Reviews:** I didn't run `da-review main` or `eng:code-review main`, because I stopped at the red acceptance test. They are still to do after the decision.

**2026-11-21 人定**：批准重新生成 `eval-unchanged.json`（见 Authority 的例外）。在原分支接着做：重新生成快照、`make check`、两道审查。

**Round 2 (continued on the same branch, commits b192af0, b5306ed, dda39b1)**

**Snapshot regenerated** (Authority exception; `tests/acceptance/test_chat_eval_unchanged.py` not changed)
- **How:** a throwaway script `/tmp/106-regen/test_regen_106.py` imports the acceptance module, calls its `harbor_dbt` / `dab_query` / `surface` and rewrites the JSON. It is outside the repo and not committed.
- **Structural check:** across both entries (`dab-query`, `harbor-dbt`), the only top-level field that changed is `mcp_tools`, and inside it the only changed tool is `classify_text`.
- **Old → new diff** (identical for both entries):

```diff
+              "allow_unresolved": {"default": false, "title": "Allow Unresolved", "type": "boolean"},
               "categories": {...unchanged...},
+              "resume_from": {"default": "", "title": "Resume From", "type": "string"},
+              "retry_unresolved": {"default": false, "title": "Retry Unresolved", "type": "boolean"},
               "sql": {...unchanged...},
               "text_column": {
-                "title": "Text Column",
-                "type": "string"
+                "anyOf": [{"type": "string"}, {"items": {"type": "string"}, "type": "array"}],
+                "title": "Text Column"
               }
```

The full diff is in `/tmp/106-regen/snapshot.diff`. The `required` list did not change.

**`make check`** (final, after the fixes; output in `/tmp/106-regen/check2.txt`)
- ruff: `All checks passed!`
- basedpyright: `0 errors, 0 warnings, 0 notes`
- pytest: `2554 passed, 2 skipped, 77 deselected`
- web: `Tests 320 passed (320)`

**Local run (step 3, rerun on the final code)**
- Command: `uv run python /tmp/106-run/run.py "$PWD" /tmp/106-run/out-r2`. This is the real `data-agent mcp-server` over stdio with a fake model endpoint.
- First call: `20 rows fetched; 17 labeled`, `unresolved: 1`, `no_label: 2`.
- Resume: `Resumed from labels_1: 3 rows sent to the model (failed, no_label or unresolved there, or not in it); 17 rows kept as they were.` → `20 labeled`.
- Full output: `/tmp/106-run/output-r2.txt`.

**Reviews**
- `da-review main` failed: `ERROR: The 'gpt-6-astra' model is not supported when using Codex with a ChatGPT account.` A read-only Fable subagent reviewed instead (per the memory rule).
- `eng:code-review main` ran its Standards and Spec subagents.

Fixed:
1. **A resume carried over rows the new query no longer returns** (Spec, Standards and Fable all found it). Those rows skipped the category check, so the reply could read `1 rows fetched; 3 labeled` while its per-category lines added up to less.
   - Fix: the new label table holds only this query's rows, and the tool description now says so.
   - Test: `test_a_resume_holds_only_the_rows_its_query_returns`, red before the fix.
   - Commit: b5306ed.
2. **The DAB server would resume from any result id** (Fable). A `query_database` result with label-table-shaped columns gave `20 labeled` with no model call.
   - Fix: the DAB server now accepts only result ids that `classify_text` itself stored, which matches the product server's `label_tables`.
   - Test: `test_dab_resumes_only_from_a_table_this_tool_made`, red before the fix.
   - I also updated one assertion in this branch's own `test_dab_resumes_from_a_result_id`: a plain `query_database` result is now refused with the stricter "there is no label table … from classify_text" message.
   - Commit: dda39b1.
3. **Description said a one-item `text_column` list adds the column name** (Fable). It doesn't; the wording now says "with more than one". Commit: dda39b1.

Not fixed, with reasons:
- **NaN keys never match on resume** (Fable, low). `check_rows` doesn't catch duplicate NaN keys either, and that predates this ticket. Since the stale-row fix, the only effect is that such a row is sent again. Not worth widening scope.
- **Different categories on resume are refused** (Spec reviewer called it scope creep). I kept it: without it, kept labels outside the new categories would make the reply's counts wrong, which breaks "tools return facts".
- **`allow_unresolved` is opt-in** (Spec reviewer: the spec doesn't say opt-in). This is how "no new parameters → same prompt and reply" holds. The human should confirm it.
- **`kept` also counts retried rows the budget left out**: the reply says this on its own line ("they keep their outcome from …"), so it isn't ambiguous.
- **Standards judgement calls** (positional tuple indexes `[-2]`/`[-3]`, `classify()` length, `allow_unresolved`+`resume` travelling together): no documented standard is broken. The positional indexes carry comments. A refactor is out of scope.

**For the main session to run (real model):**
- `make test-integration`, covering `tests/test_classify.py::test_the_run_model_classifies_the_samples`.
- `uv run python /tmp/106-run/run.py "$PWD" /tmp/106-run/out-real` with `DATA_AGENT_*` pointed at the real model.

**主会话真实运行（2026-11-21，dda39b1，真 DeepSeek 端点）**
- `make test-integration` 里的分类那条：`tests/test_classify.py::test_the_run_model_classifies_the_samples` → `1 passed`。
- 产品 `mcp-server` stdio，20 行夹具文本 + 2 行故意含糊的文本，两列 `title`、`body`：第一次 `allow_unresolved` → `22 rows fetched; 20 labeled`，`unresolved: 2`；`resume_from=labels_1, retry_unresolved` → `2 rows sent to the model …; 20 rows kept as they were.`，`22 labeled`。两次共 $0.0005。脚本在本机 scratchpad（`t106/real.py`），不进 git。

**退回（2026-11-21，主会话，PR #115 第 1 轮）：Codex P2 属实。** `evals/dab_server.py:428-455` 的 `label_ids` 只是 result id 的集合，不记来源库；DAB 一次运行里两个库列和键对得上时，`resume_from` 拿库 A 的标签表去续接库 B，`resumed_rows` 只比列和键，会把 A 的标签当 B 的结果保留。产品服务器用 `made[0] is db.labels` 绑定来源（`src/data_agent/mcp_server.py:503`）。修法：DAB 侧把每个标签结果和它的 `db_name` 一起存，跨库续接在调模型前拒绝；配一个修复前会失败的测试。

**第 3 轮（退回修复，PR #115 Codex P2；分支 `worktree-106-classify-unresolved-resume`，提交 2a7d58b、88a298d）**

**修复**（只改了 `evals/dab_server.py` 和 `tests/test_classify.py`）
- `label_ids: set[str]` 改成 `label_dbs: dict[str, str]`，每个标签结果和它的 `db_name` 一起存。
- 续接别的库的标签表，在取数、`setup_from_env`、调模型之前就拒绝，报错是 `label table 'query_1' was made from database 'prices_db', not 'copy_db': resume_from takes a label table of the same database`。
- 不是本工具存的 id 或空的 `resume_from`，行为不变。
- 工具描述的 `resumed=` 跟产品服务器一致，改成 "…made in this run on the same database"。
- 88a298d 是审查提出的小改动：查 `label_dbs` 挪进 `results.lock`，写入一直在锁里。

**测试**
- 新测试 `test_dab_resumes_only_on_the_database_the_table_came_from`：两个 sqlite 库的列和键相同、正文不同，在 `prices_db` 分类后拿 `query_1` 去 `copy_db` 续接。断言报错、报错文字，以及假模型一次都没被调用。
- 修复前是红的：调用成功，回 `20 rows fetched; 20 labeled with a category.`，A 库的标签被当成了 B 库的结果。修复后是绿的。

**检查**
- `make check`（最后一次，88a298d，输出在 `/tmp/106-r3-check2.txt`）：
  - ruff `All checks passed!`
  - basedpyright `0 errors, 0 warnings, 0 notes`
  - pytest `2555 passed, 2 skipped, 77 deselected`
  - web `Tests 320 passed (320)`
- `tests/fixtures/chat/eval-unchanged.json` 从 dda39b1 起没变（`git diff dda39b1 HEAD --stat` 只有上面两个文件）。

**真实运行（第 3 步）**
- 这次的改动只在 DAB 服务器的参数检查上，不调模型。我用新测试的进程内 DAB 服务器加假模型跑过（输出见上面"测试"）。
- 没有用真模型跑 DAB 的 stdio 进程，因为 worktree 里加载不了模型 env。需要的话由主会话跑。

**审查**
- `da-review main` 不可用：`ERROR: The 'gpt-6-astra' model is not supported when using Codex with a ChatGPT account.` 照 memory 的规则，改由一个只读的 Fable 子 agent 审 2a7d58b，结论是没有 bug。它核对了 Mongo 路径、空 `resume_from`、拒绝发生在任何花费之前，以及测试能区分修复前后。
- `eng:code-review main`（Standards、Spec 两个子 agent）：Standards 没有违反规则的地方，Spec 显示退回的要求全部做到、没有超出范围。

修了：
- 查 `label_dbs` 在锁外（Standards 和 Spec 都提到）→ 88a298d。

不修，理由如下：
- **产品服务器跨库续接时，报的是笼统的 "there is no label table … in this run"**，DAB 这边报的是具体库名。退回只要求改 DAB 一侧，产品服务器拒绝的时机本来就对。统一报错文字可以另做。
- **绑定只到库，不到来源查询**（Fable）：在同一个库的另一张表上续接，键重叠时旧标签还会保留。产品服务器也是这样，不在这次 P2 的范围内；要收紧的话应该开后续工单。
- **两个服务器各写了一份"只在来源库续接"的规则**（Standards，小的重复）：它们存标签表的方式不同，按 CODING_STANDARDS 的"plain, explicit"留着。
- **测试里手写第二个库的配置**：只有这一处用到，不值得给 `make_dab` 加选项。

**给主会话（push 后）**：PR #115 的新提交要等 Codex GitHub app 出审查；没出的话评论 `@codex review`。

**合入（2026-11-21，主会话）**：Codex 对 88a298d 无意见，CI 绿；人审快照差异后批准合入（"if is mergeable, just merge"）。PR #115 squash 合入。
