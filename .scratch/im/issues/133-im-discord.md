# 133 — IM 入口：Discord 实现（discord.py，gateway）

Status: in-review
Blocked by: 131（done）
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-25
Branch: worktree-133-im-discord

## Goal

`data-agent web --im <配置>` 的配置里写了 `[discord]` 时，在网页进程里起 Discord 机器人（discord.py 2.7.1，gateway，不需要公网地址）。

- 服务器频道里 @ 它提问：它从这条消息建一个线程，在线程里回复；线程里继续说话就是同一个对话。
- 私聊：一个私聊对应一个"当前对话"，`/new` 开一个新对话。

和网页对话、Slack 共用同一套对话、运行目录、事件文件。公共部分 131 已经做完；本工单只加平台实现、`service` 里启用它的两处小改，以及 README 和 `docs/setup.md` 里建 app 的步骤。真实 Discord 的验收由人建好 app、主会话来跑（见 Evidence required），不阻塞本工单交回。

## Authority

- May edit：
  - `src/data_agent/im/discord.py`（新建；如果模块名和库名 `discord` 冲突导致导入出问题，就改用 `discord_bot.py`，并在 Comments 里说明）：Discord 平台实现，满足 `platform.py` 的 `Platform` 协议。
  - `src/data_agent/im/service.py`：只改两处。`LIBRARIES` 加 `"discord"` 和它要的库（`:34`）；`make_platform`（`:63-67`）按 `config.platform` 选实现。其他行为不改。
  - `pyproject.toml`、`uv.lock`：`im` extra 和 dev 组各加 `discord.py==2.7.1`，照 131 加 slack-bolt 的做法（`pyproject.toml:24-31`、`:36-55`）；`uv.lock` 只能有本工单的包变化。
  - `tests/`：新建 `tests/test_im_discord.py`。`tests/fixtures/im/`：可以新建 Discord 的事件样例，只放假 id，不放 token。`tests/test_im_service.py`：只改 `test_a_platform_not_implemented_yet_is_refused_by_name`（`:89-98`），见 Context "和 132 并行"。
  - `README.md`：在 "Slack" 一节（`:116` 起）后面加 "Discord" 一节，或者把那一节扩成多平台。`docs/setup.md`：在 "Slack 机器人（工单 131）"（`:189` 起）后面加 "Discord 机器人（工单 133）" 一节。
