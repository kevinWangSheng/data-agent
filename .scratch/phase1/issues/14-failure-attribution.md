# 14 — 失败归因报告

Status: ready-for-agent
Blocked by: 03, 10
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

对开发集失败题，一条命令让 agent 起草报告：每题一个 FM1–FM4 标签、引用相关 Skill 原文、给出通用修改建议；不自动改 Skill。

## Authority

- May edit: `evals/`、`src/data_agent/`（只限归因入口）、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $2

## Context

CONTEXT 的 Failure mode、Failure attribution loop；plan §3「失败 → 改 Skill」；ADR-0005（只用 FM1–FM4 的分类思路；judge 提示自己写，不照抄 DAB）。只读开发集，绝不读留出集。

## Acceptance

- [ ] 对夹具或真实开发集失败题产出报告
- [ ] 建议不含任何题号、表名等榜单专属内容
- [ ] 只读开发集
- [ ] `make check` 通过

## Evidence required

一份真实报告；花费。

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
