# 12 — 消融运行器

Status: ready-for-agent
Blocked by: 06, 07, 09, 10, 11, 15, 16
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

一条命令在留出集上跑 6 个配置 × 3 次，经 scrub 与花费护栏，产出消融表（每层贡献与配对 bootstrap 区间）。本地用 1 题 × 6 配置验证；全量在服务器上跑，等 `da-remote`。

## Authority

- May edit: `evals/`、`tests/`（除 `tests/acceptance/`）、`src/data_agent/configs/full-old-skill.toml`、冻结的旧版 Skill 目录（新建，如 `skills-frozen/<版本>/`，内容是当前 `skills/` 的原样副本）、`pyproject.toml`（只限 force-include 该目录）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $3

## Context

ADR-0004（6 配置 × 3 次 = 540 次）；plan §7 降级顺序与护栏。

## Acceptance

- [ ] 本地 1 题 × 6 配置跑通并出消融表
- [ ] 留出集结果只以汇总形式出现
- [ ] 调 `compare` 时传 `--manifest`/`--split`（工单 10 的 compare 不强制，见 10 的 Comments）
- [ ] 每次报告用新的空 `--out` 目录；遇到配置类报错（`AgentAuthenticationError`、`ModelNotFoundError`、`UnsupportedModelParametersError`）停下找人，不出报告（工单 10 已知限制）
- [ ] 冻结旧版 Skill 到独立目录，`full-old-skill` 配置指向它（工单 05 留的缺口；目前等于 `full`）；该目录加进 `pyproject.toml` 的 force-include
- [ ] DAB runner 接上 context 层开关（工单 13 已知限制：DAB server 不注册 context tools，`full` 与 `no-context` 在 DAB 上行为相同）：把开关传给 `evals/dab_server.py` 并注册对应工具，或拒绝在 DAB 上跑 `context = true` 的配置
- [ ] DAB 续跑核对加上 `git_commit` 与 `skill_version`（工单 13 已知限制：`evals/dab.py` 的续跑检查只比 config、model、hints、DAB commit）
- [ ] DAB 续跑可靠性（工单 13 已知限制）：`submission.json` 先写临时文件再原子替换；运行条件里 DAB commit 带脏工作区标记，脏 checkout 拒绝续跑
- [ ] `make check` 通过

## Evidence required

本地小规模运行的命令与消融表。

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

### 2026-10-16 主会话：派工前修正 Authority

验收标准里"冻结旧版 Skill、`full-old-skill` 指向它、加进 force-include"是主会话从工单 05 转来的，但 Authority 原来只有 `evals/`、`tests/`，自相矛盾；已把对应的三处加进 May edit。"旧版"取当前 main 上的 `skills/`（工单 14 的失败归因还没改过 Skill）。本工单只做本地 1 题 × 6 配置验证；全量等 `da-remote` 与服务器升配，不在本工单内。
