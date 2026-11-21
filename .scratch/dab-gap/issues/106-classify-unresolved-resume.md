# 106 — classify_text：未定结果、只重跑没标上的行、多列文本

Status: ready-for-agent
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

- Attempts: 1
- Review rounds: 0
- Model spend: $0
- Outcome: implemented; blocked on the eval-snapshot decision

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
