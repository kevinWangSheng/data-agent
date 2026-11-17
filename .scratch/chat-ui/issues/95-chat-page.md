# 95 — 对话页面（A「对话流」）

Status: in-review
Blocked by: 94（93 只挡合并：人 2026-11-17 定提前开工，见 Comments）
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-17
Branch: worktree-95-chat-page

## Goal

本地版的主入口是对话页面：左边对话列表，右边单栏对话，每轮步骤按阶段折叠成卡片嵌在回答里，能追问、点澄清选项、运行中插话、取消，显示累计花费，每条回答能打开三栏运行页。现有提交表单保留（dbt 和单次运行仍从这里提）。Playwright 主流程转绿。

## Authority

- May edit: `web/src/`（新增对话页面；`App.tsx` 路由；`history-page/` 改成对话列表；`local/NewRun.tsx` 只改入口位置）、`web/src/chat-view/`（验收测试以外，只为页面需要加导出）；新增的 vitest 单元测试
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`；`src/`
- Server access: none
- Spend cap: $0

## Context

- spec 第 1、5、6、7、9 条。界面选定 A「对话流」，原型在 `prototype/chat-ui` 分支（`566e353`，`web/src/prototype-chat/`，本地 `pnpm --dir web dev` 后开 `/prototype/chat?variant=A`）；实现时重写，不直接搬原型代码。
- 卡片样式和事件折叠尽量复用现有运行页的组件（`StepTag`、`StepChip`、`VerdictChip`、`DataTable`、`Diff`、`Pre`、`Md` 等）。
- 每条查询卡片标出查的是哪个库（第 20 条）。
- 单次运行（含 dbt）在对话列表里显示成一轮，不能追问。
- 本工单删掉 `TICKET_95` 标记。合并前主会话跑 Playwright 主流程（真后端、真模型）。只用 pnpm。

## Acceptance

- [ ] Playwright 主流程通过（主会话跑），`TICKET_95` 标记删掉，测试内容一字未改
- [ ] 现有运行页、提交表单、在线版页面照常能用（现有测试全绿）
- [ ] `pnpm --dir web test`、`pnpm --dir web build` 通过
- [ ] `make check` 通过

## Evidence required

`make check`、`pnpm --dir web test` 的输出。真实运行由主会话做（工单 agent 加载不了模型环境）：最终回复写好要跑的命令，主会话跑完把命令和真实输出抄进 Comments。主会话跑 Playwright 主流程，再按 `run` 技能起本地版走一段对话（追问、澄清、插话、取消各一次），贴截图给人看样式（spec：样式由人看着定）。

## Stop conditions

- 某条行为要改后端接口或视图模型的契约。
- spec 自相矛盾，或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 2
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

### Main session, 2026-11-17: early start

- The human decided (2026-11-17) to start 95 before 93 (chat backend) merges; 94 is done. Build the page against the ticket-88 route contract (`tests/acceptance/test_chat_api.py` docstring) and the merged view model. Develop and test against a stand-in backend you run locally (for example `tests/fixtures/chat/fake_chat_run.py` through the existing `create_app`, or vitest mocks); don't add anything under `src/`.
- Remove the `TICKET_95` marker as the ticket says. The Playwright main flow can only go green with a real backend, so the main session runs it after 93 merges; merge waits for that.
- This is a frontend ticket: per `AGENTS.md` (after workflow step 7), step 5 runs only `eng:code-review main`, not `da-review`.

### Attempt 1 (ticket agent, branch `worktree-95-chat-page`, commits 07c73a6, c34b3f2), final reply as given

I built the chat page for ticket 95 and committed it on `worktree-95-chat-page`. The Playwright main flow is not proven yet: it needs the real backend from ticket 93, so the main session has to run it after 93 merges. Against a local stand-in backend that follows the ticket-88 contract, the unchanged browser test passes.

**Proposed status:** `Status: in-review`

**What's in it** (two commits, 07c73a6 and c34b3f2; nothing under `src/`, `NewRun.tsx` unchanged):
- **Main page:** the local build now opens on the chat page. The conversation list is on the left; single runs, dbt included, appear there as one-turn conversations with no message box. `chats/<id>` opens a conversation, and `runs/<id>` is still the three-pane run page.
- **Turns:** steps show as cards grouped by phase. The phase that is running is open, and the others fold.
- **Cards:**
  - every query card names the database it ran on;
  - SQL is highlighted, with its result table;
  - verifier cards show PASS/FAIL and each check;
  - rejected calls are marked;
  - thinking is folded by default.
- **Clarification:** the options send the next message and can only be clicked once the backend says no turn is running.
- **Running turn:** messages sent while a turn runs go into it and show "SENDING", then "DELIVERED TO THE AGENT". Cancel is there too.
- **Cost and full view:** the header shows the conversation's total cost, and each turn links to its full run view.
- **Browser test:** the `TICKET_95` marker is removed from `web/e2e/chat.spec.ts`, and nothing else in that file changed.
- **Tests:** new vitest tests cover the conversation client (`source.test.ts`), the address (`route.test.ts`) and the turn layout (`layout.test.ts`, run against the ticket-88 fixtures).

**For the ticket's `## Comments`:**
- **Baseline before changes:** `pnpm --dir web test` 241 passed; `make check` passed.
- **After the fixes:**
  - `make check` exit 0: pytest 2330 passed, 1 skipped, 63 deselected, 8 xfailed; web 16 files, 260 tests passed.
  - `pnpm --dir web build` exit 0.
