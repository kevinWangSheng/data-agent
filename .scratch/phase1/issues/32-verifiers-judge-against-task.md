# 32 — 验证者拿题目原文当标准

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

开了验证者层的 dbt 运行，会在工作区外落一份题目原文文件，只读。

- 两个验证者都拿它当标准，`technical_spec.md` 只当作"主 agent 的意图"。
- 结构验证者新增一项检查：每个过滤、inner join、删行、换源，都要在题目或 YML 里找到出处。
- 数值验证者按题目里的定义重算题目定义过的量。

## Authority

- May edit:
  - `src/data_agent/agent.py`、`src/data_agent/workspace_guard.py`、`src/data_agent/verifiers.py`
  - `src/data_agent/agents/verifier.md`、`src/data_agent/agents/value-verifier.md`
  - `skills/dbt-workflow/SKILL.md`（只改第 8 步）
  - `tests/`（`tests/acceptance/` 除外）
- Must not edit:
  - `tests/acceptance/`、`evals/`、`scripts/`、`src/data_agent/configs/`
  - 其他 Skill
  - 其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（第 3 步的真实运行和初筛由主会话在服务器上做）
- Spend cap: none（工单 agent 不跑付费运行）

## Context

- 诊断：`runs/30-diagnosis/summary.md`；工单 30 Comments 的失败模式 A、B、E、G。
  - full 输的 12 条里，验证者都拿 agent 自己写的 technical_spec 当标准，错误结果判 PASS。
  - 验证者拿不到题目原文。题目只从 `--task` 内联进 `DBT_PROMPT` 的 `<task>`（`src/data_agent/agent.py:124-140`、`:795-801`），没有落成文件。
  - Skill 第 8 步（`skills/dbt-workflow/SKILL.md`）派验证者时只传项目目录、模型名和 spec 路径，并且明令不传列定义。
- 已和用户逐条定下的设计（2026-11-01，Q1–Q5、Q13）：
  1. **题目文件放在工作区外**，例如 `/tmp/data-agent/<每次运行唯一>/task.md`，内容是 `--task` 原文，一字不改。
     - 评分器看的目录里不能多出东西。
     - `workspace_guard` 只放行这一个固定路径，只允许 Read：
       - 不允许 Write、Edit；
       - 不允许读它旁边的其他文件；
       - 不允许 Glob、Grep 借它的目录越出工作区。
  2. **只在开了验证者层时才写**（`uses_verifiers(task.type, config)` 为真：dbt 任务，并且是 full、no-skill、no-context 中的一个）。
     - 主 agent 的提示词里加一句文件路径，压缩上下文后可以重读。
     - baseline、no-verifiers 和所有查数任务的提示词一字不改，也不写这个文件。这样 dev-3 的 baseline 仍可复用。
  3. **验证者怎么拿到路径**：在构建验证者定义时，把路径写进两个验证者的提示词（`verifier_agents` 加参数），不靠主 agent 转交。
     - 理由：`VERIFIERS_PROMPT`（`src/data_agent/verifiers.py`）在 no-skill 下也生效，而 Skill 第 8 步只在开了 Skill 层时才会加载。
     - Skill 第 8 步和 `VERIFIERS_PROMPT` 里，把 `technical_spec.md` 的用途改成"主 agent 的意图，以题目原文为准"。
     - 两处原有的"不要告诉验证者列的含义或 SQL 写法"保留。
  4. **结构验证者**：在 CHECK 5（保留源表）旁边新增一项检查。
     - 模型里每个 WHERE / HAVING 过滤、inner join（会删行的）、删行逻辑、换用的源表，都要在题目原文或 YML 里找到依据。找不到就 FAIL，并引用那段 SQL。
     - 报告格式里加上这一项，`parse_checks` 能解析出来。
     - 不加更细的规则。
  5. **数值验证者**：题目里定义过的量（同名指标、公式、分桶、缺省值），按题目原文里的定义重算，不按 spec 里的写法。
  6. Step 8 原有的 FAIL → 修 → 重验循环不变，不加仲裁规则。
- 两个验证者的 md 是从 SignalPilot 抄来的（`NOTICE`）。文件头的 "Modified for data-agent" 记录要同步写上这次的改动。
- ADR-0005：Skill 和验证者提示词只写通用规则，不能出现任何榜单题目的名字或特有口径。
- 下游：工单 33（没有 YML 时用题目输出列当列契约）在本工单合入后接着做。

## Acceptance

- [ ] 单元测试：
  - [ ] 开了验证者层时，题目文件存在，内容与 `--task` 原文一字不差；主 agent 提示词和两个验证者提示词里都有这个路径。
  - [ ] baseline、no-verifiers 和查数任务不写这个文件，提示词和改动前逐字相同。
  - [ ] guard 允许 Read 这个文件；拒绝对它 Write、Edit；拒绝 Read 它所在目录的其他文件；拒绝从它的目录 Glob、Grep。
  - [ ] `parse_checks` 能解析结构验证者新增那一项的 PASS / FAIL。
  - [ ] Skill 和验证者提示词里没有出现开发集或榜单的题名（可复用现有的 ADR-0005 检查方式，没有就新加）。
- [ ] `make check` 通过
- [ ] （主会话）真实运行：在服务器上用 PR 分支 commit 跑 1 道目标题 1 次（约 ¥1）。确认两点：
  - 事件文件里验证者 Read 过题目文件；
  - `verifier_end` 的报告里出现新增的那一项。
- [ ] （主会话）初筛（ADR-0008 Update）：在 PR 分支 commit 上跑 full，6 道目标题，每题 3 次，共 18 条。
  - 目标题：multi-warehouse-stock-rebalance、rfm-customer-segmentation、customer-cross-sell-insights、receivables-aging-buckets、web-session-quality-scoring、supplier-payment-optimization。
  - 过关条件：成功次数合计 > 10/18（dev-3 full）。
  - 花费和轮数只记录。
  - 开跑前和用户确认范围和花费。

## Evidence required

- 单元测试输出、`make check` 输出。
- 主会话：`da-remote` 命令与输出；初筛表（每题 dev-3 full 的成功次数对本次）；花费。

## Stop conditions

- 需要改 `tests/acceptance/` 才能过。
- guard 放行这个文件时，必须放宽别的路径规则。
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
