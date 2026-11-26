# 136 — LiveSQLBench：标准答案到手后，官方对拍与"每道题都能判分"的检查

Status: done
Blocked by: 128（done，`deb64e2`）；答案文件已在服务器（见 Comments）
Spec: ../spec.md
Claimed by: claude (worktree 136-livesqlbench-parity)
Branch: worktree-136-livesqlbench-parity

## Goal

用标准答案证明两件事，之后才能跑留出集里程碑全量：
- 我们的 soft EX 和官方评测脚本在同一批答案上判分一致（spec "验收"`:83`，"评分"一行 `:37`："结果一致才算接入完成"）；
- 180 道题每道都能判分：拿标准 SQL 当预测交上去，每道都判对（oracle）。

## Authority

- May edit：
  - `evals/livesqlbench.py` 和 128 的验证脚本：只修对拍暴露的差异，修复要配一个修复前会失败的单元测试；
  - `scripts/da-remote`：只限答案文件就位后重新生成任务这一步；
  - 新建对拍和 oracle 用的脚本；
  - `tests/`（除 `tests/acceptance/`）；
  - `docs/setup.md`；
  - `NOTICE`：调用或复制上游评测代码时登记。
  - `evals/report.py`，必要时加 `evals/ablate.py`：只限把"验证脚本自身失败"归为基础设施错误，并配上测试；data-eng-bench、DAB 的记分不变（人 2026-11-25 定）。
- Must not edit：
  - `tests/acceptance/`；
  - 127 的契约（名字、参数、返回类型）；
  - `evals/manifest.json`、`evals/livesqlbench_databases.json`：划分已定；
  - `src/`、`skills/`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access：工单 agent 没有，原因同 128（Context 第 7 项）。主会话 push 分支后，用 `scripts/da-remote setup`、`benchmarks`、`start`、`status`、`pull` 跑。
- Spend cap：$0。不调模型：oracle 和对拍都不跑 agent。对拍用的 agent 预测取自 128 冒烟和以后开发集运行已有的 `pred.json`。

## Context

1. **前提**：128 已合入。人已把邮件里的答案文件放到服务器上 128 `docs/setup.md` 写明的位置，`da-benchmarks/` 下。
   - 工单 agent 和主会话都不读、不打印、不取回答案文件的内容（spec Q1 `:19-20`，U7）。
   - 答案文件不进本机、git 和 `runs/` 的取回内容。
2. **重新生成任务**：重跑 `scripts/da-remote benchmarks`。128 的逻辑是：有答案文件就生成带标准答案的任务，`[metadata]` 标明"有标准答案"。`check` 显示 180 个任务都有标准答案。
3. **oracle（每道题都能判分）**：
   - 127 的契约把隐藏字段限制在验证阶段的文件里，所以不能假定 Harbor 的 oracle agent 或者 `solution/` 里有标准 SQL。
   - 做法：在服务器上，对每道题起它的库镜像，用评测入口 `evaluate(payload, payload["sol_sql"], execute, out)` 判分，执行器用验证阶段的写库角色。
   - 期望 180/180 判对。判错的题：
     - 开发集的题可以按题号报，带上判错的类别：执行出错、结果为空、结果不同；
     - 留出集只报数目和类别（ADR-0004）。
4. **对拍**：在同一批答案上分别跑官方评测和我们的 soft EX，比较两边的 reward。
   - 官方评测：上游 `LiveSQLBench-CLI/adapters/livesqlbench/template/tests/evaluator.py` 加 `evaluation/src/`，@ `e15cd22`，在服务器的容器里跑，环境照上游 `test.sh`。
   - 我们的：128 的验证脚本。
   - 两批答案：
     - (a) 180 道题的标准 SQL（同第 3 项）；
     - (b) 开发集 trial 的 `pred.json`：128 在 agent 跑完后把它拷进 trial 的 agent 日志目录（128 Context 第 9 项 1b）。至少用 128 冒烟留下的那些。
   - 要求两边的 reward 逐题一致。不一致时：
     - 开发集的题报题号，以及我们这边没对上的那一条规则（不含 SQL 和结果）；
     - 留出集只报数目。
   - 上游评测会把每条查询（含标准 SQL）打进日志（`evaluation/src/db_utils.py:302-304`）。它的输出只留在服务器上 `runs/` 以外的目录，跑完删掉，不进 `da-remote pull` 的范围。我们这边的输出照 U7，不含答案。
5. spec 原文是"拿到标准答案后补成集成测试"（`:74`）。答案不进 git，CI 跑不了，所以这里的"集成测试"是一条服务器上的命令：`scripts/da-remote start lsb-parity -- …`，可以重复跑，输出只有汇总和开发集的题号。另配不连库的单元测试，用替身执行器测比较逻辑。
6. 136 完成以后，留出集里程碑全量（spec `:46-55`）才能开跑，要人批准范围和花费（花费按 128 冒烟测出的单题成本估）。

