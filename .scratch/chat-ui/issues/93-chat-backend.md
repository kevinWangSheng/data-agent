# 93 — 对话后端：存储、接口、子进程编排

Status: in-review
Blocked by: 89, 90, 91, 92
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-17
Branch: worktree-93-chat-backend

## Goal

`data-agent web` 有对话接口：列出对话、新建对话、发消息（空闲时开新一轮，运行中送进子进程的 stdin）、取消。对话按目录和索引文件存；单次运行显示成一轮对话、不能追问。后端接口验收测试和真模型端到端 a–d、g 转绿。

## Authority

- May edit: `src/data_agent/web/`（`app.py`、`launcher.py`、`runs.py`、`sources.py`，可新增对话模块）、`cli.py`（只 `web` 子命令）；`tests/`（新增单元测试）；`docs/setup.md` 或 README 的本地启动一节
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`；agent 的运行逻辑（`agent.py`、验证者、守卫、网关，归 89–92）
- Server access: none
- Spend cap: $0

## Context

- spec 第 6、9、13、17 条，"Testing Decisions"后端接口一组。契约（对话目录、索引、接口、`data-agent run` 参数、stdin 字段）以工单 88 合入的为准。
- 先例：`src/data_agent/web/launcher.py:40` `StartRun`、`:263-330`（工作目录）、工单 78 的取消和"同时只跑一条"、工单 85 的退出时不留子进程。
- 子进程以 stdin 管道启动；第二轮起传上一轮事件文件里的 session id（`agent.py:907` 写的那个）。
- 数据源：后端把 `--data-sources` 配置里的全部数据源交给子进程（第 20 条），连接串不出现在参数、接口输出、事件里。
- 一条用户消息一次运行、一个事件文件；现有的运行列表、读事件、SSE、三栏运行页照常能用（每条回答的"完整视图"）。
- 本工单删掉 `TICKET_93` 标记。合并前主会话跑 `make test-integration`，a–d、g 每个 3 次都过才合并（spec "判定"）。

## Acceptance

- [ ] 后端接口验收测试全部通过，`TICKET_93` 标记删掉，测试内容一字未改
- [ ] 真模型端到端 a–d、g 每个 3 次都过（主会话跑）；失败时读轨迹判定是实现问题还是模型偶发，写进 Comments
- [ ] 服务退出时不留下它启动的子进程（沿用工单 85 的测试）
- [ ] 评测配置不变一组（`tests/acceptance/test_chat_eval_unchanged.py`）一直通过
- [ ] 新一轮开始后（发消息返回 201 时），`GET /api/runs/<run_id>/events` 立刻返回 200 和空列表或已有事件，不返回 404（照 `launcher.py:88` 先建运行目录；来源：工单 95 的 Opus 复审，页面对读失败按退避重试，404 会让新一轮晚出现）
- [ ] `make check` 通过

## Evidence required

`make check` 和后端接口验收测试的输出。真实运行由主会话做（工单 agent 加载不了模型环境）：最终回复写好要跑的命令，主会话跑完把命令和真实输出抄进 Comments。主会话跑 `make test-integration`，贴每个场景 3 次的结果和花费。

## Stop conditions

- 某条接口行为要改 agent 的运行逻辑才能做到。
- 端到端某个场景反复不过，读轨迹判定是 89–92 的实现问题（退回对应工单）。
- spec 自相矛盾，或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

## Comments

### Main session, 2026-11-17: dispatch notes

- 89–92 are merged. Read their tickets' Comments for what the child process now does:
  - `--data-sources` (89);
  - `--chat --conversation-dir --turn --resume` (90);
  - the `clarification` event, after which a turn ends as a success with no answer (91);
  - stdin `{"id","text"}` lines. `steer_delivered` is written when the CLI echoes the line. A steer still unread when the turn ends gets an `error` event, and lines after the turn's result are never sent (92).
- Ticket 95's page (PR #100, not merged) is built against the ticket-88 contract. It waits for idle in the window after the last turn's `result` and before the process exits, and shows NOT DELIVERED for unread steers. Do not change the contract.
- Known limitation from 92/95: a message sent after the child writes `result` but before it exits still gets 202 (steer) and is not delivered. The page shows it as NOT DELIVERED.

### Attempt 1 (ticket agent, branch `worktree-93-chat-backend`, commits 9f703c7, 784648d, 9090b7e), final reply as given

I've built the chat backend for ticket 93 on `worktree-93-chat-backend` (3 commits, not pushed). `make check` passes and all 8 backend API acceptance tests pass. The real-model end-to-end runs (a–d, g, 3 times each) are still to do: I can't load the model environment, so the main session has to run them before merging.

**What it does now**
- **Conversations:** create, list, show, send a message, cancel. Each conversation lives in `<runs-dir>/conversations/<id>/`, the layout ticket 88's contract specifies.
- **A message to an idle conversation** starts a new turn: one `data-agent run` with every configured data source and, from turn 2, the previous turn's session id. The data sources go in a file holding names, kinds, paths and variable names only, never connection strings.
- **A message while a turn runs** is written into that turn's stdin as a steer; no new turn starts.
- **One run at a time** counts the run form and all conversations together; anything else gets 409.
- **Old command-line and form runs** show up in the conversation list as one-turn conversations that refuse follow-ups.
- **The bug the real run needed:** `data-agent web` now gives every run its own stdin pipe. Before, a turn inherited the server's stdin and steering could not work.

**Evidence**
- `make check`: `2412 passed, 1 skipped, 63 deselected`; ruff and basedpyright clean. Baseline was 2397 passed plus 8 xfailed.
- `tests/acceptance/test_chat_api.py`: `8 passed`. In it and `test_chat_e2e.py` the only changes are deleting the `TICKET_93` markers and their definitions.
- `test_chat_eval_unchanged.py` passes, and the ticket-85 shutdown tests still pass (4 passed).
- New tests:
  - a turn's events read as 200 with an empty list right after the 201;
  - a turn is steered through a real stdin pipe and leaves no processes after the server gets SIGTERM;
  - steers can't block cancel or shutdown;
  - a broken index doesn't break the list;
  - the data-source file round-trips names that need escaping.
- **Real run with no model** (`data-agent web` on the fixture data, then create a conversation and send a message):
  - The message got `{"kind":"turn","turn":1,"run_id":"20261117T231011Z-db1100"}` with HTTP 201.
  - The real `data-agent run` accepted every argument, then stopped with `data-agent run: error: DATA_AGENT_API_KEY is not set`, so no model call was made.
  - That turn's events read `{"events":[]}` with HTTP 200, and the turn shows `failure`.

**Reviews (fixes: 1 round each)**
- **`da-review` (Codex), 1 finding, fixed:** writing a steer to stdin happened while holding the launcher's lock, which a turn not yet reading stdin could block. That stalled cancel and shutdown; the new test reproduced the hang first. Each run now has its own writer thread.
- **`eng:code-review`, 1 finding fixed:** a `conversation.json` missing required fields broke the whole conversation list; it is now skipped. I also shared the duplicated test setup.
- **Not fixed, with reasons:**
  - An index write failing after the turn has started leaves that turn unrecorded. This is the same failure mode as the existing run-record write and unlikely on a local directory.
  - A steer to a run without a stdin pipe is recorded but dropped. The real server always gives a pipe, so only fake test runners hit this.
  - The conversation list holds the launcher's lock while it reads files. This is deliberate, so a row, a new turn and a steer agree on what is running.
  - A missing data source returns 400 even while another run is going (the contract asks for 409). This keeps the run form's existing check order.
  - The rest were naming and smell judgement calls.
- **Known limitation, unchanged:** a message sent after a turn writes `result` but before its process exits still gets 202 and is never delivered.

**For the main session to copy into the ticket**
- `Status: in-review`
- Ledger: Attempts 1 · Review rounds 2 · Model spend $0 · Outcome —
- Comments: the evidence and review results above.
- Then run the integration tests:
  ```bash
  make test-integration
  # or only the chat scenarios:
  set -a && . ~/.config/data-agent/env && set +a && uv run pytest -q -m integration tests/acceptance/test_chat_e2e.py
  ```
  a–d and g must each pass all 3 times. A failure needs its trajectory read to decide whether it's this backend, tickets 89–92, or model noise.

I claimed the ticket in the main checkout (`in-progress`, Claimed by, Branch). The smoke-run leftovers are in `/tmp/da93/` and safe to delete.

### Main session, 2026-11-17: real-model end-to-end (`uv run pytest -m integration tests/acceptance/test_chat_e2e.py -v`, at 9090b7e)

- Run 1: "1 failed, 23 passed in 215.31s". The failure was c[3].
  - The answer was "C01: 9 orders; C02: 7 orders; C03: 6 orders". Its assumptions said "C03's 6 is followed by C04's 5, so no tie at the cutoff". The check `C04 not in answer` caught the assumption line.
  - Ticket 92's real run wrote the same kind of line ("next is C04 with 5"), so this check would keep failing on wording.
  - The human decided (2026-11-17) to narrow the check to the listed result, the part before "Assumptions". Commit 7d0eda7 on this branch is the only change to ticket 88's test. Checked by hand on five answer shapes: plain, C04 listed, bold "**Assumptions:**", a "### Assumptions" heading, and C04 with no assumptions part.
- Run 2 (with 7d0eda7; `-k test_c` also matched the module name, so all 24 ran): "1 failed, 23 passed in 252.50s". c passed 3/3. The failure was b-ambiguous[1]: the agent did not ask.
  - In its trajectory it saw the ambiguity ("include refunded? … The difference matters"). It decided "Revenue usually excludes refunded orders", answered 1474.50, and stated that assumption.
  - The chat prompt (`agent.py` `CLARIFICATION_PROMPT`) already asks for a question in this case and is more specific than nao's (`system-prompt.tsx:151`).
  - Across today's real runs of this question the agent asked 8 times out of 9: 93 run 1 3/3, run 2 2/3, ticket 91's three runs 3/3.
  - Judged model variance, not an implementation fault.
- b-ambiguous rerun 3×: "3 passed in 51.57s".
- Every scenario has a 3/3 run: a, d, f, g in run 1; c in run 2 after the narrowed check; b in the rerun.
