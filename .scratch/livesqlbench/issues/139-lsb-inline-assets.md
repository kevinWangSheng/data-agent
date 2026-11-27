# 139 — LiveSQLBench：可选把 schema、列含义、知识库写进任务说明（spec D3）

Status: done
Blocked by: —
Spec: ../spec.md（"Update 2026-11-27"一节，D3）
Claimed by: claude (139 worktree)
Branch: worktree-139-lsb-inline-assets

## Goal

生成 LiveSQLBench 任务时多一个可选开关：打开后，任务说明里除了题面和文件路径，还直接写入该库的 schema、列含义和知识库（字段和官方 baseline 相同）。在服务器上能用一条命令生成这样一套任务，放在和现有任务不同的目录里，交给 `ablate --tasks-dir` 在开发集上跑 `no-verifiers`。开关默认关闭，现有任务一个字节都不变。

## Authority

- May edit：
  - `evals/livesqlbench.py`：`instruction`、`write_task`、`generate_tasks` 加开关；
  - `evals/livesqlbench_server.py`：`tasks`、`prepare` 步加开关和输出目录；
  - `scripts/da-remote`：只限 `benchmarks` 里 LiveSQLBench 的那一段（`scripts/da-remote:520-532`）和 `check` 里 LiveSQLBench 的检查，用来在服务器上生成这套内联任务（例如一个选项，或另一个输出目录），`check` 不能因为多了这套任务报错；`start` 一段归 138，不动；
  - `tests/`（除 `tests/acceptance/`）；
  - `NOTICE`：照抄上游提示词的段落标题、字段选择时登记；
  - `docs/setup.md`：写生成命令和 `ablate` 命令。
- Must not edit：
  - `evals/livesqlbench_images/`：镜像 tag 是这个目录里文件的摘要（`evals/livesqlbench.py:122-128` `image_version`），动了它 18 个库镜像都要重建、现有任务都要重新生成；只改 `evals/livesqlbench.py` 不影响 tag；
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
6. 主会话之后的运行（写进 `docs/setup.md`）：`ablate --benchmark livesqlbench --tasks-dir <内联任务目录> --manifest evals/manifest.json --split dev --config no-verifiers --reference no-verifiers --trials 3 --out runs/lsb-dev-inline-3 --budget 5`（实扣上限，spec D3）；之后用 `evals compare` 和 D1 配对（spec "判定口径"）。

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

- Attempts: 1
- Review rounds: 1（只按审查意见补了一个测试，代码没有要修的问题）
- Model spend: $0
- Outcome: 已在分支 `worktree-139-lsb-inline-assets` 实现（`9cf1357`、`843a0f1`），待主会话 push、看 CI 和 Codex PR 意见、合并
- Human review minutes: —

## Comments