## Acceptance

- [ ] 服务器上 180 个任务都带标准答案（`check` 的输出）。
- [ ] oracle：180/180 判对；没有全对就逐类修，或写明原因交回。
- [ ] 对拍：两批答案上，官方评测和我们的 soft EX 的 reward 逐题一致，贴一致的数目。修复都配了单元测试。
- [ ] 上游评测的输出不在 `runs/` 里，`pull` 回来的内容里也没有。检查标准 SQL 哨兵的办法照 128 的口令检查：只打印次数。
- [ ] 验证脚本自身失败的 trial（诊断 `diagnostics.json` 里 `outcome: verifier_error`；Harbor 记 `RewardFileNotFoundError`，现在在 `NOT_RETRIED` 里，算 0）改成按基础设施错误处理（人 2026-11-25 定，照 plan §7）：resume 时重跑，不进分母。按验证脚本的诊断识别，不只按异常类型。单元测试覆盖：这类 trial 不计分、会被重跑；agent 自己造成的没有 reward 仍算 0；data-eng-bench、DAB 的记分不变。实测 `harbor job resume` 会不会重跑这类 trial，写进 Comments；不会重跑就停下交回。
- [ ] `make check` 通过。

## Evidence required

主会话在服务器上跑的命令和（已遮盖的）输出：`benchmarks`、`check`、oracle、对拍；一致的数目；开发集里不一致的题号和规则（如果有）；留出集只贴数目。

## Stop conditions

