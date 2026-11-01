# 32 — 验证者拿题目原文当标准

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: claude (opus-5-5)
Branch: worktree-32-verifiers-judge-against-task

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0（工单 agent）；服务器运行见 Comments
- Outcome: 单元测试类验收完成；服务器真实运行与初筛由主会话做
- Human review minutes: —

## Comments
- 2026-11-01 工单 agent（主会话转抄）：
  - 分支 `worktree-32-verifiers-judge-against-task`，提交 6118b77、3241230、4634d50。
  - 改动：
    - 开了验证者层的运行，用 `tempfile.mkdtemp(prefix="data-agent-task-")` 在工作区外建临时目录，把 `--task` 原文逐字节写进只读的 `task.md`；运行结束时删除这个目录。
      - 服务器上的路径是 `/tmp/data-agent-task-XXXX/task.md`，不是 spec 示例里的 `/tmp/data-agent/<id>/`。
      - 用 mkdtemp，是为了避免和别的用户共用一个目录。
    - `DBT_PROMPT` 加一句话给出这个路径。没开验证者层时，提示词和原来一字不差。
    - 两个验证者在构建时把路径写进提示词：`verifier_agents(server, task_file)`。
    - `workspace_guard(..., readable=)`：Read 只能打开这一个文件；Write、Edit、Glob、Grep，以及它旁边的文件，仍然一律拒绝。
    - 结构验证者新增 CHECK 6（Row Removal Has a Basis），输出格式加上对应的一行。
    - 数值验证者：题目定义过的量，按题目文件里的定义重算。
    - Skill 第 8 步和 `VERIFIERS_PROMPT` 改成：`technical_spec.md` 只代表主 agent 的意图。"不要告诉验证者列的含义、SQL 写法"这两句保留。
  - 不调模型的检查（`uv run python runs/32/demo.py runs/32/demo/ws`，SDK 的 query 换成桩）：
    - full：
      - 提示词里有 task.md 的路径；文件内容与题目逐字节一致；权限 0o444。
      - 两个验证者的提示词都带上了这个路径。
      - guard：Read task.md 放行；Write task.md 拒绝；Glob 它的目录拒绝；Read 同目录下的 other.md 拒绝。
      - 运行结束后目录已删除。
    - baseline：提示词里没有题目文件，和原来一致；没有 agents。
  - make check：ruff 和格式检查通过；basedpyright 0 errors；1354 passed，1 skipped（改动前是 1324）。pypi 连不上，用 `UV_OFFLINE=1` 跑。
  - da-review：
    - P1"CHECK 6 会让题目或 YML 没提到的每个源表都判 FAIL"：属实。3241230 已修，改成只有换过的源表才要找依据，并加了测试。
    - P1"某测试会抛 IndexError"：不属实。`## Task file` 在 md 正文里，不在追加的那一行路径里；这个测试在 make check 里是通过的。
  - eng:code-review（Standards）：
    - 没有硬性违规。
    - 4634d50 已改：`readable_paths` 命名；条件判断合并；guard 测试复用 decide()。
    - 不改的几处及理由：
      - 两个验证者的 md 里有同样的 `## Task file` 一节：每个 agent 的提示词必须各自完整。
      - protected 和 readable 两个参数：只有两个，不值得为它们单独定义一个类型。
      - 测试靠切分提示词找路径：措辞一改，测试会明确失败，不会悄悄通过。
      - test_task_file 里重复的 hook 调用：它测的是运行中实际构建出来的 guard，decide() 测不到这一层。
  - eng:code-review（Spec）：
    - 没有缺漏或错误。
    - 有意保留的两处：
      - Skill 文件头的修改记录写在第 8 步之外：复制来的上游文件必须登记修改（AGENTS.md）。
      - CHECK 3 的扇出豁免不再接受 technical spec 作依据：这符合 Goal 里"spec 只当作主 agent 的意图"。
  - 已知副作用：no-verifiers 也加载 Skill，所以第 8 步的措辞改动会让它的系统提示词跟着变。它的任务提示词不变，也不写题目文件。baseline 和查数任务完全不变。
- 2026-11-01 主会话：
  - no-verifiers 的系统提示词变化可以接受。dev-3 的 no-verifiers 全是 ApiUsageLimitError，没有有效结果要复用；最终消融时 6 个配置都会在新版本上重跑。dev-3 的 baseline 仍然可以复用。
- 2026-11-01 主会话：服务器真实运行 `t32-real`（commit 4634d50，`ablate --split dev --config full --task dbt-rfm-customer-segmentation --trials 1`）：reward 1，$0.1627（冒烟 cohort-retention-matrix reward 0，$0.1349）。轨迹核对：主 agent 派出的两个验证者和复查的结构验证者都 Read 了 `/tmp/data-agent-task-7lfqahsw/task.md`（3 次）；两份结构验证者的 `verifier_end` 都有 CHECK 6（数值验证者本来没有 CHECK 6）。
- 2026-11-01 主会话：退回：PR #31 Codex P2（`src/data_agent/agent.py:155`）属实。`saved_task` 直接 `tempfile.mkdtemp()`，`--workspace` 是 `/tmp` 或 TMPDIR 落在 workspace 里时，`task.md` 会建在 workspace 内，违反"workspace 之外"的约定（评分方会读到多出来的文件，guard 的边界也不成立）。修法：建临时目录前确认父目录不在 workspace 内（在的话换一个 workspace 外的父目录，或者直接报错），配一个修复前会失败的测试。Harbor 里 workspace 是 `/app`，不受影响，所以 4634d50 上的初筛 `t32-screen2` 结果对修复后的版本仍然有效。
