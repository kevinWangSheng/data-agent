# 94 — 对话视图模型

Status: ready-for-agent
Blocked by: 88
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
