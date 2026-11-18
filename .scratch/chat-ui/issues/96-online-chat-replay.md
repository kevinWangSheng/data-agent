# 96 — 在线版：录好的对话回放

Status: ready-for-agent
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

- Attempts: 1
- Review rounds: 1
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

### Attempt 1 (ticket agent, branch `worktree-96-online-chat-replay`, commits efdb5c6, e7ec481), final reply as given

The branch is done and ready for your review: the exporter can now export a recorded conversation, and the online site replays it at `chats/<id>` with no way to send a message. Two commits on `worktree-96-online-chat-replay` (`efdb5c6`, `e7ec481`). `make check` passes and `pnpm --dir web build` succeeds. The real recording on the retail database is still yours to do; the commands are in the last section.

**What changed**
- **Export:** `python -m evals export --conversation <runs dir>/conversations/<id>` writes the conversation's index into `index.json` and each turn as an ordinary run in `runs/<run_id>/`. Everything goes through the existing scrub. The conversation's workspace and the SDK session record are not exported.
- **What the export refuses:**
  - a conversation whose `notes.json` lacks a title, or lacks `local_data: true` or a dev-set `task_id` (the featured-run rule). Holdout refusals print only a count.
  - a turn that hasn't ended (no `result` event, and not cancelled or failed by the server's record).
  - a turn whose run is missing.
  - a turn run id the scrub would change.
- **Site:**
  - `chats/<id>` opens the conversation and starts playing on the run page's replay clock and controls, taking about a minute.
  - The header gets a link to each recorded conversation.
  - "Full run view" opens each turn's three-pane page.
  - There's no message box, and the clarification options are disabled.
- **Shared code:** the transcript is split out of `Conversation.tsx` so the live and online pages share it. The run page's replay loop moved into `run-page/useReplay.ts`, used by both pages.
- `web/site-data.ts` is unchanged: `homeData` copies `conversations` from `index.json` into `home.json`. Exports without conversations keep the old layout.

**Status: in-review**

**Comments (for the ticket):**
- Baseline before starting: `make check` green (pytest 2412 passed, vitest 275 passed).
- Real run on the fixture conversation (5 turns: clarification, steer, cancelled turn), laid out as `data-agent web` stores it under `runs/96/demo`:
  - `uv run python -m evals export --manifest evals/manifest.json --out runs/96/demo/site --run runs/96/demo/featured/query-shop --conversation runs/96/demo/runs/conversations/20261117T100000Z-c0ffee` printed `scrub: changed 0 file(s) of 14 …` and `export: 6 run(s), 1 conversation(s) -> runs/96/demo/site`.
  - Re-running `python -m evals scrub runs/96/demo/site` printed `changed 0 file(s) of 14; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0`.
- Browser check: built with `SITE_DATA=… pnpm --dir web build`, served with `vite preview`, opened in headless Playwright:
  - The page autoplays: 1 turn at 0:05, 3 turns at 0:35 of 1:13, with 0 textareas.
  - "Show all" displays all 5 turns, including the delivered steer and the cancelled turn. "Full run view" opens the three-pane page.
  - The first screen still autoplays.
  - These screenshots are of the fixture conversation, not the demo recording: `runs/96/replay-midway.png`, `runs/96/replay-all-shown.png`.
- New tests:
  - `tests/test_eval_export_conversation.py` (10): index and every turn's events exported, scrubbing, holdout refused by count, missing provenance refused, unfinished turn refused, failed turn marked, missing run refused, repeated id refused, leaky run id refused.
  - `web/src/chat-page/replay.test.ts` (9).
  - `web/src/site/RecordedConversation.test.ts` (5): opens and replays; no textarea, no send button, options disabled.
  - Additions to the `homeData` and `speedsWith` tests.
- Final `make check`: exit 0, pytest 2422 passed / 1 skipped, vitest 291 passed, ruff and basedpyright clean.
- Review: `da-review` was not run (frontend ticket). `eng:code-review main` ran once and both rounds of fixes fit in one pass:
  - **Spec:** no findings. Authority is respected: `tests/fixtures/chat/`, `tests/acceptance/`, `web/e2e/`, `web/src/chat-view/` and `src/data_agent/` are untouched.
  - **Standards:** no hard violations. Fixed:
    - the copied replay loop, now `useReplay`;
    - `check_conversations` duplicating `check_featured`;
    - the no-op clarification props, now optional;
    - a wrong `turn_notes` docstring.
  - **Not fixed:**
    - The title ternary in `App.tsx` follows the pattern already there, so changing it would be an unrelated refactor.
    - The status strings are shared between Python and TypeScript, and the risk is low.
  - **Known limit (reviewer note):** `local_data: true` relies on your word that the questions aren't benchmark tasks, the same trust featured runs get (ticket 80).