- Must not edit：
  - `src/data_agent/im/` 里的 `platform.py`、`adapter.py`、`config.py`、`format.py`、`progress.py`、`slack.py`，以及 130 定下的名字、签名、常量。公共部分有缺口时停下，见 Stop conditions。
  - `tests/acceptance/`（全部），`tests/fixtures/` 里已有的文件，`tests/test_im_slack.py`、`tests/test_im_adapter.py`。
  - `src/data_agent/web/`、`cli.py`、`agent.py`、`events.py`，以及 `src/` 里上面没列的文件。`web/`、`evals/`、`Makefile`、`.github/workflows/`、`.env*`、`skills/`、`configs/`。
  - 132（Telegram）的文件，`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap：工单 agent $0（不调模型，不连真实 Discord）。主会话的真实验收上限 $2，属于默认放行的小额运行。

## Context

代码现状按 main `8589823` 核对，行号以本节为准。

- **spec**：`.scratch/im/spec.md`。
  - "会话映射"里 Discord 那条和它的 Update 2026-11-25：私聊照 Telegram 做；线程在机器人第一次往里发消息时才建。
  - "群里要 @"的 Update 2026-11-25：怎样算 @。
  - "澄清、进度、取消"（Discord 编辑间隔约 2 秒）、"答案排版"（上限 2000）、"配置"、"出错"、"库"、"验收"。
- **130/131 的契约（已定，不再问）**：
  - `src/data_agent/im/platform.py`：模块 docstring `:15-26`。其中 Discord 的 `thread_id` 是"机器人从这条消息建的线程，线程 id 就是这条消息的 id"；`text` 去掉 @ 机器人；`send`/`edit` 把公共 Markdown 转成平台格式；用户 id 一律转成字符串。
  - `Incoming.new_conversation`（`:50-52`）：契约里写的是 Telegram 的 `/new`，Discord 私聊照同样的方式用（spec Update 2026-11-25）。
  - `Platform` 协议 `:105-128`。
  - 适配器的规则在 `adapter.py:1-72`，触发判断在 `handle_message`（`:232-240`）：已映射、`thread_id` 不为 None 的键算"机器人参与过的线程"，在里面不用 @。适配器只在决定回应时才调 `send`。名单外的人在频道里发消息时，适配器不调用任何东西（`:239-247`）。
  - `Capabilities.typing` 设为 False：适配器现在不调 `typing`。
- **照 Slack 实现的形状做**：`src/data_agent/im/slack.py`。
  - 模块 docstring 写行为；`LIMITS` 常量；用一把锁按顺序把消息交给适配器（`:20-21`）；丢掉重复投递；按钮先 ack 再交出去（`:117-120`）。
  - 测试照 `tests/test_im_slack.py`：用假的客户端或对象记录调用，不连网。
- **discord.py 2.7.1（读的是 wheel 源码，2026-11-25）**：
  - PyPI：2026-03-03 发布，MIT。`requires_dist` 核心依赖只有 `aiohttp<4,>=3.7.4`，以及 Python ≥3.13 时的 `audioop-lts`。lock 里的 aiohttp 3.14.5 在范围内。本仓库 `requires-python >=3.12`（`pyproject.toml:6`），所以 lock 里会多出带环境标记的 `audioop-lts`。这是本工单的包变化，可以接受。
  - 在已有的事件循环里跑：`Client.start` 就是 `login` 加 `connect`，会一直阻塞（`discord/client.py:829-852`）。
    - "连上就返回"的做法：先 `await client.login(token)`（`:647`，token 错时报错），再用 `asyncio.create_task(client.connect())`（`:695`）在后台连，最后 `await client.wait_until_ready()`（`:1218`）。
    - 停止：`await client.close()`（`:793`）。
    - 只有 `Client.run` 会设日志和 `asyncio.run`（`:920-936`）；`start`、`login`、`connect` 不装信号处理，也不读环境变量里的 token。
  - 事件回调各开一个 task，并发执行（`client.py:524-526`）。所以要像 Slack 那样，用一把锁按顺序交给适配器。
  - 线程：
    - `Message.create_thread(*, name, auto_archive_duration, ...)`（`message.py:1642-1697`）要求消息在服务器的文字频道里（`:1654`）；私聊里没有 guild，直接抛 `ValueError`（`:1676-1695`）。
    - Discord 文档原文："The id of the created thread will be the same as the id of the source message"；线程名 1-100 字符。
    - 机器人往归档的线程里发消息时，API 会自动取消归档。用户在归档线程里回复会不会取消归档，`未确认`。
  - Message Content intent：没有它时，用户写的内容字段是空的，例外是私聊、@ 了机器人的消息和机器人自己发的消息（`flags.py:1256-1263`，Discord 文档）。`Intents.default()` 打开了除 presences、members、message_content 以外的所有 intent（`flags.py:833-842`），所以要另设 `message_content=True`。
  - 按钮：
    - `on_interaction` 对每个交互都会触发，包括没有 view 处理的旧按钮（`state.py:818-831`）。按钮的 id 在 `interaction.data["custom_id"]`，最长 100 字符（`ui/button.py:68`）。
    - 默认的 `View` 180 秒后超时（`ui/view.py:233`）。
    - `interaction.response.defer(...)`（`interactions.py:823`）；3 秒内必须响应是 Discord 的规定，没在源码里看到，`未确认`。
  - 判断 @：`client.user.mentioned_in(message)` 在有 `@everyone` 时也返回真（`user.py:343-360`），所以直接查 `message.mentions`。
  - 限流：429 由 discord.py 自动等 `retry_after` 再重试（`http.py:711-736`），超过 `max_ratelimit_timeout` 才抛错。
  - 单条消息上限 2000 字符：spec 和取证笔记里有，但子 agent 只在 forum 线程的参数里读到原文，实现时到 Discord 文档的 Message 资源页再核一次。
- **上游做法（人 2026-11-25 确认照多数）**：Hermes `f85f33bf`、OpenClaw `acb3939a8`、Chat SDK `e490e84`。只参考设计，不复制代码。
  - 私聊每个一个会话，`/new` 是全局斜杠命令，启动时同步：Hermes `plugins/platforms/discord/adapter.py:102`、`:2334`，`website/docs/user-guide/messaging/discord.md:17,45,313`。
  - 自动建线程：名字取前 80 字，超出时截成 77 字加 `...`，`auto_archive_duration` 1440，消息本来就在线程里就不建。Hermes `adapter.py:5189-5240`、`:5937-5957`；Chat SDK `packages/adapter-discord/src/index.ts:1551`、`:2801`。
  - 判断 @：去掉 `<@id>`/`<@!id>`，回复机器人的消息算 @，`@everyone` 不算。Hermes `adapter.py:4860-4900`；OpenClaw `preflight-helpers.ts:55-72`；Chat SDK `index.ts:2471`。
  - 按钮走原生交互，按 custom_id 分发，先 defer：OpenClaw `custom-id-codec.ts`；Chat SDK `index.ts:855-863`。
  - 排版：Markdown 原样发，表格另转，上限 2000。Hermes `adapter.py:4237`；Chat SDK `markdown.ts:271`、`index.ts:1589`。
  - intent 与权限：Hermes `discord.md:146,200-226`；OpenClaw `docs/channels/discord/setup.md:24-60`；Chat SDK README `:109-123`。
- **和 132 并行（Telegram，同时派工）**：
  - 两张工单都改 `service.py` 的 `LIBRARIES`/`make_platform`、`pyproject.toml`/`uv.lock`、README、`docs/setup.md`。后合入的那张由主会话先把 main 合进来、解冲突（`uv.lock` 用 `uv lock` 重新生成）。
  - `tests/test_im_service.py:89-98` 现在用 `[telegram]` 测"未实现的平台按名字拒绝"，本工单不用改它的平台。原因是 Discord 实现之后，这个用例照样成立，只要 telegram 还没实现。
  - 两张都合入后，这个用例和 `service.start` 里"not implemented yet"的分支（`service.py:46-48`）就走不到了，由后合入的那张一起删掉。如果你开工时 main 上已经有 Telegram 实现，就直接删。

## 主会话 2026-11-25 定的点（实现时照做，不是停下的理由）

1. **会话键**：
   - 服务器频道里的顶层消息：`chat_id = str(频道 id)`，`thread_id = str(消息 id)`，也就是将来那个线程的 id。
   - 线程里的消息：`chat_id = str(线程的 parent_id)`，`thread_id = str(线程 id)`。这样从 @ 消息开始的对话，和之后在线程里的回复落到同一个键上。
   - 私聊：`chat_id = str(私聊频道 id)`，`thread_id = None`，`direct = True`。
   - `user_id = str(author.id)`。
2. **线程在第一次 `send` 时才建**：`send(chat_id, thread_id, ...)` 的 `thread_id` 对应的线程还不存在时（本进程没建过，频道里也查不到），就从那条消息建线程。线程名取消息前 80 字，去掉 @；超出时截成 77 字加 `...`；空了就用一个固定的英文名。`auto_archive_duration` 取 1440。线程已经存在时，直接发到线程里。消息本来就在一个线程里时不另建。名单外的人在频道里发消息时，适配器不调用 `send`，所以不会留下线程。建线程失败时写日志，改成在频道里回复这条消息，同 Chat SDK 的退路。
3. **怎样算 @**：`message.mentions` 里有机器人就算 `mentioned=True`；回复机器人的消息时 Discord 也会把它放进 `mentions`。`@everyone`、`@here`、角色 @ 都不算。`text` 里去掉 `<@机器人 id>` 和 `<@!机器人 id>`。
4. **`/new`**：启动时注册一个全局斜杠命令 `/new`，带英文说明，用 `app_commands.CommandTree` 同步；只在命令集合变了时才同步，避免每次启动都全量同步。用户调用它时：
   - 先在 3 秒内响应这次交互（比如回一条 ephemeral 的确认，或者 defer），保证用户最终只看到一条可见的回复，不能重复；
   - 再交给适配器一个 `Incoming`，`new_conversation=True`、`text=""`。
   - 键照第 1 条：私聊里是私聊的键；在机器人的线程里是那个线程的键；在频道顶层用的话，`thread_id=None`，适配器只回一句提示，不留状态。

   文字形式的 `/new` 不另做解析（`未确认` Discord 客户端会不会把它当普通文字发出来，真实验收时看一次）。`stop` 照公共部分走文字命令和按钮。
5. **按钮**：不用 `discord.ui.View` 的回调，发消息时直接带按钮组件。`custom_id` 用一个固定前缀加 `Button.value`（≤100 字符），在 `on_interaction` 里按前缀认出来，先 `defer` 再经锁交给适配器。这样重启后旧按钮也能点。`Press` 的几个字段：
   - `message_id` 是按钮所在消息的 id，转成字符串；
   - `chat_id`、`thread_id` 照第 1 条，按钮所在的频道是线程时取 parent 和线程 id；
   - `direct` 表示是不是私聊。

   一行最多 5 个按钮，超出就换行。
6. **排版**：Markdown 基本原样发（表格已由 `format.py` 转成代码块）。只做 Discord 必须的处理：
   - 普通文字里的 `@everyone`、`@here` 要让它不生效：发送时用 `allowed_mentions=AllowedMentions.none()`，回答里的 @ 一律不通知；
   - 其余照 Discord Markdown。

   `Limits.max_chars` 在 2000 以内留出代码块重新开合的余量（照 `slack.py:56-58`，常量旁写明理由），`edit_interval` 取 2.0 秒（spec "进度"）。
7. **无关事件**：编辑、删除、系统消息（`message.type` 不是 default 或 reply），以及机器人自己和其他机器人（`author.bot`）的消息，都不交给适配器。重复投递按消息 id 去重，同 Slack 的 `SEEN`。
8. **`start`/`stop`**：
   - `start` 依次做三件事：`login`（token 错就抛出，由 `service.Bot.serve` 写日志）；把 `connect` 放进后台任务；`wait_until_ready` 之后同步 `/new`，然后返回。
   - `stop` 调 `close`，连不上时也不报错。
   - 不调 `Client.run`，也不装日志 handler。
   - intent 用 `Intents.default()`，再加 `message_content=True`。
9. **限流**：discord.py 自动处理 429。编辑重试太久的问题，靠给 `max_ratelimit_timeout` 设一个上限（比如 30 秒，同 Slack）解决，超过就抛出，由适配器跳过这次编辑；发送超过上限时重试一次，再失败就抛出（spec "出错"）。
10. **token 不外泄**：确认 discord.py 和 aiohttp 在 DEBUG 级别的日志里不会出现 token（它放在 `Authorization` 头里）。会出现的话，在本模块里给相关 logger 加过滤，并写一个测试。
11. **依赖**：`im` extra 和 dev 组都钉 `discord.py==2.7.1`，不装 voice、speed 这些 extra。`uv export --frozen --no-dev --no-emit-project` 的输出里不能有 discord.py 和 audioop-lts。如果加了它会迫使 lock 里已有的包换版本，就停下。

## 测试没钉、本工单要做的

都写进 `tests/test_im_discord.py`，用假的 message、channel、thread、interaction 对象，或者 discord.py 自带的类型构造，假 id、假 token，不连网：
- 消息到 `Incoming` 的转换：
  - 频道里 @：`thread_id` 是消息 id，`mentioned` 为真，`text` 去掉了 `<@id>` 和 `<@!id>`；
  - 线程里的回复：`chat_id` 是 parent，`thread_id` 是线程 id；
  - 私聊：`direct` 为真，`thread_id` 是 None；
  - `@everyone` 不算 @；
  - 回复机器人的消息：`mentioned` 为真；
  - 机器人发的消息和系统消息：不交给适配器；
  - 重复投递：只交一次；
  - 各个 id 都是字符串。
- `send`：
  - 线程还没有时，从消息建线程（检查名字的截断和 `auto_archive_duration`），然后发进去；
  - 线程已经有时，不重建；
  - `thread_id` 为 None 时发到频道或私聊；
  - 建线程失败时，改成在频道里回复，并写日志；
  - 带 `allowed_mentions`；
  - 按钮组件的 `custom_id` 有前缀，超过 5 个会换行。
- `edit` 会替换内容和按钮；不带按钮的编辑会去掉按钮。
- 交互：
  - 按钮交互先 `defer`，再变成 `Press`，字段照"主会话定的点"第 5 条；
  - 不是本机器人前缀的 custom_id 不处理；
  - `/new` 交互先响应，再变成 `new_conversation` 的 `Incoming`，键照第 4 条。
- 依次交付：两个事件同时到达时，第一个处理完才处理第二个。
- 限制：每条消息都不超过 2000；`edit_interval` 是 2.0。发送遇到限流的那一条，测试里不真等。
- `start`：
  - token 错时抛错；
  - 不调用 `Client.run`；
  - intent 里有 message_content；
  - `stop` 在没连上时也不报错。
- token 不出现在本模块、discord.py、aiohttp 的 DEBUG 日志里。
- `service`：配置里有 `[discord]` 时 `make_platform` 返回 Discord 实现；没装 discord.py 时报错，并提示 `uv sync --extra im`。
- README 的 "Discord" 一节写这些：
  - 用法、TOML 示例（只写 `DATA_AGENT_DISCORD_BOT_TOKEN`）、允许名单（数字 id 写成字符串）；
  - 频道里 @ 后机器人建线程，私聊里用 `/new`；
  - 已知限制：线程对频道成员可见；没有对话总花费上限；用户在归档线程里回复的行为 `未确认`，以真实验收为准。
- `docs/setup.md` 的 "Discord 机器人（工单 133）" 一节写这些步骤：
  1. 在 Developer Portal 建应用，加 Bot，拿到 token；
  2. 打开 Message Content Intent；
  3. 用 OAuth2 URL 生成器选 `bot` 和 `applications.commands` 两个 scope，权限选 View Channels、Send Messages、Read Message History、Create Public Threads、Send Messages in Threads；
  4. 把机器人邀请进测试服务器；
  5. 打开开发者模式，复制自己的用户 id 填进名单；
  6. token 写进 `~/.config/data-agent/env`。

  每一步附官方文档链接和访问日期，没核实的标 `未确认`；不写任何真实 token、服务器名、id。

## Acceptance

- [ ] "测试没钉、本工单要做的"每条都做了，能单测的都有新测试。
- [ ] `uv run pytest tests/acceptance/test_im_*.py -q` 全部通过，和 main 一样；本工单不碰这些测试。
- [ ] `pyproject.toml` 的 `im` extra 和 dev 组里有 `discord.py==2.7.1`；`uv sync --extra im` 能装上；`uv export --frozen --no-dev --no-emit-project | grep -iE 'discord|audioop'` 输出为空；`uv.lock` 里已有包的版本没变。
- [ ] CI 的 `uv sync --locked` 环境下 `make check` 能过。
- [ ] `git diff main --stat` 只有 Authority 里列的文件；`src/data_agent/im/service.py` 只有 `LIBRARIES` 和 `make_platform` 的改动。
- [ ] README 和 `docs/setup.md` 写好；没有密钥、真实 id、服务器地址；pre-commit 的扫描通过。
- [ ] `make check` 通过。
- [ ] 主会话做（不阻塞本工单交回）：人建好 app 后的真实验收，spec "验收"逐条做到（见 Evidence required）。

## Evidence required

工单 agent 贴进最终回复的 Comments（$0，不连真实 Discord，不调模型）：
- `make check` 的输出（用 `tee` 存文件）。
- `uv export --frozen --no-dev --no-emit-project | grep -iE 'discord|audioop'`（应为空）；`git diff main -- uv.lock | grep '^[-+]version'`，证明已有包的版本没变。
- 不连 Discord 的真实运行：
  - 装了 extra、token 用假值（运行时拼出来，不写进文件）：`data-agent web --im <只含 [discord] 的文件>` 起来后，适配器 `login` 失败，只写日志（贴出错误类型，不含 token 值）；网页照常服务（`curl -s http://127.0.0.1:<port>/api/options` 返回 200）；Ctrl-C 后进程正常退出。
  - 没装 extra：贴出提示 `uv sync --extra im` 的报错，并照实写用了什么办法得到不含 extra 的环境。
