# 138 — LiveSQLBench：照官方 baseline 的单次调用对照（spec D2）

Status: in-review
Blocked by: —
Spec: ../spec.md（"Update 2026-11-27"一节，D2）
Claimed by: claude-code (138 worktree session)
Branch: worktree-138-lsb-single-shot-baseline

## Goal

能在服务器上用一条 `da-remote start <name> -- <evals 子命令>` 在开发集上跑"官方 baseline 的做法"：同一个模型 `deepseek-flash`，提示词里直接放 schema、列含义、知识库，单次调用写 SQL，交 `/app/pred.json`，由任务自己的验证脚本（136 已对拍）判分，得出每题 3 次的准确率和花费。用来回答"分数低是模型本身还是 agent 的做法"。

## Authority

- May edit：
  - 新建 `evals/` 下的模块（如 `evals/lsb_single_shot.py`）和 `evals/configs/` 下的新 job 配置；
  - `evals/__main__.py`：只加注册新子命令的几行；
  - `tests/`（除 `tests/acceptance/`）；
  - `NOTICE`：登记复制的上游代码；
  - `docs/setup.md`：写服务器上的运行命令；
  - `scripts/da-remote`：只限 `start` 里决定 source LiveSQLBench 写库口令的那个条件（`scripts/da-remote:578`），加上本工单的新子命令（验证阶段要 `${LSB_PG_WRITE_PASSWORD}`，136 当时也加过 `livesqlbench-parity`）。其余部分不动（`benchmarks` 一段归 139）。
- Must not edit：
  - `src/`、`skills/`；
  - `evals/livesqlbench.py`、`evals/livesqlbench_server.py`（工单 139 改）；
  - `evals/ablate.py`、`evals/report.py`、`evals/spend.py`、`evals/harbor_agent.py`、`evals/manifest.json`；
  - `tests/acceptance/`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access：工单 agent 没有（会话里不能 source 模型的 env 文件）。真实运行由主会话做。
- Spend cap：工单 agent $0。主会话的真实运行算在 spec D1–D4 合计 $15 里。

## Context

1. **上游做法**（`/Users/shenghuikevin/dev/AI/data-agent-refs/livesqlbench/`，根目录 `LICENSE` 是 MIT，"Copyright (c) 2024 bird_sql"）：
   - 提示词：`baseline/src/prompt.py` `assistant_prompt`，资料的读法：`baseline/src/prompt_generator.py:37-98`（列含义的键转小写；知识库每条只留 `id`、`knowledge`、`description`、`definition`，不给 `type`、`children_knowledge`）。
   - 调用参数：`baseline/src/call_api.py:112-116`（temperature 0、max_tokens 6000）。
   - **推理设置照 agent 运行，不照上游**（人 2026-11-27 定的对照目的是"同一个模型"）：agent 不设 effort，Claude Code CLI 发 `high`（`src/data_agent/agent.py:137-145`），事件里有 `thinking`。单次调用用同样的推理设置；max_tokens 要够思考加 SQL，不被思考吃光（6000 不够就调大，写明数值和依据）。和上游的这两处偏差写进 Comments。
   - 从回复里取 SQL：`baseline/src/post_process.py` `parse_sql` / `extract_sql_from_response`。
   - 照抄，不重写；复制的部分保留原版权与许可声明，登记 `NOTICE`（AGENTS.md MUST）。
