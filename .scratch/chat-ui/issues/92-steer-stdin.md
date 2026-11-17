# 92 — 运行中插话：stdin 通道和送达事件

Status: in-review
Blocked by: 90
Spec: ../spec.md
Claimed by: claude-code (opus) 2026-11-17
Branch: worktree-92-steer-stdin

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

### Attempt 1 (ticket agent, branch `worktree-92-steer-stdin`, commits c5419c7, bbc7dec), final reply as given

工单 92 的实现做完了：`make check` 通过，两道审查都跑了。但 Codex 和 Spec 审查都指出一个结束时的竞态，它落在本工单的停止条件里，所以新状态是 `Status: ready-for-human`，不是 `in-review`。分支 `worktree-92-steer-stdin`，两个提交：`c5419c7`（实现）、`bbc7dec`（审查修复）；没有 push。

**卡住的那件事：** 模型已经走完最后一步、但我们还没读到 `ResultMessage` 时写进的插话，会照样记下 `steer_delivered`，随后客户端在第一个 `ResultMessage` 就断开，模型没读到它。Codex 用假客户端复现了这个顺序。
- CLI 对这样一条消息是并进本轮、开第二个 `ResultMessage`、还是丢掉，没有验证过。
- 2026-11-17 探针得出"只出一个 `ResultMessage`"时用的 `receive_response()` 读到第一个 result 就停，所以有第二个它也看不到。这是推断，没有验证。
- SP 的做法是每条插话计一个待处理数、结束后等一个宽限期再继续读下一个 result。如果插话在一轮中途送进只出一个 result，照搬这个做法会把这一轮挂到截止时间。
- 我跑不了真模型，所以没有凭猜测改代码。

**我推荐下一步：** 主会话先跑下面第 2 条的探针，再按结果定：照 SP 做（`replay-user-messages` 回显加 uuid 对账、计数、宽限期），还是接受成已知限制。定下来之后把工单退回给我，我在原分支上接着做。

### 实现了什么
- 只有 `--chat` 时，`data-agent run` 才从 stdin 按行读 `{"id","text"}`。读的是文件描述符 0：一个守护线程直接 `os.read`，所以 stdin 一直开着进程也能退出，这一点有子进程测试。
- 每条消息在活着的 `ClaudeSDKClient` 上调一次 `query(text)`，返回后写 `steer_delivered`（`id`、`text`），事件的 `seq` 就是它送进时的位置。
- 不开对话模式时根本不建这个读取器，评测路径不变。
- 只改了 `agent.py`、`cli.py`，新增 `tests/test_steer_stdin.py`（12 个测试，用假客户端）；`events.py` 不用改。

### 要写进工单的 `## Comments`
- **边界怎么处理：**
  - 坏行（不是 JSON、不是对象、`id` 或 `text` 不是非空字符串）：写一个 `error` 事件，`source="steer"`，内容是 `stdin line N skipped: <原因>`，然后跳过；空行直接忽略。
  - stdin 关闭：只是不再插话，这一轮照常跑完。
  - 收到本轮 `ResultMessage` 之后才到的行：不送出，也不写事件。
  - 某条送不进去（CLI 已经退出）：写 `error`，内容是 `stdin line N not sent: …`，之后不再插话。
  - 跑到截止时间时客户端照样断开：这是自审时发现的问题，已修，有测试。
  - 取消仍按信号杀进程，之后自然不再读 stdin。
- **给工单 93 的已知限制：** 子进程写出 `result` 到退出之间，后端还把对话算成运行中，会对新消息回 202，但子进程已经不读了，这条消息既没送达，也不会开新一轮。
- **`make check`：** ruff 全过、`315 files already formatted`、basedpyright `0 errors`、pytest `2341 passed, 1 skipped, 63 deselected, 8 xfailed`、vitest `241 passed`，退出码 0。`test_chat_eval_unchanged.py` 2 个都过。另外，开工前的基线里 `test_eval_report.py::test_dev_report_has_process_metrics_per_task` 失败过一次，单独重跑通过，后两次完整的 `make check` 也都通过，看着是负载下偶发，和本工单无关。
- **审查：**
  - `da-review main`：只有一条 P2，就是上面那个竞态，没修，原因见上。
  - `eng:code-review` Standards 那边，修了三条：测试复用 `run_steered`、断言改成 `len(...) == 1`、去掉多余的类型注解引号。
  - Standards 那边没修的：
    - 把 stdin 读取挪进单独模块：新模块不在 Authority 允许的范围里。
    - `query` 按 `steering` 分支调用：10 个现有测试替换 `query` 时用的签名就是 `(*, prompt, options)`，单次运行的调用要保持原样。
    - 解析结果用 `str` 表示原因：这个大小下问题不大。
  - Spec 那边：
    - 竞态：同上。
    - 插话没带 uuid：要和对账方案一起定，只发 uuid、不开回显，什么也换不来，而且 CLI 怎么校验 uuid 也没验证过。
    - `error` 事件不在合约里：两个视图模型都不处理 `error` 类型的事件，只留在原始事件文件里。
