# 12 — 消融运行器

Status: done
Blocked by: 06, 07, 09, 10, 11, 15, 16
Spec: ../spec.md
Claimed by: Claude Code (claude-opus-5-5) 2026-10-16
Branch: worktree-12-ablation-runner

## Goal

一条命令在留出集上跑 6 个配置 × 3 次，经 scrub 与花费护栏，产出消融表（每层贡献与配对 bootstrap 区间）。本地用 1 题 × 6 配置验证；全量在服务器上跑，等 `da-remote`。

## Authority

- May edit: `evals/`、`tests/`（除 `tests/acceptance/`）、`src/data_agent/configs/full-old-skill.toml`、冻结的旧版 Skill 目录（新建，如 `skills-frozen/<版本>/`，内容是当前 `skills/` 的原样副本）、`pyproject.toml`（只限 force-include 该目录）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $3

## Context

ADR-0004（6 配置 × 3 次 = 540 次）；plan §7 降级顺序与护栏。

## Acceptance

- [x] 本地 1 题 × 6 配置跑通并出消融表
- [x] 留出集结果只以汇总形式出现
- [x] 调 `compare` 时传 `--manifest`/`--split`（工单 10 的 compare 不强制，见 10 的 Comments）
- [x] 每次报告用新的空 `--out` 目录；遇到配置类报错（`AgentAuthenticationError`、`ModelNotFoundError`、`UnsupportedModelParametersError`）停下找人，不出报告（工单 10 已知限制）
- [x] 冻结旧版 Skill 到独立目录，`full-old-skill` 配置指向它（工单 05 留的缺口；目前等于 `full`）；该目录加进 `pyproject.toml` 的 force-include
- [x] DAB runner 接上 context 层开关（工单 13 已知限制：DAB server 不注册 context tools，`full` 与 `no-context` 在 DAB 上行为相同）：把开关传给 `evals/dab_server.py` 并注册对应工具，或拒绝在 DAB 上跑 `context = true` 的配置
- [x] DAB 续跑核对加上 `git_commit` 与 `skill_version`（工单 13 已知限制：`evals/dab.py` 的续跑检查只比 config、model、hints、DAB commit）
- [x] DAB 续跑可靠性（工单 13 已知限制）：`submission.json` 先写临时文件再原子替换；运行条件里 DAB commit 带脏工作区标记，脏 checkout 拒绝续跑
- [x] `make check` 通过

## Evidence required

本地小规模运行的命令与消融表。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2（另 PR #17 退回 2 轮）
- Model spend: 约 $0.76（消融 $0.7269 + DAB $0.0212；中止的第 1 次运行有 1 个 trial 没记，估计 < $0.15），在 $3 上限内
- Outcome: done，PR #17 squash 合并（2 轮退回）
- Human review minutes: —

## Comments

### 2026-10-16 主会话：派工前修正 Authority

验收标准里"冻结旧版 Skill、`full-old-skill` 指向它、加进 force-include"是主会话从工单 05 转来的，但 Authority 原来只有 `evals/`、`tests/`，自相矛盾；已把对应的三处加进 May edit。"旧版"取当前 main 上的 `skills/`（工单 14 的失败归因还没改过 Skill）。本工单只做本地 1 题 × 6 配置验证；全量等 `da-remote` 与服务器升配，不在本工单内。

### 2026-10-16 工单 agent：实现与验证（主会话抄录）

改动：新命令 `python -m evals ablate`（`evals/ablate.py`）：每个配置先过花费护栏再跑一个 Harbor job；三类配置错误停下找人、不出报告；未完成或有基础设施错误的 job 续跑；全部完成后 scrub、每个配置报告写进新的空目录，`full` 对每个配置跑 `compare --manifest --split holdout`，写 `ablation.json` / `ablation.md`（只有汇总）。旧 Skill 冻结为 `skills-frozen/fa6028eef62b/`（`skills/` 原样副本，以 Skill 版本哈希命名），`full-old-skill` 指向它并 force-include；`skills-frozen/ruff.toml` 让 ruff 不查上游脚本。DAB：拒绝 context 打开的配置；续跑核对 git commit、Skill 版本，两个 checkout 有未提交改动时拒绝；`submission.json` 临时文件 + 原子替换。

本地真实运行（1 题 × 6 配置）：临时 manifest 的 holdout 只放一道 dev 题（`{"holdout": ["dbt-fix-division-by-zero"]}`），没看真实留出集题目。commit fa23851，colima m1-kind，`deepseek-flash`：

