# 90 — 多轮续接：ClaudeSDKClient、resume、对话目录

Status: in-review
Blocked by: 88
Spec: ../spec.md
Claimed by: claude (ticket agent, 2026-11-17)
Branch: worktree-90-multi-turn-resume

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

### Attempt 1 (ticket agent, branch `worktree-90-multi-turn-resume`), final reply as given

Ticket 90 is implemented and committed on `worktree-90-multi-turn-resume` (`29315c4`, then review fixes in `823d171`), and both reviews passed. The two-turn real run hasn't been done yet: ticket agents can't load the model env, so that falls to you, with the commands below. Until it runs, one cost mechanism (point 3) rests only on reading the CLI's code.

**What changed** (`src/data_agent/agent.py`, `cli.py`)
- **Client:** every run now goes through `agent.query()`, which uses a `ClaudeSDKClient`: connect, send the prompt, read up to that turn's result, disconnect. That covers single runs, Harbor, DAB and chat turns.
- **New arguments:** `data-agent run --chat --conversation-dir DIR --turn N [--resume SID]`, as in contract item 5. They build a `ConversationTurn`, which sets:
  - `CLAUDE_CONFIG_DIR` = `DIR/claude-config` (created if it is missing);
  - `resume=SID`;
  - the `task` event's `conversation` and `turn` fields.

  Without `--chat`, none of these is set.
- **Session id:** `status` and `result` now carry `session_id` in every run.
- **Spend:** reading the bundled CLI 2.1.301 shows that on resume it restores the session's last `cost-state` entry. A resumed turn's `total_cost_usd` and `modelUsage` would then include all earlier turns, and the chat view would double-count. Each turn's result now subtracts what was restored, and records it as `restored_sdk_cost_usd`. If the result is smaller than the restored amount, the result is kept as is and an `error` event with `source: "spend"` is written; costs never go negative.

**Spec decision 18, owner of each piece of state**

| State | Owner | Test |
|---|---|---|
| Turn budget | per turn | `test_resumed_turn_gets_the_whole_turn_limit_and_its_own_message` |
| Spend and the spend cap | per turn | `test_resumed_turn_counts_only_its_own_spend`, `test_first_turn_and_single_run_spend_is_the_result_as_given`, `test_result_below_the_restored_state_is_kept_and_reported` |
| Verifier dispatch count and Stop state | never inside a conversation: `--chat` refuses `--type dbt` | `test_cli_rejects_a_turn_out_of_place` (the dbt case) |

Spec decision 16 needs no code: for query turns, the only path the model ever sees is the workspace, and it is the same in every turn. The dictionary and spend-ledger temp dirs reach the tool servers only through their environment.

## Comments (to copy into the ticket)

- **`make check`** (`823d171`):
  - ruff "All checks passed!", "314 files already formatted"; basedpyright "0 errors, 0 warnings, 0 notes".
  - pytest "2330 passed, 1 skipped, 63 deselected, 8 xfailed"; vitest "Tests 225 passed | 16 expected fail (241)".
  - Baseline before the change: 2309 passed.
  - One earlier run hit a full disk (`OSError: could not create numbered dir`, 778 MiB free). The 23 affected files passed on rerun (458 passed), and the full check above came after.
- **Acceptance group:** `tests/acceptance/test_chat_eval_unchanged.py` passes (2 passed). No acceptance test was touched.
- **Existing test fakes adapted:** these fakes now accept the new keyword or the client API; their assertions are unchanged.
  - `tests/test_agent_outcomes.py:685-701` fakes `ClaudeSDKClient` instead of `InternalClient`.
  - `tests/test_run_cli.py:113` and `tests/test_trial_deadline.py:254` accept `turn=`.
