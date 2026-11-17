# 94 — 对话视图模型

Status: in-review
Blocked by: 88
Spec: ../spec.md
Claimed by: claude (opus-5-5)
Branch: worktree-94-chat-view-model

## Goal

`web/src/chat-view/` 的视图模型实现好：输入一段对话的索引加各轮事件文件（可以只是运行中的前缀），输出对话视图。视图模型验收测试转绿。

## Authority

- May edit: `web/src/chat-view/`（验收测试以外的文件）；`web/src/run-view/`（只为复用加导出，不改行为）；新增的 vitest 单元测试
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`；`src/`
- Server access: none
- Spend cap: $0

## Context

- spec 第 5–7 条，"Testing Decisions"对话视图模型一组。类型和函数签名以工单 88 合入的为准。
- 每轮的卡片复用 `web/src/run-view/model.ts` 的 `buildRunView`，不重写。
- 原型 `prototype/chat-ui` 分支（`566e353`）的 `web/src/prototype-chat/parts.tsx` `groupsOf`、`variants.tsx` `steersIn` 可以参考思路，不直接搬代码（spec 第 5 条）。
- 本工单删掉 `TICKET_94` 标记。只用 pnpm。

## Acceptance

- [ ] 对话视图模型验收测试全部通过，`TICKET_94` 标记删掉，测试内容一字未改
- [ ] `pnpm --dir web test`、`pnpm --dir web build` 通过
- [ ] `make check` 通过

## Evidence required

`make check`、`pnpm --dir web test` 的输出。

## Stop conditions

- 复用 `buildRunView` 做不到某条行为，要改运行视图模型的行为。
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

### Attempt 1 (ticket agent, branch `worktree-94-chat-view-model`), final reply as given

I implemented ticket 94 (`buildConversationView` in `web/src/chat-view/model.ts`). All 16 chat-view acceptance tests now pass. The branch is `worktree-94-chat-view-model`, with two commits: `e3a8daf` (implementation) and `7d4102a` (review fixes). I did not push or merge.

Each turn's cards come straight from `buildRunView`; the run view model's behavior is unchanged. The only change in `web/src/run-view/` is that `asString` is now exported, which the Authority line allows for reuse.

Here is what to copy into the ticket:

```
Status: in-review
```

```
## Comments

- Implementation: `buildConversationView` in `web/src/chat-view/model.ts`.
  - Each turn's `run` = `buildRunView(events, notes)`; a missing event file → `[]`.
  - `message` = the index message; for a single run, falls back to `run.summary.task`.
  - Clarification: the turn's last `clarification` event. `chosen` = the next turn's message when it is one of the options.
  - `answersClarification` = whether the previous turn's `chosen` is set.
  - Steers: the index's steers in the order sent, then delivered ones the index does not list.
  - `afterStep` = the last main-agent `tool_start` (no parent, same test as run-view) before `steer_delivered`.
  - `queries`: run.steps order; `database` from the step's last `sql` event.
  - `costUsd`: sum of known costs, else null. `running` = any turn's status is `running`. `canFollowUp` = not a single run.
  - `web/src/run-view/model.ts`: only `asString` is exported; behavior unchanged.
- Acceptance: removed `TICKET_94` (the const plus 16 `TICKET_94(` → `test(`). A grep of the diff shows no other changes in the test file.
- Evidence, after the final commit:
  - `pnpm --dir web test`: `Test Files 14 passed (14) / Tests 241 passed (241)` (baseline: 225 passed | 16 expected fail).
  - `pnpm --dir web build`: `✓ built in 463ms`. `pnpm --dir web typecheck`: no errors.
  - `make check`: exit 0. ruff "All checks passed!", basedpyright "0 errors, 0 warnings, 0 notes", pytest "2309 passed, 1 skipped, 63 deselected, 8 xfailed", vitest 241 passed.
- Real run (step 3): `node --import /tmp/94-demo/hook.mjs /tmp/94-demo/demo.ts <worktree>`, reading the real files of tests/fixtures/chat/conversation:
  - `{id:'20261117T100000Z-c0ffee', singleRun:false, canFollowUp:true, running:false, costUsd:0.023}`
  - turn1 clarification chosen "Net: exclude refunded orders"; turn2 answersClarification:true, queries [{t2-q1, orders}].
  - turn3 steers [{steer-1, delivered:true, seq:7, afterStep:"t3-q1"}], queries orders/customers.
  - turn4 status cancelled; turn5 answer "East 4, North 3, South 2, West 5."
  - Live prefix (turn 3 cut at seq 6): running:true, costUsd:0.01, steer delivered:false/seq:null/afterStep:null, queries [{t3-q1, orders}].
- Review 1, `da-review main` (Codex): "No actionable defects found".
- Review 2, `eng:code-review main` (Standards + Spec): no hard violations, no missing requirements, no scope creep.
  - Fixed (7d4102a):
    - Exported `asString` from run-view instead of duplicating it.
    - The steers parent test is now the same as run-view's (`asString(parent) === null`).
    - The steer literals are merged.
    - `answersClarification` now comes from the previous turn's `chosen`, so the duplicate clarification computation is gone.
    - `Question` → `AskedClarification`, with a doc comment.
  - Not fixed:
    - `queriesOf` scans the `sql` events again for `database`. The run view does not expose the database; adding a field would change the run-view type beyond "export only".
    - When `tool_start` events repeat the same id, `queries` follows `run.steps` one per step, the same as the run view. Today's writer does not produce such files.
    - `running` comes only from each turn's status. There is a gap between the `result` event and the process exiting, during which the backend still treats a message as a steer. Also, a past turn with no entry in `runs` reads as running. The ticket-88 contract says the view model does not read the index's `running`, so this needs handling in the page / data-loading ticket; it is not a view-model bug.
```

```
## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)
```