- **真实运行：** 待主会话跑，命令见下面。

### 主会话要跑的命令
1. 验收要的真实运行（在 worktree 根目录）：
```bash
set -a; . ~/.config/data-agent/env; set +a
R=runs/steer-92-$(date +%H%M%S); mkdir -p "$R"
uv run python tests/fixtures/chat/build_sources.py "$R/db"
C="$R/conv-20261117T000000Z-steer092"; mkdir -p "$C/workspace"; mkfifo "$R/stdin"
uv run data-agent run --task "List every customer_id in the orders data with its number of orders, from most to fewest." \
  --type query --config full --duckdb "$R/db/orders.duckdb" --events-dir "$R/events" \
  --workspace "$C/workspace" --chat --conversation-dir "$C" --turn 1 \
  < "$R/stdin" > "$R/answer.txt" 2> "$R/stderr.txt" & pid=$!
exec 3> "$R/stdin"
until grep -qs '"type": "tool_start"' "$R"/events/*.jsonl; do sleep 0.5; done
printf '%s\n' '{"id": "6f1c2a9e-0b7d-4f3e-9a51-2d8c7e4b1a60", "text": "只要前 3 名"}' >&3
wait $pid; echo "exit=$?"; exec 3>&-
grep -E '"type": "(steer_delivered|answer|result)"' "$R"/events/*.jsonl; cat "$R/answer.txt"
```
   期望：只有一个 `steer_delivered`、只有一个 `result`，答案只列 C01、C02、C03。
2. 竞态探针：用原始的 `ClaudeSDKClient`，问一个不用工具、要写长段文字的问题；在第一个 `AssistantMessage` 开始出来之后再调 `query("末尾加上单词 BANANA")`；然后用 `receive_messages()`（不是 `receive_response()`）读 60 秒。记下：
   - 出了几个 `ResultMessage`，各自的 `num_turns`；
   - 答案里有没有 BANANA；
   - 加上 `extra_args={"replay-user-messages": None}` 时，回显出现的时机。

### `## Ledger`
- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: —

工单文件里我只在认领时改了 `Status`、`Claimed by`、`Branch` 这三行，上面的 Comments 和 Ledger 请主会话抄进工单。

### Main session, 2026-11-17: real run and race probe

- Acceptance run (attempt 1 commands, at bbc7dec): exit 0. One `steer_delivered` (seq 11, id `6f1c2a9e-…`, "Only the top 3, please."), one `result` (seq 29, 6 turns, $0.0031). Answer: "C01 9, C02 7, C03 6".
- Race probe (raw `ClaudeSDKClient`, CLI 2.1.301, DeepSeek; script outside the repo; no tools; read with `receive_messages()` for 60 s; steer "Also end your reply with the single word BANANA." sent right after the first `AssistantMessage`):
  - Without replay: Assistant (0 chars), steer sent at 2.1 s, then Assistant 1651 chars without BANANA. First `Result` at 4.4 s: num_turns 1, no BANANA. Then Assistant 1659 chars with BANANA, and a **second `Result`** at 6.6 s: num_turns 1, BANANA in result.
  - With `extra_args={"replay-user-messages": None}`: the prompt echo came at 1.0 s and the steer was sent at 1.0 s. First `Result` at 3.5 s, no BANANA. The **steer's echo came at 7.0 s, after the first result**. A second `Result` at 8.1 s had BANANA.
  - Conclusion: a steer the model has not taken in by the end of a step is not dropped by the CLI. It runs as the next step and gets its own `ResultMessage`. Stopping at the first result is what loses it. The echo shows when the CLI hands the steer to the model.

退回（主会话定，依据 spec 第 9 条"照 SP"和上面的探针）：照 SP 用回显对账。
- 打开 `replay-user-messages`，每条插话带 uuid（stdin 行的 `id`）。
- 读消息时，只有在所有已送出插话的回显都到了，并且之后又来了一个 `ResultMessage`，才算这一轮结束。不加宽限期：上面的回显顺序可以确定，不靠等待。
- `steer_delivered` 改在收到这条插话的回显时写，事件的 `id` 不变（契约第 6 项）。那时 agent 确实读到了，界面上的"已送达 agent"才属实。
- 一轮出了多个 `ResultMessage` 时，`result` 事件的花费、轮数、`session_id` 要按整轮算对：取最后一个，或累加，按 SDK 的字段语义定，写测试；同时对照 90 的 `restored_sdk_cost_usd`。
- 先用假客户端复现"最后一步后才送进"的顺序，写一个修复前会失败的测试。
- 截止时间和取消的行为不变。
- 修完后照第 3 步写出主会话要重跑的命令：验收运行，加一条在最后一步后送入的插话的真跑。