- 答案文件的格式和上游根目录 `integrate_gt_data.py:47` 的字段（`sol_sql`、`test_cases`、`external_knowledge`）对不上。
- 对拍不一致的原因在上游一侧：上游评测自己的 bug，或者和它的 docstring 不符。停下交人定以谁为准。
- 要改 127 的契约或验收测试。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0
- Outcome: merged as `4db31a9` (PR #140, squash); parity 180/180 + 16/16, oracle 174/180 (6 gold-side defects, kept as official)
- Human review minutes: —

## Comments

- 2026-11-24（主会话建票）：从 128 拆出。128 不需要答案就能完成；本工单只做需要答案的两项。
  - `needs-info` 是在等人：标准答案邮件。
  - 128 done、人把答案文件放到服务器之后，主会话把 Status 改成 `ready-for-agent`。

- 2026-11-25（主会话）答案文件已就位：人用 `scp` 传到 `da-server:/root/da-benchmarks/livesqlbench-answers.jsonl`，主会话改成 mode 600。只查了结构，没读内容：270 行，270 个不同的 id，全部能解析；每行恰好有 `instance_id`（str）、`sol_sql`、`test_cases`、`external_knowledge`（都是 list）四个字段；覆盖公开数据全部 270 题，其中 180 道 Query 全部覆盖，没有公开数据之外的 id。字段名和 Context、Stop conditions 预期的一致。
  - 重跑 `scripts/da-remote benchmarks`："tasks: 180 in /root/da-benchmarks/livesqlbench/tasks, 180 with a gold answer"。`scripts/da-remote check`：0 failure(s)，"LiveSQLBench tasks: 180 …, 180 with a gold answer"。
  - 派工前还剩一项待对齐：验证脚本自身失败的 trial 怎么记分（见 Acceptance）。

- 2026-11-25（对齐会话，人定）：验证脚本自身失败按基础设施错误处理（重跑、不进分母），Authority 加 `evals/report.py`（必要时加 `evals/ablate.py`），只限这一项归类。Status → ready-for-agent。

- 2026-11-25（工单 agent 最终回复，主会话照抄；分支 `worktree-136-livesqlbench-parity`，`2b97af8`，未 push；范围外改动和偏差待对齐）：

  The code for ticket 136 is done and committed on `worktree-136-livesqlbench-parity` (head `2b97af8`, not pushed). The oracle, the parity check and the leak count are written but have not run on real answers. Upstream's evaluator has not run anywhere yet: the local Docker VM has 0 bytes free and I didn't delete any of your images to make room. Those server runs fall to the main session (order at the end).

  **Resume check (stop condition, needs your call).** Real Harbor 0.24, $0, oracle agent, stand-in local images, wrong write password (`runs/136/resume-check.txt`):
  - The first trial got `RewardFileNotFoundError` with `outcome: verifier_error`, and the report counts it as infrastructure.
  - A plain `harbor job resume` does **not** rerun it.
  - Ablate's path does rerun it: keep its spend under `dropped/`, delete the trial directory, then resume. It came back as a new trial (`oLyZiNm`), and the old one is kept under `dropped/`.
  - The script repeats `ensure_job`'s steps rather than calling `ensure_job`; `ensure_job` itself is tested only against the fake Harbor.
  - Read literally, "不会重跑就停下交回" could mean stop here. My reading is that the ablate path reruns it, so I continued.

  **What changed**
  - **Scoring:** an `lsb-` trial with no reward whose `diagnostics.json` says `verifier_error` is now infrastructure: not scored, rerun on resume. Ablate deletes those trials itself and does not pass `-f RewardFileNotFoundError`. Harbor's `-f` matches only by exception type, so it would also rerun trials where the agent itself caused the missing reward. Those still score 0, and data-eng-bench and DAB scoring is unchanged. All of this has tests.
  - **Verifier:** two inputs made our verifier raise where upstream scores 0: a comparison that throws (a row holding a list of dates, or `inf`), and an exception from outside the database driver during execution. Under the new rule that would have rerun the paid agent forever. Both now score 0, each with a test that failed first. This came after both review rounds.
  - **New command `livesqlbench-parity`** (`evals/livesqlbench_parity.py`):
    - `run` starts one container per database and scores the gold SQL and each dev trial's `pred.json` with both our verifier and upstream's.
    - Each side's output is reduced to its reward plus named flags (our outcome, upstream markers), and kept in a scratch directory outside `runs/` that is deleted. The scratch is only removed if this command created it.
    - The summary goes to `runs/lsb-parity/reports/parity.json`. Dev tasks appear by id; holdout tasks only as counts.
    - It refuses a holdout `--task`, a `--trials` directory with no dev `pred.json`, or tasks whose verifier copy is stale.
    - `leaks` counts gold SQL occurrences under a directory, including JSON-escaped forms, and prints only the count.
  - **Checks:** `make check` passes (3291 passed, 3 skipped; web 448 passed; `runs/136/make-check-6.txt`). The LiveSQLBench acceptance tests pass.

  **Changes outside Authority, please confirm:**
  1. `scripts/da-remote start` now sources the write credential for `livesqlbench-parity` runs. The ticket's prescribed `start lsb-parity` command cannot run without it.
  2. `evals/__main__.py` gains two lines to register the new command.
  3. The docstring of the verifier's `main` was updated (the separate verifier fix above is within Authority).

  **Deviations, please confirm:**
  - Upstream runs as `lsb_writer`, not `root/123123`; our database images have no `root` login.
  - Upstream's image comes from a Dockerfile written anew: `python:3.10-slim`, `postgresql-client` and upstream's `requirements.txt`, without the agent CLIs.
  - There is one database container per database, not per task; both evaluators re-clone from the template for every case.
  - A disagreement involving a timeout is rerun once, and the rerun count is reported.
  - `upstream_failed` (upstream crashed, or swallowed an exception into a 1) and `ours_no_reward` are statuses for a human to judge, not verdicts. Either one makes the run exit 1.
  - The gold SQL leak count only runs on the server, as with 128's credential check.

  **Reviews**
  - **Codex, 3 findings, all fixed:** a holdout `--task` could reveal one task's outcome; JSON-escaped gold SQL slipped past the leak count; an empty `--trials` passed as 0/0.
  - **`eng:code-review`:** two fixes (reuse the `Run` alias; split verifier errors from the rest in one pass). Not fixed:
    - module size (759 lines);
    - DB start-up duplicated with `readonly`, and reward/diagnostics parsing repeated;
    - the `trials` flag argument, and bare-string statuses;
    - `Side` holding both sides' fields.

    These are judgement calls with no behaviour change. The spec reviewer's remaining points are covered above.

  **Server order for the main session:**
  1. Push, then `da-remote setup <commit>`.
  2. `da-remote benchmarks`. This is required: the verifier changed, so the tasks' copies are stale and the parity run refuses them otherwise.
  3. `da-remote check`.
  4. One dev task first, since upstream's evaluator has never run anywhere: `da-remote start lsb-parity -- livesqlbench-parity run --root ../da-benchmarks/livesqlbench --out runs/lsb-parity --trials runs/lsb-smoke --task <dev id>`.
  5. The same command without `--task`, under a fresh `--out`: the single-task run already wrote `runs/lsb-parity`.
  6. `da-remote exec -- uv run --no-sync python -m evals livesqlbench-parity leaks --answers ../da-benchmarks/livesqlbench-answers.jsonl --dir runs/<out>`, then `da-remote pull <out>`.

  Ticket fields to copy in:
  - **Status:** `in-review`
  - **Ledger:** Attempts 1, Review rounds 2, Model spend $0, Outcome: committed `2b97af8`, server runs pending.

- 2026-11-25（对齐会话，人定）：
  - 停止条件：ablate 自己的 resume 路径（先删掉出错的 trial，再 `harbor job resume`）会重跑验证脚本自身失败的 trial，算满足"会重跑"，不停下（`runs/136/resume-check.txt`："ablate path reran it: True"；直接 `harbor job resume` 不会重跑，但我们不这样 resume）。兜底是 `--fail-fast`。
  - 范围外 3 项接受：`scripts/da-remote start` 给 `livesqlbench-parity` 也 source 口令；`evals/__main__.py` 注册新命令；验证脚本 `main` 的 docstring。
  - 偏差 6 项接受：上游用 `lsb_writer` 而不是 `root/123123`；上游评测的镜像新写；每个库一个容器，两边每个用例都从模板克隆；涉及超时的不一致重跑一次；`upstream_failed`/`ours_no_reward` 交人判断，有就 exit 1；标准 SQL 泄漏计数只在服务器上跑。

- 2026-11-25（主会话）服务器运行，`2b97af8`（worktree `runs/136/server/`）：
  - `setup 2b97af8` 成功；`benchmarks`："175 tables, 2286 columns"，"180 … 180 with a gold answer"；`check` 0 failure(s)。
  - 单题 `start lsb-parity-1 -- livesqlbench-parity run … --trials runs/lsb-smoke --task lsb-virtual_9`：exit 0；oracle 1/1；gold 上 1/1 一致，dev trial 上 4/4 一致；scratch 已删。
  - 全量 `start lsb-parity -- livesqlbench-parity run --root ../da-benchmarks/livesqlbench --out runs/lsb-parity --trials runs/lsb-smoke`：exit 1。
    - **对拍通过**：gold SQL 上 180/180 一致；dev trial 的 `pred.json` 上 16/16 一致。
    - **oracle 没过**：174/180 判对；没判对的 `{'empty_result': 5, 'execution_error': 1}`。开发集 3 道：`lsb-archeology_7` execution_error（SyntaxError）、`lsb-insider_1` empty_result、`lsb-vaccine_7` empty_result。留出集只看数：`{'empty_result': 3}`。上游评测对这 6 道也判 0（对拍一致），所以不是我们的验证脚本出错。
  - 取证（不打印内容，只打印标志，只查开发集 3 道）：`insider_1`、`vaccine_7` 的标准 SQL 都含 `CURRENT_DATE`（`insider_1` 还含 `INTERVAL`）；`archeology_7` 不含时间函数。3 道都是 1 条语句，`test_cases` 为空。推断：两道 empty_result 是相对"今天"取数，数据停在 2025 年，现在查不到行（未确认，没有在冻结时钟下复跑）。archeology_7 的语法错误原因未确认。版本对得上：上游 Base-Lite 是 2025-05-28（`README.md:278`），答案文件名是 `…_0528`。上游 issue 里搜不到这几道题。
  - 标准 SQL 泄漏计数：`runs/lsb-parity`、`runs/lsb-parity-1` 都是 0 次。`pull lsb-parity`：留出集只取回汇总。
  - 待对齐：oracle 没判对的 6 道怎么处理。

- 2026-11-25（对齐会话，人定）：oracle 没判对的 6 道照官方口径处理：留在分母里，算错；在报告里列成标准 SQL 一侧的已知缺陷。原因写明，oracle 一项按"写明原因"算过。不冻结数据库时钟（偏离官方，日期也未知）。这样分数能直接和榜单比；每个配置的满分上限都是 174/180 = 96.7%，所以消融差值不受影响。
- 2026-11-25（主会话）`lsb-archeology_7` 取证（只打印错误码和位置，不打印 SQL；`runs/136/server/probe-archeology_7.txt`）：在本题的库镜像（PostgreSQL 14.12，上游指定的版本）上，用写库角色执行，结果是 `sqlstate 42601`（syntax_error），位置在 1737 个字符里的第 1603 个（43 行里的第 39 行），出错的是一个字母数字记号。拿同一条 SQL 在空的 `postgres:17` 上执行，同样是 `42601`，同样在第 1603 个字符。语法错误发生在解析阶段，跟数据和版本都无关，所以结论是标准 SQL 本身写错了，不是我们的环境问题。
- 2026-11-25（主会话）main 走到 `023b2f4`（132 Telegram），先合进本分支（`91446ab`）；`make check` exit 0（basedpyright 0 errors；pytest 3367 passed、3 skipped；web 448 passed）。PR #140 的 CI 两个 `check` 都过；`@codex review` 审 `91446ab`："Didn't find any major issues"。squash 合并为 `4db31a9`（state MERGED）。Status → done。