- 写好交给主会话的真实验收步骤（见下），确认命令、路径、worktree 名。

交给主会话和人（工单 agent 不做也不等，**没有 app 和 token 不是停下的理由**）：

1. 人：
   - 照 `docs/setup.md` 新加的一节建 app，打开 Message Content Intent，把机器人邀请进一个测试服务器；
   - 把 `DATA_AGENT_DISCORD_BOT_TOKEN` 写进 `~/.config/data-agent/env`；
   - 准备一个放在仓库外的 `im.toml`，`allowed_users` 填自己的用户 id。
2. 主会话在本工单的 worktree 里：`uv sync --extra im`，确认网页已构建（`pnpm --dir web build:local`），然后
   ```bash
   mkdir -p runs/133-live && set -a && . ~/.config/data-agent/env && set +a && \
     uv run data-agent web --runs-dir runs/133-live --data-sources <仓库外的 sources.toml> --im <仓库外的 im.toml> 2>&1 | tee runs/133-live/web.log
   ```
3. 人在 Discord 里做 spec "验收"的每一条：
   - 私聊问一个需要澄清的问题，点按钮回答，拿到答案；追问一轮，进同一个对话；`/new` 之后再问，进一个新对话；
   - 频道里 @ 机器人问一次：机器人建线程，在线程里回答；在线程里不 @ 追问一轮，进同一个对话；
   - 运行中插一句话；点"停止"取消一次；
   - 名单外：照 131 的先例，临时把 `allowed_users` 换成一个假 id，重启后私聊收到"未授权"和自己的 id；频道里 @ 时没有回应，也没有新线程；之后改回名单。
   - 顺便看两件 `未确认` 的事：私聊里打字发 `/new` 的行为；在归档线程里回复的行为。