```
runs/12/run-ablate.sh --manifest runs/12/manifest-1.json --out runs/12/ablation-2 --budget 2.5 --trials 1 --n-concurrent 1   → exit 0
| config | accuracy (%) | ± stderr | trials | cost (USD) | input tokens (uncached / cached) | output tokens |
| full           |   0.0 | 0.0 | 1 | 0.1227 | 101,445 / 1,835,648 | 67,725 |
| baseline       | 100.0 | 0.0 | 1 | 0.1031 |  46,119 / 2,256,256 | 63,128 |
| no-skill       |   0.0 | 0.0 | 1 | 0.1375 |  74,636 / 1,990,144 | 85,941 |
| no-verifiers   |   0.0 | 0.0 | 1 | 0.0941 |  49,127 / 2,453,248 | 53,865 |
| no-context     | 100.0 | 0.0 | 1 | 0.1733 | 132,222 / 2,928,896 | 96,707 |
| full-old-skill |   0.0 | 0.0 | 1 | 0.0962 |  88,502 / 1,394,688 | 51,040 |
| all layers | baseline | -100.0 | [-100.0, -100.0] | yes |   (Skill / verifiers / attribution loop: +0.0, no; context: -100.0, yes)
```

1 题 1 次是噪声，只证明流水线能跑。每个 job 前护栏都跑了，6 次后读数 $0.7269；scrub 后 job 里没有 home 路径，报告目录只有 `report.json`、`ablation.json`、`ablation.md`；6 个 job 都记录 commit fa23851、`deepseek-flash` 和预期 Skill 版本；同一 `--out` 再跑不启动 Harbor job、写出新的报告目录（exit 0）。第 1 次运行故意中止（会混 commit，第 1 轮修复后会拒绝），残留的任务容器已删。

DAB 真实运行（stockindex query 1）：`--config full` → exit 2（"turns on the context layer, but DAB's tool server has no context tools"）；`--config no-context --runs 1` 完成，同目录 `--runs 2` 只跑 run1；旧 commit 的 out 目录 → exit 2，点名 DAB commit（当时 `…-dirty`）和我们的 git commit。

审查：
- `da-review` 第 1 轮 3 条全修并配测试：P1 续跑删掉的 trial 丢花费 → 事件文件复制到 `OUT/dropped/`，护栏统计 OUT 下全部；P2 复用旧 job 不核对条件 → 只复用同 commit、同 Skill 版本、干净 checkout 的 job，表要求所有配置同一 commit 与模型；P2 scrub 改写别的配置的未完成 job → 只 scrub 本表已完成的 job。
- `eng:code-review` 第 2 轮已修：DAB 续跑拒绝脏的 data-agent checkout（配测试）；`event_files` helper、`check-attr` 解析注释；DAB 测试固定干净 commit。
- 不修及理由：`--model` 改了要到建表时才发现（记录的模型名无法可靠对上 Harbor 的 `-m`），已知限制；`evals_cli` 故意走 CLI（验收点名 `--manifest`/`--split`）；`skill_version(...) if skill else "none"` 重复 4 处，挪进 `RunConfig` 超出 Authority；"原子写没有测试"不属实（`test_an_interrupted_submission_write_keeps_the_old_file`）；`OUT/logs/` 下 Harbor 与护栏/scrub 日志含留出集题名，与 Harbor 自身 job 目录同等暴露、不打印，接受。
- 两轮修复用完，第 2 轮后没再跑 `da-review`。

已知限制：护栏在 job 之间检查，超支最多一个 job（全量时 90 次运行）；DAB 脏检查忽略 LFS 数据文件（本机没装 git-lfs，`download.sh` 放好数据后 git 永远报这些文件已修改，否则每次续跑都被拒；代价是 DAB 数据库文件被改不会被发现）；DAB 只能跑 `no-context`，`full` 上 DAB 需要后续工单做多库 context tools。

### 2026-10-16 主会话

NOTICE 超出本工单 Authority，主会话在分支上补了一行（cc19f4b：`skills-frozen/fa6028eef62b/` 是 `skills/` 原样副本，沿用同样上游与声明）。

### 2026-10-16 主会话：PR #17 第 1 轮退回

CI 绿（cc19f4b）。Codex 两条，对照代码核实都属实：

- 退回：`evals/ablate.py:167` `check_conditions` 里 `if found[key] and ...`：事件文件缺失/为空或版本头缺 `git_commit`/`skill_version` 时 `versions()` 给空列表，比较被跳过，于是没有版本的 trial 会被复用、或和新续跑的 trial 混在一张表里。缺任何必需版本字段应算条件不符（Stop）。配一个修复前会失败的测试。
- 退回：`evals/dab.py` `dab_commit()`：只因路径带 `filter=lfs` 就当干净，下载后被改过的数据库文件不会被发现，续跑可能混用不同数据。改成：有改动的 LFS 路径要对照 DAB 自带的 `dataset_manifest.tsv`（sha256）校验，不一致或不在清单里就算 dirty（只读清单和文件，不复制 DAB 代码——DAB 无许可证）。配测试。

