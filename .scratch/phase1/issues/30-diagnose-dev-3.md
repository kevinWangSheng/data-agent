# 30 — 诊断 dev-3：full 为什么不如 baseline（不跑新评测）

Status: ready-for-human
Blocked by: 28
Spec: ../spec.md
Claimed by: 主会话 + 人
Branch: —（只读分析，不改代码）

## Goal

用 dev-3 已有的 180 条 full / baseline 轨迹，找出 full 在开发集上输给 baseline 的具体失败模式并计数，定下工单 31 补跑哪些开发集消融配置，以及之后每个修复工单针对哪个失败模式、哪几道目标题。

## Authority

- May edit: 本工单的 Comments；分析产出放 `runs/30-diagnosis/`（不进 git）
- Must not edit: 代码、`skills/`、其他工单
- Server access: `da-remote pull dev-3`（工单 28 合并后，开发集运行可整份取回）、`da-remote start`（只跑工单 28 的报告命令）
- Spend cap: $3（只在需要 `evals attribute` 辅助时）

## Context

- ADR-0008"先读轨迹"：人先读、自由记录，再归纳计数；LLM 归因只作辅助。
- dev-3（commit `f095f2d`，Skill `f0805b10e5ae`，k=3）：full 57.8%、baseline 68.9%，差值 [-20.0, -3.3]。逐题不相等的 9 题：baseline 赢 8 题（customer-cross-sell-insights 1/3 对 3/3、fix-marketing-attribution 0/3 对 2/3、web-session-quality-scoring 1/3 对 3/3，其余 5 题 2/3 对 3/3），full 赢 1 题（pos-operations）。工单 25 之前的 dev-2（k=1）已是 full 60.0% 对 baseline 66.7%。
- 已有的 LLM 归因：`runs/dev-3-attr/report/report.md`（38 个 full 失败：FM4 22、FM3 15、FM2 1）。其中一个反复出现的模式：key 级自检复用了模型自己的谓词，验证者也给 PASS。
- 先看工单 28 的过程指标：full 是否常被 $1 花费上限截停、验证者结论、改模型次数——这些可能直接解释一部分失分。

## Acceptance

- [ ] 过程指标表（full 对 baseline）贴进 Comments
- [ ] 9 道分歧题每题至少读一条输的轨迹，自由记录；归纳出失败模式及各自的题数
- [ ] 结论：哪些开发集消融配置要在工单 31 里补跑（及理由）；候选修复工单列表，每条写明失败模式与目标题

## Evidence required

命令与输出、阅读笔记的位置、失败模式计数表。

## Stop conditions

轨迹取不回来；需要的判断超出开发集（不能看留出集）。

## Failure handling

在 Comments 写清卡在哪里。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