- **`da-review main`:** "No actionable regressions". `da-review 29315c4`, run on the fix commit: same result.
- **eng:code-review, fixed in `823d171`:**
  - the guard against negative spend;
  - `Turn` renamed to `ConversationTurn`, because it sat next to `TurnBudget` with a different meaning;
  - `this_turn` renamed to `minus_restored`, and its plain-`if` cleanup;
  - `record_message` reuses `message_session`;
  - the `--resume` help text names `--chat`;
  - a test that a session id cannot reach another record.
- **eng:code-review, not fixed, with reasons:**
  - Moving the session-record code into its own module: a new file is outside Authority.
  - The repeated `turn is None` branches: each site does something different, so methods would only move the conditional.
  - Escaping `--resume` in the glob: a record entry is used only when its `sessionId` equals the id exactly; `test_a_session_id_is_not_a_path` pins this.
- **Needs the real run to confirm** (from reading the CLI, not from a run; recorded in ADR-0001 under Update 2026-11-17):
  - the CLI restores the cost state on resume;
  - it counts `max_turns` from 1 again for each prompt;
  - stopping at the first result reads the same messages as the old one-shot `query()`.

**Real run for the main session** (in this worktree, model env loaded):

```bash
D=runs/t90-real; C=$D/conversations/conv-t90; mkdir -p $C/workspace $D/single-ws
uv run python tests/fixtures/chat/build_sources.py $D/db
uv run data-agent run --chat --conversation-dir $C --turn 1 --type query --config full \
  --duckdb $D/db/orders.duckdb --workspace $C/workspace --events-dir $D/turn1 \
  --task "How many orders were refunded?"
SID=$(jq -r 'select(.type=="status").session_id' $D/turn1/run-*.jsonl); echo $SID
uv run data-agent run --chat --conversation-dir $C --turn 2 --resume "$SID" --type query --config full \
  --duckdb $D/db/orders.duckdb --workspace $C/workspace --events-dir $D/turn2 \
  --task "How many of those were placed in 2024?"
jq -c 'select(.type=="status" or .type=="result" or .type=="error") | {type,session_id,num_turns,sdk_cost_usd,cost_usd,restored_sdk_cost_usd,source}' $D/turn*/run-*.jsonl
ls $C/claude-config/projects/*/$SID.jsonl; ls ~/.claude/projects/*/$SID.jsonl 2>&1
uv run data-agent run --type query --config full --duckdb $D/db/orders.duckdb \
  --workspace $D/single-ws --events-dir $D/single --task "How many orders were refunded?"
```

What a pass looks like:
- Answers: 11, then 6 (ticket 88's fixture).
- Both turns have the same `session_id`.
- The session record is under `$C/claude-config`, and the `~/.claude` lookup finds nothing.
- Turn 2's `restored_sdk_cost_usd` is close to turn 1's `sdk_cost_usd`, with no spend `error` event.
- Turn 2's `num_turns` is small, not cumulative.
- The single run answers 11, exits 0, and its `task` event has no `conversation` or `turn`.

## Ledger
- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: —

`Status: in-review`

### Main session, 2026-11-17: real run (commands as in the reply above, at 823d171)

- Turn 1: "refunded_orders: 11", exit 0. Turn 2 (`--resume 19dc799a-…`): "refunded_orders_2024: 6", exit 0; its assumption says "those" means the refunded orders from the previous answer.
- Both turns: the same `session_id`. `task` events carry `conversation: conv-t90`, `turn: 1` / `2`.
- The session record is at `<conv>/claude-config/projects/<workspace>/19dc799a-….jsonl`. `~/.claude/projects/*/19dc799a-….jsonl`: no such file.
- Spend: turn 1 `sdk_cost_usd` 0.053602; turn 2 `restored_sdk_cost_usd` 0.053602 and `sdk_cost_usd` 0.028444 (its own); no `error` event. `num_turns` 4 and 4 (not cumulative). This confirms the three points that rested on reading the CLI.
- Single run: "11 orders were refunded.", exit 0; `task` has no `conversation` / `turn`; `result` has a `session_id`.
- Model cost (`cost_usd`): $0.0026 + $0.0009 + $0.0034.
