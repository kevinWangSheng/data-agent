# 132 — IM 入口：Telegram 实现（aiogram，长轮询）

Status: ready-for-agent
Blocked by: 131（done）
Spec: ../spec.md
Claimed by: claude (worktree 132-im-telegram)
Branch: worktree-132-im-telegram

## Goal

`data-agent web --im <配置>` 的配置里写了 `[telegram]` 时，在网页进程里起 Telegram 机器人（aiogram 3.34.0，长轮询，不需要公网地址）。私聊或在群里 @ 它提问，它在这个聊天里回复。和网页对话、Slack 共用同一套对话、运行目录、事件文件。

公共部分（`Adapter`、配置、排版、进度、`service`）131 已经做完。本工单只加平台实现，以及 `service` 里启用它的两处小改。README 和 `docs/setup.md` 写明建 bot 的步骤。真实 Telegram 的验收由人建好 bot、主会话来跑（见 Evidence required），不阻塞本工单交回。

## Authority

- May edit：
  - `src/data_agent/im/telegram.py`（新建）：Telegram 平台实现，满足 `platform.py` 的 `Platform` 协议。
  - `src/data_agent/im/service.py`：只改两处。`LIBRARIES` 加 `"telegram"` 和它要的库（`:34`）；`make_platform`（`:63-67`）按 `config.platform` 选实现。其他行为不改。
  - `pyproject.toml`、`uv.lock`：`im` extra 和 dev 组各加 `aiogram==3.34.0`，照 131 加 slack-bolt 的做法（`pyproject.toml:24-31`、`:36-55`）；`uv.lock` 只能有本工单的包变化。
  - `tests/`：新建 `tests/test_im_telegram.py`。`tests/fixtures/im/`：可以新建 Telegram 的更新样例（如 `telegram_updates.json`），只放假 id，不放 token。`tests/test_im_service.py`：只改 `test_a_platform_not_implemented_yet_is_refused_by_name`（`:89-98`），见 Context "和 133 并行"。
  - `README.md`：在 "Slack" 一节（`:116` 起）后面加 "Telegram" 一节，或者把那一节扩成多平台。`docs/setup.md`：在 "Slack 机器人（工单 131）"（`:189` 起）后面加 "Telegram 机器人（工单 132）" 一节。