### Attempt 2（工单 agent，分支 `worktree-92-steer-stdin`，提交 099301e、27f4787、3354fa4）

**怎么修的：**
- 插话以 stream-json user 消息送进，uuid 就是 stdin 行的 `id`；对话模式下打开 `replay-user-messages`。
- 收到这条插话的回显时才写 `steer_delivered`，`id` 不变，`seq` 就是模型读到它的位置。
- 收到一个 `ResultMessage` 时，如果没有插话还在等回显，这一轮就结束；否则接着读。不加宽限期。
- 后面几步各自的 `init` 和提示词回显都不往下传，所以一轮只有一个 `status`、一个 `result`。

**CLI 行为的取证（bundled CLI 2.1.301 对着本地假的 Messages API，不花钱、不碰密钥，脚本在仓库外）：**
- 回显带的是客户端给的 uuid，非 UUID 格式的字符串也原样带回。
- 最后一步之后才送进的插话，CLI 会把它当下一步跑，并给出自己的 `ResultMessage`；这和主会话的探针一致。
- 两条一起送进的插话会并成一步，但每条各有一个回显。
- **uuid 重复的消息被 CLI 丢掉，不回显。** 所以重复 `id` 的行现在会被拒：写 `error`，内容是 `stdin line N skipped: id '…' already sent`。不拒的话，这一轮会一直等一个不会来的回显。
- 各字段在多个 result 之间的语义：

| 字段 | 语义 | 合并时 |
|---|---|---|
| `total_cost_usd`、`model_usage`、`duration_api_ms` | 进程内累计（例：100→200 tokens，0.00045→0.0009） | 取最后一个 |
| `num_turns`、`usage`、`duration_ms` | 每步各算各的 | 累加 |
| `session_id` | 不变 | 取最后一个 |

- 和 90 的 `restored_sdk_cost_usd` 对照过：`minus_restored` 作用在合并后的结果上，因为最后一个 result 本来就是会话累计数；有测试。

**修复前会失败的测试：** `test_a_message_sent_after_the_last_step_is_read_before_the_turn_ends`。在 bbc7dec 上跑，它拿到的答案是第一步的 "all the customers"。

**其他边界：**
- 坏行、空行、stdin 关闭、送不进去：处理和 attempt 1 一样。
- 一轮结束后才到的行：不送出，也不写事件。
- 某一步出错（`is_error` 或 subtype 不是 `success`）：这一轮照样结束。还在等回显的插话各写一个 `error`，内容是 `message '…' was not read: the turn ended`。截止时间或花费上限把这一轮停掉时，也这样写。
- 截止时间和取消的行为不变，相关测试照常通过。

**审查：**
- `da-review main` 两条 P2，都修了：
  1. 插话开出的新一步会拿到 CLI 一份全新的轮数上限，违反 spec 第 13 条。现在各步加起来仍受这一轮的上限约束，规则照 CLI 自己的计法：每个带工具调用的回复，其工具都答完后加一。超了就停，`result` 写 `error_max_turns`、`terminal_reason: "max_turns"`、`num_turns` 为上限加 1，做法和截止时间一样。最后一轮已经用掉时，还在等的插话不再开新一步，记成未读。
  2. 回显到了、但那一步还没出 result，流就正常结束了：以前会把上一步的旧答案当成功；现在写 `error`（source `sdk`），这一次运行算未完成。
- `eng:code-review` Standards（只有需要判断的项，没有硬违规），修了三条：
  - 会改状态的方法按做的事改名：`absorb`、`end_step`、`count_turns`、`turns_used`。
  - 截止时间和轮数上限两处重复的 result 字段，合进 `stopped_by_harness`。
  - `steer()` 里不再中途把消息变量换成跳过原因的字符串。
- Standards 没修的：
  - `agent.py` 太长：Authority 不允许新建模块。
  - `over_limit` 和 `track_open_calls` 结构相似：两者过滤条件和存的值都不同，合并会把截止时间的未答调用清单和轮数计数绑在一起。
  - `steering` 和 `emit` 总是一起传：10 个现有测试替换 `query` 时用的签名是 `(*, prompt, options)`，attempt 1 已说明。
  - 解析结果用 `str` 表示原因：attempt 1 已说明。
  - `steered.max_turns or 0`：只是为了类型检查。
