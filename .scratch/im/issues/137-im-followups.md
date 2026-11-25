# 137 — IM 跟进：`/new` 后线程免 @、Telegram `/start`、同一行的三反引号代码、Discord 异常里的 interaction token

Status: ready-for-agent
Blocked by: —（原 133；133 代码已合入 main `747cdcf`，只差真实验收，不阻塞本工单）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

三处小改，都照 spec "会话映射" Update 2026-11-25 晚（人定"照上游"）和 CommonMark：

1. 在机器人参与过的线程里 `/new` 之后，不 @ 的消息照样回应（Slack、Discord 的线程；Telegram 没有线程，不受影响）。
2. Telegram 的 `/start`（带不带参数都算）当作平台 ping：不交给 agent，不回复。
3. 一行里已闭合的三反引号代码（```` ```SELECT 1``` ````）在公共排版和 Slack 里不再被当成代码块的栅栏，不丢内容。
4. Discord interaction callback 出错（如超时）时，记下的异常和 traceback 里不出现 interaction token。

## Authority

- May edit：
  - `src/data_agent/im/adapter.py`：只为第 1 条。"机器人参与过的线程"和"当前对话"分开记，`/new` 只换对话。持久化的形状由你定（如 `threads.json` 里加一项或另一个文件），旧的 `threads.json` 要能读回。
  - `src/data_agent/im/telegram.py`：只为第 2 条（`to_incoming` 的 `bot_command` 分支，`:306` 起），以及第 3 条里把栅栏判断换成共用的那一份（如果你把它移进 `format.py`）。
  - `src/data_agent/im/format.py`、`src/data_agent/im/slack.py`：只为第 3 条。
  - `tests/test_im_adapter.py`、`tests/test_im_telegram.py`、`tests/test_im_slack.py`、`tests/test_im_discord.py`（第 4 条），新建 `tests/test_im_format.py`（如果还没有，否则在已有的格式测试文件里加）。
  - `src/data_agent/im/discord_bot.py`：只为第 4 条（`RedactTokens`、`REDACTED_LOGGERS` 及其测试所在的 `tests/test_im_discord.py`）。
  - `README.md`：删掉 Discord 一节里 "`/new` 后要再 @ 一次" 的那句（133 合入后才有）；在 Telegram 一节写明 `/start` 不会触发回答。
- Must not edit：`tests/acceptance/`（全部）、`tests/fixtures/` 里已有的文件；`platform.py`、`config.py`、`progress.py`、`service.py`；130 定下的名字、签名、常量；`src/` 里其他文件；`web/`、`evals/`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap：$0（不调模型，不连真实平台）。

## Context

主会话 2026-11-25 按 main `99242cd` 核对，行号以此为准；133 合入后开工时再核一次。