- Must not edit：
  - `src/data_agent/im/` 里的 `platform.py`、`adapter.py`、`config.py`、`format.py`、`progress.py`、`slack.py`，以及 130 定下的名字、签名、常量。公共部分有缺口时停下，见 Stop conditions。
  - `tests/acceptance/`（全部），`tests/fixtures/` 里已有的文件，`tests/test_im_slack.py`、`tests/test_im_adapter.py`。
  - `src/data_agent/web/`、`cli.py`、`agent.py`、`events.py`，以及 `src/` 里上面没列的文件。`web/`、`evals/`、`Makefile`、`.github/workflows/`、`.env*`、`skills/`、`configs/`。
  - 133（Discord）的文件，`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap：工单 agent $0（不调模型，不连真实 Telegram）。主会话的真实验收上限 $2，属于默认放行的小额运行。

## Context

代码现状按 main `8589823` 核对，行号以本节为准。

- **spec**：`.scratch/im/spec.md`。
  - "会话映射"里 Telegram 那条和它的 Update 2026-11-25（不支持论坛话题）。
  - "群里要 @"的两条 Update 2026-11-25：怎样算 @，以及隐私模式。
  - "澄清、进度、取消"（Telegram 编辑间隔约 1 秒）、"答案排版"、"配置"、"出错"、"库"、"验收"。
- **130/131 的契约（已定，不再问）**：
  - `src/data_agent/im/platform.py`：模块 docstring `:15-26` 写了平台实现负责的事，Telegram 相关的有三条：
    - `thread_id` 为 None；
    - `/new` 交给适配器时是一个 `Incoming`，`new_conversation=True`，命令后面剩下的文字放在 `text` 里，单独一个 `/new` 时 `text` 为空；
    - 用户 id 一律转成字符串。
  - `Platform` 协议 `:105-128`：`start` 连上就返回，之后依次 await 每个回调；另有 `stop`、`send -> id`、`edit`、`typing`。
  - 适配器的规则在 `adapter.py:1-72`，触发判断在 `handle_message`（`:232-240`）。没有线程的群里，每次都要 @（`:27-29`）。
  - `Capabilities.typing` 设为 False：适配器现在不调 `typing`（`adapter.py` 里没有调用）。
- **照 Slack 实现的形状做**：`src/data_agent/im/slack.py`。
  - 模块 docstring 写行为；`LIMITS` 常量；用一把锁按顺序把消息交给适配器（`:20-21`）；丢掉重复投递；发送遇到限流时等 `retry_after` 再重试一次（`:246` `retry_after`，131 Comments 第 1 轮）。
  - 测试照 `tests/test_im_slack.py`：用假的客户端记录调用，不连网。
- **aiogram 3.34.0（读的是 wheel 源码，2026-11-25）**：
  - PyPI：2026-11-22 发布，MIT，`requires_python >=3.11,<3.16`。`requires_dist` 有 `aiohttp>=3.9.0,<3.15`、`pydantic>=2.8.0,<2.15`、`aiofiles>=23.2.1,<26.0`、`magic-filter>=1.0.12,<1.1`、`certifi`、`typing-extensions<=5.0`。lock 里现在是 aiohttp 3.14.5、pydantic 2.14.1，都在范围内。aiofiles、magic-filter 是新加的包。
  - 构造：`Bot(token, ...)`（`aiogram/client/bot.py:299-305`）。token 显式传入，aiogram 不读环境变量。
  - 连上：`await bot.me()`（`bot.py:394`，就是缓存过的 `get_me`）。token 错时它会报错，用它当连上的判断，同 Slack 的 `auth.test`。它也给出机器人的 `username` 和 `id`。
  - 轮询：`Dispatcher.start_polling`（`aiogram/dispatcher/dispatcher.py:631`）会一直跑到停止为止，所以放进后台任务。
    - 必须传 `handle_signals=False`：默认会对 SIGTERM/SIGINT 调 `loop.add_signal_handler`（`:686-701`），盖掉 uvicorn 的信号处理。
    - 必须传 `handle_as_tasks=False`：默认每条更新各开一个 task 并发处理（`:496-505`）；传了之后依次 await（`:508`），满足协议的"依次"。
    - `close_bot_session` 默认 True（`:639`），按停止顺序决定传不传。
  - 停止：`await dp.stop_polling()`（`:594`）。轮询没在跑时它抛 `RuntimeError("Polling is not started")`，`stop` 要能容忍这种情况；还要关掉 bot 的 session。
- **Telegram 平台事实（core.telegram.org/bots/api、/bots/features，2026-11-25 读）**：
  - `sendMessage`、`editMessageText` 的 `text` 是 "1-4096 characters after entities parsing"。
  - `callback_data` 是 1-64 字节；按下按钮后要调 `answerCallbackQuery`，否则客户端一直转圈。
  - 限流时返回的 `retry_after` 是要等的秒数。faq 写单个聊天每秒不超过 1 条，群里每分钟不超过 20 条。
  - MarkdownV2 有一组字符必须转义；HTML 模式只需要转义 `<`、`>`、`&`。
  - 隐私模式（/bots/features#privacy-mode）的原文："By default, all bots added to groups run in Privacy Mode and only see relevant messages and commands: Commands explicitly meant for them (e.g., /command@this_bot)… Replies to any messages implicitly or explicitly meant for this bot." 没写普通的 @ 能不能收到。
  - MessageEntity：`mention` 是 `@username`，`text_mention` 用于没有用户名的用户，`bot_command` 形如 `/start@jobs_bot`。offset 和 length 按 UTF-16 码元计。
  - `Chat.type` 取值有 `private`、`group`、`supergroup`、`channel`；`Chat.id`、`User.id` 都是整数。
- **上游做法（人 2026-11-25 确认照多数）**：Hermes `f85f33bf`、OpenClaw `acb3939a8`、Chat SDK `e490e84`。只参考设计，不复制代码（spec "Further Notes"）。
  - 判断 @：Chat SDK `packages/adapter-telegram/src/index.ts:3862-3885`；Hermes `plugins/platforms/telegram/telegram_addressing.py:47-127`。
  - 回复机器人的消息算 @：Hermes、OpenClaw。回复论坛话题创建消息的不算：Chat SDK `:3830-3850`。
  - 排版用 MarkdownV2，Telegram 报 "can't parse entities" 时改用纯文本重发：Hermes `adapter.py:3207-3222`，Chat SDK `:4990-5010`。
  - "message is not modified" 当成功：Hermes `adapter.py:1417`，OpenClaw。
  - `setMyCommands` 注册 `/new`：Hermes `adapter.py:2498-2509`，OpenClaw `bot-native-command-menu.ts:317-336`。
  - 收到回调就 `answerCallbackQuery`：OpenClaw `bot-handlers.callback-router.ts:110-124`，Chat SDK `:1534`。
- **和 133 并行（Discord，同时派工）**：
  - 两张工单都改 `service.py` 的 `LIBRARIES`/`make_platform`、`pyproject.toml`/`uv.lock`、README、`docs/setup.md`。后合入的那张由主会话先把 main 合进来、解冲突（`uv.lock` 用 `uv lock` 重新生成）。
  - `tests/test_im_service.py:89-98` 现在用 `[telegram]` 测"未实现的平台按名字拒绝"。本工单把这个用例改成用 `[discord]`，断言不变（"discord" 在报错里，"not implemented" 在报错里）。
  - 两张都合入后，这个用例和 `service.start` 里"not implemented yet"的分支（`service.py:46-48`）就走不到了，由后合入的那张一起删掉。如果你开工时 main 上已经有 Discord 实现，就直接删。

## 主会话 2026-11-25 定的点（实现时照做，不是停下的理由）

1. **怎样算 @**：在群里，满足下面任一条就算 `mentioned=True`：
   - 有 `mention` 实体，文字等于 `@<机器人用户名>`（比较时不分大小写）；
   - 有 `text_mention` 实体，指向机器人的 id；
   - 有 `bot_command` 实体，以 `@<机器人用户名>` 结尾；
   - `reply_to_message` 是机器人发的消息，但回复论坛话题创建消息的不算。

   `text` 里去掉 `@<机器人用户名>`。私聊时 `direct=True`（`chat.type == "private"`）。
2. **`/new`**：消息开头的 `bot_command` 是 `/new` 或 `/new@<机器人用户名>` 时，交给适配器的 `Incoming` 是 `new_conversation=True`，`text` 取命令后面剩下的文字。带别的机器人用户名的 `/new@other_bot` 不算。启动时用 `setMyCommands` 注册 `/new`，附一句英文说明。`stop` 照公共部分走文字命令，不注册成命令。
3. **会话键**：`chat_id = str(chat.id)`，`thread_id = None`，`user_id = str(from_user.id)`。论坛话题不支持（spec Update）：带 `message_thread_id` 的消息照普通群消息处理，回复也不带 `message_thread_id`。这一条写进 README 的已知限制。
4. **排版**：`send`/`edit` 把公共 Markdown 转成 MarkdownV2。手写一个小转换，照 `slack.py` 的 `to_mrkdwn`，覆盖粗体、斜体、链接、标题、代码块、行内代码和必须转义的字符，不加依赖。Telegram 返回 "can't parse entities" 时，改用不带 `parse_mode` 的纯文本重发一次。
5. **`Limits`**：`max_chars` 要给 MarkdownV2 的转义留余量。4096 是 "after entities parsing" 的长度，具体取多少照 Slack 的 2900/3000 那样算出来，并在常量旁写明理由。`edit_interval` 取 1.0 秒（spec "进度"）。
6. **按钮**：用 InlineKeyboardMarkup，每个按钮一个，`callback_data` 直接用 `Button.value`（契约已限 ≤64 字节 ASCII）。收到 callback query 先 `answerCallbackQuery`，再经锁交给适配器。`Press` 的几个字段：
   - `message_id` 是按钮所在消息的 `message_id`，转成字符串，和 `send` 返回的 id 一致；
   - `direct` 按 `chat.type` 判断；
   - `thread_id` 为 None。
7. **编辑**：Telegram 返回 "message is not modified" 时当成功。
8. **限流**：编辑遇到限流时抛出来，由适配器跳过这次（spec "进度"）；发送遇到限流时，等 `retry_after`（最多 30 秒，同 Slack 的 `RATE_LIMIT_WAIT`）再重试一次。
9. **重复与无关更新**：只处理有 `text` 的消息和 callback query。编辑过的消息、频道帖子、加入或离开等服务消息，以及机器人自己和其他机器人（`from_user.is_bot`）发的消息，都不交给适配器。
10. **`start`/`stop`**：
    - `start` 先 `bot.me()`（token 错就抛出，由 `service.Bot.serve` 写日志）、`setMyCommands`，再把 `start_polling(..., handle_signals=False, handle_as_tasks=False)` 放进后台任务，然后返回。
    - `stop` 停轮询，轮询没在跑时也不报错，并关掉 session。
11. **token 不外泄**：日志和报错里不能出现 token。aiogram 的请求 URL 里带 token（`/bot<token>/method`），要确认 aiogram 和 aiohttp 在 DEBUG 级别的日志里不会打出来；会打的话，在本模块里给相关 logger 加过滤，并写一个测试。
12. **依赖**：`im` extra 和 dev 组都钉 `aiogram==3.34.0`。`uv export --frozen --no-dev --no-emit-project` 的输出里不能有 aiogram、aiofiles、magic-filter。如果加了它会迫使 lock 里已有的包换版本，就停下（见 Stop conditions）。

## 测试没钉、本工单要做的

都写进 `tests/test_im_telegram.py`，用 aiogram 的类型或手写的更新 JSON 样例，假 id、假 token，不连网：
- 更新到 `Incoming` 的转换：
  - 私聊：`direct` 为真；
  - 群里有 `@用户名`：`mentioned` 为真，`text` 去掉了 @；
  - 有 `text_mention`；
  - 回复机器人的消息：`mentioned` 为真；
  - 回复论坛话题创建消息：`mentioned` 不为真；
  - 普通群消息：交给适配器，`mentioned` 为假；
  - 机器人发的消息和服务消息：不交给适配器；
  - `/new`、`/new 问题`、`/new@机器人`、`/new@other_bot`：前三个 `new_conversation` 为真且 `text` 对，最后一个不是 `/new`；
  - `chat_id`、`user_id` 是字符串，`thread_id` 是 None。
- callback query 到 `Press` 的转换，并且先调了 `answerCallbackQuery`。
- `Outgoing` 到 `sendMessage`/`editMessageText` 的参数：`parse_mode`、按钮的键盘；不带按钮的编辑会去掉键盘。
- Markdown 转 MarkdownV2：每类元素一个用例，转义字符一个用例；解析失败时改用纯文本重发。
- 限制：每条消息转换后都不超过 4096；`edit_interval` 是 1.0。
- 发送遇到限流时等待再重试一次（`sleep` 可以替换，测试里不真等）；编辑遇到限流时抛出；"not modified" 当成功。
- 依次交付：两个更新同时到达时，第一个处理完才处理第二个。
- `start` 传了 `handle_signals=False`、`handle_as_tasks=False`；token 错时 `start` 抛错；`stop` 在没开始轮询时也不报错。
- token 不出现在本模块、aiogram、aiohttp 的 DEBUG 日志里。
- `service`：配置里有 `[telegram]` 时 `make_platform` 返回 Telegram 实现；没装 aiogram 时报错，并提示 `uv sync --extra im`（照 `tests/test_im_service.py` 已有的缺库用例）。
- README 的 "Telegram" 一节写这些：用法、TOML 示例（只写变量名 `DATA_AGENT_TELEGRAM_BOT_TOKEN`）、允许名单（数字 id）、`/new`、群里要 @ 或回复机器人；已知限制写三条：不支持论坛话题、群里关掉隐私模式后机器人会收到所有消息、没有对话总花费上限。
- `docs/setup.md` 的 "Telegram 机器人（工单 132）" 一节写这些步骤：
  1. 找 @BotFather 发 `/newbot`，拿到 token，写进 `~/.config/data-agent/env`；
  2. 在 BotFather 里关掉 Group Privacy（Bot Settings → Group Privacy → Turn off），或者把机器人设为管理员；
  3. 已经在群里的机器人，要移出群再重新拉回；
  4. 查出自己的数字用户 id，填进名单：私聊机器人一次，它的"未授权"回复里就有。

  每一步附官方文档链接和访问日期，没核实的标 `未确认`；不写任何真实 token、用户名、id。

## Acceptance

- [ ] "测试没钉、本工单要做的"每条都做了，能单测的都有新测试。
- [ ] `uv run pytest tests/acceptance/test_im_*.py -q` 全部通过，和 main 一样；本工单不碰这些测试。
- [ ] `pyproject.toml` 的 `im` extra 和 dev 组里有 `aiogram==3.34.0`；`uv sync --extra im` 能装上；`uv export --frozen --no-dev --no-emit-project | grep -iE 'aiogram|aiofiles|magic-filter'` 输出为空；`uv.lock` 里已有包的版本没变。
- [ ] CI 的 `uv sync --locked` 环境下 `make check` 能过。
- [ ] `git diff main --stat` 只有 Authority 里列的文件；`src/data_agent/im/service.py` 只有 `LIBRARIES` 和 `make_platform` 的改动；`tests/test_im_service.py` 只改了那一个用例。
- [ ] README 和 `docs/setup.md` 写好；没有密钥、真实 id、服务器地址；pre-commit 的扫描通过。
- [ ] `make check` 通过。
- [ ] 主会话做（不阻塞本工单交回）：人建好 bot 后的真实验收，spec "验收"逐条做到（见 Evidence required）。

## Evidence required

工单 agent 贴进最终回复的 Comments（$0，不连真实 Telegram，不调模型）：
- `make check` 的输出（用 `tee` 存文件）。
- `uv export --frozen --no-dev --no-emit-project | grep -iE 'aiogram|aiofiles|magic-filter'`（应为空）；`git diff main -- uv.lock | grep '^[-+]version'`，证明已有包的版本没变。
- 不连 Telegram 的真实运行：
  - 装了 extra、token 用假值（运行时拼出来，不写进文件）：`data-agent web --im <只含 [telegram] 的文件>` 起来后，适配器连不上 Telegram，只写日志（贴出错误类型，不含 token 值）；网页照常服务（`curl -s http://127.0.0.1:<port>/api/options` 返回 200）；Ctrl-C 后进程正常退出，没有遗留信号处理带来的异常。
  - 没装 extra：贴出提示 `uv sync --extra im` 的报错，并照实写用了什么办法得到不含 extra 的环境。
