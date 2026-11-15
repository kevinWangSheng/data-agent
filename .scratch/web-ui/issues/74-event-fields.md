# 74 — 事件补字段：任务、检查名字、最终答案、拒绝种类、验证派出的关联

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
