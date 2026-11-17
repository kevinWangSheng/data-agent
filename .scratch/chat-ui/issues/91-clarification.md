# 91 — 澄清：提问工具和澄清事件

Status: ready-for-agent
Blocked by: 90
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

对话模式下 agent 有一个提问工具：给一个问题加 2–5 个选项，写一个澄清事件，这一轮随即结束。只在歧义会改变结果时才问，不问就把假设写进答案。不开对话模式时没有这个工具。

## Authority

- May edit: `src/data_agent/agent.py`、`mcp_server.py`、`events.py`、`tool_annotations.py`；`skills/sql-workflow/`（只加什么时候该问、什么时候写假设的通用说明，ADR-0005）；`tests/`（新增单元测试）
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec 第 8、19 条。澄清事件的字段以工单 88 合入的为准。
- 先例：nao `apps/shared/src/tools/clarification.ts:3-24`（工具定义）、`apps/backend/src/agents/stop-conditions.ts:21-24`（调用后这一轮结束）、`apps/frontend/src/components/tool-calls/clarification.tsx:51`（选项作为下一条普通消息发出）；路径相对 `~/dev/AI/data-agent-refs/nao/`。不用 SP 那种挂起运行等回答的状态。
- 评测路径（场景 f）已经能做到"不问、写出假设"，提示词改动不能让它变差。

## Acceptance

- [ ] 对话模式下有提问工具，调用后写澄清事件、这一轮正常结束（不是错误、不是取消）（单元测试）
- [ ] 选项少于 2 个或多于 5 个时工具报错（单元测试）
- [ ] 不开对话模式时工具清单里没有它；评测配置不变一组（`tests/acceptance/test_chat_eval_unchanged.py`）一直通过
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实运行由主会话做（工单 agent 加载不了模型环境）：最终回复写好要跑的命令，主会话跑完把命令和真实输出抄进 Comments。命令：对话模式下问工单 88 夹具库里有歧义的问题，贴出澄清事件；把一个选项作为第二轮发出，贴出答案；再问一个没有歧义的问题，确认不问。场景 f 的真跑（`make test-integration` 里那一条）贴结果。

## Stop conditions

- 工具调用后没法让这一轮干净地结束。
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