- 写好交给主会话的真实验收步骤（见下），确认命令、路径、worktree 名。

交给主会话和人（工单 agent 不做也不等，**没有 bot 和 token 不是停下的理由**）：

1. 人：
   - 照 `docs/setup.md` 新加的一节建 bot，把 `DATA_AGENT_TELEGRAM_BOT_TOKEN` 写进 `~/.config/data-agent/env`；
   - 在 BotFather 里关掉 Group Privacy，建一个测试群并把机器人拉进去；
   - 准备一个放在仓库外的 `im.toml`，`allowed_users` 填自己的数字 id。
2. 主会话在本工单的 worktree 里：`uv sync --extra im`，确认网页已构建（`pnpm --dir web build:local`），然后
   ```bash
   mkdir -p runs/132-live && set -a && . ~/.config/data-agent/env && set +a && \
     uv run data-agent web --runs-dir runs/132-live --data-sources <仓库外的 sources.toml> --im <仓库外的 im.toml> 2>&1 | tee runs/132-live/web.log
   ```
3. 人在 Telegram 里做 spec "验收"的每一条：
   - 私聊问一个需要澄清的问题，点按钮回答，拿到答案；
   - 追问一轮，进同一个对话；`/new` 之后再问，进一个新对话；
   - 运行中插一句话；点"停止"取消一次；
   - 群里 @ 机器人问一次，再回复机器人的消息追问一次；群里不 @ 的消息，机器人不回应；
   - 名单外：照 131 的先例，临时把 `allowed_users` 换成一个假 id，重启后私聊收到"未授权"和自己的 id，群里没有回应；之后改回名单。
