# 83 — `version` 事件记录注入的 Skill 名字

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-code (opus) 2026-11-15
Branch: worktree-83-version-skills

## Goal

`data-agent run` 写的 `version` 事件多一个字段 `skills`：运行开始时注入主 agent 系统提示的 Skill 名字列表。只加字段，agent 的行为不变。

## Authority

- May edit: `src/data_agent/agent.py`（只动写 `version` 事件的地方，必要时把 Skill 名字的计算提成 `system_prompt` 和 `version` 共用的一处）；现有事件测试（`tests/test_events.py`、`tests/lifecycle_events.py`、`tests/test_agent_outcomes.py`、`tests/test_dictionary.py` 里事件字段的断言）；`evals/atif.py`（只在它因新字段需要忽略或转换时）；退回第 1 轮加（2026-11-15 人定）：`evals/accept.py`（只把 `skills` 加进 `CHANGED_BY_DESIGN`，必要时改它旁边的注释和模块文档）、`tests/test_eval_accept.py`（加回归测试）、`docs/setup.md`（第 83 行"旧版复用"那条）
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
- [ ] `evals accept` 的旧版复用不因 `skills` 失效（退回第 1 轮加）：`tests/test_eval_accept.py` 有回归测试——旧 job 的 version 没有 `skills`、新 job 有，其余字段一致时判可复用（修复前判 `rerun-old`）；`config`、`layers`、`model`、`sdk_version`、`cli_version` 的比较照旧生效（现有测试不改且通过）
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

- Attempts: 2
- Review rounds: 2
- Model spend: $0.0066 (main-session real run, price table; data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted after 1 return (PR #84 → 2b45e99; CI green)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-15 claude-code (opus), 第 1 次实现（worktree-83-version-skills：c7ca6b8、1c3b1cf、0d12220）：
  - `agent.py` 的 `injected_skills(task_type, config, dialect)` 是 `system_prompt` 和 `version.skills` 共用的唯一来源（`config.skill` 为假时 `[]`）。
  - 测试在 `tests/test_agent_outcomes.py`（工单 74 那块之后）：参数化 query+duckdb/full、dbt+postgres/full、query+duckdb/no-skill、dbt+postgres/no-skill、dbt+duckdb/baseline；每个都检查 `skills/*/SKILL.md` 恰好在系统提示含它时出现在 `skills` 里；dbt+duckdb/full 提示里有 VERIFIERS_PROMPT，`skills` 只有四个 Skill。新测试先以 KeyError 'skills' 失败。现有提示测试未改且通过；`evals/atif.py` 未改（只读 VERSION_FIELDS）。
  - make check：ruff "All checks passed!"，basedpyright "0 errors, 0 warnings, 0 notes"，pytest "2201 passed, 1 skipped, 39 deselected, 10 xfailed"。
  - da-review（Codex）：[P2] 单元测试的 context 层用真实 psycopg 连接建字典 → 1c3b1cf 修（stub `prebuilt_dictionary`）；[P2] `evals/accept.py:250-253` 对旧版缺的 version 字段一律判不一致 → 超出 Authority，交主会话。
  - eng:code-review：Spec 无缺漏；`evals/dab.py:438` 自己写的 DAB version 事件没有顶层 `skills`，不在本工单 Goal 内（工单 75 是否读 DAB 事件文件未确认）。Standards 修于 0d12220（删多余断言；helper 的 task_type 用 Literal）。不修：`skill_version` 和 `skills` 对 `config.skill` 的判断写法不同，`skill_version` 按工单不改。
- 真实运行（主会话，2026-11-15，分支 0d12220）：README 快速上手查数（`--config no-verifiers`，`runs/t83-real/shop.duckdb`）→ "Gold: 710.00"，$0.0066。`version` 行：`{"seq": 0, "type": "version", "git_commit": "0d12220c…", "skill_version": "f75a117a18b5", "skills": ["sql-workflow", "duckdb-sql"], "config": "no-verifiers", "layers": {"skill": true, "verifiers": false, "context": true}, "model": "deepseek-flash", …}`
- **退回：`evals accept` 的旧版复用被 `skills` 打断**（2026-11-15 人核实）。`evals/accept.py:53` 只忽略 `git_commit`、`skill_version`；加了 `skills` 后旧 job 是 `(missing)`、新 job 有值，`reuse_problems`（`accept.py:249`）误判 `rerun-old`。人复现：修复前 `rerun-old`，把 `skills` 加进 `CHANGED_BY_DESIGN` 后 pass。修法：
  - `skills` 加进 `CHANGED_BY_DESIGN`；`config`、`layers`、`model`、`sdk_version`、`cli_version` 的检查照旧。
  - `tests/test_eval_accept.py` 加回归测试（修复前会失败）。
  - `docs/setup.md:83` 和 `accept.py` 的注释写明：`skills` 是观测用的元数据，不参与复用匹配（它由已比较的 `config`/`layers` 和任务决定）。
  - 人的基线：`tests/test_eval_accept.py` 44 passed；工单 83 的事件测试 47 passed。
  - 在原分支 `worktree-83-version-skills` 上接着修；修完重跑 `make check` 和两道审查。
- 2026-11-15 claude-code (opus)，退回第 1 轮修复（worktree-83-version-skills：96173d6、b92a89b；PR #84）：
  - `evals/accept.py`：`skills` 加进 `CHANGED_BY_DESIGN`；旁边注释与模块文档写明 `skills` 是观测用的元数据，由已比较的 `config`/`layers` 和任务决定，旧版没有记录，不参与复用匹配。`REQUIRED` 与 `config`、`layers`、`model`、`sdk_version`、`cli_version` 的比较未改。`docs/setup.md:83` 同步写明。
  - 回归测试 `tests/test_eval_accept.py::test_reuse_ignores_the_injected_skills_an_old_version_did_not_record`：修复前 "1 failed, 44 passed"（判 `rerun-old`），修复后 "45 passed"；现有测试未改。主会话在分支上复跑 45 passed。
  - make check（96173d6）：ruff "All checks passed!"，basedpyright "0 errors, 0 warnings, 0 notes"，pytest "2202 passed, 1 skipped, 39 deselected, 10 xfailed"。b92a89b 只改注释：ruff check/format 通过，test_eval_accept 45 passed（全量由 PR CI 跑）。
  - da-review（Codex）："No actionable correctness issues were found in the diff"。
  - eng:code-review：Spec 无缺漏、无越权（审查者把 `CHANGED_BY_DESIGN` 改回旧值，新测试 1 failed）；Standards 修于 b92a89b（`CHANGED_BY_DESIGN` 首行注释改成"不参与复用比较的字段"）。不修：`docs/setup.md:83` 句子偏长；测试用 `agent.SKILLS_DIR` 而非 `config.skills_dir`；helper 的 `dialect` 没用 Literal；新旧 `skills` 取值不同的情况没测（按设计同样忽略）。
- 已知限制（人 2026-11-15 同意接受）：`evals/dab.py:438` 的 DAB version 事件把 Skill 名字记在 `dab.skills`，不是顶层 `version.skills`；网页只读精选运行和开发集 Harbor trial，DAB 只出汇总，不受影响。
- 合入前核对 Codex GitHub app（PR #84，b92a89b）：P1"`evals/accept.py` 不在 Authority 里"不属实——它读的是 PR 分支上 912d36d 时的旧工单副本；main 上 254d149 已把 `evals/accept.py`、`tests/test_eval_accept.py`、`docs/setup.md` 加进 Authority（第 15 行"退回第 1 轮加（2026-11-15 人定）"）。理由已回复在 PR 上。
