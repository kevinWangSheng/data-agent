# 139 — LiveSQLBench：可选把 schema、列含义、知识库写进任务说明（spec D3）

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md（"Update 2026-11-27"一节，D3）
Claimed by: —
Branch: —

## Goal

生成 LiveSQLBench 任务时多一个可选开关：打开后，任务说明里除了题面和文件路径，还直接写入该库的 schema、列含义和知识库（字段和官方 baseline 相同）。在服务器上能用一条命令生成这样一套任务，放在和现有任务不同的目录里，交给 `ablate --tasks-dir` 在开发集上跑 `no-verifiers`。开关默认关闭，现有任务一个字节都不变。

## Authority

- May edit：
  - `evals/livesqlbench.py`：`instruction`、`write_task`、`generate_tasks` 加开关；
  - `evals/livesqlbench_server.py`：`tasks`、`prepare` 步加开关和输出目录；
  - `scripts/da-remote`：只限在服务器上生成这套内联任务（例如 `benchmarks` 的一个选项，或另一个输出目录），`check` 不能因为多了这套任务报错；
  - `tests/`（除 `tests/acceptance/`）；
  - `NOTICE`：照抄上游提示词的段落标题、字段选择时登记；
  - `docs/setup.md`：写生成命令和 `ablate` 命令。
- Must not edit：
  - `src/`、`skills/`、`evals/ablate.py`、`evals/report.py`、`evals/harbor_agent.py`、`evals/manifest.json`、`evals/livesqlbench_databases.json`、`evals/livesqlbench_verifier.py`；
  - 工单 138 新建的文件；
  - `tests/acceptance/`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access：工单 agent 没有。生成任务和真实运行由主会话做。
- Spend cap：工单 agent $0。主会话的真实运行算在 spec D1–D4 合计 $15 里。

## Context

1. **现在的任务说明**：`evals/livesqlbench.py:169-192` `instruction`，只给题面、库名、三份资料的路径和输出格式；资料以文件挂在 `/app/db_assets/`。
2. **官方 baseline 的内联方式**（`/Users/shenghuikevin/dev/AI/data-agent-refs/livesqlbench/baseline/src/`，根目录 MIT，"Copyright (c) 2024 bird_sql"）：
   - `prompt.py` `assistant_prompt`：段落 `# Database Schema:`、`# Column Meanings:`、`# External Knowledge:`，列含义用 `json.dumps(..., indent=2)`；知识库每条只留 `id`、`knowledge`、`description`、`definition`。
   - `prompt_generator.py:37-98`：列含义的键转小写。
   - 照它的内容和字段选择；内联后仍保留文件路径和现在的输出格式说明。
3. **不给的东西**：`external_knowledge`、`sol_sql`、`test_cases`（spec U7、U8、`:27`）。知识库文件本身是公开资料，不是 `external_knowledge` 字段。
4. **大小**：最大的库三份资料合计约 75 KB（insider、archeology；主会话 2026-11-27 量的本地生成任务）。先在代码里核实这么长的任务说明从 `evals/harbor_agent.py` 到 agent 的提示词一路不会被截断或拒绝（`src/data_agent/agent.py` 的 `TASK_FILE_NOTE` 等），在 Comments 写明依据。要改 `src/` 或 `harbor_agent.py` 才行就停下。
5. **任务目录**：服务器上现有任务在 `../da-benchmarks/livesqlbench/tasks`（`scripts/da-remote:534`），生成要写库口令（`prepare` 在 `scripts/da-remote:526-528` source 它）。内联任务用另一个目录（如 `tasks-inline`），题号不变，所以 `evals/manifest.json` 的 `dev`/`holdout` 判断照旧有效。验证脚本与 `task_payload.json` 和现有任务相同。
6. 主会话之后的运行（写进 `docs/setup.md`）：`ablate --benchmark livesqlbench --tasks-dir <内联任务目录> --manifest evals/manifest.json --split dev --config no-verifiers --reference no-verifiers --trials 3 --out runs/lsb-dev-inline-3 --budget <余额内>`。

## Acceptance

- [ ] 单元测试（只用公开字段的小样例，不连库、不调模型）：
  - 开关关闭时，生成的任务（instruction、`task.toml`、`environment/`、`tests/`）和现在逐字节相同；
  - 开关打开时，任务说明含该库的 schema、列含义（键小写）、知识库，知识库每条只含 `id`、`knowledge`、`description`、`definition`，并保留文件路径与输出格式说明；
  - 两种情况下任务说明都不含 `external_knowledge`、`sol_sql`、`test_cases` 的内容和答案文件路径；
  - 开关打开时题号、`[metadata]`、验证脚本与关闭时相同。
- [ ] 第 4 项的核实写进 Comments（文件:行）。
- [ ] `scripts/da-remote check` 在多出这套任务后仍是 0 failure（本机能测的部分配测试）。
- [ ] `docs/setup.md` 写明生成命令和 `ablate` 命令。
- [ ] 本机用小样例生成一套内联任务，Harbor `Task()` 能加载，贴命令与输出（$0）。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出；本机生成与加载的命令和输出；第 4 项的依据；给主会话的服务器顺序（push 后 `da-remote setup <commit>`、生成内联任务、`check`、再 `ablate`）。

## Stop conditions

- 长任务说明要改 `src/` 或 `evals/harbor_agent.py` 才能完整送到 agent。
- 默认关闭时做不到现有任务逐字节不变。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（对齐会话建票）：人定 spec D1–D5（"Update 2026-11-27"）。本工单是 D3；和 138 互不依赖，可并行。
