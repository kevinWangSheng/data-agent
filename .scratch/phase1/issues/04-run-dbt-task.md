# 04 — dbt 任务端到端

Status: ready-for-agent
Blocked by: 03
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

给 `data-agent run` 一个 dbt 任务和迷你 dbt 项目，agent 改模型、`dbt run`、交付，模型数值正确。

## Authority

- May edit: `src/data_agent/`、`skills/`（dbt-workflow、dbt-write、dbt-debugging）、`tests/`（除 `tests/acceptance/`）、`NOTICE`、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $2

## Context

plan §3（dbt 任务先加载 dbt-workflow，最大轮数 200）；plan §5 dbt 版本对齐（dbt 1.10.x、dbt-duckdb 1.10.0、duckdb 1.2.2）；CONTEXT 的 dbt write。

## Acceptance

- [ ] 接缝 A 中 dbt 任务的验收测试转绿
- [ ] dbt 版本与 data-eng-bench 镜像一致
- [ ] `make check` 通过

## Evidence required

一次真实 dbt 任务运行的命令、输出、模型查询结果；花费。

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
