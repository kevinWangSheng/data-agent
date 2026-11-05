# 49 — 提示词和 Skill 不再教 agent 改 `profiles.yml`；不 Read dbt 生成的大 JSON

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 49-dbt-prompt-no-profiles-edit)
Branch: worktree-49-dbt-prompt-no-profiles-edit

## Goal

dbt 任务里 agent 按工单 40 放行的方式工作：`cd <项目目录> && dbt ...`，不传 `--project-dir`/`--profiles-dir`，不改 `profiles.yml`；validate 的时限说明和代码一致（300 s）；Skill 明确不要 Read `target/` 下 dbt 生成的 JSON。

## 已和用户定下的（2026-11-05）

- 类别：**改变行为**（ADR-0008 Update 2026-11-04）。验证是单元测试，加主会话初筛目标题 × 3，对照 accept-2 full。
- 三处改动，不扩：
  1. `src/data_agent/agent.py` 的 `DBT_PROMPT`（main b62226f 第 130–146 行）：把 "for a project in a subdirectory, pass --project-dir and --profiles-dir. Relative paths in a profile resolve from the working directory." 改成教 `cd <project_dir> && dbt ...`（守卫允许，见 `src/data_agent/command_guard.py:6-7`），profile 里的相对路径在项目目录下解析，不要改 `profiles.yml`。
  2. `skills/dbt-workflow/SKILL.md:385`："default 60" 改成 300，与 `skills/dbt-workflow/validate_project.py:3` 一致。
  3. `skills/dbt-workflow/SKILL.md` Rules 一节加一条：不要 Read `target/` 下 dbt 生成的 JSON（`run_results.json`、`manifest.json`），它们是单行超长文件，读不进上下文；要看运行结果用 dbt 命令的输出。原因：工单 41 初筛里内置 Read 读 `target/run_results.json` 触发 2 次超长报错。
- 不改守卫（改守卫是改安全边界，要另外讨论）。不改 Skill 的其他步骤（工单 47 会改第 8 步）。
- 按 ADR-0005，不写针对任何题目的内容。

## Authority

- May edit: `src/data_agent/agent.py`（只改 `DBT_PROMPT` 文本）、`skills/dbt-workflow/SKILL.md`（只改上面三处中的两处）、`tests/test_dbt_skills.py`、`tests/test_agent.py` 或对应测试文件
- Must not edit: `tests/acceptance/`、守卫、`src/data_agent/verifier_v2/`、`src/data_agent/agents/`、`skills/` 其他文件、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（初筛由主会话做）
- Spend cap: $0

## Context

- 工单 40 的 Comments（2026-11-04 主会话 `t40-real`）：agent 没用 `cd … && dbt`，而是 `--project-dir` 加 Edit `profiles.yml`，原因是提示词和 Skill 仍这样教。accept-2 里 97/180 个 trial 改过 `profiles.yml`。
- 工单 41 的 Comments（`t41-screen`）：Read `target/run_results.json` 两次超长。
- 守卫规则：`src/data_agent/command_guard.py:1-10, 51`。

## Acceptance

- [ ] 测试：`DBT_PROMPT` 渲染结果不含 `--project-dir`，含 `cd`；SKILL.md 的 validate 段落写 300；Rules 含 `target/` 规则（现有 `tests/test_dbt_skills.py` 的模式）。
- [ ] `make check` 通过。
- [ ] （主会话）初筛：dbt-fix-daily-cohorts × 3（full），对照 accept-2 full。贴出：是否改过 `profiles.yml`；dbt 命令用的是 `cd` 还是 `--project-dir`；Read `target/*.json` 次数。过线：成功数不低于 accept-2 full 同题，`profiles.yml` 改动 0 次。

## Evidence required

测试命令与输出；渲染后的 `DBT_PROMPT` 全文。

## Stop conditions

- 守卫实际不放行 `cd <project_dir> && dbt`（和工单 40 的记录矛盾）。
- 第二轮审查没过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1（eng:code-review 已修；`da-review` 工单会话 2 次、主会话 1 次都报 Codex "at capacity"，改看 PR 上的 Codex app 审查）
- Model spend: $0（工单会话无模型调用；初筛花费由主会话记）
- Outcome: done，PR #44 squash 合入；初筛 t49-screen 3/3，profiles.yml 0 次改动
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（worktree 49-dbt-prompt-no-profiles-edit）

**改动**
- `src/data_agent/agent.py` `DBT_PROMPT`：教 `cd <project_dir> && dbt ...`，profile 相对路径在项目目录下解析，不要改 `profiles.yml`；不再出现 `--project-dir`/`--profiles-dir`。
- `skills/dbt-workflow/SKILL.md`：validate 段 default 300；Rules 加一条不 Read `target/` 下的 JSON（`run_results.json`、`manifest.json`、`catalog.json`）；文件头注释登记这两处改动（CODING_STANDARDS 对上游复制件的要求，这是工单两处之外的第三处，属于文档义务）。
- `tests/test_dbt_skills.py`：三条验收各一个测试。`tests/test_task_file.py`：固定的提示词副本更新成新措辞。