- **Browser flow against the stand-in:** `web/e2e/chat.spec.ts`, unchanged, run with a temporary config pointing at the stand-in on port 8795. Both tests passed ("serves the built UI" 0.6s; "send, see the cards live, pick a clarification option, follow up" 31.3s), before and after the review fixes. The stand-in is the real `create_app` plus the ticket-88 conversation routes and a scripted turn process. It is throwaway and not committed: it lives in `runs/stand-in-95/` in this worktree, which git ignores.
- **Manual pass in a headless browser:** I tried a steer, a cancel, a clarification, a single run from the New run form, and the full run view link. Screenshots are in `runs/stand-in-95/` in this worktree: `steer-running.png`, `after-answer.png`, `cancelled.png`, `clarification.png`, `single-run.png`, `full-view.png`.
- **Review (`eng:code-review main`):** frontend ticket, so no `da-review`. One round of fixes, all in c34b3f2:
  - a live stream left open after leaving a conversation;
  - narration filed under a verify phase was not shown;
  - the list and the header computed the title two different ways;
  - the clarification check named the backend's running flag `busy`;
  - a slow conversation-list read could overwrite a newer one;
  - redundant list reloads;
  - `HistoryPage` (now online-only) had a stale comment and a prop that was always the same.
- **Review findings not fixed, with reasons:**
  - **Groups fold once a turn ends:** this is layout A as chosen. The human judges styles from screenshots.
  - **No tests for the live hook or the components:** the repo has no DOM test setup, and adding one is a dependency change. The browser main flow covers this path.
  - **Rejection-banner colour logic copied from the run page:** sharing it means refactoring run-page code, which is outside this ticket.
  - **The answer's text is matched by content, not position:** the fixtures show the `answer` event repeating the `text` event just before it, so matching by position would show the answer twice.
  - **A dbt single run shows in the chat page without its diff:** its Full run view link covers that.

**For the ticket's `## Ledger`:** Attempts: 1; Review rounds: 1; Model spend: $0.

**For the main session after 93 merges:**
1. `pnpm --dir web build:local`, then `make test-integration` (or `pnpm --dir web exec playwright test`) for the Playwright main flow.
2. Following the `run` skill, start the local app and walk one conversation with a follow-up, a clarification, a steer and a cancel, then send the human screenshots.

The real backend may start a turn before that turn's events can be read; the page simply tries again on the next poll. There is an untracked `.playwright-mcp/` folder at the worktree root left by the browser tool; it is not committed.

### Main session, 2026-11-17: clean-context Opus 5.5 review of PR #100 (frontend rule, AGENTS.md)

The reviewer ran `pnpm --dir web test` (260 passed) and `typecheck` (exit 0). It checked theme tokens in light, system-dark and forced-dark: all defined. Routing, unmount cleanup and the clarification guard hold. It agreed with every not-fixed reason in attempt 1. The main session checked each finding below against the code; all hold.

退回（第 1 轮）：
1. P2 `web/src/chat-page/Conversation.tsx:87-90`：`submit` 只看 `followUp && !busy`。这一轮已写出 `result`、进程还没退出时（`row.running` 为真、视图里这一轮已结束），追问会被后端当插话（202）送进已结束的会话，消息丢失，界面一直 "SENDING"。改：这段时间发送框和澄清选项一样等空闲（发送按钮禁用并说明"等这一轮收尾"），不改契约。
2. P3 `web/src/chat-page/Turn.tsx:252-260`：没送达的插话在已取消或已结束的轮上仍显示 "SENDING"。轮不在运行时改成"未送达"。
3. P3 `web/src/chat-page/useConversation.ts:84`：读某轮事件失败后只从 `loaded` 删掉，注释说"下一行再试"，但空闲对话不再拉行，这一轮永远空着且没有报错。改：空闲时也会重试（如失败后定时重拉），或显示错误和重试入口。
4. P3 `web/src/chat-page/useConversation.ts:43-51`：`refresh` 没有防旧响应后到（`ChatPage.tsx:35-46` 有），取消后马上发送时旧响应可能把行改回空闲、停止轮询，新一轮不出现。照 `ChatPage` 的写法加序号保护。
5. P3 `web/src/chat-page/ChatPage.tsx:78`：`grid-cols-[260px_minmax(0,1fr)]` 窄屏没有退路，375px 下对话栏约 115px 不可用。照运行页 `RunPage.tsx:285` 的 `max-[900px]` 写法。
每条配一个修复前会失败的测试（能在 vitest 里测的测；纯布局的 5 用截图证明，375px 宽）。修完 `make check`，`eng:code-review main` 只看新提交（前端工单，不跑 `da-review`）；最终回复照工作流程第 6 步。

