# 19 — Postgres 运行时验证者与 Skill 用对方言

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (opus-5.5, worktree 19-postgres-dialect)
Branch: worktree-19-postgres-dialect

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

- [x] 验证者工具的方言来自本次运行的数据源（DuckDB / Postgres），不再写死；DuckDB 下所有现有测试不变
- [x] 一个修复前会失败的测试：Postgres 方言独有写法（如 `::` 类型转换、`DATE_TRUNC('month', ...)`、`ILIKE`）在 Postgres 运行里被正确解析、基线 SQL 能在 Postgres 上执行
- [x] `SKILL.md:103` 改成不点名方言（"the SQL skill for your database"）；Skill 版本随之变化，`skills-frozen/` 不动
- [x] 真实运行：`python -m evals jaffle`（本地 Postgres）跑一个 dbt 用例，贴结果
- [x] `make check` 通过

## Evidence required

修复前失败、修复后通过的测试输出；jaffle 真实运行的命令与结果。

## Stop conditions

需要改验证者 md 或验证逻辑本身；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: 约 $0.07（两次 jaffle）
- Outcome: done，PR #18 squash 合并（0 轮退回）
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

来自工单 07、08、16 的已知限制。改 `skills/dbt-workflow/SKILL.md` 会改变 Skill 版本；开发集检查（`runs/dev-1`，commit 09c1d77）在此之前已启动，不受影响。

### 2026-10-20 工单 agent：实现（aa4fecb、4616b4e，主会话抄录）

- 改动：验证者工具的方言取自本次运行的数据库；`register_verifier_tools(..., dialect)` 贯穿解析、`qualify`、标签、基线计数 SQL、类型折叠。`dialect` 在 `register_verifier_tools`、`parse_model`、`normalized_type`、`column_roles` 上为必填（忘传不会悄悄回落 DuckDB）；`verify_values`、`same_type` 默认 DuckDB，现有 DuckDB 测试不变。
- 越出 Authority 一行：方言在 `src/data_agent/mcp_server.py:90` 传入（`gateway.dialect`）——验证者工具在 MCP server 里注册，`agent.py` 只给 MCP server 传 `--postgres-env`，没改。
- 实际会坏的 Postgres 写法：`::`、`DATE_TRUNC`、`ILIKE` 经 sqlglot DuckDB 方言原样输出不坏；会坏的是 `~`（变 `REGEXP_FULL_MATCH`，Postgres 没有，且语义成全匹配）、`to_char(...)`（悄悄变 `CAST(... AS TEXT)`，计数错）、`EXTRACT(dow ...)`（变 `DAYOFWEEK`）。测试覆盖 `::`、`DATE_TRUNC`、`ILIKE`、`~`、`to_char`。
- 修复前失败的测试：`tests/test_postgres.py::test_verifier_reads_and_counts_a_postgres_model_in_postgres`（集成，真 Postgres 17，经 MCP server）修复前 `Query error: function regexp_full_match(text, unknown) does not exist`，修复后通过（3 行 "Model matches"）；`tests/test_verifier_tools.py::test_a_postgres_model_is_read_and_counted_in_postgres` 修复前 `TypeError`；`tests/test_dbt_skills.py::test_workflow_names_no_dialect_skill` 改 SKILL.md 前失败。
- SKILL.md：原 103 行（现 105–106）"(duckdb-sql)" → "the SQL skill for your database"；顶部注释 12–13 行同改。Skill 版本 `fa6028eef62b` → `6ee2d4432631`；`skills-frozen/` 未动。
- `make check` 通过（1020，基线 1018）；`tests/test_postgres.py` 对本地 Postgres 9/9 通过。
- 真实运行（本地 Postgres 17）：`python -m evals jaffle --postgres-env DA_JAFFLE_URL --out runs/19/jaffle-b1 --case b1_monthly_revenue` → `PASS b1_monthly_revenue exit=0 cost=0.03646842`（验证者读到了 SQL；模型按计算出的 `date_trunc` 分组，按已有规则不切片）；`--case b2_fix_customers_fanout` → 1/1 通过，$0.0309，`verify_model_values` 按 `customer_id` 切片、在 Postgres 上跑改写后的 `COUNT(order_id)`：模型 5、基线 5、"Model matches"。容器 `da19-pg` 已删。
- 审查：`da-review main` 前两次 "model is at capacity"，第三次（4616b4e）无发现；`eng:code-review main` 无硬伤，4616b4e 修了 dialect 必填、删掉钉死 Markdown 换行的断言、改 `DIALECT` 注释。不修：`dialect: str` 多处透传沿用文件里的普通参数写法；固定 schema `da19_dialect` 沿用 `da_opt` 的做法。

### 2026-10-20 主会话

接受 `mcp_server.py:90` 一行越界：建票时把注入点错写成 `agent.py`，实际注册点在 `mcp_server.py`，改动最小。

### 2026-10-20 主会话：合并 PR #18

CI 绿（4616b4e），Codex 无意见（`has_any_findings: false`）。已 `gh pr merge 18 --squash`。
