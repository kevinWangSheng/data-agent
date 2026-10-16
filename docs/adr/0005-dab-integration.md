# DAB 接入：用它自带的工具与官方提示，不用针对 DAB 调的 prompt

DAB README 的 "Create Your Customized Agents" 一节允许自带 agent、复用 `common_scaffold/tools`（已处理 PostgreSQL / MongoDB / SQLite / DuckDB 连接）。榜单有两列：Hints（是否用 `db_description_withhint.txt`）与 Tuned prompt（是否针对 DAB 的任务惯例专门写 prompt）。2026-09-28 核对：前 25 名全部用了 Hints；Tuned prompt 前 25 名中 16 个用、9 个不用（含第 15、18–25 名）；脚注 7 批评的是提交者自写的提示，不是官方文件。

决定：
- DAB 运行时把 `common_scaffold/tools` 包成本项目的 MCP 工具；一期因此不另做 MongoDB 连接器，产品侧 MongoDB 挪出一期。
- 使用官方提示文件（与榜单惯例一致，否则分数不可比）。
- 不写针对 DAB 的 prompt：本项目是通用 agent，Skill 不含任何基准专属内容；榜单提交标记 Tuned prompt = 否。
- Postgres 方言没有上游 Skill，按插件 `duckdb-sql` / `sqlite-sql` 的格式补一个 `postgres-sql`。

## Consequences

- 一期"接任意数据源"的产品能力只覆盖 Postgres + dbt；MongoDB 仅在 DAB 评测里可用。
- 失败归因采用 DAB 自带的 FM1–FM4 与其 judge prompt（`failure_analysis/`），两个榜共用；SignalPilot 无失败分类可参考。
- Trace 只用基准自带格式（Harbor 的 `trajectory.json`、DAB 提交要求的轨迹）；SignalPilot 未用 Langfuse，Langfuse 挪二期。

## Update 2026-10-16：judge 提示自己写

DAB 没有许可证，不能复制它 `failure_analysis/` 的 judge 提示原文（AGENTS.md 硬规矩）。失败归因沿用 FM1–FM4 的分类思路（定义见 `CONTEXT.md`），judge 提示由本项目自己写；上面 Consequences 里"采用其 judge prompt"作废。spec（`.scratch/phase1/spec.md` Further Notes）已按此写。
