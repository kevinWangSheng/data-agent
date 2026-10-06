# 不运行 SignalPilot 网关；复制其 `engine/` 并移植 6 个验证工具

SignalPilot 的 MCP 工具（62 个）挂在 FastAPI 网关内，即便本地免鉴权模式也强制要 Postgres 元数据库 + Alembic 迁移，依赖树含 boto3/vercel/claude-agent-sdk，且不支持 MongoDB（`docs/research/2026-09-21-signalpilot-mcp-reuse.md`）。验证子 agent 只需 6 个工具（list_tables / map_columns / check_model_schema / audit_model_sources / verify_model_values / query_database，核心逻辑约 700–900 行，只依赖 connector.execute/get_schema + sqlglot），只读 SQL 守卫是独立的 `engine/`（651 行，仅依赖 sqlglot，自带测试）。决定：`engine/` 原样复制到本仓库，6 个工具按其实现移植到本项目自己的 MCP server，去掉 store/审计壳；不运行网关。

## Consequences

- MongoDB（DAB 需要）无论如何要另做；对标物是 DAB `common_scaffold` 的 `query_db/list_db/execute_python`。
- `verify_model_values` 硬编码 `SHOW TABLES`（DuckDB/MySQL），`audit_model_sources` 用 `COUNT(*) FILTER`；移植到 Postgres 时要改。