- **第 1 条**：`adapter.py` `handle_message` 里 `in_bots_thread = conversation is not None and thread is not None`；`/new`（`message.new_conversation`）时 `del self.threads[key]`，所以之后线程里不 @ 的消息被丢掉。上游：Hermes 的线程参与集合（`ThreadParticipationTracker`，`gateway/platforms/helpers.py:367`）和会话分开存，reset 不清（`plugins/platforms/discord/adapter.py:2527-2534`）；Chat SDK 订阅按线程 id 存，只有显式 `unsubscribe()` 才去掉（`packages/chat/src/chat.ts:600-615`）；OpenClaw 按线程所有者是不是机器人判断（`extensions/discord/src/monitor/allow-list.ts:400-423`）。130 的契约（`tests/acceptance/test_im_sessions.py:6-10`）"`/new` drops the key's conversation"不变：对话照样丢，丢的不是"参与过"。
- **第 2 条**：现在 `/start` 走 `bot_command` 分支，不是 `/new`，就成了一条文本为 `/start` 的普通私信，允许的用户第一次点 Start 就起一个付费轮次（132 工单 Comments "待人定的点"1）。上游：Hermes 把 `/start` 当平台 ping，记一行日志后不回复（`gateway/run_inbound.py:980-982`、`hermes_cli/commands.py:85-86`）；Chat SDK 把 `bot_command` 走斜杠命令 `processSlashCommand`，不当普通消息（`packages/adapter-telegram/src/index.ts:1397-1430`）。照 Hermes：不交给适配器（`to_incoming` 返回 None 或等价做法），不回复。`/start@别的机器人` 照旧当普通消息处理（和 `/new@别的机器人` 一样）。
- **第 3 条**：`format.py:62` `is_fence` 只看行首是不是 ```。主会话在 main 上跑过：
  - `slack.to_mrkdwn("Total:\n```SELECT SUM(revenue) FROM orders```\ndone")` 返回 `'Total:\n```\ndone'`，SQL 丢了。
  - `format.tables_to_code` 对这种行之后的管道表格不转成等宽块。
  - 132 的审查还跑出 `split_message` 对被拆分的长回答：后面每块重复这一行，除最后一块外都以空代码块结尾（132 工单 Comments，退回第 1 轮）。
  - 132 已在 `telegram.py` 里按 CommonMark 修了自己的转换（`FENCE_LINE`，`:120`；开头 3 个以上反引号、info string 不含反引号；结尾至少同样长、后面没东西）。照它做，最好把栅栏判断收成 `format.py` 里一份，Slack、Telegram、`split_message`、`tables_to_code` 都用它。
- **第 4 条**（133 第 4 轮 `da-review` 的 P2，人 2026-11-25 定放进本工单）：callback 超时时 aiohttp 抛 `ConnectionTimeoutError(f"Connection timeout to host {req.url}")`（`aiohttp/client.py:858-859`），URL 带 interaction token；异常由 `discord.client`（`client.py:582`，`_log.exception`）和 `discord.app_commands.tree`（`tree.py:819`，`exc_info=error`）记下。133 的 `RedactTokens` 只挂在 `REDACTED_LOGGERS` 上、只改 `getMessage()`。要做：这两个 logger 也挂上，并脱敏异常文本（如格式化 `exc_info` 后把脱敏结果写进 `record.exc_text`、清掉 `exc_info`）。行号按 133 合入后的 main 再核。PR #141 上 Codex 把这一条标为 P1（引 AGENTS.md 不在运行产出里写密钥）。
- 停机期间到的消息：spec 定为保持现状，不改。

## Acceptance

- [ ] 第 1 条：Slack 和 Discord 键（`thread_id` 不为 None）的线程里，bare `/new` 和 `/new <问题>` 之后，不 @ 的下一条消息开（或进）新对话并得到回答；新适配器读同一个 runs 目录后仍然如此；旧格式的 `threads.json` 能读回。Telegram（`thread_id` 为 None）的行为不变。
- [ ] 第 2 条：`/start`、`/start payload`、`/start@本机器人` 不产生 `Incoming`（或适配器收到后什么都不做），不调 `conversations.create`/`send`，不发消息；`/start@别的机器人` 照旧。
- [ ] 第 3 条：上面三处 `format.py`/Slack 的例子各有一个修复前会失败的测试；Telegram 已有的栅栏测试照样通过；4 个以上反引号的栅栏、结尾栅栏后的文字照 CommonMark。
- [ ] 第 4 条：一个让 interaction callback 超时（或抛带 token URL 的异常）的测试，在 DEBUG 下断言日志和 traceback 里都没有 token；修复前失败。
- [ ] `tests/acceptance/test_im_*.py` 原样通过；`make check` 通过。

## Evidence required

`make check` 输出；对第 3 条贴出三处例子修复前后的真实返回值；第 1、2 条贴出对应测试名和通过输出。

## Stop conditions

- 第 1 条要改 130 定下的名字、签名、常量，或要改验收测试才能过。
- 两轮审查修复后还有新问题。

## Failure handling

Status `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-25（主会话）：来源是 132、133 交回时的待定点；人定"照上游"，上游取证由子 agent 读 Hermes `4d8ebf8c`、OpenClaw `0b99555d`、Chat SDK `e490e84` 源码，结论写进 spec "会话映射" Update 2026-11-25 晚。Blocked by 133：要删 133 加进 README 的那句，且 133 改 Discord 线程判断。