- 2026-11-27（对齐会话建票）：人定 spec D1–D5（"Update 2026-11-27"）。本工单是 D3；和 138 互不依赖，可并行。
- **2026-11-27（工单 agent）**

  **改了什么。** `generate_tasks` / `write_task` / `instruction` 加了开关 `inline_assets`，默认关。打开时，在"Files describing the database"列表和"Output format"之间插入三段，写法照 `baseline/src/prompt.py` 与 `prompt_generator.py`：
  - 段落标题 `# Database Schema:`、`# Column Meanings:`、`# External Knowledge:`；
  - 列含义的键转小写；
  - 知识库每条只留 `id`、`knowledge`、`description`、`definition`，按名字建 dict；
  - 用 `json.dumps(indent=2)`。

  `evals livesqlbench tasks` 加了 `--inline-assets`。`prepare` 同时写 `ROOT/tasks` 和 `ROOT/tasks-inline`。NOTICE 和 `docs/setup.md`（生成命令、服务器顺序、`ablate` 命令）已更新。

  **偏离工单，请主会话确认：**
  1. `prepare` 没有加开关，总是同时生成两套任务。所以 `da-remote benchmarks` 的 `prepare` 命令一字未变，`tests/test_da_remote.py` 里的原断言照旧通过。spec D3（`spec.md:175`）的"开关默认关"指的是留出集结果、产品默认、data-eng-bench 和 DAB 不受影响，不要求 prepare 可选。现有 `tasks/` 一个字节不变。
  2. 改了 `livesqlbench_server.py` 的 `check()`，它不在这个文件的 Authority 里（只写了 `tasks`、`prepare`）。有 `tasks-inline/` 时，它多报两行：任务数、镜像 tag 过期的任务数；没有这个目录时不报、也不算失败。严格说这不是必须的，旧的 `check()` 本来就不看这个目录。要退回只需删掉这一处循环。
  3. `scripts/da-remote` 改的是 `benchmarks` 最后的 echo（`:534-536`），在给定的 520-532 范围之后一点。那一行列出 `tasks-inline` 目录，`start` 一段没动。

  **第 4 项：长任务说明不会被截断或拒绝（文件:行）。**
  - `evals/harbor_agent.py:426` 把任务说明写成 `--task {shlex.quote(instruction)}`，拼进一条命令。
  - Harbor 在 `agents/installed/base.py:984` 前面加 `set -o pipefail;`，再由 `environments/docker/docker_unix.py:283` 用 `["bash", "-c", command]` 执行，整条命令是一个 argv 参数。Linux 单个参数上限 MAX_ARG_STRLEN 是 131072 字节。
  - 实测 180 道内联任务，用真实的 `DataAgent._run_command` 加上 Harbor 前缀，最长的是 `lsb-polar_3`：任务说明 82753 字节，整条命令 85416 字节，占上限的 65%。
  - `src/data_agent/cli.py:447` 把 `args.task` 原样作为任务文本，没有长度检查。`agent.py:2110` 的 `check_no_credentials` 只查 workspace 里的文件，不看任务文本。
  - 提示词是 `QUERY_PROMPT.format(task=...)`（`agent.py:2295-2302`），经 `ClaudeSDKClient.query` 发出（`agent.py:780`）。SDK 总是用 `--input-format stream-json`（`claude_agent_sdk/_internal/transport/subprocess_cli.py:742`），提示词走 stdin，不走 argv。
  - `TASK_FILE_NOTE` 和 `saved_task` 只在开 verifier 时用（`agent.py:2230`），`no-verifiers` 不涉及。
  - 模型上下文：`agent.py:1276-1281` 设了 1,000,000 tokens 的窗口，367,000 时压缩。约 82 KB 的任务说明大约 25k tokens，余量足够。
  - 不用改 `src/` 或 `harbor_agent.py`。

  **更正工单里的两处：**
  - 内联后最大的库是 polar（三份资料 83097 字节）和 museum（74381），不是 insider 和 archeology。
  - `fake` 的知识库有两条同名，照上游按名字建 dict 后剩 86 条（文件 87 行）。三份文件仍挂在 `/app/db_assets/`。

  **本机生成与加载（$0，公开的 HF 数据）。**
  - 命令：`LSB_PG_WRITE_PASSWORD=… uv run --no-sync python -m evals livesqlbench tasks --public <hf>/livesqlbench_data.jsonl --answers runs/139/empty.jsonl --db-assets <hf> --out runs/139/tasks-inline --inline-assets`
    - 输出：`inline tasks: 180 in runs/139/tasks-inline, 0 with a gold answer`
  - `runs/139/measure_command.py` 输出：`loaded 180 tasks with Task()`，`lsb-polar_3: instruction 82753 bytes, command 85416 bytes`，`max command 85416 bytes of 131072 (65%)`。

  **默认关时逐字节不变。**
  - 用 `origin/main` 的 `evals/livesqlbench.py` 和 `livesqlbench_verifier.py` 生成全部 180 道任务，与本分支默认关的输出 `diff -r`，1980 个文件完全一致（`IDENTICAL-180`）。
  - 夹具上改动前后两次生成也一致。
  - 这两次都是一次性核对，不是提交进仓库的测试。提交的测试把 `instruction.md` 钉成逐字原文，并检查打开开关时只有 `instruction.md` 不同。

  **`make check`（HEAD `843a0f1`）：** ruff `All checks passed!`；`474 files already formatted`；basedpyright `0 errors, 0 warnings, 0 notes`；pytest `3444 passed, 3 skipped, 79 deselected`；web `448 passed`；exit 0。

  **审查结果：**
  - `da-review main`（Codex）：没有要改的问题。它自己验证了默认关的字节一致、与上游各段一致、隐藏字段不进任务说明。
  - `eng:code-review main`，Spec 一路：没有阻塞问题。
    - 它指出 `task.toml`、`environment/`、`tests/` 没有和改动前的代码比过。上面的 1980 文件对比已经覆盖。
    - 它提出的两处范围偏离，就是上面"偏离工单"第 1、2 条。
    - 它确认 `tasks-inline/` 不完整或镜像过期时 `check` 会报 FAIL，这是预期行为。
    - 按它的意见补了一个测试：完整的内联任务不给 `check` 加失败（`843a0f1`）。
  - `eng:code-review main`，Standards 一路：没有硬违规。它提的五条都是判断题，都没改：
    1. 布尔开关一路往下传，在 `write_task` 里才换成路径：开关是工单要求的公开接口，换成路径只是挪个地方。
    2. 标签（"tasks" / "inline tasks"）写在两处：只有两个取值，各在一个函数里。
    3. `check` 循环里按目录名做特殊判断：只有内联这一套是可选的，这样写最直接。
    4. `prepare` 里两次几乎一样的 `generate` 调用：只有两次，循环起来反而更难读。
    5. 变量名 `by_name`：docstring 已经说明了同名去重的规则。

  **给主会话的服务器顺序：** push → `da-remote setup <commit>` → `da-remote benchmarks`（会生成 `tasks-inline/`）→ `da-remote check`（应为 0 failure，多出两行 `LiveSQLBench inline tasks`）→ `da-remote start lsb-dev-inline-3 -- ablate --benchmark livesqlbench --tasks-dir ../da-benchmarks/livesqlbench/tasks-inline --manifest evals/manifest.json --split dev --config no-verifiers --reference no-verifiers --trials 3 --out runs/lsb-dev-inline-3 --budget 5` → 用 `evals compare` 和 D1 配对。
- 2026-11-27（主会话）：接受两处偏离（prepare 总是生成两套；check 多报内联任务两行），都不改现有任务和产品默认。push、开 PR。
- 2026-11-27（主会话）：PR #143 CI 绿，Codex GitHub app 审查无意见（👍），squash 合并为 `be919ec`。
