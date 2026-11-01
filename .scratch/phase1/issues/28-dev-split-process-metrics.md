# 28 — 开发集运行：逐题配对分歧与过程指标，并能把轨迹取回本机

Status: ready-for-agent
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

- Attempts: 1
- Review rounds: 2
- Model spend: $0
- Outcome: implemented (982fcc6, 158cece, c8594f5); colima real run open
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