**真实运行证据**
- 基线 `uv run pytest tests/test_dbt_skills.py tests/test_task_file.py -q` 54 passed；新测试先红后绿，57 passed。
- `make check`：ruff、format 通过；basedpyright 0 errors；`1583 passed, 1 skipped, 38 deselected, 40 xfailed in 429.04s`。
- 守卫核对（`command_guard.denial`，workspace 内有 `proj/`）：`cd proj && dbt run` → None；`cd proj && dbt run --select fct_x 2>&1 | tail -50` → None；单独 `cd proj` → 拒绝。停止条件未触发。
- 渲染后的 `DBT_PROMPT`（server=data，database=DuckDB，workspace=/work/ws，带脚本）：

```
Task type: dbt
Database: DuckDB. dbt writes to it through the project's profile; you read it only through the `data` MCP tools.
Workspace (your working directory; the dbt project is this directory or one inside it): /work/ws
Bash runs only dbt commands and the dbt-workflow scripts. When the project is in a subdirectory, run them as `cd <project_dir> && dbt ...` so that relative paths in the profile resolve from the project directory. Do not edit `profiles.yml`.

<task>
<task text>
</task>

End with a final message that lists the models you created or changed, how the last dbt run went, then the assumptions you made.
```

**审查结果与处理**
- `da-review main`：两次都 "Selected model is at capacity"，没有产出。
- `eng:code-review main`：Spec 轴无缺项、无越权；Standards 轴 1 条硬问题、3 条判断题。
  - 修了（51bd7a0）：SKILL.md 文件头没登记新改动；`assert "60" not in section` 太脆，改成 `"(default 300)" in` / `"default 60" not in`；`"cd " in text` 收紧成完整的 `cd <project_dir> && dbt ...`。
  - 不修：`DBT_PROMPT_BEFORE` 改名（与 `QUERY_PROMPT_BEFORE` 成对，注释已说明）；测试夹具里重复提示词句子（夹具就是为了冻结措辞抓漂移）。
  - 记录：Rules 那条多列了 `catalog.json`，同类文件同一规则。

### 2026-11-05 主会话

- 主会话在 worktree 里再跑 `da-review main`，第三次仍 "at capacity"；改以 PR #44 上的 Codex app 审查代替。
- 初筛 `t49-screen` 开跑：服务器 setup 到 51bd7a0，`ablate --split dev --task dbt-fix-daily-cohorts --out runs/t49-screen --budget 2 --config full --trials 3`。对照 accept-2 full 同题 3/3（trial 7n8t9SV、84NpxAx、9FNRgGg 都 reward 1.0）。
- 2026-11-05 主会话：初筛 `t49-screen`（commit 51bd7a0；`ablate --split dev --config full --task dbt-fix-daily-cohorts --trials 3 --budget 2`）：成功 **3/3**（trial ffjEhFL、joNQgrn、nN5SMyU 都 reward 1.0；accept-2 full 同题 3/3）。过程指标（机械计数，`runs/t49-screen/jobs/full/*/agent/events/`）：`profiles.yml` Edit 0 次；`--project-dir` 0 次；`cd <dir> && dbt` 3、4、3 次；Read `target/*.json` 0 次；"exceeds maximum allowed tokens" 0 次。实际价 $0.3599（4 个事件文件全在高峰）。内存峰值 2.9 GB。过线。
- 2026-11-05 主会话：Codex 不可用（本地 3 次 at capacity，PR #44 的 app 审查 3 次 unknown error），按人 2026-11-05 的决定改用 Fable 5.1 只读审查。结果：无 P1/P2；核实了 `cd proj && dbt run` 过守卫、每次 Bash 都从 workspace 起步、`validate_project.py` 默认 300、新测试查的是正文不是文件头注释、`DBT_PROMPT_BEFORE` 是逐字节比较所以必须更新。三条 P3 都不修，记为后续：① `skills/dbt-workflow/validate_project.py:200` 上游文案 "Fix profiles.yml - dbt cannot find a valid profile" 与提示词的 "Do not edit profiles.yml" 相抵，缺 profile 时 agent 可能二选一；② SKILL.md 写 "default 300" 但脚本把任何值封顶在 300，传 600 会被静默截断；③ `agent.py:135` "run them as cd … && dbt" 的 "them" 也指 dbt-workflow 脚本，脚本是以项目目录为参数的，措辞略含糊。①②③ 交对齐会话定是否开小工单。PR #44 squash 合入。
