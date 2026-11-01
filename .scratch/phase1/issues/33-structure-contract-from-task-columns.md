# 33 — 没有 YML 时，结构验证者用题目的输出列当契约

Status: ready-for-agent
Blocked by: 32
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

结构验证者 CHECK 2 的列契约按以下顺序取：

1. 模型有 YML，用 YML。
2. 没有 YML，用题目原文里给的输出列清单：列名，题目写了类型的，也带上类型。
3. 两者都没有，才判 NEEDS_INFO。

## Authority

- May edit:
  - `src/data_agent/agents/verifier.md`（同步文件头修改记录）
  - `src/data_agent/verifier_tools.py`（只在 `check_model_schema` 的入参需要调整时）
  - `tests/`（`tests/acceptance/` 除外）
- Must not edit:
  - `tests/acceptance/`、`evals/`、`scripts/`、`skills/`
  - 其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- 诊断：失败模式 C、E（`runs/30-diagnosis/summary.md`）。
  - workforce-analytics 的源列 `effective_from` 没有改名成题目要求的 `effective_date`。
  - 结构验证者没有 YML，CHECK 2 判 NEEDS_INFO 放行（`src/data_agent/agents/verifier.md` 的 CHECK 2 一节）。
  - pos-operations 同类。
- 依赖工单 32：题目原文文件和它的路径，已经写进验证者提示词。
- 已和用户定下（2026-11-01，Q6）：
  - YML 与题目冲突时不处理，以 YML 为准。
  - 题目没给输出列清单时，仍判 NEEDS_INFO。
- ADR-0005：只写通用规则。

## Acceptance

- [ ] 测试：
  - 有 YML 时，契约取 YML；
  - 没有 YML、题目有输出列清单时，契约取题目里的列名（和类型）；
  - 两者都没有时判 NEEDS_INFO。
  - 测试接缝放在提示词结构或 `check_model_schema` 入参上，由工单 agent 选最贴近现有测试的位置。
- [ ] `make check` 通过
- [ ] （主会话）真实运行：在服务器上用 PR 分支 commit 跑 workforce-analytics 1 次（约 ¥1），看 CHECK 2 的结论不是 NEEDS_INFO。
- [ ] （主会话）初筛（ADR-0008 Update）：在 PR 分支 commit 上（已包含 32）跑 full。
  - 目标题：workforce-analytics、pos-operations，每题 3 次，共 6 条。
  - 过关条件：成功次数合计 > 5/6（dev-3 full）。
  - 花费和轮数只记录。
  - 开跑前和用户确认。

## Evidence required

- 单元测试输出、`make check` 输出。
- 主会话：`da-remote` 命令与输出、初筛表、花费。

## Stop conditions

- 需要改 `tests/acceptance/`。
- 第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
