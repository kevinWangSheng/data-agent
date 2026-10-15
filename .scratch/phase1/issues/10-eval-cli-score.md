# 10 — 评测 CLI：抽样、算分、比较、报告

Status: ready-for-agent
Blocked by: 01
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

一条命令就能：抽开发集（fast-30）与留出集（其余 73 题按难度分层、种子进 git）；按榜单算法算分；对两个配置做按题配对 bootstrap；出报告——留出集只出汇总，开发集出逐题；基础设施报错与 agent 失败分开统计。

## Authority

- May edit: `evals/`、`tests/`（除 `tests/acceptance/`）、`NOTICE`、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

ADR-0004；plan §4 算分与超过基线；CONTEXT 的 Development set、Evaluation holdout、Deterministic pass rate；上游 data-eng-bench `leaderboard/src/leaderboard/core/metrics.py`（复制并登记 `NOTICE`）。

## Acceptance

- [ ] 接缝 C 中抽样、算分、出错记 0、bootstrap、两类报告的验收测试转绿
- [ ] 留出集清单与种子进 git
- [ ] `make check` 通过

## Evidence required

对夹具结果目录跑一次报告命令的输出（留出集部分只有汇总）。

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