4. 主会话：
   - 在网页对话列表里看到这几次对话，能打开回放；
   - 在运行目录和 `web.log` 里 `grep` token 值，确认没有；
   - 从事件文件读这几轮的花费；
   - 把命令、结果、花费抄进 Comments。不跑分。失败就写"退回：原因"交回本工单。

## Stop conditions

- 要改公共部分（`adapter.py`、`platform.py`、`config.py`、`format.py`、`progress.py`）或 130 定下的名字、签名、常量才能做到。
- 要改 `tests/acceptance/` 的任何东西。
- 加 aiogram 会迫使 lock 里已有的包换版本，或者 aiogram 及其依赖进了 `uv export --no-dev`。
- `make check` 在 CI 的 `uv sync --locked` 环境下过不了，又不能在 `pyproject.toml` 的范围内解决。
- spec 或本工单 Context 写的代码现状、库的行为和实际对不上。
- 第二轮审查修复后仍不过。

没有真实 bot 和 token、不能真跑 Telegram，**不是**停下的理由。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: 已实现，两道审查通过；真实 Telegram 验收待人建 bot、主会话跑
- Human review minutes: —

## Comments

- 2026-11-25（对齐会话）：
  - 依据：上游做法由人确认照多数（spec 的 Update 2026-11-25）；aiogram 3.34.0 的接口和 Telegram 平台事实由子 agent 读 wheel 源码和官方文档核实，见 Context。
  - 与 133 并行派工，冲突处理见 Context "和 133 并行"。