4. 主会话：
   - 在网页对话列表里看到这几次对话，能打开回放；
   - 在运行目录和 `web.log` 里 `grep` token 值，确认没有；
   - 从事件文件读这几轮的花费；
   - 把命令、结果、花费抄进 Comments。不跑分。失败就写"退回：原因"交回本工单。

## Stop conditions

- 要改公共部分（`adapter.py`、`platform.py`、`config.py`、`format.py`、`progress.py`）或 130 定下的名字、签名、常量才能做到。
- 要改 `tests/acceptance/` 的任何东西。
- 加 discord.py 会迫使 lock 里已有的包换版本，或者 discord.py 及其依赖进了 `uv export --no-dev`。
- `make check` 在 CI 的 `uv sync --locked` 环境下过不了，又不能在 `pyproject.toml` 的范围内解决。
- spec 或本工单 Context 写的代码现状、库的行为和实际对不上。
- 第二轮审查修复后仍不过。

没有真实 app 和 token、不能真跑 Discord，**不是**停下的理由。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3
- Review rounds: 4（另有一次只核修复的 Codex 复查）
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: 已合并（`747cdcf`）；traceback 里的 interaction token 归 137；真实 Discord 验收待人建 app
- Human review minutes: —

## Comments

- 2026-11-25（对齐会话）：
  - 依据：上游做法由人确认照多数（spec 的 Update 2026-11-25）；discord.py 2.7.1 的接口和 Discord 平台事实由子 agent 读 wheel 源码和官方文档核实，见 Context。
  - 与 132 并行派工，冲突处理见 Context "和 132 并行"。
