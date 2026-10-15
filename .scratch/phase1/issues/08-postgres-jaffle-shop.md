# 08 — Postgres 支持与 jaffle-shop 用例

Status: ready-for-agent
Blocked by: 06, 07
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

agent 能接本地 Postgres 与 dbt 官方 jaffle-shop：查数和改 dbt 都能跑通；约 5 个用例加 1–2 个要求直接写库的反例，反例被网关拦住。

## Authority

- May edit: `src/data_agent/`、`skills/`（postgres-sql）、`evals/`（jaffle-shop 用例）、`tests/`（除 `tests/acceptance/`）、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（本地用 colima 起 Postgres 容器，本 worktree 独占库名或端口）
- Spend cap: $3

## Context

plan §4「接任意 Postgres + dbt」；ADR-0002 Postgres 方言要改的写法；ADR-0005（按 duckdb-sql 格式补 postgres-sql）；上游 SignalPilot `e2e-eval/eval-set/eval.json` 格式。

## Acceptance

- [ ] 接缝 B 的 Postgres 版用例转绿（`make test-integration`）
- [ ] jaffle-shop 用例与反例全部真实跑过
- [ ] `make check` 通过

## Evidence required

每个 jaffle-shop 用例的命令、结果与判定；反例被拦的事件；花费。

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
