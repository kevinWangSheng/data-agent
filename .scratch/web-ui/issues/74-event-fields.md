# 74 — 事件补字段：任务、检查名字、最终答案、拒绝种类、验证派出的关联

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 74-event-fields)
Branch: worktree-74-event-fields

## Goal

`data-agent run` 写出的事件文件多出 spec "事件：新增和补充的字段"列的五样，都是只加不改；旧事件文件的读者不受影响。

## Authority

- May edit: `src/data_agent/`（写事件的地方）；`tests/test_events.py`、`tests/lifecycle_events.py`、`tests/test_agent_outcomes.py` 等现有事件测试；`evals/atif.py`（只在它因新事件类型需要忽略或转换时）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、`skills/`、提示词、`src/data_agent/configs/*.toml`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- spec "事件：新增和补充的字段"。字段名用那一节的表，工单 73 的测试用同一组。
- 五样：
  - `task` 事件，在 `version` 之后写一次：任务原文、类型、数据源名字。数据源名字不是连接串；`--postgres-env` 时用环境变量名，`--duckdb` 时用文件名，另加一个可选的命令行参数让调用方直接给名字。任务原文照旧经过 `EventLog` 的脱敏。
  - `verifier_end.checks` 每项带检查的名字。名字来自验证者提示里的检查清单，不让模型另外生成。
  - 最终答案：标记哪段文本是最终答案，带上它依据的查询的 `tool_use_id`（没有就留空）。只记录事实：依据的查询取 agent 明确给出的；agent 没给时留空，不要猜。实现前先读代码确认 agent 现在怎么交付答案，做法写进最终回复。
  - `tool_end` 带拒绝种类，区分守卫（`command_guard`、`workspace_guard`）和只读网关；没被拒绝时不带这个字段。
  - `verifier_dispatch` 带对应的 Agent 工具调用的 `tool_use_id`。
- 不做：Skill 加载事件、`sql` 的 `parent`、验证者的思考和文本、`dbt run` 的 `is_error`。
- 这些改动不能改变 agent 的行为（提示、工具返回给模型的内容都不变），只多写事件。

## Acceptance

- [ ] 五样各有单元测试，写在现有的事件测试里
- [ ] 旧的事件读者（`evals/` 里读事件的代码）测试不变且通过
- [ ] 给模型的提示和工具返回没有变化（现有的提示测试通过）
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实运行由主会话做：照 README 快速上手跑一次查数运行，贴出事件文件里新字段所在的行。

## Stop conditions

- 某个字段没法在不改变 agent 行为的前提下拿到（尤其是"最终答案依据的查询"）。
- spec 自相矛盾或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0.0095 (main-session real run, price table; data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted (PR #81 → 1a615eb; CI green, Codex review no findings)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- How the agent delivers its answer (checked in code first): the final answer is `ResultMessage.result`, the agent's last message (agent.py; QUERY_PROMPT/ANSWER_RULE). No prompt asks the agent to name the query its answer rests on, so `answer.sql_tool_use_id` is always null（工单"agent 没给时留空，不要猜"）. The `answer` event is written before `result`, only on SDK `subtype: success` with no `is_error` and no API error; stopped runs (budget/deadline) get none. dbt runs get one too (their closing message).
- `task`: written after `version`. `data_source` defaults to the `--postgres-env` variable name or the DuckDB file name (`Task.data_source_name`); new `--data-source-name` overrides it. Task text is masked by `EventLog`.
- `verifier_end.check_names`: parsed from the `CHECK n: <…> — <name>:` template lines in `agents/verifier.md` (verifier_v2/report.py `check_names`) → {1 exists, 2 columns, 3 grain, 4 window ordering, 5 sources, 6 row logic, 7 compare_by_key}. Keys are ints in Python, strings in JSON, same as `checks`.
- `tool_end.rejected_by`: `guard` = command_guard/workspace_guard denied that tool_use_id (`guard_recorder` wraps the hooks; returns unchanged). `gateway` = an `mcp__data__*` error starting `Error executing tool <name>: Query blocked by the read-only gateway:` (prefix now `gateway.BLOCKED_PREFIX`; model-visible text byte-identical).
- `verifier_dispatch.tool_use_id`: the PreToolUse Agent call (hook input `tool_use_id`, else the callback argument).
- Also edited beyond the ticket list: `tests/test_dictionary.py` (event-type list gained task/answer), `tests/test_run_cli.py` (new CLI flag test).
- `make check`: ruff "All checks passed!", "279 files already formatted", basedpyright "0 errors, 0 warnings, 0 notes", pytest "2180 passed, 1 skipped, 39 deselected".
- 真实运行（主会话，2026-11-15，分支 5452ac3）：`uv run data-agent run --type query --config no-verifiers --duckdb runs/t74-real/shop.duckdb --workspace runs/t74-real/work --events-dir runs/t74-real/events --task "Which customer tier has the highest total amount of completed orders, and how much is it?"` → "Gold has the highest total amount of completed orders, at 710.00."，16 turns，$0.0095，75 条事件。事件文件里：
  - `{"seq": 1, "type": "task", "task": "Which customer tier …", "task_type": "query", "data_source": "shop.duckdb"}`
  - `{"seq": 30, "type": "tool_end", "tool": "mcp__data__query_database", "is_error": true, "content": "Error executing tool query_database: Query blocked by the read-only gateway: Statement stack…", "rejected_by": "gateway"}`
  - `{"seq": 73, "type": "answer", "text": "Gold has the highest total amount of completed orders, at 710.00. …", "sql_tool_use_id": null}`
- 审查 1：`da-review` 跑不了（`codex: not found`），用只读 fable 审查代替。
  - 已修：SDK 报 success 的 API 错误也会写 `answer` → 改成和 `infra_error` 一样排除（加了测试）。
  - 不修：`compare_by_key` 内部的网关拦截没有 `rejected_by`（compare.py 写成 "Error: the comparison could not run: …"，和数据库错误分不开；要区分就得改模型看到的文本）→ 已知限制。
  - 不修：verifier_v2 的 deny_dispatch/deny_query 没有 `rejected_by`，工单只点名 command_guard/workspace_guard。
- 审查 2：eng:code-review。Standards 无硬违规；已修：`rejected_by` 返回 `Literal["guard","gateway"]`。不修（判断项）：agent.py 体量（拆分是工单外重构）；tool_use_id 回退写了 3 处（既有模式）；`data_source` 与 `data_source_name` 命名（字段是输入，属性是解析后的名字）；check_names 正则依赖模板措辞（测试钉住真实提示词）。Spec：五个字段齐全；已修一个误导性的测试名。
- 主会话核对：`check_names` 在 JSON 里是字符串键，与 `checks` 一致，前端按 `checks` 的方式读即可；dbt 运行也写 `answer` 不违反 spec（spec 第 109 行没限定任务种类），保留。
- PR #81（commits 6d7c6d9, 5452ac3）。
