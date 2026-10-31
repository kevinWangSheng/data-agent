# 28 — 开发集运行：逐题配对分歧与过程指标，并能把轨迹取回本机

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

在开发集上跑和看结果不再借"临时 manifest 把开发集题放进 holdout"的办法：`ablate` 能直接跑开发集（可只跑其中几题），开发集报告给出两个配置逐题的配对分歧和每题的过程指标；只含开发集题的运行可以用 `da-remote pull` 把轨迹取回本机，供人读。留出集的保密不变。

## Authority

- May edit: `evals/`（`ablate`、`report`、`compare` 或新模块）、`scripts/da-remote`（`pull` 的判断）、`docs/setup.md`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（接缝 C 保证留出集只出汇总）、`evals/scrub.py`（工单 26）、`src/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（服务器上的取回实测由主会话在合并后做）
- Spend cap: $2

## Context

- ADR-0008"先读轨迹"要求人读开发集上的配对分歧题；ADR-0004 只禁止看留出集的逐题结果。
- 现状：`evals/ablate.py` 固定 `SPLIT = "holdout"`，只跑 manifest 的 holdout 列表；dev-1/2/3、smoke17、deb21b、deb22 都用临时 manifest 把开发集题放进 holdout。`da-remote pull` 对 `ablate` 运行一律只取汇总（`scripts/da-remote` 约第 593–612 行），所以 dev-3 的轨迹取不回来。
- 取回放宽的条件必须在服务器上按 `evals/manifest.json`（真 manifest，不是运行自带的）核对：运行里每个 trial 的题都在 dev 列表里，才整份取回；任何一题不在、或判断不了，照旧只取汇总。dev-3 的 jobs 全是开发集题，修好后应能取回。
- 过程指标从事件文件读（事件类型见 `src/data_agent/events.py`、`agent.py`）：用了多少轮、是否被花费上限或轮数上限截停（SDK `ResultMessage` 的结束原因）、验证者是否被调用及其结论、dbt 模型被改了几次、花费；再加每道成功题的平均花费。
- 配对分歧：两个 job（如 `full` 与 `baseline`）在每道开发集题上的成功次数，列出不相等的题。dev-3 现有数据：不相等的 9 题里 baseline 赢 8 题、full 赢 1 题。

## Acceptance

- [ ] `ablate --split dev` 跑真 manifest 的开发集，可以用参数只跑其中几题；holdout 的行为不变；有测试
- [ ] 开发集报告：每题的过程指标，以及两个 job 的逐题配对分歧表；对 holdout 拒绝输出逐题内容；有测试
- [ ] `da-remote pull`：只含开发集题的运行整份取回，含任何非开发集题或无法判断的照旧只取汇总；两种情况都有测试
- [ ] 接缝 C 的验收测试全部保持通过
- [ ] 真实运行：本地 `runs/dev-2/out/jobs/full` 对 `runs/dev-2/out/jobs/baseline` 出配对分歧与过程指标，贴输出；再在 Mac colima 上 `ablate --split dev` 跑 1 题 k=1
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令与输出。

## Stop conditions

做到某一步必须改接缝 C 的验收测试；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
