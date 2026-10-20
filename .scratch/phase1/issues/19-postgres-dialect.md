# 19 — Postgres 运行时验证者与 Skill 用对方言

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

在 Postgres 数据源上跑 dbt 任务时，验证者工具按 Postgres 方言解析和生成 SQL，dbt-workflow Skill 指向实际加载的 SQL Skill；DuckDB 行为不变。

## Authority

- May edit: `src/data_agent/verifier_tools.py`、`src/data_agent/agent.py`（只限把数据源方言传给验证者工具）、`skills/dbt-workflow/SKILL.md`（只改方言措辞）、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试、`skills-frozen/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $1

## Context

- `src/data_agent/verifier_tools.py:115` `DIALECT = "duckdb"`，用于 `:802`、`:816`、`:951`、`:1003`、`:1010`、`:1031`、`:1322`、`:1330` 的 sqlglot 解析与生成；Postgres 运行（工单 08）也走它。
- `skills/dbt-workflow/SKILL.md:103` 写"the SQL skill for your database (duckdb-sql)"；实际加载哪个方言 Skill 已由 `src/data_agent/agent.py:67`、`:565`（`DIALECT_SKILLS`）按数据源决定。
- 工单 07、16 的已知限制；ADR-0005（Skill 不写针对榜单题目的内容）。

## Acceptance

- [ ] 验证者工具的方言来自本次运行的数据源（DuckDB / Postgres），不再写死；DuckDB 下所有现有测试不变
- [ ] 一个修复前会失败的测试：Postgres 方言独有写法（如 `::` 类型转换、`DATE_TRUNC('month', ...)`、`ILIKE`）在 Postgres 运行里被正确解析、基线 SQL 能在 Postgres 上执行
- [ ] `SKILL.md:103` 改成不点名方言（"the SQL skill for your database"）；Skill 版本随之变化，`skills-frozen/` 不动
- [ ] 真实运行：`python -m evals jaffle`（本地 Postgres）跑一个 dbt 用例，贴结果
- [ ] `make check` 通过

## Evidence required

修复前失败、修复后通过的测试输出；jaffle 真实运行的命令与结果。

## Stop conditions

需要改验证者 md 或验证逻辑本身；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

来自工单 07、08、16 的已知限制。改 `skills/dbt-workflow/SKILL.md` 会改变 Skill 版本；开发集检查（`runs/dev-1`，commit 09c1d77）在此之前已启动，不受影响。
