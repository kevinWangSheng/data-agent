# 05 — 运行配置与层开关

Status: ready-for-agent
Blocked by: 03
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

6 个命名配置（基线全关、全开、关 Skill、关验证子 agent、关上下文策略、全开旧版 Skill）可选；配置决定加载哪些 Skill、暴露哪些工具；关掉的层不留事件。后续工单把各自的层接到这里的开关上。

## Authority

- May edit: `src/data_agent/`、配置文件目录、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $1

## Context

ADR-0004（6 个配置）；CONTEXT 的 Baseline、Ablation；spec「运行配置」。

## Acceptance

- [ ] 接缝 B 中「工具列表随配置变化」的验收测试转绿（以目前已有的工具为限）
- [ ] 接缝 A 中基线配置无验证者痕迹的测试，若验证层尚未实现，保持 xfail
- [ ] Skill 版本取 Skill 目录内容哈希并写进版本头
- [ ] `make check` 通过

## Evidence required

用基线与全开两个配置各跑一次同一题，贴两份事件文件的工具列表差异。

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