2. **不给的东西**：`external_knowledge`、`sol_sql`、`test_cases` 都是标准答案（spec U8、`:27`），不进提示词。运行器不读答案文件，也不读任务的 `tests/`。
3. **模型**：和 agent 同一个模型与入口——`deepseek-flash`，`DATA_AGENT_BASE_URL`（默认 `https://api.deepseek.com/anthropic`，`src/data_agent/agent.py:106-107`、`:1243`）、`DATA_AGENT_*` 的密钥变量；`anthropic` SDK 已在依赖里（`pyproject.toml:8`）。价格表和峰谷计费照 `src/data_agent/agent.py:405` 与 `evals/spend.py`。
4. **判分**：任务的验证脚本（separate verifier，128/136）。推荐做法是写成一个 Harbor agent（`harbor/agents/base.py:29` 的 `BaseAgent`，不装进容器，在宿主上调模型；资料从 agent 环境的 `/app/db_assets/` 读，答案写到 `/app/pred.json`——先核实宿主侧 agent 能经环境接口读写这两处），用一个新的 job 配置跑生成好的任务，这样判分、`pred.json` 拷贝、trial 目录结构都和 agent 运行一样。若 Harbor 0.24 的接口让这条路走不通，换别的做法也行，但判分必须走任务自己的验证脚本，在 Comments 写明理由。
5. **产出必须能和 D1 配对**：结果是一个 Harbor job 目录，`python -m evals compare --baseline runs/lsb-dev-3/jobs/no-verifiers --candidate <本工单的 job 目录> --manifest evals/manifest.json --split dev --benchmark livesqlbench --trials 3` 能直接读（`evals/__main__.py:64-76`，`load_jobs` 读 trial 的 `result.json`）。用替身 job 目录配一个测试。
6. **花费与报告**：每个 trial 的 token 和花费要记下来，能和 agent 运行同口径汇总（标价、实扣、峰谷）。先读 `evals/spend.py`、`evals/report.py` 需要的事件文件格式：能写出它们认的最小事件文件就用现有的 `evals spend` / `evals report`；不能的话，新子命令另出花费汇总（标价与实扣），但第 5 项的配对仍必须成立，在 Comments 写明为什么不能复用。不改这两个模块。运行要有实扣上限（spec：D2 实扣 $3），到了就停。
7. **只跑开发集**：新子命令只接受 `--split dev`（或只跑清单 `dev` 里的 `lsb-` 题），给留出题直接拒绝（ADR-0004；spec 本轮不跑留出集）。输出放 `runs/<out>/`，不含标准答案（U7）。
8. **不开 `--network`**（AGENTS.md MUST）。
9. 服务器上的任务目录：`../da-benchmarks/livesqlbench/tasks`（`scripts/da-remote:534`）。

## Acceptance

- [ ] 单元测试（替身模型客户端，不联网、不连库）：
  - 提示词含该库的 schema、列含义、知识库，知识库每条只含 `id`、`knowledge`、`description`、`definition`；
  - 提示词里没有 `external_knowledge`、`sol_sql`、`test_cases` 的任何内容，也没有答案文件路径；
  - 调用参数：模型 `deepseek-flash`、temperature 0、推理设置与 agent 运行相同、max_tokens 为 Context 1 定下的值；
  - 产出的 job 目录能被 `evals compare` 读（Context 5）；
  - 回复里取出的 SQL 按官方格式写成 `{"instance_id", "predicted_sql": [...]}`；取不出时也写出文件（空列表或原样），由验证脚本判 0，不报基础设施错误；
  - 留出题被拒绝；
  - 每个 trial 的 token 与花费被记下。
- [ ] 复制的上游代码保留版权头，`NOTICE` 已登记。
- [ ] `docs/setup.md` 写明服务器上的命令（`da-remote start <name> -- …`，开发集 60 × 3）。
- [ ] 本机用 Harbor 加替身镜像或替身模型跑通一题（$0），贴命令与输出。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出；本机 $0 跑通一题的命令和输出；给主会话的服务器运行顺序（push 后 `da-remote setup <commit>`、`check`、先 1 题、再 60 × 3）。

## Stop conditions

- 判分只能绕开任务的验证脚本才做得到。
- 复用花费汇总必须改 `evals/spend.py` 或 `evals/report.py`，或者产出没法让 `evals compare` 读。
- 需要改 `src/` 或 139 的文件。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2, plus one post-review fix (`b4fddfe`) that Codex has not seen
- Model spend: $0
- Outcome: implemented; the local Harbor and Docker run is blocked by the full disk, and the server's 1-task run closes it
- Human review minutes: —

## Comments

