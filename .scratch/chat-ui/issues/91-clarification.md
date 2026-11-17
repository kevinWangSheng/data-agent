# 91 — 澄清：提问工具和澄清事件

Status: in-review
Blocked by: 90
Spec: ../spec.md
Claimed by: claude-code (opus) worktree 91-clarification
Branch: worktree-91-clarification

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

### Attempt 1 (ticket agent, branch `worktree-91-clarification`), final reply as given

Ticket 91 is implemented on branch `worktree-91-clarification`. `make check` passes and both review rounds are done; the real-model run is still owed and falls to you, since this session can't load the model env. Nothing pushed; the ticket file still says `in-progress` and needs the block below copied in.

**What changed**
- **The tool:** in conversation mode (`--chat`) the data MCP server lists `clarification`, which takes a question and 2–5 options. A call with too few or too many options, a blank question, or blank or repeated options is a tool error.
- **The turn ends:** a hook after a successful call writes the `clarification` event and stops the turn. A test run of the real bundled CLI 2.1.301 against a local fake model showed no further model call, a `success` result and no `answer` event, so the turn is not an error or a cancellation.
- **Outside `--chat`:** single runs and evaluations don't get the tool or the extra prompt text.
- **Decision for you — the `sql-workflow` Skill:** I left it unchanged, although the ticket allowed adding guidance there. It goes into every evaluation's system prompt, so editing it would change evaluation behaviour. The when-to-ask guidance is added to conversation turns' system prompt only.
- **Two edits a reviewer flagged:**
  - `cli.py` is outside Authority, so the server learns conversation mode from `DATA_AGENT_CHAT`. Every run now sets it to `""` unless it is a chat turn, so a value exported in the shell can't reach an evaluation.
  - That meant updating one existing test in `tests/test_postgres.py` that pins the exact set of variables passed to the SDK.

---
Copy-in for the ticket (main checkout):

```
Status: in-review

## Comments

- 实现：mcp_server 在对话模式下注册 `clarification(question, options)`（2–5 个、非空、互不相同；否则报 ToolError）。agent.py 只给对话轮加 PostToolUse 钩子（matcher `mcp__data__clarification`），写 `clarification` 事件（tool_use_id、question、options）并返回 `continue: false`；同一轮多个调用只记第一个。对话模式经 SDK 环境变量 `DATA_AGENT_CHAT` 传给 MCP 服务（cli.py 不在 Authority 内）；非对话的运行一律设为 ""，shell 里导出的值进不了评测；它也列为保留的 --postgres-env 名。何时提问的说明只追加到对话轮的系统提示（CLARIFICATION_PROMPT）。
- 没改 skills/sql-workflow：Authority 允许但不要求；该 Skill 进每个评测的系统提示，改了会改变评测行为（ADR-0008、spec 19）。
- 改了一个已有测试 tests/test_postgres.py：它钉死 SDK 环境变量的全集，现在多了 DATA_AGENT_CHAT=""；另把 DATA_AGENT_CHAT 加进保留名的参数表。
- 结束机制的取证（一次性探针，真 CLI 2.1.301 + 本地假模型端点，不调真模型，脚本不进仓库）：
  - 合法调用：钩子返回 continue:false 后不再调模型；ResultMessage subtype=success、is_error=false、terminal_reason=hook_stopped、result=""；没有 answer 事件，退出码 0。
  - 下一轮 --resume 同一 session，历史里带着这次工具调用。
  - 只给 1 个选项：走 PostToolUseFailure（tool_timing failed:true），不写 clarification 事件，agent 接着回答。
  - 不带 --chat 的单次运行：工具清单里没有它（强行调用得到 "No such tool available"）。
- make check（最后一次，3baf99b）：ruff 通过、315 files already formatted、basedpyright 0 errors，pytest 2345 passed / 1 skipped / 8 xfailed，web 241 tests passed，make exit=0。tests/acceptance/test_chat_eval_unchanged.py 2 passed。
- 审查：
  - da-review 两轮：无可执行问题。
  - eng:code-review 第 1 轮修了：同一轮只记一次提问；删掉没人用的 build_server(clarification=) 参数和 chat_mode 包装；测试里 ResultMessage 的重复构造。
  - eng:code-review 第 2 轮：Spec 无新问题；Standards 修了 asked 改成 bool、测试里的变量遮蔽。
  - 不修及理由：
    - CHAT_ENV 放在 tool_annotations.py：Authority 不允许新建模块，放 mcp_server 又会循环导入 classify→agent。
    - hooks 合并循环重复、agent.py 体量大：原有写法，不做顺手重构。
    - 提示词和工具说明有重叠：照 nao，两处都写。
    - "2 to 5" 写在文字里：上下限由工单 88 的契约定死。
    - build_options 里几处 turn is None 判断：轻微。
- 真实运行：待主会话跑（命令在最终回复里），命令和输出抄到这里。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0 (no data-agent model calls; probes used a local fake endpoint)
- Outcome: —
```

