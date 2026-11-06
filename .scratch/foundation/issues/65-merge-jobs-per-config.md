# 65 — 评测工具按配置合并多个 job

Status: ready-for-agent
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06 的补充）
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
