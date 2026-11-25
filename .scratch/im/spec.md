# IM 入口：在 Slack、Telegram、Discord 里和 agent 对话

日期：2026-11-23。决策来自当天的 grilling（Q1–Q3、Q12 由人定；其余按上游多数做法和仓库已有设计定，人确认）。取证笔记：

- `docs/research/2026-11-23-im-entry.md`：nao、dash、SignalPilot 的 IM 集成，三个平台的收消息方式与限制，本仓库对话 API
- `docs/research/2026-11-23-im-gateway-designs.md`：Hermes、OpenClaw、Vercel Chat SDK、LangBot 对 10 个设计点的做法对照

## Problem Statement

agent 现在只能在网页对话页和 CLI 里用。数据团队的问题多在 Slack 这类 IM 里提出，三个上游（nao、dash、SignalPilot）都做了 Slack 入口，plan §1 当时把 Slack 列为"暂不做"。

## Solution

- `data-agent web --im <配置文件>`：在网页进程里启动 IM 适配器，适配器直接调用对话模块（`src/data_agent/web/conversations.py` 的 `Conversations`），和网页对话共用同一套对话、运行目录、事件文件。
- 一套窄的平台接口，三个实现：Slack（Socket Mode）、Telegram（长轮询）、Discord（gateway），都不需要公网地址（Q1）。先做接口加 Slack，再做 Telegram，最后 Discord（Q2）。
- 只有允许名单里的用户能让机器人运行（Q3）；机器人在它所在的任何频道都回应，频道可见范围由平台控制（Q12）。

## Implementation Decisions

### 进程与接口

- 适配器跑在 `data-agent web` 进程里（上游 4 个实现都是同进程调用，取证笔记设计点 1）。网页退出时适配器一起停，运行中的任务照现有逻辑取消（工单 85）。
- 调用：新建对话 `Conversations.create()`、发消息 `send()`（运行中会自动成为插话）、取消 `cancel()`；进度和结果读这一轮运行的事件文件（和网页的事件读取同一套）。不改 HTTP API，不改对话逻辑。
- 平台接口（照 Chat SDK、Hermes 的窄接口）：
  - 必需：收消息（带平台、聊天、线程、用户 id、是私聊还是群、是否 @ 了机器人；Update 2026-11-24：补"私聊还是群"，触发和"未授权"的规则要用）、发消息
  - 可选：编辑消息、按钮及其回调、typing 指示；平台没有就降级（不编辑就只发最终答案；没有按钮就只用文字编号）
- 运行的选项和网页对话一样：`--data-sources` 的全部数据源、`--network`、`--charts`、`--mcp-servers`（如果网页带了）。

### 会话映射

- 会话键：平台 + 聊天 id + 线程 id。
  - Slack：在频道里 @ 机器人或私聊发一条新消息，开一次新对话，机器人在这条消息的线程里回复；线程里继续回复就是同一对话的下一轮。
  - Discord：新消息开新对话时，机器人自动建一个线程，在线程里回复。
    - （Update 2026-11-25，人确认，照上游多数：Hermes `f85f33bf`、OpenClaw `acb3939a8`、Chat SDK `e490e84`）
      - 私聊里建不了线程（discord.py 2.7.1 `message.py:1654`、`:1676-1695`），所以私聊照 Telegram 做：一个私聊对应一个"当前对话"，`thread_id` 为 None；`/new` 注册成全局斜杠命令，开一个新对话（Hermes `plugins/platforms/discord/adapter.py:102`）。
      - 频道里线程的 id 就是被 @ 那条消息的 id（Discord 文档 "Start Thread from Message"）。线程在机器人第一次往里发消息时才建，名单外的人在频道里发消息因此不留下任何东西；线程名取消息前 80 字（去掉 @），`auto_archive_duration` 1440；消息本来就在线程里就不另建（Hermes `adapter.py:5189-5240`，Chat SDK `packages/adapter-discord/src/index.ts:1551`）。
  - Telegram：私聊和群没有线程，按聊天对应"当前对话"；发 `/new` 开一个新对话。
    - （Update 2026-11-25，人确认）论坛话题（`message_thread_id`）不支持，记为已知限制。上游 Hermes、OpenClaw 按话题分会话，但那要改 130 的契约（Telegram 的 `thread_id` 为 None），而且"机器人参与过的线程免 @"放到话题里会变成每条都回。
- 群里要 @ 机器人才回应；机器人已经参与的线程里不用再 @。
  - （Update 2026-11-25，人确认，照上游多数）回复机器人的消息也算 @（Hermes、OpenClaw；Chat SDK 可配）。Discord 只认 `message.mentions` 里的机器人，`@everyone` 不算（Chat SDK `index.ts:2471`）。Telegram 认 `mention` 等于 `@机器人用户名`、`text_mention` 指向机器人、带 `@机器人用户名` 的 `bot_command`（Chat SDK `packages/adapter-telegram/src/index.ts:3862-3885`）。
  - （Update 2026-11-25，人确认）Telegram 默认的隐私模式下，官方文档只保证机器人收到命令和对它消息的回复（core.telegram.org/bots/features#privacy-mode）。所以 `docs/setup.md` 要求在 BotFather 里关掉 Group Privacy（或把机器人设为管理员），再把机器人移出群、重新拉回（Hermes `website/docs/user-guide/messaging/telegram.md:253-277`，OpenClaw `docs/channels/telegram/setup.md:99-107`）。适配器对不该回应的群消息照旧什么都不做，也不写日志。
