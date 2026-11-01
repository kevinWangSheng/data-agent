# 30 — 诊断 dev-3：full 为什么不如 baseline（不跑新评测）

Status: done
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

- Attempts: 1
- Review rounds: 0
- Model spend: $0（读轨迹用 9 个子 agent，未调评测模型）
- Outcome: done；汇总 runs/30-diagnosis/summary.md
- Human review minutes: —

## Comments

## Comments

- 2026-11-01 主会话（准备，人读之前）：
  - `da-remote setup dcba420`；`scripts/da-remote pull dev-3` → `pull: server runs/dev-3 -> .../runs/dev-3 (dev set only: the whole run)`，取回 jobs/{full,baseline,no-context,no-skill,no-verifiers}。
  - `uv run python -m evals diverge --baseline runs/dev-3/jobs/baseline --candidate runs/dev-3/jobs/full --manifest evals/manifest.json --out runs/30-diagnosis/diverge` → "30 paired tasks; successes differ on 9: baseline better on 8, full better on 1."（与工单 Context 一致）。完整过程指标表：`runs/30-diagnosis/diverge/diverge.md`。
  - 过程指标要点：两边都没有 trial 被花费上限或轮数上限截停（"stopped: budget / turns" 全为 0 / 0），排除"被 $1 上限截停"这一解释；成功 trial 平均花费 baseline $0.0762、full $0.1605（约 2 倍）；full 每题 6–10 次验证者调用，输的分歧题里验证者多数给 PASS（如 dbt-fix-marketing-attribution 0/3：N/A 6、PASS 18，无 FAIL）。
  - 阅读入口：`runs/30-diagnosis/reading-index.md`（9 道分歧题输的一方的 13 个 trial 目录）。
  - 阅读包（用户选：主会话整理不带解读的原文，人读）：`runs/30-diagnosis/reading/README.md`，每道分歧题一个文件：题目原文、输的一方全部输的 trial（写过的文件列表、评分器输出末尾 120 行、逐步轨迹，长返回截断到 2500 字符）、赢的一方第一条成功 trial 作对照。生成脚本在会话 scratchpad（render_pack.py），不进仓库。笔记写 `runs/30-diagnosis/reading/notes.md`。
- 2026-11-01 主会话（诊断结论）：
  - 分工变更：用户决定由 agent 读轨迹、归类，人审汇总，不写自由笔记（与 ADR-0008"人先读、自由记录"原文不同，按用户决定执行）。9 个子 agent 各读一道分歧题，不看 runs/dev-3-attr，以免被已有归因带偏。汇总：`runs/30-diagnosis/summary.md`。
  - 失败模式（full 输的 12 条）：
    - A 偏离题目字面口径（自加过滤 / 删行 / 换源表）：3 题 4 条，multi-warehouse、rfm、cross-sell ×2
    - B 有歧义时没回到题内定义或输出列（同名量另造公式、选中间层列、"没有"写成 NULL）：3 题 4 条，receivables、web-session ×2、supplier
    - C 输出列不符题目契约（漏改名）：1 题 1 条，workforce
    - D 项目位置或 schema 判断错，评分器找不到上游表：1 题 3 条，marketing-attribution（baseline 也有 1 条输在这里，更像题面和评分器不一致）
    - E 共同放行者：12/12 条里验证者对照的是 agent 自写的 technical_spec，而不是题目原文，错误结果判 PASS；Structure 验证者没有 YML 就判 NEEDS_INFO 放行
    - F 验证者给出错误修法：1 条（cross-sell 8UZ7pRK 的 QoQ，未实跑确认）
  - pos-operations（baseline 输）：6 条里 5 条第一次就写对，判为偶然，不算 full 的收益。
  - 工单 31 补跑：dev-3 的三个消融配置无效（no-skill 60/90、no-verifiers 90/90、no-context 90/90 都是 ApiUsageLimitError，见 runs/dev-3/run.log）。用户决定补跑剩余配置，按工单 31 原范围：full、baseline、no-skill、no-verifiers、no-context。
  - 候选修复工单：
    1. 验证者拿到题目原文并以它为标准，专查 spec 与题目的偏离（过滤、join、源表、缺省值、列名）。针对 E/G，兼顾 A/B/C。目标题：multi-warehouse、rfm、cross-sell、receivables、web-session、supplier、workforce
    2. 没有 YML 时，Structure 验证者用题目的输出列清单作为契约。针对 C/E。目标题：workforce
    3. Skill 通用规则：不加题目没写的过滤、join 或换源；空结果可以接受；有歧义时先找题内定义。针对 A/B。目标题：同 1 中的 A/B 题
    4. 上下文压缩后保留题目原文。针对 C。目标题：workforce
    - marketing-attribution 暂不修（ADR-0005 风险），记为待确认。
