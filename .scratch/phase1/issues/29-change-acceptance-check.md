# 29 — 一条命令判断一个改动是否按 ADR-0008 过关

Status: ready-for-agent
Blocked by: 27（运行记录）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

给定改动前、改动后两份开发集结果、本改动的目标题和回归集文件，一条命令按 ADR-0008 给出"过关 / 不过 / 需补跑哪几题"，并说明旧版结果能不能复用；主会话验收改动时不再手算。

## Authority

- May edit: `evals/`（`compare` 或新子命令）、`tests/`（除 `tests/acceptance/`）、`docs/setup.md`
- Must not edit: `tests/acceptance/` 下的验收测试、`src/`、`skills/`、`scripts/`、`evals/regression.json`（回归集由主会话在工单 31 维护）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- ADR-0008 的"改动过关"三条、"回归集"、"复用旧版结果"原文就是规格；这里不重述，以 ADR 为准。
- 回归集文件 `evals/regression.json`（题名列表，只含开发集题）由工单 31 生成；本工单用测试夹具，文件格式自己定一个最简单的并写进 `docs/setup.md`。
- 补跑：某回归题一轮低于 3/3，命令列出它，补跑结果作为额外输入再判一次；补跑仍 ≤1/3 才算退步。
- 复用：读两份结果的 version 头和工单 27 的运行记录；字段不一致、benchmark commit 或机器标签不同、超过两周、该轮有基础设施报错，任一条就判"旧版需重跑"。
- 只用于开发集：输入里出现非开发集题就拒绝（ADR-0004）。
- 本地已有 `runs/dev-2/out/jobs/{full,baseline}`（不同 commit，k=1）可作真实运行。

## Acceptance

- [ ] 三条过关条件各有通过与不通过的测试：点估计、目标题成功次数、回归集（含补跑后转为通过与判退步两种）
- [ ] 复用条件每条不满足时都判"旧版需重跑"；有测试
- [ ] 输入含非开发集题时拒绝；有测试
- [ ] 真实运行：对 `runs/dev-2` 的两份 job 跑一次（随意指定 2 道目标题、3 道回归题的夹具文件），贴输出
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令与输出。

## Stop conditions

ADR-0008 的规则有歧义、需要人定；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