- 映射存在 runs 目录下适配器自己的文件里（如 `<runs-dir>/im/threads.json`），不改 `conversation.json`。IM 发起的对话在网页对话列表里照常出现，能打开回放。

### 澄清、进度、取消

- 澄清：问题和选项按编号写在消息正文里，按钮上只写编号（绕开 Slack 按钮约 30 字的截断、Telegram `callback_data` 64 字节的上限；做法照 Hermes）。点按钮等于发一条内容是该选项文字的普通消息；直接打字回复也行（和网页一致：澄清的回答就是一条普通消息）。
- 进度：一轮开始时发一条状态消息，按事件更新当前步骤（如"查询数据库…""验证模型…"），节流编辑：Telegram 约 1 秒一次，Slack、Discord 约 2 秒一次；遇到平台限流就跳过这次更新。跑完后状态改成"完成 · 用时 · 花费"，另发一条消息放答案。不逐字流式输出答案。
- 运行中再发消息：插话（和网页一致，工单 92）。"`result` 已写出、进程还没退出"时发的消息送不到，沿用这个已知限制。
- 取消：状态消息上一个"停止"按钮；也可以发文字 `stop`（Slack 线程里斜杠命令会被拦截，所以用文字命令）。

### 答案排版

- Markdown 转成各平台的格式（Slack mrkdwn、Telegram 支持的格式、Discord Markdown）。
- 超过平台上限（Slack 约 4000 字、Telegram 4096、Discord 2000）按段落拆成多条。
- 表格转成等宽代码块；超过约 20 行或放不下时截断，注明总行数。
- 图表不在 IM 里显示，附一句"这次答案有 N 张图，请在网页查看"。

### 权限（Q3、Q12）

- 默认拒绝：配置里的用户允许名单（平台用户 id）里的人才能让机器人运行。
- 不在名单里的人：私聊时回一句"未授权"并告诉对方他的用户 id（方便把自己加进名单）；群里不回应。
- 频道不设名单：机器人被拉进哪个频道就在哪个频道回应，回答对频道里所有人可见，这由平台的成员管理控制。

### 配置

- 配置文件在仓库外，TOML，由 `--im` 传入，没有默认路径（和 `--data-sources` 一样）：

```toml
[slack]
bot_token_env = "DATA_AGENT_SLACK_BOT_TOKEN"
app_token_env = "DATA_AGENT_SLACK_APP_TOKEN"   # Socket Mode 的 app-level token（connections:write）
allowed_users = ["U012ABCDEF"]

[telegram]
bot_token_env = "DATA_AGENT_TELEGRAM_BOT_TOKEN"
allowed_users = [123456789]

[discord]
bot_token_env = "DATA_AGENT_DISCORD_BOT_TOKEN"
allowed_users = ["123456789012345678"]
```

- 文件里只写环境变量的名字，变量必须是 `DATA_AGENT_<平台>_` 前缀（如 `DATA_AGENT_SLACK_`；Update 2026-11-24：原为 `DATA_AGENT_`，那样 `DATA_AGENT_API_KEY` 这类别的密钥也能通过）；token 的值放 `~/.config/data-agent/env`。写了哪个平台的段落就启动哪个，可以同时启动多个。
- 变量没设、名单为空：启动时报错并点名是哪个平台。
- token 不进运行（Update 2026-11-24，主会话对照代码核实）：运行子进程现在继承网页进程的全部环境（`src/data_agent/cli.py` 的 `start_run`，现 `:562-571`，`Popen` 没传 `env=`），事件文件的遮罩也不含 IM token（`agent.py:2201-2210`）。所以启动运行时，从子进程环境里去掉配置里点名的 IM token 变量；运行拿不到这些值，事件文件里也就不会有，不用另加遮罩。

### 出错

- agent 运行失败、预算用完、轮数用完：在线程里回一句简短说明（取自 `result` 事件的结束原因），附上这次运行在网页里的位置（运行 id）。没有 `result` 的运行（被取消、崩溃）读 `web-run.json` 的状态。用时按 `result.ended_ts`（工单 122），没有时用 `result.ts`。（Update 2026-11-24：删"超时"——网页对话的每轮不传 `--agent-timeout-sec`，`src/data_agent/web/conversations.py:136-146`，IM 里不会发生。）
- 平台断线：由各平台库自己重连（slack_bolt、aiogram、discord.py 都有）；适配器出错只写日志，不影响网页。
- 发送失败：重试一次，再失败写日志。

