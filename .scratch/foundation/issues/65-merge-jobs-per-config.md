# 65 — 评测工具按配置合并多个 job

Status: done
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06 的补充）
Claimed by: claude-opus-5-5 (worktree 65-merge-jobs-per-config)
Branch: worktree-65-merge-jobs-per-config

## Goal

统一评估分两段跑（第 1 段 70 个 trial、第 2 段 200 个），每段在各自的 ablate OUT 里各成若干个 Harbor job。评测命令要能把同一配置的多个 job 合在一起，当成每题 3 次来算分、比较、看分歧，同时拒绝把不同条件下跑的 trial 混在一起。

## 已和用户定下的（2026-11-06，grilling Q3）

- `evals report`、`evals compare`、`evals diverge` 允许一个配置给多个 job 目录，把 trial 合在一起算；`evals accept` 的 `--old`、`--new` 也一样。
- 合并的前提，任何一条不满足就拒绝，报错只给计数、不点留出集的题名：
  - 所有 trial 记录的 git 提交和 Skill 版本相同（事件文件的 `version` 事件）；
  - 各 job 所在 OUT 的最后一份运行记录（`OUT/records/*.json`）里，benchmark 提交、机器、模型相同；
  - 合并后每题的总次数仍按 `check_complete` 检查（默认 3）。
- 只给一个 job 时，行为和现在完全一样。
- ablate 本身不改：分段由编排会话用不同的 `--out` 跑。

## Authority

- May edit: `evals/harbor.py`、`evals/__main__.py`（report、compare 的参数）、`evals/report.py`、`evals/accept.py`、`evals/diverge.py`、对应的 `tests/test_eval_*.py`、新的测试文件
- Must not edit: `evals/ablate.py`、`evals/compare.py` 的算法（`paired_bootstrap`、`percentile_interval`）、`tests/acceptance/`、`src/`、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- `evals/ablate.py:349-368`：`OUT/request.json` 锁定一份请求，换任务、换次数要换 `--out`，所以两段的结果在不同的 OUT 下。第 1 段至少三个 OUT：full 目标题（5 题，`--trials 3`）、full 其余（25 题，`--trials 1`）、baseline（30 题，`--trials 1`）；第 2 段同理。
- `evals/harbor.py:34` 的 `load_trials(job_dir)` 只读一个 job；`evals/__main__.py:34-52` 的 compare、report 各取一个路径；`accept.py:53-54,103` 和 `diverge.py:37-38,63` 同样。
- 运行记录的字段在 `ablate.py:510-521`（`benchmark_commit`、`machine`、`git_commit`、`model`）；比较方式照 `check_record`（`ablate.py:525-561`）。
- 每个 trial 的提交和 Skill 版本：事件文件里的 `version` 事件（`src/data_agent/agent.py:1033-1042`：`git_commit`、`skill_version`、`config`、`model`、SDK 与 CLI 版本）。`evals/accept.py` 已有"两边版本头一致才复用旧结果"的比较（模块说明 :13 起），照它做。
- 同一 job 不应被给两次；同一 trial 名出现两次要拒绝，免得重复计数。

## Acceptance

- [ ] 新测试：同一配置两个 job（一个每题 1 次、一个每题 2 次）合并后 report 通过，结果等于把 trial 放进一个 job 的结果。
- [ ] 新测试：两个 job 的 git 提交或 Skill 版本不同，拒绝；运行记录的 benchmark 提交、机器或模型不同，拒绝。
- [ ] 新测试：合并后有题不是 3 次，拒绝（报错只给计数）。
- [ ] 新测试：同一个 job 给两次，或 trial 名重复，拒绝。
- [ ] 新测试：compare、diverge、accept 各接受多个 job，结果和单 job 等价情形一致。
- [ ] 只给一个 job 时，现有测试全部通过、输出不变。
- [ ] `make check` 通过。

## Evidence required

测试输出；用本地已有的两个开发集 job（同一提交的，例如 `runs/t47-screen3` 下的 job 拆成两份目录副本）跑一次合并后的 `evals report --split dev --trials <n>`，贴命令和输出。

## Stop conditions

- 事件文件里读不到可靠的提交或 Skill 版本，分不出两段是否同一版本。
- 必须改 `evals/ablate.py` 或 `evals/compare.py` 的算法。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: done (PR #60 → 7afc7c6)
- Human review minutes: —

## Comments

- 2026-11-06 ticket agent (claude-opus-5-5), branch `worktree-65-merge-jobs-per-config`: 0178182 (merge several jobs of one config), 9c910a8 (review fixes)
  - Implementation: `load_jobs` in `evals/report.py`. With one job it returns `load_trials` unchanged. With several jobs it refuses any of these: a job given twice or a repeated trial name; trials that ran (infrastructure errors excluded) with different git_commit / skill_version / config; last run records that differ on benchmark_commit / machine / model; an unset or empty value; a `-dirty` benchmark commit (the same rules as `check_record`). Errors show counts only. `report`, `compare`, `diverge` and `accept --old/--new` each take one or more jobs. accept checks for dropped infra errors in every old job, and `last_record` moved from `accept.py` to `report.py`.
  - Beyond the ticket text: the version check also requires the same config, so a baseline job cannot be merged into full.
  - Tests: `tests/test_eval_merge.py` (20 new). Full suite 1872 passed, 1 skipped (on 0178182). After 9c910a8 (help text and one rename): lint, format and types clean; the 4 affected test files gave 105 passed.
  - Real run: the 9 trials in `runs/t47-screen3/jobs/full`, split 1 + 2 per task into `runs/t65-merge/{a,b}`:
    `uv run python -m evals report runs/t65-merge/a/jobs/full runs/t65-merge/b/jobs/full --manifest runs/t65-merge/manifest.json --split dev --trials 3 --out runs/t65-merge/report-merged` → `dev: accuracy 66.7% ± 0.0% over 9 trials`, and `report.json` is byte-identical (cmp) to the single-job report. b alone → `error: 3 of 3 dev tasks do not have exactly 3 trials` (exit 2); a given twice → `error: 1 job directory(ies) are given more than once` (exit 2).
  - `da-review main`: no correctness defects.
  - `eng:code-review main`, Spec: no gaps, within Authority, one-job behaviour unchanged.
  - `eng:code-review main`, Standards: fixed in 9c910a8: a shared `MERGE_HELP`; `label` → `side_label`.
  - Not fixing:
    - `harbor.version_header` duplicates `ablate.header`: ablate is outside Authority, and report cannot import ablate.
    - `accept.RECORD_KEYS` overlaps `MERGE_RECORD_KEYS`: accept leaves out model on purpose.
    - The `-dirty` special case stays, because it is `check_record`'s rule.
    - accept passes only the first job's record, which `load_jobs` has already checked for agreement.
    - `report.py` grows about 115 lines.
  - Known limitation: `diverge` does not check exactly-k trials per task (unchanged for one job). Stage 1/2 scoring uses `report`, which does check it.

- 2026-11-06 main session: PR #60 CI green; Codex GitHub app on 9c910a8: no findings. Squash-merged to main as 7afc7c6.