- 2026-11-25（工单 agent 最终回复，主会话照抄；分支 `worktree-132-im-telegram`，HEAD `5c5eb44`）：

**实现**：新建 `src/data_agent/im/telegram.py`；`service.py` 只改 `LIBRARIES` 和 `make_platform`；`im` extra 和 dev 组钉 `aiogram==3.34.0`；新建 `tests/test_im_telegram.py`（68 个测试）和 `tests/fixtures/im/telegram_updates.json`；`test_im_service.py` 只改"未实现平台"那一例，改用 `[discord]`、变量在测试里设，断言不变；README 和 `docs/setup.md` 加 Telegram 一节。`git diff main --stat` 只有 Authority 列出的文件。

**检查**
- `make check`（`5c5eb44`，`runs/132/make-check-3.log`）：ruff "All checks passed!"，basedpyright "0 errors, 0 warnings, 0 notes"，pytest "3316 passed, 3 skipped, 79 deselected"，web "Tests 448 passed (448)"，exit 0。`uv sync --locked`："Audited 163 packages"。IM 验收测试原样通过。
- 依赖：`uv export --frozen --no-dev --no-emit-project | grep -iE 'aiogram|aiofiles|magic-filter'` 无输出；`git diff main -- uv.lock | grep '^[-+]version'` 只新增 aiofiles 25.1.0、aiogram 3.34.0、magic-filter 1.0.12，已有包版本不变、无删除。
- 装 extra、假 token（运行时拼出，不落文件；dev venv 和 `uv sync --frozen --no-dev --extra im` 的独立 venv 各跑一次），`--im` 只含 `[telegram]`：日志 `telegram: the bot could not connect; the web UI goes on without it` / `aiogram.exceptions.TelegramUnauthorizedError: Telegram server says - Unauthorized`；`/api/options` 在机器人失败前后都 200；SIGINT 后 exit 0，无信号处理异常；日志里 grep 不到 token。局限：`getMe` 带假 token 真的连到 api.telegram.org 拿到 401；轮询没开始，所以 SIGINT 没覆盖活的轮询。
- 不装 extra（`uv sync --frozen --no-dev` 的独立 venv）：`data-agent web: error: --im: the telegram bot needs the IM libraries (aiogram is not installed); run \`uv sync --extra im\` (or install \`data-agent[im]\`)`，exit 2。