- 2026-11-25（工单 agent 最终回复，主会话照抄）：

**状态**：`ready-for-human`。两轮修复（`c6817aa`、`e035c98`）后，只核修复的第三次 `da-review` 找出新 bug，按修复上限停下。分支 `worktree-133-im-discord`（`c148965`、`c6817aa`、`e035c98`）。最终 `make check`：3287 passed、3 skipped；web 448/448；ruff、格式、basedpyright 干净。

**未修的 bug（待人定：第三轮修，或记成已知限制）**：在 Discord 论坛帖子的第一条消息里 @ 机器人，这条消息的 id 和帖子线程的 id 相同；机器人当成顶层消息（`src/data_agent/im/discord_bot.py:315-319`），在线程里再建线程失败，回复发到不收普通消息的论坛频道，提问没有回答。spec 的"论坛话题不支持"只说 Telegram，没覆盖 Discord 论坛。可能的修法：用取到的频道类型判断消息是否已在线程里，加一个"起始消息 id 等于线程 id"的测试。

**交对齐会话定**：在机器人的线程里 `/new`，adapter 会删掉这个线程的 key（`adapter.py:249-252`），之后不 @ 的消息被忽略。Discord 代码在不违反定的点 3（什么算 @）的前提下修不了，改 adapter 是停下条件。README 已写明 `/new` 后要再 @ 一次。

