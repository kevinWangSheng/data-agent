# 02 — MCP server 与只读网关（DuckDB）

Status: ready-for-agent
Blocked by: 01
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

经 stdio 连上本项目的 MCP server，能对 DuckDB 执行只读查询、列出表；DDL、DML、危险函数被只读网关拦截并返回可读错误。

## Authority

- May edit: `src/data_agent/`（网关、MCP server、DuckDB 连接）、`tests/`（除 `tests/acceptance/`）、`NOTICE`、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

ADR-0002（复制 SignalPilot `engine/`，随附其测试）；plan §5（官方 `mcp` SDK 2.x、stdio）；上游 `~/dev/AI/data-agent-refs/SignalPilot` 的 `engine/`。

## Acceptance

- [ ] 接缝 B 中关于工具列表（基础工具部分）、SELECT 执行、DDL/DML/危险函数拦截的验收测试转绿
- [ ] SignalPilot `engine/` 自带测试随代码复制并通过
- [ ] 复制的文件保留版权头并登记 `NOTICE`
- [ ] `make check` 通过

## Evidence required

测试输出；用 MCP 客户端真实调用一次查询和一次被拦的写语句，贴输出。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