**审查**（每轮一次 da-review 加一次 `eng:code-review`，各跟一轮修复）
- 第 1 轮 Codex P2：`text_mention` 留在 `text` 里，`<mention> stop` 停不了轮次。已修，配测试。
- 第 1 轮 `eng:code-review` 已修：标题里的粗体把标题的粗体关掉；send 和 edit 共用一套 MarkdownV2 失败转纯文本；改名 `connected`→`require_bot`、`post`→`send_once`、`bold_`→`bold_underscores`；README 写明群里要 `/new@bot`；删掉重复投递检查（aiogram 处理完每条更新就把 `getUpdates` offset 移过去，`aiogram/dispatcher/dispatcher.py:319`，单进程不会收到两次），模块 docstring 写明。
- 第 2 轮 Codex 无意见；`eng:code-review` 找出第 1 轮修复带来的 bug：标题丢掉字面的 `__` 和 `**`。已修：只去掉代码外真正的粗体标记，词内 `__`（`stg_shop__orders`）不当粗体。由此引起的长串下划线深递归也修了，最坏长度测试加了 `_ * ~ \``。Codex 复查最终 HEAD："No actionable regressions"。
- 不修及理由：锁保留（工单要求逐条交给适配器）；`MAX_TEXT=4096` 保留为文档上限，测试对它核对；`make_platform` 的 if/else 正是 Authority 允许的；`/help@bot` 等非 `/new` 命令不从 `text` 剪掉，adapter 没有别的命令，无害；测试夹具经 JSON 往返复制，属风格小节。