**实现与检查**
- 模块名 `src/data_agent/im/discord_bot.py`：叫 `discord.py` 时 basedpyright 把 `import discord` 解析成模块自己，按工单允许改名，docstring 写了原因。
- 依赖：`im` extra 和 dev 组钉 `discord.py==2.7.1`。`uv.lock` 只新增 audioop-lts 0.2.2、discord-py 2.7.1，已有版本不变；`uv export --frozen --no-dev --no-emit-project | grep -iE 'discord|audioop'` 无输出；`uv lock --check` 通过。
- `make check`（`e035c98`）：输出在 worktree 的 `runs/133-fake/make-check-final.log`。
- 装 extra、假 token（`runs/133-fake/run.sh`，`data-agent web --im <只含 [discord]>`）：日志 "discord: the bot could not connect; the web UI goes on without it"，`discord.errors.LoginFailure: Improper token has been passed.`（HTTP 401）；`/api/options` 200；Ctrl-C exit 0；`web.log` 里 token 出现 0 次。注意：这次用假 token 向 Discord 真实 API 发了一次登录请求，Evidence 要这个失败，但和 Spend cap 行的"不连真实 Discord"相冲。
- 不装 extra（`UV_PROJECT_ENVIRONMENT=runs/133-fake/noextra-venv uv sync --frozen --no-dev`，因为 dev 组带了 discord.py）：`--im: the discord bot needs the IM libraries (discord is not installed); run uv sync --extra im (or install data-agent[im])`，exit 2。
- token 不进日志：测试让 discord.py 连本地 HTTP 服务器，抓 401 和 200 登录的 DEBUG 日志，token 都没出现；discord.py 只记方法、URL、body，不记请求头。网关 IDENTIFY（带 token）不记日志，读源码确认，没写测试。
- 文档：`docs/setup.md` 链 Discord 官方文档（2026-11-25 读）；菜单准确名称、Developer Mode / Copy User ID 路径标 未确认（Discord 帮助站拒绝自动抓取）。

**审查**
- `da-review` 第 1 轮，都已处理：`/new` 命令同步失败时网关还在跑、adapter 却没开始轮询（已修）；线程里 `/new` 丢 key（即上面的 adapter 缺口，只改 README）。
- `da-review` 第 2 轮，三条都修：未缓存线程的获取放进投递锁内保证顺序；取不到频道的消息记日志丢弃，不再编 key；别人已从该消息建了线程（Discord 错误 160004）时发进那个线程。
- 复查：上面的论坛起始消息 bug，未修。
- `eng:code-review` Spec（`c148965`）已修：归档（未缓存）线程里的消息没有频道类型、key 错，改为取频道；线程名去掉别的用户和角色的 mention；`/new` 设为服务器和私信都能用，同步检查比较可用范围。不修：重启后的编辑发到父频道（adapter 只在内存里存轮次和问题，`adapter.py:183-185`，重启后不会编辑旧消息）；启动失败不关 client（`Bot.stop` 总会调 `platform.stop()` 关掉）。
- `eng:code-review` Standards：无硬违规。已修：会话 key 分支收进 `channel_key`；`SEEN` 改名 `REMEMBERED`，`find` 改名 `thread_or_source`。不修：共用的有界 map 辅助、`service.py` 改成按平台一张表，都要动本工单不许改的 `slack.py` 和 `service.py` 其他部分；模块大小和六个有界 map 各有单一用途和注释；chat/thread/direct 三元组和 id 的字符串/整数转换由共用的 `Incoming`/`Press` 类型决定；命令描述用 `getattr` 是因为命令树类型包含没有描述的右键菜单。

