# 92 — 运行中插话：stdin 通道和送达事件

Status: ready-for-agent
Blocked by: 90
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

对话模式下 `data-agent run` 从 stdin 按行读 JSON 消息，每条在活着的 `ClaudeSDKClient` 上转成一次 `query()`，送进后写一个插话送达事件；不打断当前一轮，模型下一步就能读到。

## Authority

- May edit: `src/data_agent/agent.py`、`cli.py`、`events.py`；`tests/`（新增单元测试）
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec 第 9 条。stdin 消息字段和送达事件字段以工单 88 合入的为准。
- 先例：SP `notebook-server/signalpilot/_server/ai/claude_agent.py:355-408`（同一客户端再调 `query()`，stream-json user 消息带 uuid 对账）；路径相对 `~/dev/AI/data-agent-refs/SignalPilot/signalpilot/`。探针已验证模型在同一轮里读到插话、只出一个 `ResultMessage`。
- 边界：stdin 关闭、一行不是合法 JSON、运行已经结束后才到的消息，都要有确定的处理并写进最终回复。
- 取消仍按现有做法（进程信号）；取消后不再读 stdin。

## Acceptance

- [ ] 一条 stdin 消息对应一次 `query()` 和一个送达事件，事件记下它送进时的位置（单元测试，用假客户端）
- [ ] 坏行、stdin 关闭、运行结束后的消息都不让进程崩溃，处理方式有测试
- [ ] 不开对话模式时不读 stdin；评测配置不变一组（`tests/acceptance/test_chat_eval_unchanged.py`）一直通过
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实运行由主会话做（工单 agent 加载不了模型环境）：最终回复写好要跑的命令，主会话跑完把命令和真实输出抄进 Comments。命令：对话模式下跑一条要查好几步的问题，跑到第一次工具调用后往 stdin 写"只要前 3 名"，贴出送达事件和最终答案（照做了）。

## Stop conditions

- 送进的消息会打断当前一轮或开出第二个 `ResultMessage`，且按 SP 的做法解决不了。
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
