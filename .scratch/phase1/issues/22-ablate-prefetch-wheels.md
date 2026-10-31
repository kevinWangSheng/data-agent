# 22 — `ablate` 起 Harbor 前先把 wheel 缓存填好

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 22-ablate-prefetch-wheels)
Branch: worktree-22-ablate-prefetch-wheels

## Goal

`python -m evals ablate` 在启动（或续跑）任何 Harbor job 之前，自动为本次题目的容器平台填好 wheel 缓存（工单 20 的 `evals/wheels.py`），冷缓存不再让最早的并发 trial 撞 360 秒 setup 上限；服务器和 Mac 都不需要手动预热这一步。

## Authority

- May edit: `evals/ablate.py`、`evals/wheels.py`、`tests/`（除 `tests/acceptance/`）、`docs/setup.md`
- Must not edit: `tests/acceptance/` 下的验收测试、`evals/configs/`、`evals/harbor_agent.py` 的安装逻辑（只可调用其已有函数）、`pyproject.toml` 与 `uv.lock`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: `da-remote` 现有子命令（真实运行用）
- Spend cap: $1

## Context

- 工单 20 已知限制：冷缓存 + 慢带宽时最早 1–2 个并发 trial 可能超时；现在靠人先跑 `uv run python -m evals.wheels <image>`（`evals/wheels.py:11-13`，`main()` 在 `:218`）。服务器（x86_64）缓存还是冷的，`da-remote` 没有跑这一步的子命令。
- `ablate` 起 Harbor 在 `evals/ablate.py:234-263`（`ensure_job` → `run_harbor`）。题目镜像：data-eng-bench 题目基于 `ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0`；具体平台以容器探测为准（`PROBE`），不要写死架构。
- 预热失败（无网络、哈希不符、没有该平台 wheel）应让 `ablate` 在花钱之前停下并给出原因，而不是让每个 trial 各自失败。

## Acceptance

- [ ] `ablate` 在第一个 Harbor job 启动/续跑前，按本次 `--tasks-dir` 题目所用镜像的平台填好缓存（同一平台只做一次）；缓存已满时不联网、耗时很短
- [ ] 预热失败时 `ablate` 以非零退出、在任何 Harbor job 前停下，信息说明原因
- [ ] 单元测试：预热在 Harbor 之前被调用、失败时不启动 Harbor、已满缓存不下载（不连网、不调模型、不起 Docker）
- [ ] 真实运行：服务器上经 `da-remote start` 跑一次 `ablate`（开发集 1 题 × `full`+`baseline` × 1，临时 manifest，同工单 21 的 `deb21b` 做法），贴冷缓存预热的输出与用时、两个 trial 的 agent setup 用时
- [ ] `docs/setup.md` 去掉/改写手动预热的说明
- [ ] `make check` 通过

## Evidence required

单元测试输出；服务器真实运行的命令、预热输出与 setup 用时。

## Stop conditions

需要改 Harbor、任务配置或依赖；镜像平台无法在 Harbor 之前确定；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 1
- Model spend: $0
- Outcome: done，PR #23 squash 合并（1 轮退回）
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

工单 20 的后续项。全量运行前的准备（用户：未完成的准备项要做完）。服务器 wheel 缓存是冷的，`da-remote` 无预热入口；与其加手动步骤，不如让 `ablate` 自己预热。

### 2026-10-20 工单 agent：实现（04d2a0e、02ed0a7，主会话抄录）
- make check: ruff All checks passed; 133 files formatted; basedpyright 0 errors; 1162 passed, 1 skipped.
- Unit tests: test_eval_ablate.py (fill runs before every Harbor call, exactly once, and not on a rerun where every job is done; a failed fill — Docker, sha256, no wheel, curl, bad probe JSON, a task with no image — means exit 2 and no Harbor call) and test_eval_wheels.py (task_image, fill once per platform, a full cache makes no download, a failed probe says why). The two files: 54 passed.
- Local check on all 103 data-eng-bench tasks: every task resolves to ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0 → "CPython 3.12, glibc 2.41, aarch64: 34 wheels in ~/.cache/data-agent/wheels, 0.1 s" (warm Mac cache, no downloads).
- Server real run: NOT DONE. da-remote setup fetches from GitHub and ticket agents may not push. Prepared: runs/deb22/manifest.json (holdout = dbt-daily-order-summary), already sent with da-remote push deb22. da-remote status showed no running runs (no mem-*). Main session, after pushing the branch:
    da-remote status        # no other run in progress
    da-remote setup 02ed0a7
    da-remote start deb22 -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest runs/deb22/manifest.json --out runs/deb22 --budget 1 --config full --config baseline --trials 1
  Then record the cold-cache `wheels:` line in run.log (with its time) and each trial's agent setup time (its install.log `host:` line).
