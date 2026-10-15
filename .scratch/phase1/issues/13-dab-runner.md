# 13 — DAB runner

Status: ready-for-agent
Blocked by: 03, 05
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

从本地 DAB clone 按路径 import 它的脚手架，把 `common_scaffold/tools` 包成 MCP 工具，用官方提示文件跑题，每题 5 次，输出 DAB 要求的提交 JSON 与轨迹。

## Authority

- May edit: `evals/`（DAB runner）、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $3

## Context

ADR-0005；plan §4 DAB 基线；上游 `~/dev/AI/data-agent-refs/DataAgentBench`（没有许可证：只能 import 和运行，不复制任何代码或文本进本仓库）。

## Acceptance

- [ ] 单题真实跑通 5 次并产出提交格式
- [ ] 本仓库里没有任何 DAB 代码或提示文本
- [ ] `make check` 通过

## Evidence required

单题运行命令与提交 JSON 片段；花费。

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