- `eng:code-review` Spec（没有阻塞项），修了一条：截止时间或花费上限停掉时，未读的插话以前不写事件，现在也写 `error`，有测试，修复前失败。
- Spec 没修的，记成已知限制：
  - CLI 如果因为重复 id 以外的原因丢了插话、不回显，而对话运行没有截止时间，这一轮读完 result 后会一直挂着，只能靠取消。spec 说不加宽限期。另一种情况也没验证：恢复的会话里，id 和会话历史里的 uuid 撞上。
  - 第二步起，`TurnBudget` 不再提醒剩几轮。它碰到"没有工具调用的回复后面还有回复"就停止提醒（`_lost`），不会给错数。上限本身由 `count_turns` 执行。
  - 模型没有价格时，轮数上限停下的 `sdk_cost_usd` 为 `None`，和截止时间一样。默认模型 `deepseek-flash` 有价格，所以走的是按价格表计费的分支。
- 修复共两轮，没超过上限。

**`make check`（3354fa4）：** ruff 全过；`315 files already formatted`；basedpyright `0 errors`；pytest `2349 passed, 1 skipped, 63 deselected, 8 xfailed`；vitest `241 passed`；退出码 0。`test_chat_eval_unchanged.py` 2 个都过。`tests/test_steer_stdin.py` 共 19 个。

**不花钱的端到端：** 真跑了 `data-agent run --chat` 加 bundled CLI，对着本地假的 API。插话在唯一一步跑到一半时写进去。结果：退出码 0；只有一个 `steer_delivered`（seq 5，在两段回复之间）；`answer` 来自第二步（"reply 20 to 3 messages"）；只有一个 `result`，`num_turns: 2`。这不能代替真实模型运行。

**已知限制（给工单 93，同 attempt 1）：** 子进程写出 `result` 到退出之间，后端仍把对话算成运行中，这时送来的消息既不会被送达，也不会开新一轮。

### 主会话要跑的命令（在 worktree 根目录）

1. 验收运行：命令和期望都同 attempt 1 的第 1 条（第一次 `tool_start` 后写"只要前 3 名"）。另外确认答案只有 C01–C03，并且 `steer_delivered` 在某个 `tool_end` 之后。
2. 最后一步之后才送入的插话：问一个不用工具的问题，在 `status` 出现后马上写插话，这时模型的唯一一步正在跑。
```bash
set -a; . ~/.config/data-agent/env; set +a
R=runs/steer-92b-$(date +%H%M%S); mkdir -p "$R"
uv run python tests/fixtures/chat/build_sources.py "$R/db"
C="$R/conv-20261117T000000Z-steer92b"; mkdir -p "$C/workspace"; mkfifo "$R/stdin"
uv run data-agent run --task "In one or two sentences, and without querying the data, say what an order is." \
  --type query --config full --duckdb "$R/db/orders.duckdb" --events-dir "$R/events" \
  --workspace "$C/workspace" --chat --conversation-dir "$C" --turn 1 \
  < "$R/stdin" > "$R/answer.txt" 2> "$R/stderr.txt" & pid=$!
exec 3> "$R/stdin"
until grep -qs '"type": "status"' "$R"/events/*.jsonl; do sleep 0.2; done
printf '%s\n' '{"id": "0b9e4c1d-7a2f-4e8b-9c3d-5f6a7b8c9d0e", "text": "Also end your reply with the single word BANANA."}' >&3
wait $pid; echo "exit=$?"; exec 3>&-
grep -E '"type": "(steer_delivered|text|tool_start|answer|result|error)"' "$R"/events/*.jsonl | cut -c1-300; cat "$R/answer.txt"
```
   期望：
   - 退出码 0。
   - 只有一个 `steer_delivered`，在第一个 `text` 之后。
   - 只有一个 `result`，`num_turns` ≥ 2，花费等于最后一个 result 的累计数。
   - 答案以 BANANA 结尾。

   如果模型还是调了工具，并且在同一步里读到了插话（只出一个 result，`num_turns` 照常），那这次没走到"最后一步之后"那条路径，换个更短的问题重跑。

### Main session, 2026-11-17: real run (at 3354fa4)

- A, mid-turn steer ("Only the top 3, please." written after the first `tool_start`): exit 0.
  - Events: two tool calls end (seq 9, 11), then `steer_delivered` (seq 12), more queries, and one `result` (seq 27, num_turns 5, $0.0028).
  - Answer: "C01: 9 orders, C02: 7 orders, C03: 6 orders".
- B, steer written as soon as `status` appeared, on a question that needs no query: exit 0.
  - Events: one tool call (seq 5–7), then `steer_delivered` (seq 8), then text, and one `result` (seq 12, num_turns 2).
  - The answer ends with "BANANA".
  - The model made a tool call first, so the steer was read inside the same step. This run did not take the "after the last step" path. That path is covered by the earlier raw-client probe and by `test_a_message_sent_after_the_last_step_is_read_before_the_turn_ends`.
- Merge: after 91 (PR #102). 92 then rebases onto main and the main session reruns A.