**真实验收交接**（worktree `/Users/shenghuikevin/dev/AI/data-agent/.claude/worktrees/133-im-discord`）：工单第 2 步命令可在该 worktree 原样运行。除两处 未确认 外另看：`/new` 在私信里是否出现、私有"思考中"提示是否消失只剩一条回复；关掉 ping 的回复是否仍算 @（定的点 3 假定只有带 ping 的算）；归档线程里的追问是否还在同一对话；登录超过 3 秒，discord.py 不读代理环境变量，网络要能直连 Discord。
- 2026-11-25（主会话，人定"照上游"）：
  - 退回：论坛帖子起始消息里 @ 机器人没有回答（`src/data_agent/im/discord_bot.py:315-319` 把它当顶层消息，在线程里再建线程）。照 spec "会话映射" Update 2026-11-25 晚：判断"已在线程里"看消息所在频道是不是线程（`discord.Thread`），不比较消息 id 和线程 id；论坛起始消息就在这个帖子线程里回复。配一个修复前会失败的测试（起始消息 id 等于线程 id、频道是论坛帖子线程）。人允许为这一条做第 3 轮修复；修完照常跑两道审查，审查再有新问题就停下交回。
  - 先把 `origin/main` 合进本分支（132 Telegram 已合入 `65631e5`，同改 `service.py` 的 `LIBRARIES`/`make_platform`、`pyproject.toml`、`uv.lock`、README、`docs/setup.md`，`tests/test_im_service.py` 的"未实现平台"那例）：两个平台都保留；`uv.lock` 用 `uv lock` 重新生成，只能有 aiogram、discord.py 两组包的变化；"未实现平台"那例和 `service.start` 里 "not implemented yet" 的分支照 Context "和 132 并行"（`:80`）一起删掉。
  - `/new` 后线程免 @ 的缺口不在本工单修，归 137（改 adapter）；README 里"`/new` 后要再 @"那句先留着，137 去掉。
- 2026-11-25（工单 agent 退回第 1 轮最终回复，主会话照抄；新提交 `7ea6821` 合入 main、`fd3adef` 论坛修复、`39bebe8` 审查小节）：
  - 论坛修复：`handle_message` 用 `channel.type in THREAD_TYPES` 判断"已在线程里"，不比较 id；论坛帖子起始消息里 @ 在帖子里回答、不建线程。新测试 `test_a_mention_in_a_forum_posts_starter_message_is_answered_in_the_post`（起始消息 id 等于线程 id、频道是帖子线程），修复前因记录到 `create_thread` 失败，修复后通过。Spec 审查确认没有别处按 id 推断线程。
  - 合入 `origin/main`（132）：`service.py` 两个平台都在 `LIBRARIES`/`make_platform`，"not implemented yet" 分支和 `test_a_platform_not_implemented_yet_is_refused_by_name` 已删；`pyproject.toml` 两个库都在；`uv lock` 重生成，相对 origin/main 只多 audioop-lts 0.2.2、discord-py 2.7.1，`uv lock --check` 通过，`uv export --frozen --no-dev --no-emit-project | grep -iE 'discord|audioop|aiogram'` 无输出；`git diff origin/main --stat` 只有 Authority 内的文件。README "`/new` 后再 @" 一句留给 137。
  - 检查：`make check`（`fd3adef`，`runs/133-fake/make-check-r3.log`）ruff/basedpyright 干净，pytest 3363 passed、3 skipped，web 448/448。`39bebe8` 之后没跑全量：ruff、格式、basedpyright 干净，`pytest tests/test_im_discord.py tests/test_im_service.py tests/acceptance/ -k im` 142 passed；IM 验收 60 passed。假 token 运行同前（LoginFailure 401，`/api/options` 200，Ctrl-C exit 0，token 0 次；同样向真实 Discord 发了一次登录请求）。
  - 审查：`eng:code-review` Spec 无缺失、无越界，一条小节（过时 docstring）已修；Standards 无硬违规，注释措辞和 README 换行已修；不修：线程判断在 `handle_message` 和 `channel_key` 两处但共用 `THREAD_TYPES`，合并要改 `to_incoming` 返回类型；新平台要动几处列表是 main 的形状；`pyproject.toml` 注释位置属外观。
  - `da-review`（`runs/133-fake/da-review-r3.log`）2 个新 P2，按退回条件未修（都在前几轮的代码里，主会话已对照代码核实）：
    1. interaction token 进 DEBUG 日志：discord.py 在 DEBUG 记每个网关事件原文（`discord/gateway.py:518`），`INTERACTION_CREATE` 带 interaction token；webhook 路由也把它放进 URL 路径（`discord/webhook/async_.py:264,300,321`）。是短期的 interaction token，不是 bot token；之前的测试只覆盖 `Authorization` 头里的 bot token。修法：给 `discord.gateway`、`discord.webhook`、`discord.http` 的 logger 加脱敏过滤，配一个喂假 interaction 的测试。
    2. 确认 interaction 会打乱顺序：`handle_new`（`discord_bot.py:376`）和 `handle_interaction` 先 await `defer` 再拿 `self.lock`，`/new` 的确认还没回来时发的消息先到适配器、进了旧对话（Codex 复现顺序 `['first question after /new', 'new']`）。修法：await 确认前先排进队列，仍在 3 秒内确认。
  - 真实验收补充：在论坛帖子起始消息里 @ 机器人，确认在帖子里回答。
