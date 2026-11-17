# 90 — 多轮续接：ClaudeSDKClient、resume、对话目录

Status: ready-for-agent
Blocked by: 88
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`data-agent run` 在对话模式下能接着上一轮的会话跑：用 `ClaudeSDKClient`，第二轮起按上一轮的 session id `resume`，会话记录写在对话自己的 `CLAUDE_CONFIG_DIR`，各轮共用对话的工作目录；事件文件记下所属对话和第几轮。不开对话模式时行为和现在一样。

## Authority

- May edit: `src/data_agent/agent.py`、`cli.py`、`events.py`、`src/data_agent/verifier_v2/hooks.py`（只为第 18 条的按轮/按对话状态）、`tool_spend.py`；`tests/`（新增单元测试）；`docs/adr/0001-agent-skeleton-sdk-cli.md`（只在 Update 2026-11-17 下补实现时发现的事实）
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec 第 14–18 条，"写工单前先验证"的探针结果（CLI 2.1.301 跨进程 resume 成立；`--session-id` 撞已有 id、`--resume` 遇到没见过的 id 都退出码 1）。契约（参数名、对话目录和索引格式、事件字段）以工单 88 合入的为准。
- 先例：SP `notebook-server/signalpilot/_server/ai/claude_agent_options.py:269-273`（resume）、`claude_agent.py:127-160`（按情况选 flag）、`:355-408`（ClaudeSDKClient）；路径相对 `~/dev/AI/data-agent-refs/SignalPilot/signalpilot/`。
- 现状：一次性 `query()`（`agent.py:1420`）；`sdk_env()` 不设 `CLAUDE_CONFIG_DIR`（`:708-735`）；按运行写死的路径（`:988-1039`、`:1359-1366`）；按运行新建的状态（`:1342-1347`）。第 18 条逐项定按轮还是按对话，定了什么写进最终回复。
- 换调用方式按 ADR-0008 Update 2026-11-04：确定性修改用单元测试加单题真实运行验证，评测行为不能变。
- 后台任务保持关闭（`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`）。

## Acceptance

- [ ] 第二轮的选项带上一轮的 session id、对话的 `CLAUDE_CONFIG_DIR` 和同一工作目录；第一轮不带 `resume`（单元测试）
- [ ] 事件文件记下对话 id 和轮次；不开对话模式时没有这些字段（单元测试）
- [ ] 第 18 条每项状态都有归属，有测试
- [ ] 评测配置不变一组（`tests/acceptance/test_chat_eval_unchanged.py`）一直通过
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实运行由主会话做（工单 agent 加载不了模型环境）：最终回复写好要跑的命令，主会话跑完把命令和真实输出抄进 Comments。命令：同一对话目录连跑两轮 `data-agent run`（第一轮问一个数，第二轮问依赖上文的问题），贴出两轮答案、两轮的 session id、会话记录文件的位置（在对话的配置目录里，不在 `~/.claude`）；再跑一条不开对话模式的单次查数，确认和改之前一样。

## Stop conditions

- resume 后 hooks、验证者或按运行写死的路径出问题，且不改 spec 第 14–18 条就解决不了。
- 不开对话模式时没法保持现在的行为。
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