**Ledger:** Attempts: 1 · Review rounds: 1 · Model spend: $0 · Outcome: —

**Commands for the main session** (in order):
1. Pull the retail database: `git -C ~/dev/AI/data-agent-refs/data-eng-bench lfs pull`.
2. Record on it with `data-agent web --runs-dir <R> --data-sources <toml for the retail db>`. Use questions you wrote yourself, covering a follow-up, a clarification and a steer.
3. Write `<R>/conversations/<id>/notes.json` containing `{"title": "…", "local_data": true}`.
4. Export: `uv run python -m evals export --manifest evals/manifest.json --out runs/site --run <featured runs…> --job <dev jobs…> --board <board.json> --dab <dab_score.json> --conversation <R>/conversations/<id>`. Keep the featured, job and leaderboard inputs from the current site so it loses nothing.
5. Check the scrub: `uv run python -m evals scrub runs/site` should report 0 changes.
6. Preview: `SITE_DATA=$PWD/runs/site pnpm --dir web build && pnpm --dir web preview`, open `/chats/<id>` and take the screenshot.
7. After your confirmation: `tar -czf site-data.tar.gz -C runs/site .`, upload it as the `site-data` release asset, then run the `pages` workflow.

### Main session, 2026-11-17: export with the demo conversation (at e7ec481)

- `notes.json` written for the demo conversation: `{"title": "Refund costs by return reason", "local_data": true}`.
- `bash runs/site-publish-2/export.sh` (ticket 82's inputs plus `--conversation runs/demo-chat/web/conversations/20261118T020857Z-6cd60d`) printed "scrub: changed 0 file(s) of 552; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0" and "export: 275 run(s), 1 conversation(s)". A re-scrub also reported 0 changes.
- `grep -rlE "sd710|/Volumes/|claude-config|shenghuikevin|/Users/"` over the export: no hits. The `index.json` `conversations` holds the 2 turns and the steer.
- The output is in `runs/site-publish-2/site` and is not in git. Nothing is published: building the tarball and replacing the `site-data` release asset waits for the human.

### Main session, 2026-11-17: reviews of PR #105

- CI `check` green. The Codex GitHub app reacted 👍 with no comments.
- A clean-context Opus 5.5 review of the frontend found no regressions:
  - Run page replay: checked line for line against the old loop (speeds, auto speed, rAF tick and cleanup, restart, scrub, skip idle, Show all, Space, first-screen autoplay).
  - Live chat page: no regressions found.
  - Replay of the real export: turn order, steer shown once delivered, disabled options, growing cost, end state.
  - Routing on Pages: correct.
  The main session checked each finding below against the code.

退回（第 1 轮）：
1. P2 `web/src/site/RecordedConversation.tsx:57`，经 `web/src/chat-page/Transcript.tsx:56-66`：回放控制条是一整行、不折行（播放、重来、6 个速度、跳过空闲、128px 滑块、88px 时间、Show all，约 700–750px），放进了 Transcript 的标题栏；运行页在 900px 以下藏起这条（`RunPage.tsx:204` `max-[900px]:hidden`）。375px 手机上页面会横向滚动，Show all 和滑块在屏幕外。改：窄屏下给出可用的控制（例如只留播放、Show all，其余藏起，或者控制条自己折行），页面不能横向滚动；用 375px 截图证明。
2. P3 回放页按空格不起作用：播放按钮提示写着 "Play or pause (space)"，只有 `RunPage.tsx:173` 有 keydown；`useReplay` 已经返回 `togglePlay`，`RecordedConversation` 没用。照运行页加上（焦点在输入控件里时不拦），配测试。
3. P3 `web/src/chat-page/Turn.tsx:62`：被取消的轮在在线版也写 "Cancelled. The next message starts a new turn."，在线版没有输入框。只读模式下只写 "Cancelled."，配测试。
- 记成已知限制，不修：从某轮的 "Full run view" 返回时，回放从 0:00 重新开始，进度和 Show all 状态丢失。要修得把回放状态提到页面之外，改动面大，收益小。
修完 `make check`，`eng:code-review` 只看新提交（前端为主，不跑 `da-review`）；最终回复照工作流程第 6 步。