- 2026-11-25（主会话，人定再修一轮）：
  - 退回：修上面 `da-review` 的两个 P2（主会话已核实 `discord/gateway.py:518`、`discord_bot.py:376`）。1）interaction token 不进日志：给 `discord.gateway`、`discord.webhook`、`discord.http` 的 logger 加脱敏过滤（只在本模块里加），配一个喂假 `INTERACTION_CREATE` 和假 webhook URL、在 DEBUG 下断言 token 不出现的测试。2）`/new` 和按钮先排进投递顺序再 await 确认，仍在 3 秒内确认；配一个修复前会失败的顺序测试（`/new` 确认未完成时到的消息进新对话）。人允许为这两条做第 4 轮修复；修完跑两道审查，审查再有新问题就停下交回，不再修。
- 2026-11-25（工单 agent 第 4 轮最终回复，主会话照抄；新提交 `c230790`）：
  - 1）token 不进日志：新 `RedactTokens` 过滤器，建平台时挂到 `REDACTED_LOGGERS`，把 `'token': '…'` 字段和 `/webhooks/{id}/{token}`、`/interactions/{id}/{token}` URL 里的 token 换成 `[redacted]`。挂在 `discord.webhook.async_` 而不是 `discord.webhook`：logger 的过滤器只看自己产生的记录，不看子 logger 的。测试 `test_no_interaction_token_is_in_the_debug_log` 走 discord.py 自己的代码（`DiscordWebSocket.received_message` 喂假 `INTERACTION_CREATE`；`AsyncWebhookAdapter` 对本地服务器做 callback 和 `@original` 删除），DEBUG 下断言。
  - 2）顺序：`/new` 和按钮立即把确认作为任务启动，再排队拿锁，拿到锁后才等确认、交给适配器。测试 `test_a_message_after_new_goes_into_the_new_conversation_while_new_is_acknowledged`、`test_a_press_keeps_its_place_while_it_is_acknowledged`（修复前失败）、`test_interactions_are_acknowledged_while_an_earlier_event_is_handed_on`（3 秒要求，防回归，新旧都过）。Spec 子 agent 在临时 worktree 里用 `c230790^` 的 `src` 跑过，前两个和 token 测试失败；`c230790` 上 44 个全过。
  - 检查：`make check`（`c230790`，`runs/133-fake/make-check-r4.log`）ruff/basedpyright 干净，pytest 3367 passed、3 skipped，web 448/448。真实运行（`runs/133-fake/r4-redact.py`，运行时生成假 token，日志 `runs/133-fake/r4-redact.log`）：DEBUG 日志里是 `'token': '[redacted]'`、`POST …/interactions/11/[redacted]/callback`、`DELETE …/webhooks/22/[redacted]/messages/@original`，token 0 次；压住 `/new` 确认时交给适配器的顺序是 `['new', 'first question after /new']`。
  - `eng:code-review` Spec：无缺失、无越界；确认过滤覆盖 discord.py 2.7.1 里所有在消息文本中记 interaction token 的地方（`gateway.py:518`、`webhook/async_.py:185`、`http.py:660/707/740`）；`/new` 经 `CommandTree` 到 `handle_new` 中间不让出（读源码确认，没跟踪运行）。Standards：无硬违规；不修：两个处理函数重复 create_task→lock→await 的形状；`RedactTokens`/`REDACT_TOKENS`/`redact_tokens` 只差大小写；`to_press`/`channel_key` 抛错或 `stop` 时等锁被取消，确认任务没人 await（最多一条 "Task exception was never retrieved" 警告）；网关测试经私有属性建 `DiscordWebSocket`（discord.py 钉 2.7.1）；两个顺序测试重复 setup。
  - `da-review`（`runs/133-fake/da-review-r4.log`）1 个新 P2，按退回条件未修，已对照代码核实：interaction callback 超时时 aiohttp 抛 `ConnectionTimeoutError(f"Connection timeout to host {req.url}")`（`aiohttp/client.py:858-859`），URL 带 interaction token；异常经 `handle_interaction`/`handle_new` 传出，由 `discord.client`（`client.py:582`，`_log.exception`）和 `app_commands.tree`（`tree.py:819`，`exc_info=error`）记下。这两个 logger 没挂过滤器，过滤器也只改 `getMessage()`，不改 traceback。仍是短期 interaction token，不是 bot token。可能的修法：两个 logger 也挂过滤器，并脱敏 `exc_info`/`exc_text`，配一个 callback 超时的测试。
- 2026-11-25（主会话，人定）：traceback 里的 interaction token 不在本工单修，写进 137 的第 4 条和验收标准；本工单开 PR。
- 2026-11-25（主会话）：PR #141 CI 绿。Codex 唯一意见（P1，`discord_bot.py:143`：过滤器只改 `getMessage()`，异常 traceback 和 `discord.client` logger 里仍有 interaction token，callback 超时时出现）就是人已定放进 137 第 4 条的那一项，不是新问题，不退回。已 squash 合并为 main `747cdcf`。Status 保持 `in-review`，等人建好 Discord app 后主会话在 worktree `.claude/worktrees/133-im-discord` 做真实验收（照 131、132 先例）。
