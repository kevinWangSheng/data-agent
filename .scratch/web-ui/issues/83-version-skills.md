# 83 — `version` 事件记录注入的 Skill 名字

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`data-agent run` 写的 `version` 事件多一个字段 `skills`：运行开始时注入主 agent 系统提示的 Skill 名字列表。只加字段，agent 的行为不变。

## Authority

- May edit: `src/data_agent/agent.py`（只动写 `version` 事件的地方，必要时把 Skill 名字的计算提成 `system_prompt` 和 `version` 共用的一处）；现有事件测试（`tests/test_events.py`、`tests/lifecycle_events.py`、`tests/test_agent_outcomes.py`、`tests/test_dictionary.py` 里事件字段的断言）；`evals/atif.py`（只在它因新字段需要忽略或转换时）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/`、`tests/fixtures/web/`）、`web/`、`skills/`、提示词文本（`SYSTEM_PROMPT`、`DBT_SYSTEM_PROMPT`、`SKILLS_PROMPT`、`VERIFIERS_PROMPT`、`agents/verifier.md`）、`src/data_agent/configs/*.toml`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- spec "事件：新增和补充的字段"的 `version.skills` 一行（2026-11-15 人定）。
- 含义：运行开始时注入主 agent 系统提示的 Skill。不是从工具调用推出的，也不是模型实际用到的。
- 来源：`system_prompt`（`agent.py:789-794`）在 `config.skill` 为真时，用 `skill_names(task.type, task.dialect)`（`agent.py:784-786`）调 `load_skills`。`skills` 必须和这里实际注入的是同一份，不另写一份规则。
- 取值：
  - Skill 层关掉（`config.skill` 为假，如 `no-skill`、`baseline`）时写 `skills: []`，不省略字段。
  - 只写名字（如 `"sql-workflow"`、`"postgres-sql"`），按注入顺序。不写 Skill 内容、路径、`${CLAUDE_SKILL_DIR}` 展开后的目录，也不写任何密钥。
  - 验证者的提示（`VERIFIERS_PROMPT`、`agents/verifier.md`）不是 Skill，不算进来；SDK 的 `skills=[]`（`agent.py:1052`）不变。
- `skill_version` 保持原样。
- 旧事件文件没有这个字段，读者按"未记录"处理（那是工单 75 的事，本工单不改读者）。
- 先例：工单 74 加 `task` 事件的写法和测试位置。

## Acceptance

- [ ] 单元测试写在现有事件测试里，覆盖：
  - 查数 + DuckDB → `["sql-workflow", "duckdb-sql"]`；
  - dbt + Postgres → `["dbt-workflow", "dbt-write", "dbt-debugging", "postgres-sql"]`；
  - Skill 层关掉 → `[]`；
  - 开了验证者的配置：列表里没有验证者；
  - `skills` 和 `system_prompt` 实际注入的名字一致。
- [ ] 给模型的提示没有变化（现有提示测试通过且未改）
- [ ] `evals/` 里读事件的代码测试不变且通过
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实运行由主会话做：README 快速上手的查数运行，贴出事件文件的 `version` 行。

## Stop conditions

- 要改提示文本或 agent 行为才能拿到这份名单。
- spec 和本工单矛盾，或缺一个需要人定的决定。
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