### 2026-10-16 工单 agent：PR #17 第 1 轮退回修复（531c92e，主会话抄录）

- 已修：`evals/ablate.py` `check_conditions`：要保留的 trial 只要没记版本（无事件文件、事件文件为空、版本头缺 `git_commit` 或 `skill_version`）就停止复用（新增 `has_versions()`，共用 `header()`）。基础设施错误的 trial 例外：续跑本来就丢掉它们，它们可能在 agent 写事件文件前就坏了，拦住会让 job 永远续不上。新测试 `tests/test_eval_ablate.py::test_jobs_whose_trials_lack_versions_are_not_reused`（4 例）修复前 4/4 失败，修复后通过；`test_an_infrastructure_error_without_events_is_still_redone` 覆盖例外。
- 已修：`evals/dab.py` `dab_commit()`：有改动的 LFS 路径必须在 DAB 自带 `dataset_manifest.tsv` 里且 size 与 sha256 一致，否则（不在清单、size 不对、哈希不对、已删除、没有清单）算 dirty。先比 size；每个 (path, size, mtime_ns) 每进程最多算一次 sha256。只读清单和数据文件，没复制 DAB 代码。本工单自己写的 `test_dab_commit_ignores_its_lfs_data_files` 改成 `test_dab_commit_accepts_lfs_data_that_matches_its_manifest`（现在需要清单条目）；新测试 `test_dab_commit_marks_lfs_data_that_differs_from_its_manifest`（4 例：下载后被改 / 同 size 不同字节 / 不在清单 / 已删除）修复前 4/4 失败，修复后通过。
- 对真实 DAB clone（只读；前后 `git status --short` 一致，只有两个 stockindex `.db` 改动）：`dab.dab_commit(...)` → `6ad9d689393ff13b51a6bdcc0b98d1d4d8209f36`（等于 HEAD，无 `-dirty`，0.50s）。
- `make check`：1016 passed，24 deselected；basedpyright 0 errors。这轮修复没再跑两道本地审查（PR 上 Codex 会再审）。

### 2026-10-16 主会话：PR #17 第 2 轮退回（最后一轮）

CI 绿（531c92e）。Codex 一条，核实属实：

- 退回：`evals/ablate.py:221` 附近：`--out` 里 job 都已完成时，换一个 `--model` 重跑会直接复用旧 job（`check_conditions()` 只比 commit 与 Skill 版本），复用的 job 模型彼此一致，跨配置模型检查也过，于是按模型 B 调用却出模型 A 的表。这是第一轮自评里"`--model` 改了要到建表时才发现"的已知限制，实际上建表时也发现不了。不需要把记录的模型名对上 Harbor 的 `-m`：把本次调用请求的模型（`--model` 原值，没给就记"job config 默认"）写进 OUT 下的运行条件文件，再跑时请求不同就 Stop（"use another --out"）。配一个修复前会失败的测试。

这是本 PR 最后一轮退回；之后 Codex 的新意见记成已知限制。

### 2026-10-16 工单 agent：PR #17 第 2 轮退回修复（6da173d，主会话抄录）

- 已修：`evals/ablate.py` 新增 `check_request()`，在任何 job 之前执行：OUT 第一次运行把请求的模型写进 `OUT/request.json`（`--model` 原值；没给记 `"the job config's default"`）；之后请求不同即 Stop（exit 2，"use another --out"），不跑 Harbor、不出表；文件读不了也 Stop。只比请求本身，不和 Harbor `-m` 对名字。
- 测试：`tests/test_eval_ablate.py::test_another_model_on_the_same_out_stops`（默认→`deepseek-pro`、`deepseek-pro`→默认）修复前两例都失败（`assert 0 == 2`），修复后通过；同一请求再跑仍复用 job。
- `make check`：1018 passed，24 deselected；basedpyright 0 errors。本轮未真实跑模型，未跑两道本地审查。
- 已知限制：本修复之前建的 OUT 没有 `request.json`，新代码第一次运行时记下当次请求，无法回溯旧 job 的请求。

### 2026-10-16 主会话：合并 PR #17

CI 绿（6da173d）。Codex 新意见一条，退回轮数已用完，记成已知限制：

- 已知限制（属实，偏安全方向）：`evals/ablate.py:258` 续跑前把将被删的 trial 事件文件复制到 `OUT/dropped/`；若 `harbor job resume` 在删掉原 trial 目录前非零退出，`dropped/` 副本与 `jobs/` 原件都在，下次花费护栏重复计数，可能误触 80% 上限挡住重试（只会多算，不会少算）。遇到时手动删 `OUT/dropped/` 里对应副本或换 `--out`；后续工单可在 resume 失败时回滚副本或按 trial 去重。

已 `gh pr merge 17 --squash`。
