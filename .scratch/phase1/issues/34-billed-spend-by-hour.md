# 34 — 花费按运行时段计价：报告、累计花费、开跑前检查

Status: in-progress
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 34-billed-spend-by-hour)
Branch: worktree-34-billed-spend-by-hour

## Goal

评测侧的花费改成接近实际扣费。

- 每次运行按开始时刻是 DeepSeek 的高峰还是闲时计价，记为"实际价花费"。
- 开跑前的余额检查、累计花费护栏和报告都用实际价花费。
- 单次运行的上限 `max_budget_usd` 和事件文件里的 `cost_usd` 不变，仍按高峰价算，agent 行为不受影响。

## Authority

- May edit: `evals/`、`tests/`（`tests/acceptance/` 除外）、`docs/setup.md`
- Must not edit: `src/`（包括 `PRICES` 和单次上限）、`skills/`、`scripts/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- 现状：
  - `src/data_agent/agent.py` 的 `PRICES` 用 deepseek-flash 的高峰价：未命中输入 $0.3、缓存命中 $0.006、输出 $1.2（每百万 token）。
  - 结果事件的 `cost_usd` 就按这个价格算。
  - `evals spend`（`evals/spend.py`）、开跑前余额检查（`evals/ablate.py` 的 `check_balance`）、`evals/dab.py` 的 `spent()`、报告（`evals/report.py`、`evals/process.py`）都直接累加 `cost_usd`。
- 实测：
  - 两次闲时运行（北京时间夜里）的实际扣费约为估算的 48%（工单 31 Comments），正好约是闲时半价。
  - 因此开跑前检查要求的余额约为实际所需的 2 倍。dev-4 因此被拦了两次。
- 官方时段（2026-11-01 核实，`docs/plan.md` §7，`api-docs.deepseek.com/quick_start/pricing`）：
  - 高峰时段：周一至周五 UTC 01:00–04:00、06:00–10:00。
  - 其余时间都是闲时。
  - 闲时每一项单价都是高峰的一半。
  - 中国法定节假日全天都是闲时。本工单不维护节假日表，节假日按高峰算，宁可多算。
- 已和用户定下的做法（2026-11-01）：
  - 单次上限仍按高峰价算。上限随时段变的话，同一个配置在白天和夜里的行为会不同。
  - 时段按每次运行开始的时刻定，取事件文件第一条事件的 `ts`（UTC）。一次运行大约 5–10 分钟，跨时段的误差可以接受。
  - 实际价花费的算法：
    - `cost_basis == "price_table"`，并且 version 事件的模型是 DeepSeek 的：闲时运行等于 `cost_usd × 0.5`，高峰运行等于 `cost_usd`。
    - 其他情况（SDK 口径、未知模型、缺 `ts`）：等于 `cost_usd`，不打折。
  - 旧事件文件不用改写，按同一规则从现有字段算出来。
- 不改 `src/`：结果事件已经有 `cost_usd`、`cost_basis`，第一条事件有 `ts` 和 version 的 `model`，评测侧能自己算。

## Acceptance

- [ ] 测试，计价规则：
  - [ ] 高峰运行等于 `cost_usd`；闲时运行等于一半。
  - [ ] 时段边界：UTC 00:59、01:00、03:59、04:00、05:59、06:00、09:59、10:00。
  - [ ] 周末全天是闲时。
  - [ ] SDK 口径、非 DeepSeek 模型、缺 `ts` 时不打折。
- [ ] 测试，`evals spend`：
  - [ ] 按实际价花费判断 80% 护栏；
  - [ ] 输出里同时给出实际价合计和高峰价合计。
- [ ] 测试，开跑前余额检查：已花掉的部分按实际价算。
- [ ] 测试，DAB 的 `--budget`：已花掉的部分按实际价算。
- [ ] 测试，报告：开发集和留出集的报告都多一列实际价花费，原有的 `cost_usd` 列保留。
- [ ] `docs/setup.md`：写明两种花费口径各自的用途，以及 `--budget` 现在按实际价算。
- [ ] 真实运行：对本地 `runs/dev-3`（Harbor jobs）跑一次 `evals spend` 和开发集报告，贴出两种口径的合计和高峰、闲时各自的 trial 数。
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令与输出。

## Stop conditions

- 需要改 `src/` 才能拿到时间或模型。
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
