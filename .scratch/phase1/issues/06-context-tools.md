# 06 — 上下文策略工具

Status: ready-for-agent
Blocked by: 02, 05
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

agent 在宽 schema 上先 discover（只回引用和 ≤200 字摘要）再 hydrate 单个对象；能按关键词查列的真实取值；能拿到按 `_id` 匹配的 join 候选；关上下文策略时这些工具不暴露。

## Authority

- May edit: `src/data_agent/`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $1

## Context

ADR-0003；CONTEXT 的 Discover→hydrate、Dictionary search；上游 SignalPilot `analyze_project_db`、ktx 的契约（只抄思路）。

## Acceptance

- [ ] 接缝 B 中 discover、hydrate、dictionary search 的验收测试转绿
- [ ] 关上下文策略配置下这些工具不出现
- [ ] `make check` 通过

## Evidence required

MCP 客户端真实调用三个工具的输出。

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
