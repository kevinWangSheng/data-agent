# 47 — 单一验证器的提示词、Skill 第 8 步和配置

Status: in-review
Blocked by: 46（done）
Spec: ../spec.md
Claimed by: claude (worktree 47, 2026-11-05)
Branch: worktree-47-verifier-prompt-skill-config

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

- Attempts: 1
- Review rounds: 1（Codex at capacity ×2，Fable 只读审查 + eng:code-review Standards/Spec，已修 8bde360）
- Model spend: $0（工单会话无模型调用）
- Outcome: in-review，branch `worktree-47-verifier-prompt-skill-config`（ad09e0a、8bde360），PR #48；初筛待主会话
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（worktree 47-verifier-prompt-skill-config）

**改动**
- `src/data_agent/verifiers.py`：一个验证者，工具 `list_tables`、`query_database` 加 4 个 v2 工具，`maxTurns=50`，FAIL 和 NEEDS_INFO 规则只在系统提示词里有一份。
- `src/data_agent/agents/verifier.md`：按 spec 的 4 步和钩子能解析的 CHECK 1–7 报告重写。`value-verifier.md` 删除，`NOTICE` 更新。
- `skills/dbt-workflow/SKILL.md` 第 8 步：一个验证者，有 `compare_by_key` 时做键级核对，FAIL 处理引用系统提示词。无验证者的回退文本从 main 原样恢复，no-verifiers 配置不变。文件头改动说明只改第 8 步相关句子。
- 测试：验收测试去 xfail；单元测试跟随新提示词和第 8 步。

**第 3 步真实行为**（无模型；`uv run python -I /tmp/t47-real.py` 对六个配置 build options）：

```
full            agents=['verifier'] maxTurns=[50] verifiers_prompt=True
no-skill        agents=['verifier'] maxTurns=[50] verifiers_prompt=True
no-context      agents=['verifier'] maxTurns=[50] verifiers_prompt=True
full-old-skill  agents=['verifier'] maxTurns=[50] verifiers_prompt=True
baseline        agents=[] maxTurns=[] verifiers_prompt=False
no-verifiers    agents=[] maxTurns=[] verifiers_prompt=False
verifier.tools: ['Read', 'Glob', 'Grep', 'mcp__data__list_tables', 'mcp__data__query_database', 'mcp__data__model_diff', 'mcp__data__contract_check', 'mcp__data__grain_facts', 'mcp__data__compare_by_key']
hooks: ['PreToolUse', 'Stop', 'SubagentStart', 'SubagentStop']
```

验收测试 `test_verifier_configs_define_one_verifier` 去 xfail 后先红（`assert set(options.agents) == {"verifier"}`）后绿。`make check`：ruff、format、basedpyright 干净；`1693 passed, 1 skipped, 38 deselected in 301.48s`。

**审查第 1 轮**：`da-review main` 两次 at capacity，Fable 只读审查代替，加 `eng:code-review main`（Standards、Spec）。
- 已修（8bde360）：第 8 步在括号里复述 FAIL 规则、无验证者回退文本被改写（Standards 1、Spec 1/2）；系统提示词的停止句没给"主 agent 自己解决了 NEEDS_INFO"留出口（Fable 1）；报告模板原样复制会解析成 PASS，改成 `<PASS|FAIL|NEEDS_INFO|N/A>`（Fable 4）；第 8 步行数句点名 CHECK 3 而它现在是粒度检查，改成 CHECK 3 和 7（Fable 3）；改动说明换行（Standards 4）。
- 不修，Authority 外：Skill `## Tools` 一节仍把 `verify_model_values` 写成"value-verifier 在第 8 步用"（Standards 3、Fable 2），运行时无害（有"仅当在你的工具列表里"的限定，且工具未注册），需后续工单；`verifier_v2/dispatch.py`、`hooks.py` 里"until ticket 47 removes it"的过时注释同理。
- 不修，设计如此：`VERIFIER_MAX_TURNS` 在 `verifiers.py` 和 `verifier_v2/hooks.py` 各定义一份（hooks 导入 verifiers，反向会循环；hooks.py 在 Authority 外），有测试钉住两者相等。`VERIFIER_NAMES` 仍是单元素元组，`agent.py` 和 `hooks.py` 按成员判断。验证者工具表不含 `map_columns`、`check_model_schema`、`audit_model_sources`：spec 的"保留的旧工具"指 server 保留（已保留），4 步不需要它们（Spec 4）。
