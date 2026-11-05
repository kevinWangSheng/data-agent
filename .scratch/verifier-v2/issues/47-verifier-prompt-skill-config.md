# 47 — 单一验证器的提示词、Skill 第 8 步和配置

Status: ready-for-agent
Blocked by: 46（done）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

只留一个验证子 agent，按 spec 的 4 个步骤和报告格式工作；拿掉值验证器；FAIL 处理规则只写在系统提示词这一处；Skill 第 8 步改成用新工具和 `compare_by_key`；`maxTurns` 设为 50。这张工单合入后，验证层就是最终上线的形态，钩子已经生效。

## 已和用户定下的（2026-11-04）

- **验证器的 4 个步骤**（spec「验证器」）：
  1. `contract_check`，照抄结果；
  2. 从题面挑粒度键，调 `grain_facts`；
  3. 对照题面判断 `model_diff` 的结果；
  4. 用第 2 步定下的键，按题面独立写参考 SQL，调 `compare_by_key`。
- 只报 CHECK 1–7，格式严格照 spec。
- **系统提示词**里的 FAIL 处理：先复现，再做最小改动；复现不出来，就带上反证再派一次。NEEDS_INFO：补上信息再派一次，或者自己把这项检查做掉。
- **Skill**（`skills/dbt-workflow/SKILL.md`）只改第 8 步，就是第 220–279 行附近。工单 41 合入后行号可能变，以 "### Step 8" 这一节为准。
  - 删掉"照处方原样改"；
  - 主 agent 自己的键级核对改用 `compare_by_key`；
  - FAIL 处理只引用系统提示词，不写第二遍。
- 删除 `agents/value-verifier.md`，更新 `NOTICE`。
- baseline 和 no-verifiers 的行为不变。
- full-old-skill 的冻结版 Skill（`skills-frozen/`）不改，派单以系统提示词为准。它里面过时的 value-verifier 说法记为已知的干扰因素，留给最终报告。

## Authority

- May edit: `src/data_agent/agents/`、`src/data_agent/verifiers.py`（提示词、`AgentDefinition`、工具列表）、`skills/dbt-workflow/SKILL.md`（只改 Step 8 一节）、`NOTICE`、`tests/`（`tests/acceptance/` 除外；只许删掉转绿测试的 `xfail`）
- Must not edit: `src/data_agent/verifier_v2/`（工具和钩子，有问题就停下交回）、`src/data_agent/configs/`、Skill 的其他节、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（初筛由主会话跑）
- Spend cap: $0.5（本地或单题真实运行）；初筛由主会话跑

## Context

- spec：`.scratch/verifier-v2/spec.md`；ADR-0009；审查报告第 8 条（Skill 和系统提示词互相矛盾）。
- 上游：SignalPilot benchmark 版的验证器和 SKILL（`benchmark/signalpilot-plugin/agents/verifier.md`、`skills/dbt-workflow/SKILL.md:264-270`）。

## Acceptance

- [ ] 44 里剩下的验收测试转绿（配置：只有一个验证器、`maxTurns` 为 50）。
- [ ] `make check` 通过。
- [ ] **初筛**（主会话，full，每题 3 次，DeepSeek 闲时，对照 accept-2 full）：
  - 目标题：dbt-fix-division-by-zero、pos-operations、dbt-customer-cross-sell-insights、web-session-quality-scoring、dbt-receivables-aging-buckets。accept-2 合计 7/15。
  - 过关两条都要满足：
    - 成功次数 ≥8/15；
    - 每个 trial 验证器墙钟时间（各次调用的起止时间段取并集）的中位数 ≤155 秒，这 5 道题在 accept-2 的基线是 311 秒。
  - ≥8/15 在噪声范围内，初筛只用来筛一下，最终判断交给统一评估。
  - 同时报告：每次验证调用的工具调用数和轮数分布（p50、p90、p95、最大值），撞到 `maxTurns` 的次数，Stop 拦截的次数和原因。

## Evidence required

验收测试输出；`make check` 输出；初筛的汇总表（由主会话贴）。

## Stop conditions

- 提示词写不出 spec 的 4 个步骤，因为工具缺东西：停下，交回给 45 或 46。
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