### 库

slack_bolt 1.30.0、aiogram 3.34.0、discord.py 2.7.1，都是 MIT，作为可选依赖 `data-agent[im]`（`[project.optional-dependencies]` 的 `im`，`uv sync --extra im`；Slack 另需 aiohttp：slack-bolt 不声明它，而 `slack_bolt/app/async_app.py:8` 顶层导入，Update 2026-11-24 工单 131；为了 CI 的类型检查和测试，也写进 dev 组）；缺库时 `--im` 报错并提示怎么装。（Update 2026-11-24，人定：原为正式依赖，那样会被打进每个 Harbor 评测容器的离线安装，`evals/harbor_agent.py:206-213` 的 `uv export --no-dev`。）不用 python-telegram-bot（pyproject 写 LGPL-3.0-only，仓库 LICENSE 是 GPL-3 全文，不一致）。

## 工单

| 工单 | 内容 | 依赖 |
|---|---|---|
| 130 | 验收测试：在 Testing Decisions 的接缝上写测试，标 `xfail`；人审后合入 main | 无 |
| 131 | 平台接口、会话映射（含 `/new` 开新对话的映射逻辑）、澄清、进度、取消、排版、权限、配置、出错的公共部分，加 Slack 实现；`--im`；README 与 `docs/setup.md`（建 Slack app 的步骤） | 130 |
| 132 | Telegram 实现（aiogram，长轮询，把 `/new` 命令解析成公共部分的"开新对话"） | 131 |
| 133 | Discord 实现（discord.py，gateway，自动建线程；Message Content 是特权 intent，要在开发者后台打开） | 131 |

132、133 可以并行。

## Testing Decisions

单元测试不连真实平台、不调模型：用一个内存里的假平台（实现平台接口）和对话模块的替身。

- **会话映射**：新消息开新对话；同一线程的回复进同一对话；Telegram 的 `/new` 开新对话；映射重启后还在（从文件读回）。
- **触发**：群里没 @ 不回应；机器人已参与的线程里不 @ 也回应；不在名单里的用户在私聊收到"未授权"和自己的 id，在群里没有任何回应，两种情况都没有调用 `send()`。
- **澄清**：`clarification` 事件变成编号正文加编号按钮；点按钮调用 `send()`，内容是该选项的文字；打字回复同样调用 `send()`。
- **进度**：按事件序列生成状态文本；节流（一秒内多个事件只编辑一次）；没有编辑能力的平台只发最终答案。
- **插话与取消**：运行中收到消息调用 `send()`（由对话模块变成插话）；"停止"按钮和文字 `stop` 调用 `cancel()`。
- **排版**：超过上限按段落拆分且每条都在上限内；表格转代码块并截断、注明总行数；有图表时附说明。
- **配置**：变量没设、名单为空、不是 `DATA_AGENT_<平台>_` 前缀的变量名都报错并点名平台；token 的值不出现在日志和事件里；启动运行时传给子进程的环境里没有 IM token 变量。
- **出错**：`result` 是失败、预算用完、轮数用完时回对应的简短说明和运行 id；没有 `result` 的运行（取消、崩溃）也有说明。
- 每个平台实现（131–133）再有一组测试：平台消息格式 ↔ 接口的转换（用平台库的事件样例）。

## 验收

- `make check` 通过。
- 每个平台真实跑一次（人建好 app 或 bot、把 token 写进 `~/.config/data-agent/env`）：
  - 私聊问一个需要澄清的问题，点按钮回答，拿到答案；
  - 在线程里追问一轮，进同一对话；运行中插一句话；点"停止"取消一次；
  - 频道里 @ 机器人问一次；名单外的账号发消息，收到"未授权"；
  - 网页对话列表里能看到这几次对话，能打开回放。
- 不跑分。

## Out of Scope

- 公网部署、webhook 收消息（Q1）。
- 配对码、账号绑定、多用户权限（Q3）。
- IM 里显示图表图片、上传文件、语音。
- 逐字流式输出答案。
- 在 IM 里管理配置（加数据源、改名单）。
- Teams、WhatsApp、Mattermost 等其他平台。
- 对话总花费上限（沿用对话 spec 的 Out of Scope）。

## Further Notes

- 上游：Hermes（MIT）、OpenClaw（MIT）、Vercel Chat SDK（MIT）、LangBot（Apache-2.0）的做法对照见取证笔记；只参考设计，不复制代码。nao 的消息平台实现基于 JS 库 `chat`，Python 后端用不上；nao 的澄清是纯文本编号列表（`apps/backend/src/utils/messaging-provider.ts:456-462`）。
- Slack Socket Mode 的 app 不能上 Slack Marketplace，符合自托管的定位。
- 已知限制：插话的送不到窗口（工单 92）；机器人在频道里的回答对所有成员可见；每次提问都花模型额度，没有对话总花费上限；Telegram 论坛话题不支持（Update 2026-11-25）。
