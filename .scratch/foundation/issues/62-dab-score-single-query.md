# 62 — dab-score：每个数据集只有 1 题时不报区间

Status: ready-for-agent
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）
Claimed by: —
Branch: —

## Goal

`dab-score` 的配对比较在每个数据集都只有 1 题时，不再报一个退化成点的区间和 "significant"，而是说明题数不够、不能估计区间。

## 已和用户定下的（2026-11-06，grilling 第 2 轮 Q7）

- 所有数据集都只有 1 道题时：不算区间，输出"每个数据集题数不够，不能估计区间"，`significant` 记 false。
- 有的数据集多题、有的只有 1 题时：照现在的算法（正式评测每个数据集都 ≥2 题，见 Context）。

## Authority

- May edit: `evals/dab_score.py`、`tests/test_eval_dab_score.py`
- Must not edit: `tests/acceptance/`、`evals/compare.py`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- 机制：`paired_bootstrap`（`evals/dab_score.py:205-226`）在每个数据集内部有放回地重抽题；每组只有 1 题时每次重抽都等于点估计，区间退化。试跑输出过 "-11.76 points (95% CI -11.76 to -11.76; significant)"（HANDOFF.md 2026-11-02）。
- 服务器上 DAB clone 的 17 个数据集各有 2–13 题（2026-11-06 用 `da-remote exec` 数过），正式评测不受影响。
- 输出文本在 `dab_score.py:309` 附近。

## Acceptance

- [ ] 新测试：每个数据集 1 题、两边结果不同时，输出不含 "significant"（作肯定判断），含"不能估计区间"的说明，`significant` 为 false（修复前会失败）。
- [ ] 现有 `dab-score` 测试全部通过。
- [ ] `make check` 通过。

## Evidence required

测试输出。合入后主会话在服务器上用 `da-remote` 按 `runs/dabtrial2-score/run.log` 第 1 行的命令重算一次（要 DAB clone，只在服务器上；$0），输出贴进 Comments。

## Stop conditions

- 必须改 `evals/compare.py` 或 Pass@1 的算法。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
