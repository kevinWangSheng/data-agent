# 15 — 单次花费上限按本项目价目表计

Status: ready-for-agent
Blocked by: 07
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

运行配置的 `max_budget_usd` 按本项目价目表（`agent.py` 的 `PRICES`，含验证子 agent 的 token）算出的真实花费生效：超过就停下，事件文件记为预算耗尽。SDK 自带的 `max_budget_usd` 只作后备，不再在真实花费约 $0.035 时截停非 Claude 模型。

## Authority

- May edit: `src/data_agent/`（花费统计与停止逻辑、运行配置读取）、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`src/data_agent/configs/*.toml` 里的数值（$1 不变）
- Server access: none
- Spend cap: $1

## Context

- 来源：工单 09 实测（见 09 Comments）：SDK 按 Claude 单价判断 `max_budget_usd=$1`，DeepSeek 下 5 次 data-eng-bench 运行有 4 次在 36–48 轮被截停（`terminal_reason` budget_exhausted），按价目表只花了约 $0.035。人决定（2026-10-16）：用自己的价目表执行上限，SDK 上限只作后备。
- spec「运行配置」：单次 `max_budget_usd`（flash 先设 $1），user story 19（每次运行有花费上限）。
- `src/data_agent/agent.py`：`PRICES`、`cost_usd` / `sdk_cost_usd` / `cost_basis` 的算法（约 :129、:336–378）；工单 07 改为按 SDK 的分模型用量计入子 agent。
- 不在价目表里的模型（如 Claude）SDK 本身计价正确，仍用 SDK 的上限。

## Acceptance

- [ ] 价目表里的模型：运行中按价目表累计花费（含子 agent），超过配置的 `max_budget_usd` 即停止；结果事件 `terminal_reason` 记为预算耗尽，`cost_usd` 为停止时的花费（只允许超出最后一次模型调用的量）。
- [ ] 价目表里的模型：传给 SDK 的 `max_budget_usd` 是后备值，不会在价目表花费远低于上限时截停（测试断言后备值与换算方式）。
- [ ] 不在价目表里的模型：行为不变，SDK 上限即配置值。
- [ ] 单元测试用假消息流覆盖以上三条；修复前会失败。
- [ ] `make check` 通过

## Evidence required

真实 dbt 运行两次（接缝 A 的 DBT_TASK，full 配置）：一次临时把上限设成 $0.005（命令行或临时配置，不改提交的配置文件）证明会按价目表停下；一次用默认 $1 证明不再在 ~40 轮被截停。贴两份结果事件。

## Stop conditions

SDK 拿不到运行中的逐轮用量、无法在运行中停止（要写明查过的 SDK 文档与源码位置）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
