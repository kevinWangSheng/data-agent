# 03 — `data-agent run` 查数任务端到端

Status: ready-for-agent
Blocked by: 02
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

对夹具 DuckDB 提一个业务问题，`data-agent run` 给出答案，并写出事件文件（版本头、序号、结果含 token 与花费）。

## Authority

- May edit: `src/data_agent/`（CLI、agent 主循环、事件流、运行配置的最小版本）、`skills/`（sql-workflow、duckdb-sql）、`tests/`（除 `tests/acceptance/`）、`NOTICE`、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $2

## Context

ADR-0001 与其 Update（模型可配置、`DATA_AGENT_*` 只传给 SDK 子进程、`setting_sources=["user"]`）；plan §3 主循环与事件流、§5 SDK 锁精确版本；spec「Implementation Decisions」CLI 接口与事件流；上游 signalpilot-plugin 的 sql-workflow、duckdb-sql。

## Acceptance

- [ ] 接缝 A 中查数任务与事件文件相关的验收测试转绿（`make test-integration`）
- [ ] `data-agent run --config` 接受 6 个配置名（`baseline`、`full` 等，名字照 spec）；本工单只需最小占位，各名字都按全开处理，层开关由 05 实现
- [ ] 单次运行有 `max_budget_usd`
- [ ] 事件文件写入前遮盖已知密钥值与端点
- [ ] `make check` 通过

## Evidence required

一次真实 `data-agent run` 的命令、答案和事件文件前几行与最后一行（遮盖后）；本工单花费。

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
