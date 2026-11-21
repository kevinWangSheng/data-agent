# 105 — sql-workflow 补通用分析规则

Status: needs-triage
Blocked by: —
Spec: ../spec.md（"通用分析规则（105）"）
Claimed by: —
Branch: —

## Goal

`skills/sql-workflow/SKILL.md` 有 spec 列的 7 条通用分析规则，第 37 行和第 50 行不再矛盾；有一个测试保证 `skills/` 里不出现 DAB 数据集名。

## Authority

- May edit: `skills/sql-workflow/SKILL.md`（含文件头的改动说明）、`tests/`（新测试）
- Must not edit: 其他 Skill、`src/`（含 `ANSWER_RULE`）、`evals/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（验证运行由主会话做）
- Spend cap: $0

## Context

- 规则清单和写法要求：spec "通用分析规则（105）" 的 1–7 条。每条一个不点名基准的例子（照现有第 7 节的 orders/customers 风格）。
- 思路来源（只读，不复制原文）：DUO 的 `benchmark.md` 通用契约、Stela 的 `analysis-verification` skill；摘要在 `docs/research/2026-11-21-dab-same-model-gap.md`。DAB fork 没有许可证，Stela 的 skill 没有声明许可，AGENTS.md 禁止从无许可证仓库复制。
- 这个 Skill 是从 signalpilot-plugin 复制的（`NOTICE`），文件头已有改动说明，照格式补一句。
- ADR-0005：不点名数据集或题目，不写针对评分脚本行为的规则（例如"评分只认其中一个"）。
- DAB 的 12 个数据集：agnews、bookreview、crmarenapro、DEPS_DEV_V1、GITHUB_REPOS、googlelocal、music_brainz_20k、PANCANCER_ATLAS、PATENTS、stockindex、stockmarket、yelp。
- 注意 Skill 长度：现在 135 行；补完保持在约 200 行以内，规则写短。

## Acceptance

- [ ] 7 条规则都在，各带一个通用例子；第 37、50 行的并列矛盾消除。
- [ ] 新测试：`skills/` 下所有文件不含上面 12 个名字（大小写不敏感）；故意放一个名字进临时文件时测试会失败（测试自己验证这一点）。
- [ ] `tests/test_skills.py` 通过；`make check` 通过。

## Evidence required

`make check` 输出；Skill 改动前后的 diff 摘要（每条规则一行）。效果验证由主会话按 spec "验证"第 2 步做。

## Stop conditions

- 某条规则写不出不点名基准的版本。
- `tests/acceptance/` 里的测试因 Skill 文本变化变红。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —

## Comments
