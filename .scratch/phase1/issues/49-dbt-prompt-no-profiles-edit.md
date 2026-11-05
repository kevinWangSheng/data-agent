# 49 — 提示词和 Skill 不再教 agent 改 `profiles.yml`；不 Read dbt 生成的大 JSON

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

dbt 任务里 agent 按工单 40 放行的方式工作：`cd <项目目录> && dbt ...`，不传 `--project-dir`/`--profiles-dir`，不改 `profiles.yml`；validate 的时限说明和代码一致（300 s）；Skill 明确不要 Read `target/` 下 dbt 生成的 JSON。

## 已和用户定下的（2026-11-05）

- 类别：**改变行为**（ADR-0008 Update 2026-11-04）。验证是单元测试，加主会话初筛目标题 × 3，对照 accept-2 full。
- 三处改动，不扩：
  1. `src/data_agent/agent.py` 的 `DBT_PROMPT`（main b62226f 第 130–146 行）：把 "for a project in a subdirectory, pass --project-dir and --profiles-dir. Relative paths in a profile resolve from the working directory." 改成教 `cd <project_dir> && dbt ...`（守卫允许，见 `src/data_agent/command_guard.py:6-7`），profile 里的相对路径在项目目录下解析，不要改 `profiles.yml`。
  2. `skills/dbt-workflow/SKILL.md:385`："default 60" 改成 300，与 `skills/dbt-workflow/validate_project.py:3` 一致。
  3. `skills/dbt-workflow/SKILL.md` Rules 一节加一条：不要 Read `target/` 下 dbt 生成的 JSON（`run_results.json`、`manifest.json`），它们是单行超长文件，读不进上下文；要看运行结果用 dbt 命令的输出。原因：工单 41 初筛里内置 Read 读 `target/run_results.json` 触发 2 次超长报错。
- 不改守卫（改守卫是改安全边界，要另外讨论）。不改 Skill 的其他步骤（工单 47 会改第 8 步）。
- 按 ADR-0005，不写针对任何题目的内容。

## Authority

- May edit: `src/data_agent/agent.py`（只改 `DBT_PROMPT` 文本）、`skills/dbt-workflow/SKILL.md`（只改上面三处中的两处）、`tests/test_dbt_skills.py`、`tests/test_agent.py` 或对应测试文件
- Must not edit: `tests/acceptance/`、守卫、`src/data_agent/verifier_v2/`、`src/data_agent/agents/`、`skills/` 其他文件、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（初筛由主会话做）
- Spend cap: $0

## Context

- 工单 40 的 Comments（2026-11-04 主会话 `t40-real`）：agent 没用 `cd … && dbt`，而是 `--project-dir` 加 Edit `profiles.yml`，原因是提示词和 Skill 仍这样教。accept-2 里 97/180 个 trial 改过 `profiles.yml`。
- 工单 41 的 Comments（`t41-screen`）：Read `target/run_results.json` 两次超长。
- 守卫规则：`src/data_agent/command_guard.py:1-10, 51`。

## Acceptance

- [ ] 测试：`DBT_PROMPT` 渲染结果不含 `--project-dir`，含 `cd`；SKILL.md 的 validate 段落写 300；Rules 含 `target/` 规则（现有 `tests/test_dbt_skills.py` 的模式）。
- [ ] `make check` 通过。
- [ ] （主会话）初筛：dbt-fix-daily-cohorts × 3（full），对照 accept-2 full。贴出：是否改过 `profiles.yml`；dbt 命令用的是 `cd` 还是 `--project-dir`；Read `target/*.json` 次数。过线：成功数不低于 accept-2 full 同题，`profiles.yml` 改动 0 次。

## Evidence required

测试命令与输出；渲染后的 `DBT_PROMPT` 全文。

## Stop conditions

- 守卫实际不放行 `cd <project_dir> && dbt`（和工单 40 的记录矛盾）。
- 第二轮审查没过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