- Reviews: da-review (Codex) found no actionable issues. eng:code-review: Spec found nothing missing besides the real run, and no scope creep. Fixed: a KeyError (bad probe JSON or lock) now stops with a reason (02ed0a7); the fill() docstring is clearer.
  Not fixed, with reasons:
  - "Full cache still runs `docker run` and `uv build`": the "no network" acceptance item is about wheels, and those make no request. The probe pulls the base image only if it isn't there, and Harbor needs that image anyway.
  - "The prepare callback is too abstract": ensure_job alone knows whether Harbor will start, so the hook keeps that decision in one place.
  - "build_artifacts+fill appears twice / prefetch_wheels is a Middle Man": two 2-line call sites; prefetch_wheels is also where errors become Stop and lines get printed.
  - "The PREFETCH_WHEELS restore trick in the tests": an autouse stub keeps the ~30 existing tests off Docker; only the tasks_dir tests put the real one back.
  - "Error messages include holdout task paths": these are setup errors, not per-task results (ADR-0004); the success path prints only image and platform.
- Known limitation (outside Authority): harbor_agent.uv_archive still downloads uv's release archive into a temp dir once per Harbor process, so the first trial of each job still pays one GitHub download.

### 2026-10-20 主会话：服务器真实运行与 PR #23

- 主会话 push 分支后：`da-remote setup 02ed0a7`；`da-remote start deb22 -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest runs/deb22/manifest.json --out runs/deb22 --budget 1 --config full --config baseline --trials 1` → exit 0；`wheels: CPython 3.12, glibc 2.41, x86_64: 34 wheels in /root/.cache/data-agent/wheels, 1.1 s`（缓存已由工单 21 的 deb21b 填过，不是冷缓存）；full 100.0（$0.0427）、baseline 100.0（$0.0148）；内存峰值 1620 MB。
- CI 绿（02ed0a7）。Codex P1（`evals/wheels.py:233`）：多阶段 Dockerfile 最后一段 `FROM builder`（前面阶段的别名）时，`task_image()` 把别名当外部镜像，`probe()` 跑 `docker run builder` 失败，`ablate` 在任何 Harbor job 前退出。属实（解析只取最后一个 `FROM`，不解析阶段别名；data-eng-bench 103 题目前都不是这种写法，但这是题目合法写法）。

退回：`task_image()` 解析多阶段 Dockerfile 时把阶段别名还原成其外部 `FROM` 镜像（`FROM x AS a` … `FROM a`），配修复前会失败的测试；其余不变。不需要再跑服务器。

### 2026-10-20 工单 agent：退回修复（fa6569c，主会话抄录）
- 修复：`task_image()` 按顺序读 Dockerfile 的 `FROM`，记下每个 `AS` 阶段名对应的外部镜像（大小写不敏感，同 Docker）；最后一段 `FROM <阶段名>` 时还原成该阶段的外部镜像，可连续多层。镜像含 `$` 仍报错。其余不变。
- 测试：新增 `test_a_last_stage_from_an_earlier_stage_resolves_to_that_stages_image`（`FROM base-image AS Base` → `FROM base as builder` → `FROM python:3.12 AS other` → `FROM BUILDER`），修复前失败（AssertionError），修复后通过；test_eval_wheels.py + test_eval_ablate.py：55 passed。
- make check：ruff All checks passed; 133 files already formatted; basedpyright 0 errors; 1163 passed, 1 skipped.
- 审查：da-review main 跑了两次，都因 Codex "Selected model is at capacity" 中断，没有产出意见；本轮没有 Codex 审查结果，需要主会话补跑 da-review，或靠 PR 上 Codex GitHub app 的审查。
- 没跑服务器（退回说明不需要）。

### 2026-10-21 工单 agent：合并 main（3cd4fb2，主会话抄录）

- 合并 origin/main（ac6b456，工单 24）：用 `git merge`（没有 rebase），合并提交 3cd4fb2。
  - 冲突：只有 `evals/ablate.py` 的导入块冲突。本分支的 `from evals import wheels` 和 main 的 `from evals.harbor import Trial, event_files, load_trials` 两边都保留，合成：
    `from evals import wheels`
    `from evals.harbor import Trial, event_files, load_trials`
    这个文件里不再有本地的 `event_files` 定义，三处调用（ablate.py:227、296、306）都用 `evals.harbor.event_files`。本工单的 `prefetch_wheels` 和它在 main 里的调用（ablate.py:419）没动。其余文件（evals/attribute.py、evals/harbor.py、tests/test_eval_attribute.py）自动合并。
  - make check（日志在 /tmp/t22-merge-check.txt）：ruff check "All checks passed!"；ruff format "133 files already formatted"；basedpyright "0 errors, 0 warnings, 0 notes"；pytest "1164 passed, 1 skipped, 25 deselected in 189.33s"。
  - 提交时 pre-commit（格式、lint、合并冲突标记、密钥扫描等）全部 Passed。没有 push。

PR #23 上 Codex 没有审查 fa6569c（等了 30 分钟无 code_review 记录）；合并提交 push 后重新触发。

### 2026-10-21 主会话：合并 PR #23

CI 绿（3cd4fb2）。重新请求后 Codex `has_any_findings: false` 并 👍（覆盖 fa6569c 的退回修复与合并提交）。已 `gh pr merge 23 --squash`。
