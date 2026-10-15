# 07 — 验证子 agent 与 6 个验证工具

Status: ready-for-agent
Blocked by: 04, 05
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

dbt 任务交付前，结构与数值两个只读验证者并行检查，逐项给通过/失败/需更多信息/不适用；主 agent 复现失败、最小修改、重审；验证者开始结束记成事件；关验证子 agent 时不出现。

## Authority

- May edit: `src/data_agent/`、`skills/`、验证者定义、`tests/`（除 `tests/acceptance/`）、`NOTICE`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $3

## Context

ADR-0002（6 个工具清单与要改的方言写法）；plan §3 验证；上游 signalpilot-plugin 的 `agents/verifier.md`、`agents/value-verifier.md` 与 dbt-workflow 第 8 步。

## Acceptance

- [ ] 接缝 B 中 6 个验证工具的 DuckDB 用例转绿
- [ ] 接缝 A 中基线配置无验证者痕迹的测试转绿
- [ ] `make check` 通过

## Evidence required

一次真实 dbt 任务的事件文件里验证者开始/结束与逐项结论；花费。

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