---
Real run for the main session, from this branch's checkout. Only `--duckdb` is used, because the `--data-sources` flag belongs to ticket 89:

```bash
set -a; . ~/.config/data-agent/env; set +a
D=runs/t91-real; uv run python tests/fixtures/chat/build_sources.py $D/sources
mkdir -p $D/conv/workspace $D/plain/workspace
# 1) ambiguous question → expect a clarification event, no answer event
uv run data-agent run --task "What was the total revenue from orders placed in 2024?" --type query \
  --workspace $D/conv/workspace --duckdb $D/sources/orders.duckdb --config full \
  --events-dir $D/turn1 --chat --conversation-dir $D/conv --turn 1
grep -h '"type": "clarification"' $D/turn1/*.jsonl
# 2) send one option as turn 2 (expect 2121.75 gross or 1474.50 net)
SESSION=$(uv run python -c "import json,glob;[p]=glob.glob('$D/turn1/*.jsonl');print([json.loads(l) for l in open(p)][-1]['session_id'])")
OPTION=$(uv run python -c "import json,glob;[p]=glob.glob('$D/turn1/*.jsonl');print([e for e in map(json.loads,open(p)) if e['type']=='clarification'][0]['options'][0])")
uv run data-agent run --task "$OPTION" --type query --workspace $D/conv/workspace \
  --duckdb $D/sources/orders.duckdb --config full --events-dir $D/turn2 --chat \
  --conversation-dir $D/conv --turn 2 --resume "$SESSION"
# 3) plain question → expect no clarification, answer 1034
uv run data-agent run --task "What is the order_id of the order with the largest amount?" --type query \
  --workspace $D/plain/workspace --duckdb $D/sources/orders.duckdb --config full \
  --events-dir $D/plain-ev --chat --conversation-dir $D/plain --turn 1
grep -c '"type": "clarification"' $D/plain-ev/*.jsonl
# 4) scenario f (3 runs)
uv run pytest tests/acceptance/test_chat_e2e.py -m integration -k test_f -q
```

Scenario b in `test_chat_e2e.py` stays `xfail` until ticket 93 adds the chat backend; this ticket doesn't flip it.

### Main session, 2026-11-17: real run (attempt 1 commands, at 3baf99b)

- Ambiguous question as a chat turn: exit 0, no `answer` event. The `clarification` event: "Orders have a status of completed or refunded. Which orders should count toward 2024 revenue?", with options "All orders placed in 2024, including refunded (total 2121.75)" / "Only completed orders placed in 2024, excluding refunded (total 1474.50)". `result` subtype `success`, session `dbcbe5e2-…`.
- Turn 2 (`--resume`, option 1 sent as the message): "Total revenue: 2121.75", exit 0.
- Plain question as a chat turn: "order_id: 1034", 0 clarification events.
- Scenario f: `pytest tests/acceptance/test_chat_e2e.py -m integration -k test_f -q` gives "3 passed, 21 deselected".
- Merge: after 89 (conflicts expected in `agent.py`); 91 rebases onto main first.

