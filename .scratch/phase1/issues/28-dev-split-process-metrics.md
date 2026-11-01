# 28 — 开发集运行：逐题配对分歧与过程指标，并能把轨迹取回本机

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 28-dev-split-process-metrics)
Branch: worktree-28-dev-split-process-metrics

## Goal

在开发集上跑和看结果不再借"临时 manifest 把开发集题放进 holdout"的办法：`ablate` 能直接跑开发集（可只跑其中几题），开发集报告给出两个配置逐题的配对分歧和每题的过程指标；只含开发集题的运行可以用 `da-remote pull` 把轨迹取回本机，供人读。留出集的保密不变。

## Authority

- May edit: `evals/`（`ablate`、`report`、`compare` 或新模块）、`scripts/da-remote`（`pull` 的判断）、`docs/setup.md`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（接缝 C 保证留出集只出汇总）、`evals/scrub.py`（工单 26）、`src/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（服务器上的取回实测由主会话在合并后做）
- Spend cap: $2

## Context

- ADR-0008"先读轨迹"要求人读开发集上的配对分歧题；ADR-0004 只禁止看留出集的逐题结果。
- 现状：`evals/ablate.py` 固定 `SPLIT = "holdout"`，只跑 manifest 的 holdout 列表；dev-1/2/3、smoke17、deb21b、deb22 都用临时 manifest 把开发集题放进 holdout。`da-remote pull` 对 `ablate` 运行一律只取汇总（`scripts/da-remote` 约第 593–612 行），所以 dev-3 的轨迹取不回来。
- 取回放宽的条件必须在服务器上按 `evals/manifest.json`（真 manifest，不是运行自带的）核对：运行里每个 trial 的题都在 dev 列表里，才整份取回；任何一题不在、或判断不了，照旧只取汇总。dev-3 的 jobs 全是开发集题，修好后应能取回。
- 过程指标从事件文件读（事件类型见 `src/data_agent/events.py`、`agent.py`）：用了多少轮、是否被花费上限或轮数上限截停（SDK `ResultMessage` 的结束原因）、验证者是否被调用及其结论、dbt 模型被改了几次、花费；再加每道成功题的平均花费。
- 配对分歧：两个 job（如 `full` 与 `baseline`）在每道开发集题上的成功次数，列出不相等的题。dev-3 现有数据：不相等的 9 题里 baseline 赢 8 题、full 赢 1 题。

## Acceptance

- [ ] `ablate --split dev` 跑真 manifest 的开发集，可以用参数只跑其中几题；holdout 的行为不变；有测试
- [ ] 开发集报告：每题的过程指标，以及两个 job 的逐题配对分歧表；对 holdout 拒绝输出逐题内容；有测试
- [ ] `da-remote pull`：只含开发集题的运行整份取回，含任何非开发集题或无法判断的照旧只取汇总；两种情况都有测试
- [ ] 接缝 C 的验收测试全部保持通过
- [ ] 真实运行：本地 `runs/dev-2/out/jobs/full` 对 `runs/dev-2/out/jobs/baseline` 出配对分歧与过程指标，贴输出；再在 Mac colima 上 `ablate --split dev` 跑 1 题 k=1
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令与输出。

## Stop conditions

做到某一步必须改接缝 C 的验收测试；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 4
- Review rounds: 8
- Model spend: $0
- Outcome: merged (PR #29); server real run and dev-only pull verified
- Human review minutes: —

## Comments

- 2026-10-31 工单 agent（分支 worktree-28-dev-split-process-metrics）：
  - 真实运行 1（c8594f5）：`uv run python -m evals diverge --baseline <main>/runs/dev-2/out/jobs/baseline --candidate <main>/runs/dev-2/out/jobs/full --manifest evals/manifest.json --out runs/28-dev2-diverge` → exit 0："30 paired tasks; successes differ on 6: baseline better on 4, full better on 2."。full 赢：dbt-customer-cross-sell-insights、pos-operations；baseline 赢：dbt-fix-daily-cohorts、dbt-multi-warehouse-stock-rebalance、web-session-quality-scoring、workforce-analytics（dev-2 k=1）。成功 trial 平均花费 baseline 0.0721 USD、full 0.1590 USD；没有 trial 被预算或轮数截停。示例行（full）：pos-operations 1/1，207 轮，4 次验证者调用（FAIL 8、N/A 83、NEEDS_INFO 22、PASS 228），43 次模型编辑，$0.6716。
  - 真实运行 2（参数检查）：`ablate --split dev --task dbt-coupon-effectiveness` → "not in the manifest's dev list"，exit 2；`ablate --task dbt-fix-division-by-zero`（holdout）→ "--task needs --split dev; the holdout is run whole"，exit 2。
  - 未做：Mac colima `ablate --split dev` 1 题 k=1（agent 不能加载 env）。
  - make check：1205 passed, 1 skipped（shellcheck）；接缝 C 验收 8/8 通过，未改。
  - da-review 第 1 轮，4 条全修（158cece）：P1 dev-only 取回漏判没有 result.json 的 trial 目录 → 每个 trial 目录按 result.json 或 Harbor trial 名（task[:32].rstrip('-_')）判断；P2 trial 数不等 / 有基础设施报错 → diverge 拒绝；P2 多个事件文件被相加 → 只取最后一个；P2 report trials.json 的 events 路径指向不存在的 agent/events.jsonl → 改为最后一个 agent/events/*.jsonl（report.py 原有 bug）。
  - eng:code-review 第 2 轮，已修（c8594f5）：只跑 --config full 的 dev 运行没有过程指标 → dev report.json 加 `process`（共用 evals/process.py）；manifest 的 dev 与 holdout 有重叠时 diverge 拒绝；若干改名。不修：da-remote 里的内联 Python 保留（在服务器的 checkout 上跑，不能 import 本提交的 evals）；attribute.py 自己的"最后一个事件文件"查找不在本工单；split 仍用字符串；经 Bash 改的模型（sed -i、heredoc）不计入编辑次数，已在 evals/process.py 写明；holdout 题与 dev 题共享 32 字符前缀的风险低（config.json 的 task_names 须全是 dev 题）。
- 2026-10-31 主会话：PR #29 CI 绿。退回：Codex GitHub app P2（evals/process.py:44）属实——`trial_metrics` 对每行直接 `json.loads`（evals/process.py:40），事件文件末尾有写了一半的行时会抛 JSONDecodeError，`report --split dev` / `diverge` 失败；evals/harbor_agent.py:137 对截断行是跳过的。要求：用同样的容错方式跳过截断行；配修复前会失败的测试。
- 2026-10-31 用户决定：本工单的 colima 真实运行改在服务器上用 da-remote 跑（本机代理拉不下 ghcr 镜像），由主会话在修复后做。
- 2026-10-31 工单 agent（退回第 1 轮，fa7ba14、90c1ad8）：
  - 修复：`trial_metrics` 对坏 JSON 行 `continue`（同 harbor_agent.parse_events）；事件文件用 `encoding="utf-8", errors="replace"` 打开，截断落在多字节字符中间也被跳过。
  - 测试：`test_a_line_cut_short_by_a_killed_run_is_skipped`（修复前 `diverge: Unterminated string starting at: line 1 column 21`）、`test_a_line_cut_inside_a_multibyte_character_is_skipped`（修复前 AssertionError）。
  - 真实运行：dev-2 的 full/baseline 副本，在 pos-operations 最后一个事件文件末尾追加 `{"seq": 999999, "type": "tool_st`；修复前 `diverge: Unterminated string starting at: line 1 column 25 (char 24)` exit 2；修复后 exit 0，"30 paired tasks; successes differ on 6: baseline better on 4, full better on 2."，pos-operations 指标与未截断时一致。
  - make check：1207 passed, 1 skipped；接缝 C 未改。
  - da-review（fa7ba14）无问题。eng:code-review：不修与 parse_events 的 4 行重复（导入会把 harbor/pydantic 带进指标路径）；不修非 dict 的 JSON 行。
  - da-review（90c1ad8）两条 P1 针对早先 da-remote pull 代码，核实后不修（主会话同意）：本地旧 jobs/logs/dropped 残留——holdout pull 只取汇总且本地已有这些目录时拒绝（scripts/da-remote 约 653–659 行），不产生新泄露；32 字符前缀歧义——task_names 须全是 dev 题，按当前 manifest 实算 holdout 与 dev 共享截断前缀数为 0。
- 2026-10-31 主会话：PR #29 CI 绿。退回（第 2 轮）：
  1. Codex GitHub app（90c1ad8）P2 属实：`scripts/da-remote` 约 628 行对 `dropped/<job>/` 下每个条目调 `dev_trial`，不查是不是目录；名字带开发集题前缀的普通文件会被当成合格 trial，整份取回。要求：只接受 trial 目录，其他条目一律判"无法判断"（只取汇总）；配修复前会失败的测试。
  2. 工单 27 已合并（f9df8e7），与本分支在 `evals/ablate.py`、`scripts/da-remote`、`docs/setup.md`、测试上会冲突：先把本分支 rebase（或 merge）到最新 origin/main，解决冲突时两边的行为都保留（27 的预检、快速失败、运行记录、check_record 与 28 的 --split dev、--task、process、dev-only pull），make check 通过。
- 2026-11-01 工单 agent（退回第 2 轮；cd536b2、1d508aa、5e4f8f7、9b01d06）：
  - 合并 origin/main（cd536b2，分支已推送所以用 merge）：冲突在 evals/ablate.py 与三个测试文件，两边行为都保留（27 的预检、--fail-fast、--skip-smoke、运行记录、check_record；28 的 --split dev、--task、request.json 记 split 与 tasks、dev 报告与 diverge），`SPLIT` 全换成 `args.split`。
  - 退回第 1 条（1d508aa）：`dropped/<job>/` 只认 trial 目录（`t.is_dir() and dev_trial(t)`），其他判"无法判断"；测试 `dropped file named like a dev trial` 修复前整份取回。
  - 合并带出（1d508aa、9b01d06）：27 的冒烟产物 `logs/smoke-<config>.log`、`smoke/<stamp>/<config>/` 原会让每个开发集运行只取汇总；现在冒烟 job 按服务器 manifest 单独判断，`smoke-<c>.log` 须有对应冒烟 job；只取汇总时本地已有 smoke/ 也拒绝。setup.md 同步。
  - make check：1272 passed, 1 skipped；接缝 C 未改（`git diff main...HEAD -- tests/acceptance` 为空）。
  - da-review（1d508aa）P1 已修（5e4f8f7）：`ablate --split dev` 拒绝 dev 与 holdout 重叠的 manifest。
  - eng:code-review 已修（9b01d06）：dev 题还要对照仓库 evals/manifest.json（服务器上即真 manifest）的 holdout，读不了就拒绝；冒烟 job 不要求同名正式 job；没有冒烟 job 的冒烟日志不接受。不修：与 diverge.py 2 行重叠检查重复；job 目录里的非目录文件（Harbor 自己的 config.json 等）；--task X 时冒烟仍跑 dev[0]（27 的设计）。
  - da-review（9b01d06）P1 未修（轮数用完，Status ready-for-human）：单独 `evals diverge` 只信给定 manifest（evals/diverge.py:150-153），留出题列进 dev 或没有 holdout 时写出逐题结果。主会话核实属实。
- 2026-11-01 用户决定：破例第 3 轮，只修这条。退回（第 3 轮）：`evals diverge`（以及 `report --split dev`，若有同类问题）对照 `REPO_MANIFEST`（evals/ablate.py:112、723 的做法）的 holdout 检查，任何留出题出现在 dev、或读不了真 manifest 时拒绝写逐题结果；配修复前会失败的测试。
- 2026-11-01 工单 agent（退回第 3 轮；686030c、f2b15ef）：
  - `report --split dev` 确有同类问题（原先完全不查留出题），一起修。新增 `evals/report.py` 的 `check_dev_list`：dev 列表对照给定 manifest 与仓库 `evals/manifest.json`（REPO_MANIFEST）的 holdout，重叠或读不了仓库 manifest 就 ValueError，报错只写路径不写题名；`write_report`（dev）与 `write_divergence` 写任何文件前调用；ablate 原有的复制版检查也改为调用它（f2b15ef）。仓库 holdout 不是字符串列表时拒绝。
  - 测试（修复前失败）：`test_a_dev_report_on_a_task_the_real_manifest_holds_out_is_refused`、`test_a_dev_list_the_real_manifest_holds_out_from_is_refused`（修复前 exit 0 并写出逐题结果）、`test_a_dev_report_on_a_task_its_own_manifest_holds_out_is_refused`；参数化测试覆盖仓库 manifest 留出 / holdout 为字符串 / 不存在；断言输出无题名、不建 out 目录。
  - make check：1282 passed, 1 skipped；接缝 C 未改。
  - eng:code-review 不修：给定 manifest 顶层不是 dict 时 TypeError 堆栈（早先代码，不泄露题名）；矛盾 manifest + 仓库 manifest 缺失时报的原因（都被拒绝）；测试内 import。
  - da-review P2"dev 列表须是仓库 dev 列表的子集"：主会话决定不修，记为已知限制——ADR-0004 只禁留出集逐题结果；改成子集会改变 `ablate --split dev` 行为并重写大量测试。

- 2026-11-01 主会话服务器真实运行（用户决定改在服务器跑，代替 Mac colima）：`da-remote setup f2b15ef`；`scripts/da-remote start t28-dev -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest evals/manifest.json --split dev --task cohort-retention-matrix --out runs/t28-dev --budget 2 --config full --config baseline --trials 1` → exit 0：
  ```
  smoke: full ok (reward 0, $0.1250)
  smoke: baseline ok (reward 0, $0.0548)
  # Ablation: dev, 1 tasks x 1 trials
  | full | 0.0 | 0.0 | 1 | 0.1440 | ...   | baseline | 0.0 | 0.0 | 1 | 0.0634 | ...
  -> runs/t28-dev/reports/20261101T093849.683594Z
  ```
  总花费 $0.3871（上限 $2），内存峰值 2050 MB。`scripts/da-remote pull t28-dev` → `pull: server runs/t28-dev -> .../runs/t28-dev (dev set only: the whole run)`；取回 jobs/、logs/、records/（machine da-server、benchmark_commit a3278ad1…、git_commit f2b15ef…）、smoke/、reports/<stamp>/{ablation.*, diverge/, full/, baseline/}。"含非开发集题只取汇总"的服务器实测没做（单元测试覆盖）。
- 2026-11-01 主会话：PR #29 CI 绿。Codex GitHub app（f2b15ef，自动与 `@codex review` 两次）P2"dev-only 判断忽略 job 目录里的普通文件，应拒绝未知文件或白名单"——记为已知限制，不修：退回轮数已用完（2 轮 + 用户授权的破例 1 轮）；这些文件是 Harbor 自己写的 config.json、result.json、job.log，job 的 task_names 须全是开发集题；白名单会随 Harbor 版本变化误拦。若要收紧，另开工单。合并。
