# 24 — `attribute` 读不到 Harbor trial 的事件文件

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (/implement session; 认领由主会话补填)
Branch: worktree-24-attribute-event-files

## Goal

`python -m evals attribute` 在真实 Harbor job 上能找到每个 trial 的事件文件并完成失败归因；然后在开发集运行 `runs/dev-2` 的 `full` job 上真实跑一次，产出可供人审阅的归因报告。

## Authority

- May edit: `evals/attribute.py`、`evals/harbor.py`（若把事件文件查找收拢成共用函数）、`evals/ablate.py`（只限改成调用共用函数）、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试、`skills/`（归因只出草稿，不改 Skill）、`evals/wheels.py`（工单 22）、`evals/dab.py`/`evals/dab_server.py`（工单 23）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $3（judge 花费）

## Context

- 主会话实测：`python -m evals attribute runs/dev-2/out/jobs/full --manifest evals/manifest.json --tasks-dir ~/dev/AI/data-agent-refs/data-eng-bench/tasks --out runs/dev-2/attribution` → `0 of 12 dev failures attributed`，每条都是 `Not attributed: no event file`。
- 原因：`evals/attribute.py:522` 找 `trial.dir / "agent" / "events.jsonl"`，而 agent 写在 `agent/events/run-<ts>-<id>.jsonl`（例：`runs/dev-2/out/jobs/full/workforce-analytics__6h6uJog/agent/events/run-20261020T213727Z-ab4c66e5.jsonl`）；`evals/ablate.py:219-221` `event_files()` 用的是正确位置。单元测试的假 trial 用了错的路径，所以没测出来。
- 一个 trial 可能有多个事件文件（续跑/重试）；按 ablate 的做法处理，选哪一个或都读要写清楚。
- 环境变量：照 `runs/dev-2/attribute.sh`（主会话写的，source 本机 env 文件后跑；不要读 env 文件内容）。
- ADR-0004：只归因开发集；`runs/dev-2` 全是开发集题目（manifest 的 dev 列表）。ADR-0005：建议不能点名题目。

## Acceptance

- [ ] `attribute` 用与 `ablate` 相同的位置找事件文件（最好共用一个函数）；配修复前会失败的测试（真实目录布局）
- [ ] 真实运行：`runs/dev-2/attribute.sh`（可改 `--out` 到新目录）在 `full` job 上归因 12 个失败，贴报告的 FM 分布与花费；报告路径写进 Comments（报告本身不进 git）
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令、输出与报告路径。

## Stop conditions

judge 花费到上限；需要人决定的事；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger
- Attempts: 1
- Review rounds: 1
- Model spend: $0.39 (judge)
- Outcome: done，PR #24 squash 合并（0 轮退回）; 12 of 12 dev failures attributed; Codex review not completed (model at capacity)
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

开发集 full 60.0% < baseline 66.7%（k=1，6 题有差异，2:4，未显著）。用户选择：先归因修 Skill，再开发集 k=3 复查，然后才全量。归因工具在真实 job 上找不到事件文件，Phase 1 遗漏。

### 2026-10-20 工单 agent：实现（53928af、4d3753a，主会话抄录）
- 修复：`event_files` 移到 `evals/harbor.py`，`attribute`/`ablate` 共用；多个事件文件时取最新一个（文件名按开始时间排序，最后一次运行才是 verifier 打分的那次）。单元测试的假 trial 改成真实布局 `agent/events/run-<stamp>-<id>.jsonl`，修复前 22/27 失败；新增"多个文件取最新"测试。提交 53928af、4d3753a。
- `make check`：ruff/basedpyright 0 errors，1147 passed, 1 skipped（第二个提交只改了测试，之后只重跑了该文件，27 passed）。
- 真实运行：`bash runs/24-attribution/attribute.sh`（worktree 内；source env 文件；job 用绝对路径 /Users/shenghuikevin/dev/AI/data-agent/runs/dev-2/out/jobs/full；`--out runs/24-attribution/report`）→ `attribute: 12 of 12 dev failures attributed, $0.3872 spent -> runs/24-attribution/report`。FM4 ×11、FM3 ×1；infra 0；非开发集 0；8/12 条建议因含 benchmark 名称被扣下（ADR-0005），标签与引文保留。报告：/Users/shenghuikevin/dev/AI/data-agent/.claude/worktrees/24-attribute-event-files/runs/24-attribution/report/{report.md,attribution.json}（不进 git）。
- 审查：`eng:code-review main`：Standards、Spec 均无硬问题。已修：多文件测试的绕弯构造（4d3753a）。不修：①把"取最新"收进 harbor 的函数——目前只有一个调用方；②测试改为 import `DataAgent.EVENTS_DIR`——会把 harbor_agent 拉进这个单元测试，路径常量保持本地。`da-review main` 4 次都因 "Selected model is at capacity" 中断，未完成（日志 runs/24-attribution/da-review-*.log）；push 前需重跑。
- 遗漏（需后续工单）：`evals/report.py:89` `_trial_rows` 仍用 `agent/events.jsonl`，真实 job 上每行 `events` 都是 None；不在本工单 Authority 内。

### 2026-10-20 主会话：PR #24 Codex 意见核实与合并

CI 绿（4d3753a）。Codex P2（`evals/attribute.py:531`）"同一 UTC 秒内产生多个事件文件时按文件名排序不代表执行顺序"——情形属实但不会发生：一个 trial 里的 agent 运行是先后进行的，后一次要等前一次结束才开始，每次运行至少几分钟（开发集 trial 都在分钟级），两个文件不可能落在同一秒；本次 job 每个 trial 也只有一个事件文件。不修，记为已知限制。`da-review` 因 Codex 容量不足未完成，以 PR 上 Codex app 的审查代替。已 `gh pr merge 24 --squash`。
遗留：`evals/report.py:89` 同样的旧路径（真实 job 上 `events` 为 None），记入后续工单。