### Attempt 2 (ticket agent, branch `worktree-91-clarification`): rebase onto main after 89

- 照指示 git fetch 后 rebase 到 origin/main（先 1548da6，main 期间只多了工单文件的提交 8d96f26，又 rebase 一次）；保留原 3 个提交，冲突逐个解决，没推倒重来。注：工单里没找到 "Main session, 2026-11-17: rebase onto main" 下的"退回"那段，按派工指令做。
- 冲突与解决（agent.py、mcp_server.py、tool_annotations.py）：
  - mcp_server：89 把 build_server 拆成 data_server / build_multi_server / register_base_tools。澄清工具改由新函数 register_clarification_tool(server) 注册，在 data_server 里按 CHAT_ENV 调用，所以单源、多源的对话轮都有它；整个对话只有一个工具，没有 `database` 参数。
  - agent.py：system_prompt 跟 89 改成 system_prompt(task, config, chat=False)；build_options 传 chat=turn is not None；evals/dab.py 的调用不传 chat，行为不变。89 把 Postgres 变量检查改成按变量循环，CHAT_ENV 照旧列进保留名。
  - tool_annotations：模块说明合并两边，89 的 database_tools 和本工单的 CHAT_ENV 都保留。
  - tests/test_clarification.py：system_prompt 的调用改为传 Task；新增 test_a_conversation_turn_on_several_sources_serves_one_clarification_tool（两个源的对话轮：query_database 带 database，clarification 只有 question/options，调用成功）。
- make check（最后一次，ec03a1a）：ruff All checks passed、317 files already formatted、basedpyright 0 errors，pytest 2375 passed / 1 skipped / 63 deselected / 8 xfailed，web 241 tests passed，make exit=0。
- 审查（rebase 后一轮）：
  - da-review main：No actionable defects。
  - eng:code-review main：Standards 无硬违规，Spec 无缺失、无错误实现。修了两条（ec03a1a）：「只在对话模式提供澄清工具」那句原先在 build_server、data_server 两处 docstring 里都写了，删掉 build_server 那句；测试改从 tool_annotations 导入 CHAT_ENV。
  - 不修及理由：
    - data_server 从 os.environ 读 CHAT_ENV：照 SNAPSHOT_ENV 的先例（verifier_tools）；cli.py 不在 Authority 内。
    - build_options 里三处 turn is None 判断：上轮已判轻微，不改。
    - 两个 id 都缺时 str(...) 得到 "None"：和 TurnBudget.hooks 写法一致；SDK 总会给 tool_use_id。
    - Spec 提到的范围外行为（拒绝空问题、空或重复选项；一轮只记第一次提问；改了 tests/test_postgres.py）：第 1 次尝试已记录理由。
- 真实运行：rebase 动了代码，需主会话在本分支 checkout 重跑（命令见最终回复），命令和输出抄到这里。

### Main session, 2026-11-17: real run after the rebase (at ec03a1a)

1. Ambiguous question, one source, chat turn: exit 0, no `answer` event. Clarification: "2024 orders include 6 with status "refunded". Which revenue should I total?", with options "All 2024 orders regardless of status" / "Only completed 2024 orders (exclude refunded)". The CLI's stderr had one line, "Unknown child process pid 43775, will report returncode 255", from asyncio's child watcher; the run still exited 0 and its events are complete. Recorded, not chased.
2. Turn 2 (`--resume`, option 1): "Total revenue: 2121.75", exit 0.
3. Plain question: "order_id: 1034", 0 clarifications.
4. Scenario f: "3 passed, 21 deselected".
5. Ambiguous question on two sources (`--data-sources`): four `sql` events on `orders`, then a clarification: "Orders in 2024 total 2,121.75 across 18 orders, of which 6 are refunded (647.25). Which should count as revenue?", with options "Only completed orders (1,474.50)" / "All orders placed in 2024 including refunded (2,121.75)".