- 2026-11-27（对齐会话建票）：人定 spec D1–D5（"Update 2026-11-27"）。本工单是 D2；和 139 互不依赖，可并行。
- **2026-11-27 (ticket agent):**

  **What was built.**
  - **Command:** `python -m evals lsb-single-shot`, in `evals/lsb_single_shot.py`.
    - It runs one Harbor job, `OUT/jobs/single-shot`, with `evals/configs/livesqlbench.single-shot.yaml`.
    - The job's agent is `evals.lsb_single_shot_agent:SingleShot`, which runs on the host.
  - **What the agent does in each trial:**
    1. Reads the three asset files from `/app/db_assets/` through `environment.download_file`.
    2. Builds upstream's prompt, from `evals/lsb_baseline.py`.
    3. Calls `deepseek-flash` once.
    4. Takes the SQL out of the reply the way upstream does.
    5. Writes `/app/pred.json` through `upload_file`, and keeps a copy and the reply text in `agent/`.
  - **Scoring:** the task's own verifier.
  - **What the agent reads:** the task id comes from the trial's `config.json`, and the database and question are parsed back out of the instruction. A round-trip test against `evals.livesqlbench.instruction` pins that parsing. It never reads `tests/` or the answers.
  - **Spend records:**
    - Each trial writes `agent/events/run-*.jsonl` with a `version` event and a `result` event, in the fields `evals spend` and `evals report` already read; neither module changed.
    - Tokens and cost also go into Harbor's `result.json`.
  - **Dev only.** Held-out tasks are refused in three places:
    - `--split dev` is the only choice;
    - every `--task` must be a LiveSQLBench dev task of the manifest;
    - both the command and the agent run `check_dev_list`, so a custom manifest cannot move a held-out task into `dev`.
  - **Spend cap:**
    - The billed-spend guard stops at 80% of `--budget` before Harbor starts, and a balance check runs first.
    - While the job runs, a watcher stops Harbor at 80% billed, reusing ablate's `stop_reason` (config errors, usage limit, fail-fast).
    - A rerun with the same `--out` resumes the job the way ablate does, but refuses a job whose trials ran at another commit.
  - **Report:** `OUT/reports/<UTC>/report.json`, plus `tasks.json` as the manifest it read.
  - **Upstream copy:** `assistant_prompt`, the asset loading, `parse_sql` and `extract_sql_from_response` are copied under the MIT header and registered in `NOTICE`.
  - **Other edits:**
    - `scripts/da-remote` sources the write credential for `lsb-single-shot` (that condition only).
    - The server command is in `docs/setup.md`.

  **Call settings and their basis.** I captured a request body from a local `data-agent run` against a fake endpoint ($0; `runs/138/requests.jsonl`). With no effort set, the CLI sent:
  ```
  "model": "deepseek-flash", "max_tokens": 32000, "thinking": {"type": "adaptive", "display": "updates"}, "output_config": {"effort": "high"}, "temperature": null
  ```
  The single call sends the same fields, plus `temperature: 0`.

  | Setting | Upstream (`call_api.py`) | This run | Why |
  |---|---|---|---|
  | Thinking / effort | none | adaptive (`display: updates`), effort `high` | the human's 2026-11-27 decision: same model and settings as the agent |
  | `max_tokens` | 6000 | 32000 | what the CLI sends; 6000 would be eaten by the thinking |
  | Temperature | 0 | 0 | as the ticket says; the CLI sends none |
  | No SQL in the reply | `[""]` or the raw text | `predicted_sql: []` | the verifier scores `[]` as 0 (`no_prediction`) without touching the database |

  Whether DeepSeek accepts or ignores `temperature: 0` while thinking is on is **未确认**. If it refuses it with HTTP 400, the first call stops the whole run as a config error. The 1-task run must check this.

  **Error handling.**
  - An HTTP error raises Harbor's own error type for that status.
  - A 400 raises `UnsupportedModelParametersError`, a config error, which stops the run for a human.
  - A dropped connection raises `ApiConnectionClosedError`.
  - The whole call, SDK retries included, has a 1500 s deadline, under the task's 1800 s limit. Past it, the agent raises `ApiResponseStalledError`, which is in the job's retry list and counts as infrastructure. A plain `TimeoutError` would have become Harbor's `AgentTimeoutError` and been scored 0.
  - Each error is raised with the key masked and the SDK's exception chain dropped. No `pred.json` is written on any error.

  **Local run: not done.** The local Docker VM is full: `overlay 30G 28G 0 100% /`, and the stand-in Postgres from ticket 128 fails with `initdb: error: could not create directory "/var/lib/postgresql/data/pg_wal": No space left on device`. I did not delete any images or volumes to make room.
  - **Done instead, at $0:**
    - Unit tests drive the real agent through a fake Harbor, a fake environment and a fake model endpoint; spend, report and compare all run for real on the output.
    - Harbor 0.24 parsed the job config and built the agent from its import path: `built SingleShot lsb-single-shot`, with the retry list as configured.
    - The real command on the real manifest refused a held-out `--task` (`exit 2`, "1 of the --task ids are not LiveSQLBench dev tasks", no id printed) and `--split holdout` (`invalid choice`).
  - **Not checked against a real environment:**
    - reading the assets and writing `/app/pred.json` through the Docker environment;
    - Harbor collecting the artifact and handing it to the separate verifier.

  **Checks.** `make check` passes: 3449 passed and 3 skipped, web 448 passed (`runs/138/make-check-r3.txt`). The baseline before any change was 3432 passed, 3 skipped.

  **Reviews.**
  - **`da-review main` (Codex), 3 findings, all fixed in `a3d112f`, each with a test that fails on the earlier code:**
    - a custom manifest could move a held-out task into `dev`;
    - an API error could leave the key and the endpoint's response in Harbor's trial files;
    - a rerun could silently reuse trials made by other code.
  - **`eng:code-review main`, Standards. Fixed in `0c4f33e`:**
    - the version event lacked `sdk_version`;
    - there were two separate dev-task lists;
    - constants and helpers were redefined instead of reused (`Tokens`, the manifest path, `git_commit`);
    - a private `ablate._log` was imported;
    - one variable was renamed (`own` → `failed_itself`).
  - **Standards findings not fixed:**
    - `ensure_job` duplicates ablate's resume logic. Sharing it would mean editing `evals/ablate.py`, which Authority forbids.
    - The budget travels as both a string and a `Decimal`, because ablate's `guard_spend` takes the string.
  - **`eng:code-review main`, Spec:**
    - the open local-run box, as above;
    - temperature is unverified, as above;
    - attempts the SDK retried internally are not priced. These are failed requests, normally unbilled, under a $3 cap. Not fixed.
  - **After both review rounds, in `b4fddfe`:**
    - the call deadline (from the advisor's check);
    - `python -m evals` no longer loads Harbor at start (it had, through the new module).

    Codex has not reviewed `0c4f33e` or `b4fddfe`.

  **Also worth knowing.**
  - `da-remote pull` of an `lsb-single-shot` run brings back only `reports/` and `run.log`, as for any subcommand except ablate. D4 doesn't read D2's trajectories.
  - One 400 stops the whole run, and the message tells the human to delete the job.

  **Server run order (main session).**
  1. Push, then `da-remote setup <commit>` and `da-remote check`.
  2. Run 1 task × 1 in its own `--out`:
     ```
     da-remote start lsb-ss-1 -- lsb-single-shot --tasks-dir ../da-benchmarks/livesqlbench/tasks --split dev --task <a dev task> --trials 1 --out runs/lsb-ss-1 --budget 0.5
     ```
     It must show:
     - no 400 from temperature 0 with thinking on;
     - `artifacts/app/pred.json` collected;
     - a verifier outcome other than `verifier_error`;
     - a non-zero `cost_usd` in `result.json`;
     - the tasks are from `tasks/`, not 139's `tasks-inline`.
  3. Run 60 × 3:
     ```
     da-remote start lsb-single-shot-3 -- lsb-single-shot --tasks-dir ../da-benchmarks/livesqlbench/tasks --split dev --out runs/lsb-single-shot-3 --budget 3
     ```
  4. Compare with D1 on the server:
     ```
     python -m evals compare --baseline runs/lsb-dev-3/jobs/no-verifiers --candidate runs/lsb-single-shot-3/jobs/single-shot --manifest evals/manifest.json --split dev --benchmark livesqlbench --trials 3
     ```
- 2026-11-27（主会话）：认领字段由主会话补写（工单会话写主 checkout 被拦）。合入 origin/main，NOTICE 冲突两条都留。未完成的验收项「本机跑通一题」改由服务器 1 题运行（`lsb-ss-1`）关闭，D1 跑完后做。
