# 10 — 评测 CLI：抽样、算分、比较、报告

Status: done
Blocked by: 01
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 10-eval-cli-score)
Branch: worktree-10-eval-cli-score

## Goal

一条命令就能：抽开发集（fast-30）与留出集（其余 73 题按难度分层、种子进 git）；按榜单算法算分；对两个配置做按题配对 bootstrap；出报告——留出集只出汇总，开发集出逐题；基础设施报错与 agent 失败分开统计。

## Authority

- May edit: `evals/`、`tests/`（除 `tests/acceptance/`）、`NOTICE`、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

ADR-0004；plan §4 算分与超过基线；CONTEXT 的 Development set、Evaluation holdout、Deterministic pass rate；上游 data-eng-bench `leaderboard/src/leaderboard/core/metrics.py`（复制并登记 `NOTICE`）。

## Acceptance

- [ ] 接缝 C 中抽样、算分、出错记 0、bootstrap、两类报告的验收测试转绿
- [ ] 留出集清单与种子进 git
- [ ] `make check` 通过

## Evidence required

对夹具结果目录跑一次报告命令的输出（留出集部分只有汇总）。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 5 (rework after PR #3 review, four times)
- Review rounds: 2 (da-review 3 fixed; eng:code-review Spec 3 of 4 fixed, Standards 0 hard violations)
- Model spend: $0 (no model calls)
- Outcome: accepted (PR #3, squash 180970a)
- Human review minutes: —

## Comments

**实现**（2026-10-16，claude-opus-5-5）：`evals/`（`python -m evals sample|score|compare|report`）。`evals/metrics.py` 从 data-eng-bench `a3278ad` 复制纯算分函数（逐字一致，已 diff 核对），登记 `NOTICE`。留出集清单 `evals/manifest.json`：seed 20261015，dev = 官方 fast-30，holdout = 其余 73 题按 difficulty 最大余数分配抽 30（medium 15 / hard 12 / very_hard 2 / easy 1），生成命令写在 `evals/__init__.py`，复跑逐字节一致。`pyproject.toml` 只把 `evals` 加进 basedpyright include；`uv.lock` 未变。

**证据**：`make check` → `15 passed, 15 deselected, 33 xfailed`（剩下的 seam C xfail 是工单 11 的 scrub/spend）。对夹具结果目录跑报告：

```
$ uv run python -m evals report tests/fixtures/harbor_job --manifest tests/fixtures/eval_manifest.json --split holdout --out runs/report-evidence/holdout
holdout: accuracy 58.3% ± 14.4% over 12 trials (0 infrastructure errors) -> runs/report-evidence/holdout
$ ls runs/report-evidence/holdout   → 只有 report.json
{"split": "holdout", "accuracy": 58.333333333333336, "accuracy_stderr": 14.433756729740645,
 "pass_at_2": 0.8333, "pass_at_3": 1.0, "n_trials": 12, "n_tasks": 4, "n_succeeded": 7,
 "n_failed": 5, "n_infra_errors": 0, "uncached_input_tokens": 220000,
 "cached_input_tokens": 1100000, "output_tokens": 44000, "cost_usd": 1.375}
$ uv run python -m evals report ... --split dev --out runs/report-evidence/dev
dev: accuracy 44.4% ± 11.1% over 9 trials (0 infrastructure errors) -> runs/report-evidence/dev
$ ls runs/report-evidence/dev   → report.json（含 per_task）、trials.json（逐 trial 结果、花费、事件文件路径）
$ uv run python -m evals score tests/fixtures/harbor_job
accuracy: 52.38095238095238 / accuracy_stderr: 9.523809523809524 / pass_at_2: 0.7143 / pass_at_3: 0.8571 / n_trials: 21
```

**审查处理**：
- da-review（Codex）3 条 P2 全修，各配一个修复前失败的测试（`tests/test_eval_report.py`）：成功但带异常的 trial 被重复计数（n_failed 为负）；token 用量未带出（plan §4 要求）；不存在/空的 job 目录静默记 0 分。
- eng:code-review Spec：(1) 基础设施报错与 agent 失败没分开 → 修：按 Harbor 默认 `RetryConfig.exclude_exceptions` 划分，Harbor 会重试的异常算基础设施（`n_infra_errors`），不重试的（如 AgentTimeoutError）算 agent 失败；两者照榜单都记 0。(2) 缺题时准确率只按在场题算会虚高 → 修：报告要求 split 内每题都有结果，否则报错（只给数量不给题名）。(4) compare 报错会列出未配对题名 → 修：只给数量。(3) bootstrap 按题等权、准确率按 trial 加权 → 不修：每题 k=3 时两者相等，已在 docstring 写明。
- eng:code-review Spec 其他：消融一条命令属工单 12（Blocked by 含 10、11），本工单不做；token/花费汇总不算越界（plan §4"报告给逐题明细和总量"，且 da-review 要求）。
- eng:code-review Standards：0 条硬违规。补了错误路径测试（未配对 compare、未知 dev 题）。不修：成功计数表达式在 compare/report 各出现一次（与复制的上游 metrics.py 同形，抽 helper 收益小）；`_trial_rows` 手列字段（显式列出便于审查留出集不泄露）；测试里的 `evals()` 辅助函数与验收测试重复（验收测试冻结，不能共享）；argparse `p` 变量名为惯用写法。
- 修复已到 AGENTS 第 5 步上限的 2 轮；第 2 轮修复后未再跑 da-review。

**需要人决定**：基础设施/agent 失败的划分直接用了 Harbor 默认不重试清单（`evals/report.py` 的 `NOT_RETRIED`）；若要把 VerifierTimeoutError、ApiUsageLimitError 等另行归类，改这一处即可。

### 2026-10-16 主会话：退回：PR #3 上 GitHub Codex 审查的 3 条意见，已核实

在原分支 `worktree-10-eval-cli-score` 上接着修（分支已 rebase 到 main 49a5e5a，PR #3 未合并）。只修下面 3 条，修完 `make check`、提交（push 由主会话做，PR 自动更新）；不再跑新一轮 da-review / eng:code-review。

1. `evals/report.py:66`：基础设施报错（Harbor 会重试的那类）现在按 0 分进了分数。plan §7："本地消融把基础设施报错和 agent 失败分开统计，前者重跑，不进分数"。改为：split 里有基础设施报错时报告拒绝生成（非零退出，给出条数，不点名留出集题目），提示先重跑。
2. `evals/report.py:115`：只检查了每题至少有一个结果；某题 trial 数少于其他题（Harbor 中断）时报告照样生成，pass@k 静默跳过该题。改为：每题 trial 数必须一致，否则拒绝（同样只给计数）。
3. `evals/metrics.py:16`：复制来的打分函数补全类型注解（`reward` 和 `list`/`dict` 元素类型），在文件头的修改说明里注明。

每条配一个修复前会失败的测试。

### 2026-10-16 返工（claude-opus-5-5）：退回 3 条已修，commit 765589e（未 push）

1. 基础设施报错不再进分数：`write_report` 在 split 内有 Harbor 会重试的异常时拒绝生成（exit 2，只给条数）。`summary` 去掉 `n_infra_errors`、`trials.json` 去掉 `infra_error`（报告能生成时它们恒为 0），CLI 输出相应去掉括号里的计数。测试 `test_report_refuses_infrastructure_errors`；原 `test_outcomes_separate_infrastructure_errors_from_agent_failures` 去掉 infra trial 改名 `test_outcomes_are_disjoint`。
2. 每题 trial 数必须一致，否则拒绝（只给计数）。测试 `test_report_refuses_uneven_trial_counts`。检查顺序：缺题 → trial 数不齐 → 基础设施报错。
3. `evals/metrics.py` 补全注解（`reward: float | None`，`dict[str, list[float | None]]`，返回 `dict[str, float]`，局部 `list[float]`），文件头修改说明已注明。测试 `tests/test_eval_metrics.py`（按路径加载模块，检查参数与返回都有注解且没有裸 list/dict/tuple）。

三个新测试修复前都失败（5 failed），修复后通过。`make check` → `426 passed, 15 deselected, 17 xfailed`；basedpyright 0 errors。

真实运行（夹具 job 的临时副本）：

```
$ uv run python -m evals report tests/fixtures/harbor_job --manifest tests/fixtures/eval_manifest.json --split holdout --out runs/rework-evidence/holdout
holdout: accuracy 58.3% ± 14.4% over 12 trials -> runs/rework-evidence/holdout
$ （副本里把 1 个 holdout trial 改成 ApiInternalServerError）evals report ... --split holdout
error: 1 infrastructure error in the holdout split; rerun those trials first      exit=2，没写 report.json
$ （再删掉 1 个 holdout trial 的 result.json，模拟中断）evals report ... --split holdout
error: 1 of 4 holdout tasks have fewer than 3 trials; rerun the interrupted trials   exit=2
```

按退回要求未跑新一轮 da-review / eng:code-review。

### 2026-10-16 主会话：退回（第 2 次）：PR #3 上 Codex 对 765589e 的 2 条意见，已核实

同一分支接着修，只修这 2 条，各配一个修复前会失败的测试；`make check`、提交（push 由主会话做）；不跑新一轮 da-review / eng:code-review。

1. `evals/__main__.py:35`：`compare` 把 trial 直接化成 reward，丢了 `exception_type`，基础设施报错按 0 分进了比较。和 `report` 一样：任一边有基础设施报错就拒绝（非零退出，只给计数），提示先重跑。
2. `evals/report.py:122`：只查了各题 trial 数一致；每题都只有 1 或 2 次时照样通过，pass@2/3 静默为 0。改为每题 trial 数必须等于规定次数（留出集 3 次，见 ADR-0004 / spec；dev 集同样按 spec 的次数），否则拒绝，只给计数。`compare` 同样适用。

### 2026-10-16 返工 2（claude-opus-5-5）：退回 2 条已修，commit d4ec78f（未 push）

1. `compare` 现在先对两边各跑一次 `check_complete`（从 `write_report` 抽出的同一检查，`evals/report.py`）：任一边有基础设施报错就拒绝（exit 2，只给条数和是哪一边）。测试 `test_compare_refuses_infrastructure_errors`。
2. 每题 trial 数必须恰好等于规定次数，`report` 与 `compare` 都适用。默认 `TRIALS_PER_TASK = 3`（ADR-0004 留出集每题 3 次；spec 没单独给 dev 次数，dev 也按榜单 k=3）；加了 `--trials N`，给 plan §7 的降级路径（留出集 3→2 次）用。测试 `test_report_refuses_fewer_trials_than_required`、`test_compare_refuses_fewer_trials_than_required`、`test_report_trials_per_task_is_configurable`。错误文案改为 "N of M <split|baseline|candidate> tasks do not have exactly K trials"。

这 4 个新测试修复前都失败（4 failed，9 passed），修复后通过。原有单元测试里 trial 数不是 3 的夹具补到 3 次（基础设施报错、trial 数不齐、token 合计、未配对 compare），各测试原本要测的点不变；验收测试未改。`make check` → `430 passed, 15 deselected, 17 xfailed`。

真实运行（夹具 job 的临时副本，`runs/rework2-evidence/`）：

```
$ uv run python -m evals report .../job --manifest tests/fixtures/eval_manifest.json --split holdout --out .../holdout
holdout: accuracy 58.3% ± 14.4% over 12 trials -> runs/rework2-evidence/holdout      exit=0
$ uv run python -m evals compare --baseline .../job --candidate .../job --json
{"diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "significant": false}                   exit=0
# 副本里把 1 个 holdout trial 改成 ApiInternalServerError
$ uv run python -m evals compare --baseline .../job --candidate .../infra --json
error: 1 infrastructure error in the candidate trials; rerun those trials first     exit=2
# 每题删掉 1 个 trial（每题都只剩 2 次，各题一致）
$ uv run python -m evals report .../k2 --manifest ... --split holdout --out ...
error: 4 of 4 holdout tasks do not have exactly 3 trials; rerun the interrupted trials   exit=2
$ uv run python -m evals compare --baseline .../job --candidate .../k2 --json
error: 7 of 7 candidate tasks do not have exactly 3 trials; rerun the interrupted trials exit=2
$ uv run python -m evals report .../k2 --manifest ... --split holdout --trials 2 --out ...
holdout: accuracy 50.0% ± 0.0% over 8 trials -> runs/rework2-evidence/k2-holdout     exit=0
```

按退回要求未跑新一轮 da-review / eng:code-review。

### 2026-10-16 主会话：退回（第 3 次）：PR #3 上 Codex 对 d4ec78f 的 1 条意见，已核实

同一分支接着修，只修这 1 条，配一个修复前会失败的测试；`make check`、提交（push 由主会话做）；不跑新一轮审查。

1. `evals/report.py:24`：`--trials 2` 时仍输出 `pass_at_3: 0.0`。只输出 k ≤ 每题 trial 数的 pass@k（`report` 与 `compare` 都适用）。

### 2026-10-16 返工 3（claude-opus-5-5）：退回 1 条已修，commit 9514961（未 push）

1. `score()`（`evals/report.py`，`report` 与 `score` 命令共用）只给 k ≤ 各题最少 trial 数的 pass@k；`--trials 2` 时不再出 `pass_at_3`。`compare` 的输出本来只有 `diff/ci_low/ci_high/significant`，不含 pass@k，无需改。测试 `test_report_omits_pass_at_k_above_trials_per_task`，修复前失败（1 failed），修复后通过。`make check` → `431 passed, 15 deselected, 17 xfailed`；basedpyright 0 errors。

真实运行（夹具 job 的副本，`runs/rework3-evidence/`；k2 副本每题只拷 2 个 trial）：

```
$ uv run python -m evals report .../k2 --manifest tests/fixtures/eval_manifest.json --split holdout --trials 2 --out .../k2-holdout
holdout: accuracy 50.0% ± 0.0% over 8 trials -> runs/rework3-evidence/k2-holdout      exit=0
report.json: {"split": "holdout", "accuracy": 50.0, "accuracy_stderr": 0.0, "pass_at_2": 0.5, "n_trials": 8, ...}   （无 pass_at_3）
$ uv run python -m evals report .../job --manifest ... --split holdout --out .../holdout
holdout: accuracy 58.3% ± 14.4% over 12 trials                                        exit=0，pass_at_2 0.8333 / pass_at_3 1.0 照旧
$ uv run python -m evals score .../k2 --json
{"accuracy": 50.0, "accuracy_stderr": 7.142857142857142, "pass_at_2": 0.5714, "n_trials": 14}
```

按退回要求未跑新一轮审查。

### 2026-10-16 主会话：退回（第 4 次，最后一轮）：PR #3 上 Codex 对 9514961 的 1 条意见，已核实

同一分支接着修，只修这 1 条，配一个修复前会失败的测试；`make check`、提交（push 由主会话做）；不跑新一轮审查。这是最后一轮，之后的新意见记成已知限制，不再返工。

1. `evals/__main__.py:36`：`compare` 只校验了两边已有的题；两边都缺同一道题时，照样在缩小的题集上给出结论。让 `compare` 和 `report` 一样接受 split（读 manifest），要求两边都完整覆盖该 split 的全部题，否则拒绝（只给计数，不点名留出集题目）。

### 2026-10-16 返工 4（claude-opus-5-5）：退回 1 条已修，commit e30937d（未 push）

1. `compare` 加了 `--manifest FILE --split dev|holdout`（两者必须同时给，否则 argparse 报错）。给了 split 时两边先各过一次 `split_trials`（从 `write_report` 抽出的同一缺题检查，`evals/report.py`）：只留该 split 的题，任一边缺 split 里的题就拒绝（exit 2，只给计数和是哪一边），再走原来的 `check_complete` 与配对检查。split 外的题（如 dev 题混进 holdout job）被滤掉，不再算未配对。测试 `test_compare_refuses_a_split_task_missing_from_both_jobs`、`test_compare_scores_only_the_split`、`test_compare_manifest_and_split_go_together`，修复前都失败（3 failed，14 passed），修复后通过。`make check` → `434 passed, 15 deselected, 17 xfailed`；basedpyright 0 errors。

**已知限制**：`--manifest/--split` 是可选的，因为冻结的 seam C 验收测试按 `compare --baseline --candidate --seed --n-boot --json` 调用、不带 manifest；不给 split 时行为同前（两边都缺同一道题仍会在缩小的题集上出结论）。本地消融应带 `--split`；若要改成必填，需要人改验收接口。

真实运行（夹具 job 的副本，`runs/rework4-evidence/`；gap/gap2 两份副本都删掉了同一道 holdout 题的全部 trial）：

```
$ uv run python -m evals compare --baseline .../job --candidate .../job --manifest tests/fixtures/eval_manifest.json --split holdout
diff: 0.0 / ci_low: 0.0 / ci_high: 0.0 / significant: False                         exit=0
$ uv run python -m evals compare --baseline .../gap --candidate .../gap2 --manifest ... --split holdout --json
error: no trial results for 1 of 4 holdout tasks in the baseline job; every task in the split is needed   exit=2
$ uv run python -m evals compare --baseline .../gap --candidate .../gap2 --manifest ... --split dev --json
{"diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "significant": false}                  exit=0
$ uv run python -m evals compare --baseline .../gap --candidate .../gap2 --json      （不带 split：已知限制）
{"diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "significant": false}                  exit=0
```

`report` 的缺题报错文案由 "in this job; a report needs every task in the split" 改为 "in this job; every task in the split is needed"（与 compare 共用），既有测试只断言 "no trial results"，不受影响。按退回要求未跑新一轮审查。

### 2026-10-16 主会话：GitHub 审查轮数到上限，以下记为已知限制，不再返工

PR #3 上 Codex 对 e30937d 的 2 条意见（已核实，属实）：
1. `evals/report.py:47`：`AgentAuthenticationError`、`ModelNotFoundError`、`UnsupportedModelParametersError` 算作 agent 失败，配置错时会得出看似正常的 0 分。
2. `evals/report.py:166`：`--out` 目录里若留有上次 dev 报告的 `trials.json`，写留出集报告时不会清掉。

两条都由工单 12（消融 runner，调用 report/compare）在调用侧兜住，已写进工单 12。