### Attempt 2 (ticket agent, branch `worktree-95-chat-page`, commits 9a18408, 71655d2): return round 1

Each returned finding, what changed, and its test. The new test is shown failing before the fix.
1. **Message sent while the finished turn's process is still exiting:**
   - **Change:** a new pure function `composer(view, running)` in `chat-page/layout.ts` returns `none`, `turn`, `steer` or `settling`. While the backend says `running` but the last turn has written its result, the mode is `settling`. In that mode Send and Enter are disabled and the hint reads "Waiting for this turn to wrap up; you can send once it has." Cancel still shows while the backend says running, so a process that hangs after its result can still be stopped.
   - **Test:** `layout.test.ts › composer`, red before the fix.
2. **Steer left at "SENDING" on a turn that ended:**
   - **Change:** a new `steerState(steer, status)` returns `delivered`, `sending` or `undelivered`. On a turn that is no longer running, an undelivered steer now shows "NOT DELIVERED".
   - **Test:** `layout.test.ts › steerState`, red before the fix.
   - **Seen on the page:** shown on the stand-in backend after a steer followed straight away by a cancel.
3. **Failed event read never retried while the conversation is idle:**
   - **Change:** the hook's loading logic moved, unchanged, into a non-React `chat-page/feed.ts` (`openFeed`); `useConversation` now only wraps it. A failed read of a turn's events is now retried on a timer, even while idle: after 1s, then doubling, up to 30s.
   - **Tests:** `feed.test.ts` ("read again while the conversation is idle"; "less and less often, up to a ceiling"), red against the moved code before the fix.
4. **Late row read undoing a newer row:**
   - **Change:** row reads are numbered in the order they are asked for, and a row the cancel returns counts as the newest. A read is shown only if it is newer than the last row shown.
   - **Tests:** `feed.test.ts` ("a row read that answers after a newer one is dropped…"; "a row the backend answered a cancel with outranks a read that was already out"), red before the fix.
5. **Narrow screen:**
   - **Change:** below 900px (the run page's breakpoint) the list sits above the conversation, at most a third of the screen height.
   - **Evidence:** screenshots at 375px in this worktree, `runs/stand-in-95/narrow-375-before.png` and `narrow-375-after.png`. Before, the conversation column was about 115px wide; after, it uses the full width.

**Checks**
- **After commit 9a18408:** `make check` exit 0 (pytest 2330 passed, 1 skipped, 63 deselected, 8 xfailed; web 17 files, 273 tests); `pnpm --dir web build` exit 0.
- **After commit 71655d2:** `make check` exit 0 (pytest 2330 passed, same counts; web 17 files, 275 tests); build exit 0.
- **Logs:** `runs/r1-95/make-check-2.log` and `runs/r1-95/build-2.log` in this worktree.

**Review (`eng:code-review c34b3f2`, new commits only; frontend ticket, so no `da-review`)**
- **Spec axis:** all five return items are covered and nothing blocks. Findings 1 and 2 count as partial: only the pure functions are tested, not how the page uses them, because the repo has no DOM test setup.
- **Fixed in 71655d2, each with a test that failed first:**
  - **Stalled poll:** a row read that never answered stopped the poll for good. I had made the poll skip a tick while a read was out; now a newer read is shown even if an older one never answers.
  - **Box took steers during settling:** `composer` checked whether *any* turn was running. An earlier turn whose events could not be read yet looks running, which brought back finding 1. It now checks only the last turn.
  - **Stale error after cancel:** the row a cancel returns did not clear an earlier "could not read" error. It does now.
  - **Smaller fixes:** clearer names (`lastRow`, `takeRow`); the message box's three per-mode texts gathered into one table; `FEED_TIMING` is no longer exported.
- **Not fixed, with reasons:**
  - **A turn whose events file never appears is retried every 30s for as long as the page is open.** That costs one request per 30s per broken turn. Stopping the retries would bring back the silent blank turn that finding 3 is about. The return note allowed retry instead of an error.
  - **A steer shows SENDING for as long as its turn's events cannot be read.** The case where events exist but end without a result is handled only partly. This uses the view model's own status: the no-events case clears once the retry reads them. A run that dies with neither a result nor a failure note is a backend status question, outside this ticket.
  - **The turn's status is passed down to each steer note:** three props; `steerState` itself is pure and tested.
  - **How the page uses `composer` and `steerState` is untested:** no DOM test setup, as in attempt 1.
  - **The settling mode has no browser screenshot:** the window is too short to catch on the stand-in; the `composer` test covers it.

**Note on the stand-in backend:** its cancel route crashed when it ran before the turn's file existed. That is the throwaway stand-in in `runs/stand-in-95/`, not this ticket's code. I stopped the two stand-in servers I had started.

**For the main session after 93 merges (unchanged from attempt 1):**
1. `pnpm --dir web build:local`, then `make test-integration` (or `pnpm --dir web exec playwright test`) for the Playwright main flow.
2. Following the `run` skill, start the local app and walk one conversation with a follow-up, a clarification, a steer and a cancel, then send the human the screenshots.
