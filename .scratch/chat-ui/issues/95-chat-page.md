# 95 — 对话页面（A「对话流」）

Status: ready-for-agent
Blocked by: 93, 94
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
