# 96 — 在线版：录好的对话回放

Status: in-progress
Blocked by: 95
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-17
Branch: worktree-96-online-chat-replay

## Goal

在线版（GitHub Pages，只读）加一段录好的真实对话回放：在 data-eng-bench 零售库上录一段多轮对话（追问、澄清、插话），导出成静态数据，在线版用同一套对话页面回放，访客不能发消息。

## Authority

- May edit: `evals/export.py`（加导出一段对话，强制脱敏沿用现有规则）、`web/src/site/`、`web/src/`（对话页面的只读模式）；`tests/`（新增单元测试）
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`；`src/data_agent/` 的 agent 运行逻辑
- Server access: none
- Spend cap: $0（录对话由主会话做）

## Context

- spec 第 4 条。零售库是 Apache-2.0，803 张表；本机 clone `~/dev/AI/data-agent-refs/data-eng-bench` 里是 Git LFS 指针（约 489 MB），录之前要先拉下来。问题自己写，不用榜单题目，不碰留出集（ADR-0004、ADR-0005）。
- 先例：工单 80（导出运行，`.scratch/web-ui/issues/80-web-export.md`）、工单 82（发布到 GitHub Pages）。导出拒绝留出集的运行、强制脱敏（`python -m evals scrub`）。
- 在线版的数据不进仓库：导出目录打成 `site-data.tar.gz`，放在 GitHub release `site-data` 里，Pages 构建时取（`.github/workflows/pages.yml:1-25`）。更新这个 release 资产是对外发布，由主会话在人确认后做。
- 在线版回放沿用现有运行页的回放时钟（`web/src/run-page/replay`）。
- 动图和视频不在本工单（plan 第 21、23 项，等本工单之后再录）。

## Acceptance

- [ ] 导出一段对话的命令有单元测试：脱敏、拒绝留出集、索引和各轮事件都导出
- [ ] 在线版能打开这段对话并回放，没有发消息的入口（vitest 或 Playwright 测试）
- [ ] `pnpm --dir web build` 通过；`make check` 通过

## Evidence required

`make check` 输出。主会话录对话（命令、问题、每轮花费），跑导出，`python -m evals scrub` 输出 0 处改动，贴在线版回放页的截图。

## Stop conditions

- 零售库拉不下来或太大，没法在本机录。
- spec 自相矛盾，或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

### Main session, 2026-11-17: demo conversation recorded

- The DB is data-eng-bench `base-image/database/retail.duckdb` (Apache-2.0), sha256 `bd2bb1b3…c2ec2d`, 488,910,848 bytes.
  - It was fetched with `git lfs smudge` onto the external volume, at `/Volumes/sd710/data-agent/retail.duckdb`. The clone's local `lfs.storage` is `/Volumes/sd710/data-agent/lfs`.
  - `git-lfs` was installed by the human.
- The questions were written for the demo and avoid the four retail leaderboard task topics (channel revenue, market ROI, cart recovery, trust risk). The human asked for a plain agent Q&A, not a script built around clarification options.
- Recorded through the real `data-agent web` (port 8799, `--runs-dir runs/demo-chat/web`, one source `retail`), all over HTTP, with the real model (DeepSeek).
- **The conversation to export: `runs/demo-chat/web/conversations/20261118T020857Z-6cd60d`.**
  - Turn 1: "Which return reasons cost us the most in refunds?" After the first tool call the user steered "Only processed returns, please.", delivered at seq 20. The agent took "processed" as status exactly `PROCESSED`, stated that assumption, and answered: Not As Described $1,562.01 (18.4%), then Gift Return $1,083.29 and Color Different $1,040.75, out of $8,509.86. 12 turns, $0.0111.
  - Turn 2: "For Damaged in Shipping, which product categories have the most returned units?" It kept the previous reading ("as in the previous answer"). Dresses 2 and Electronics 2 are tied, then Skincare 1 and Women's Pants & Skirts 1. 5 units could not be attributed, because their order lines are missing. 22 turns, $0.0153.
  - Main-session SQL check: the top 3 reasons and the $8,509.86 total match; the category units match, including the 5 unattributed units.
- Two earlier takes in the same runs dir are not to be exported:
  - `20261118T020652Z-278a98`: two clarifications in three turns, because the follow-up said "product types", which matches a `PRODUCT_TYPE` column as well as categories.
  - The first take, also `…278a98`, stopped after turn 1.
- Exporting to the site and updating the `site-data` release is an outward publish: it needs the human's go-ahead.