**`max_chars=4000` 的理由**（定的点 5）：Telegram 上限是 "1-4096 characters after entities parsing"，MarkdownV2 的转义和标记不算，所以转换后的消息显示字符数不超过原 Markdown；留 96 给 emoji 按两个 UTF-16 码元计的情况（未确认）。原始 MarkdownV2 可能超过 4096，需真实验收第 3 条核一次。

**待人定的点**（已确认行为，未定）
1. `/start`：Telegram 的 Start 按钮发 `/start`，作为普通私信、文本 `/start` 到达（对照 `to_incoming` 核过），所以允许的用户第一次点就会起一个付费轮次。
2. 启动时补投：机器人停机期间来的更新，启动时会交给适配器。

**真实验收补充项**（在 `.claude/worktrees/132-im-telegram` 按 Evidence required 第 1–4 步做，另加）
1. 机器人连上时 Ctrl-C 正常退出（exit 0，无 traceback）。
2. 最后一次状态编辑后 Stop 按钮消失（发空键盘去掉按钮离线无法证明）。
3. 一条接近 4000 字、标点多、带 emoji 的回答能送达；Telegram 嫌长就调低 `max_chars`。
4. `grep "MarkdownV2 was refused" runs/132-live/web.log`：有命中就是转换器 bug 被纯文本兜底掩盖了。
5. 机器人的 `text_mention` 在群里可用，`<mention> stop` 也可用。
- 2026-11-25（主会话，PR #139 第 1 轮 Codex 意见）：
  - 退回：`to_markdown_v2` 把整行的三反引号行内代码当成代码块的开头栅栏（`src/data_agent/im/telegram.py:371-376`：`is_fence` 只看行首是否为 ```，后面的"语言"不匹配 `LANGUAGE` 就丢掉）。主会话核实：`to_markdown_v2("Total:\n```SELECT SUM(revenue) FROM orders```\ndone")` 返回 `'Total:\n```\ndone\n```'`，SQL 丢了，后面的 `done` 也被包进代码块。要求：同一行里已闭合的 ```…``` 按行内代码处理（或原样保留内容），不丢回答内容；配一个修复前会失败的测试。在原分支 `worktree-132-im-telegram` 上修。
