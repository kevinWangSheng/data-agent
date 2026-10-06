# 02 — 只读 MCP server + 基础工具（构成基线）

Status: ready-for-agent
Blocked by: 01
Spec: ../spec.md

## 做什么

复制 SignalPilot `engine/`（含测试）；DuckDB / Postgres 连接器；MCP 工具 `list_tables` / `query_database`（validate_sql → inject_limit → execute → 表格化）。接进 `agent/`，开关全关时也提供这两个工具。

## 验收

- [ ] `engine/` 自带测试在本仓库通过
- [ ] DDL/DML 被拦截的测试（DuckDB、Postgres 各一）
- [ ] fast-30 k=1 基线分数记录在 Comments

## 参考

ADR-0002；`docs/research/2026-09-21-signalpilot-mcp-reuse.md` §3–4

## Comments
