# 33 — 没有 YML 时，结构验证者用题目的输出列当契约

Status: ready-for-agent
Blocked by: 32
Spec: ../spec.md
Claimed by: claude (implement session)
Branch: worktree-33-structure-contract-from-task-columns

## Goal

结构验证者 CHECK 2 的列契约按以下顺序取：

1. 模型有 YML，用 YML。
2. 没有 YML，用题目原文里给的输出列清单：列名，题目写了类型的，也带上类型。
3. 两者都没有，才判 NEEDS_INFO。

## Authority

- May edit:
  - `src/data_agent/agents/verifier.md`（同步文件头修改记录）
  - `src/data_agent/verifier_tools.py`（只在 `check_model_schema` 的入参需要调整时）
  - `tests/`（`tests/acceptance/` 除外）
- Must not edit:
  - `tests/acceptance/`、`evals/`、`scripts/`、`skills/`
  - 其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- 诊断：失败模式 C、E（`runs/30-diagnosis/summary.md`）。
  - workforce-analytics 的源列 `effective_from` 没有改名成题目要求的 `effective_date`。
  - 结构验证者没有 YML，CHECK 2 判 NEEDS_INFO 放行（`src/data_agent/agents/verifier.md` 的 CHECK 2 一节）。
  - pos-operations 同类。
- 依赖工单 32：题目原文文件和它的路径，已经写进验证者提示词。
- 已和用户定下（2026-11-01，Q6）：
  - YML 与题目冲突时不处理，以 YML 为准。
  - 题目没给输出列清单时，仍判 NEEDS_INFO。
- ADR-0005：只写通用规则。

## Acceptance

- [ ] 测试：
  - 有 YML 时，契约取 YML；
  - 没有 YML、题目有输出列清单时，契约取题目里的列名（和类型）；
  - 两者都没有时判 NEEDS_INFO。
  - 测试接缝放在提示词结构或 `check_model_schema` 入参上，由工单 agent 选最贴近现有测试的位置。
- [ ] `make check` 通过
- [ ] （主会话）真实运行：在服务器上用 PR 分支 commit 跑 workforce-analytics 1 次（约 ¥1），看 CHECK 2 的结论不是 NEEDS_INFO。
- [ ] （主会话）初筛（ADR-0008 Update）：在 PR 分支 commit 上（已包含 32）跑 full。
  - 目标题：workforce-analytics、pos-operations，每题 3 次，共 6 条。
  - 过关条件：成功次数合计 > 5/6（dev-3 full）。
  - 花费和轮数只记录。
  - 开跑前和用户确认。

## Evidence required

- 单元测试输出、`make check` 输出。
- 主会话：`da-remote` 命令与输出、初筛表、花费。

## Stop conditions

- 需要改 `tests/acceptance/`。
- 第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0（工单 agent，只在本地）；服务器运行见 Comments
- Outcome: CHECK 2 契约顺序 YML → 题目输出列 → NEEDS_INFO，已在提示词里实现并配测试；等主会话的服务器运行和初筛
- Human review minutes: —

## Comments
- 2026-11-01 工单 agent（主会话转抄）：
  - 分支 `worktree-33-structure-contract-from-task-columns`，提交 187a662（CHECK 2 契约顺序 YML → 题目输出列 → NEEDS_INFO；冲突时以 YML 为准）、adc6093（审查修复）。
  - 测试接缝选在提示词结构：`tests/test_verifiers.py::test_verifier_takes_the_column_contract_from_yml_then_the_task_then_needs_info` 检查三步的顺序和措辞、"YML 为准"的说法、没有 YML 时 map_columns 输出怎么读、CHECK 2 输出行写明契约来源。改提示词前失败（IndexError），改后通过；`tests/test_verifiers.py` 26 passed。
  - make check：187a662 上 1402 passed，1 skipped，basedpyright 0 errors。adc6093 上 lint、格式、类型都干净；有 7 个失败，都在 `tests/test_harbor_agent.py`、`tests/test_packaging.py`，原因是 `uv build` 从 pypi 取 hatchling 时 "tls handshake eof"；单独重跑这两个文件 59 passed。属于网络问题，与代码无关（以 PR 的 CI 为准）。
  - 本地真实运行（不调模型）：用 `agent.build_options(Task(type='dbt'), load_config('full'), …)` 构建验证者，打印 CHECK 2 一节：三步 1/2/3 顺序正确，有 "Call `check_model_schema(model_name, yml_columns)` with the contract's columns"；`parse_checks('- CHECK 2: FAIL - task; missing effective_date')` → `[{'model':'m','check':'CHECK 2','status':'FAIL'}]`。
  - 第 1 轮审查：da-review 无问题。eng:code-review 的 Standards 和 Spec 都指出：map_columns 那一步仍然默认有 YML，没有 YML 时会打印 "YML contract: NONE"，把每个上游列都列成 UNMAPPED-INCLUDE，和题目契约冲突，容易引出 NEEDS_INFO；另外，改动模型的"之前就缺"豁免会放过缺失的 effective_date。adc6093 已修：
    - 契约和 check_model_schema 挪到 map_columns 之前。
    - 有题目契约时，"YML contract: NONE" 不算 NEEDS_INFO，上游的 UNMAPPED 列也不算缺陷。
    - 题目列出的列，模型里没有就报出来，改动模型也一样。
    - CHECK 2 输出行写明契约来源（YML / task / none）。
    - 文件头的修改记录已更新。
  - 第 2 轮审查：da-review 无问题。
  - 不修的几处及理由：
    - `check_model_schema` 的 docstring 和报错文字还写着 "YML"（`verifier_tools.py:1398、1407`）。Authority 只允许在入参变化时改这个文件，参数名 `yml_columns` 又被 `tests/acceptance/test_seam_b_mcp_server.py` 钉住。如果服务器运行里验证者因为这个措辞不肯传题目列，再开后续工单改 docstring。
    - 测试里重复切分 CHECK 一节、测试钉死具体措辞：沿用 `test_verifiers.py` 的现有写法，属于判断，不算违反规范。
    - frontmatter 的 description 只提 map_columns：这是上游文本，保持原样。
- 2026-11-01 主会话：验收里写的"初筛开跑前和用户确认"按 plan §10 和 HANDOFF 的默认放行规则（2026-11-01，写在本工单之后）处理：初筛属于小额运行，直接跑，跑完汇报。dev-3 full 在这两道题上是 5/6（workforce 2/3，pos 3/3），所以过关要 6/6。
- 2026-11-02 主会话：PR #34 CI 绿。退回：PR #34 Codex P2（`src/data_agent/agents/verifier.md:95`）属实。契约第 3 步写着 YML 和题目都没有列时 CHECK 2 = NEEDS_INFO，但后面 map_columns 的规则（CREATED 的模型报告每个 UNMAPPED-INCLUDE 列）只在"题目给了契约"时豁免；没有契约时两条指令冲突，验证者仍可能把上游列报成 FAIL，而不是 NEEDS_INFO，引出不必要的模型改动。修法：给"没有契约"加一个明确分支，这时 map_columns 的 UNMAPPED 列不算缺陷，CHECK 2 只报 NEEDS_INFO；配测试。初筛 `t33-screen` 在 adc6093 上运行，两道目标题走的是题目契约分支，主会话会在轨迹里核对 CHECK 2 的契约来源；若核实属实，初筛结果对修复后的版本仍然有效。
