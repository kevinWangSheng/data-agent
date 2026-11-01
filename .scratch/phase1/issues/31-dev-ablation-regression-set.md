# 31 — 开发集诊断消融补跑与回归集入集

Status: ready-for-human
Blocked by: 26、27、28、30
Spec: ../spec.md
Claimed by: 主会话
Branch: —（运维；只提交 `evals/regression.json`）

## Goal

在服务器上同一个 commit 下跑开发集的 full、baseline 和工单 30 定下的消融配置，得到每一层在开发集上的贡献；full 的结果同时作为回归集第二轮，dev-3 与这次都 3/3 的题写进 `evals/regression.json`。

## Authority

- May edit: `evals/regression.json`（新建）、本工单的 Comments；运行产出在服务器 `runs/`
- Must not edit: 代码、`skills/`、其他工单
- Server access: `da-remote setup/push/start/status/pull`
- Spend cap: $70（按 dev-3 实测：full 90 次 $14.46、baseline $7.06；6 个配置全跑约 $65；实际配置数由工单 30 定，开跑前重算并写进 Comments）

## Context

- ADR-0008：开发集诊断消融不计入留出集次数；回归集入集要两轮 6/6；跑前预检、跑中快速失败（工单 27）。
- 不能续跑 dev-3：`ablate` 只在 trial 记录的同一 commit 上复用已有 job，ADR-0008 的复用条件也要 `git_commit` 一致；而预检、`ApiUsageLimitError` 的处理和 `--split dev` 都在 26–28 合并后的新 commit 上。所以在一个固定的新 commit（固定 checkout，运行中不改）上开新运行，`full`、`baseline` 与工单 30 定下的消融配置一起跑，开发集 30 题 × 3 次。
- 这次 `full` 的结果同时是回归集的第二轮：dev-3 里 full 3/3 的 12 道题（`runs/dev-3-rep-full/report/report.json`），在这次也 3/3 的入集。代价是 full 多跑 18 题（约 $8），换来同一 commit 下完整的一张表。
- 若 26–28 只改了 `evals/`、`scripts/`、`docs/`（`src/`、`skills/` 未变），agent 行为与 dev-3 相同；在 Comments 里写明，dev-3 与这次可作为同一版本的两轮样本参考，但过关判断只用同一 commit 的结果。

## Acceptance

- [ ] 预检通过的输出贴进 Comments
- [ ] 补跑配置的开发集报告与对 full 的配对比较（工单 28 的分歧表）
- [ ] 回归集入集：12 题第二轮结果，`evals/regression.json` 只含两轮都 3/3 的题，提交到 main
- [ ] 总花费与是否有基础设施报错

## Evidence required

`da-remote` 命令与输出；报告路径。

## Stop conditions

预检不过；花费到上限的 80%；出现基础设施报错无法续跑。

## Failure handling

在 Comments 写清卡在哪里。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-01 主会话（开跑前，两次被预检拦下，未花钱）：
  - 准备：`da-remote setup ba1aca5`。
  - 运行命令：`scripts/da-remote start dev-4 -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest evals/manifest.json --split dev --out runs/dev-4 --budget 70 --config full --config no-verifiers --config no-skill --config no-context --config baseline --trials 3 --n-concurrent 3`。
  - 第 1 次（17:41Z）：`balance (76.76 CNY (about $10.52)) is below the $70.00 left of --budget`。
  - 用户充值后第 2 次（17:55Z）：`balance (176.75 CNY (about $24.21)) is below the $70.00 left of --budget`。
  - 估算与实际花费：
    - 花费口径按 `src/data_agent/agent.py` 的 PRICES 计算，用的是 deepseek-flash 官方价格区间的上限，没有计闲时半价（ADR-0001：$0.15–0.30 / $0.6–1.2，缓存读 $0.003–0.006）。
    - 实测：t27-smoke 开跑前余额 ¥79.62，dev-4 开跑前 ¥76.76，实扣 ¥2.86（约 $0.39）；这两次估算合计 $0.81，实扣约为估算的 48%。两次都在北京时间夜间跑，样本很小。
    - dev-3 full 90 条的花费构成：缓存读 3.25 亿 token，占输入的 96%，花费 $1.95；未命中输入 1300 万 token，$3.88；输出 720 万 token，$8.63（占 60%）。合计 $14.46。
    - 实际花费在估算的 50%–100% 之间，取决于高峰与闲时的比例。
  - 量与时长：dev-3 原计划 450 条，有效 210 条，其中 no-skill 30/90，no-verifiers、no-context 都是 0/90；另外 240 条是 ApiUsageLimitError，没花钱。dev-3 跑 180 条用了约 12.8 小时（并发 3）。
  - 用户在重新考虑是否补跑（花费）；本工单暂停，未开跑。
