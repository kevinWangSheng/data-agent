# IM 网关设计对照：4 个开源实现怎么把 agent 接到多个 IM（2026-11-23）

只取证，不做设计决定。接 `2026-11-23-im-entry.md`。本次读的是本地浅克隆（只读，未运行、未装依赖），目录 `<scratch>/im-refs/`，`<scratch>` = `/private/tmp/claude-501/-Users-shenghuikevin-dev-AI-data-agent/ab0b6835-4ba1-4bbc-b5cf-8e797d9d04d3/scratchpad`。访问日期均为 2026-11-23。"未确认"= 没读到证据。

## 0. 选了哪几个

| 简称 | 仓库 / commit | 许可证（GitHub API + LICENSE 开头） | 活跃度（pushed_at） | 语言 | 本次用途 |
|---|---|---|---|---|---|
| Hermes | NousResearch/hermes-agent `b4bacb95` | MIT | 2026-11-23 | Python | 可参考、可复制（保留版权头、登记 NOTICE） |
| OpenClaw | openclaw/openclaw `91dee122e` | MIT | 2026-11-23 | TypeScript | 同上；文档最全 |
| Chat SDK | vercel/chat `3527c72`（`chat` 4.45.0） | MIT | 2026-11-21 | TypeScript | 同上；即 nao 用的库 |
| LangBot | langbot-app/LangBot `6eef18d` | Apache-2.0 | 2026-11-23 | Python | 同上；UI 里配置的多平台机器人平台 |

没选的：
- AstrBot（AstrBotDevs/AstrBot）：许可证 AGPL-3.0（GitHub API），按硬规矩只描述思路，本次没读源码，未取证。
- OpenHands 的 Slack 集成：克隆 `OpenHands/OpenHands`（MIT，`315a06e`）全库没有 slack 实现，只有一个图标 `src/icons/slack.svg`；该集成不在这个开源仓库里（疑在其云版/企业仓库，未核实），所以换成 LangBot。
- 对照说明：Hermes 与 OpenClaw 同源关系明显（Hermes 配置注释写"Ported from openclaw/openclaw#72038"，`gateway/config.py` 第 526 行附近），两者做法相近，数"多数"时要意识到它们不是完全独立的两票。

读到的深度不同：Hermes 与 OpenClaw 读了文档加关键源码；Chat SDK 读了文档加 `types.ts` 与适配器入口；LangBot 读了适配器基类、会话管理、控制器、聊天处理器。

## 1. 对照表（设计点 × 实现）

| 设计点 | Hermes | OpenClaw | Chat SDK | LangBot |
|---|---|---|---|---|
| 1 适配器与后端关系 | 独立网关进程；agent 同进程调用；每平台一个 `BasePlatformAdapter` 子类 | 独立网关（Gateway）进程；渠道是插件；agent 同进程 | 库，不是进程；你的 handler 里自己调 agent；适配器 `Adapter` 接口 | 独立服务，一个 bot 实例 = 一个平台适配器 + 一条 pipeline；agent 同进程（或 runner 插件） |
| 1 抽象接口 | connect/disconnect/send/get_chat_info 为抽象；edit/typing/image/document/clarify 等可选 | 声明式 message adapter（`send.text`、capabilities、receipt） | postMessage/editMessage/deleteMessage/startTyping/stream/reactions/modal 等，大量可选 | send_message/reply_message 等抽象；reply_message_chunk、edit_message、upload_file、send_interaction 可选 |
| 2 会话键 | `<ns>:<platform>:<chat_type>[:scope][:chat_id][:thread_id][:user]` | `agent:<id>:<channel>:<kind>:<id>[:thread:<ts>]`；私聊默认全部折进 main | 线程 id 如 `slack:C123:1234.5678`；会话映射由你的代码自己做 | `(实例,工作区,bot_uuid,launcher_type,conversation_scope)` |
| 2 群里要不要 @ | 要（Slack）；Discord `require_mention` 默认 true；线程里 bot 已参与则免 | 默认要；bot 回过的线程里可免（`threadParticipation`）；可配 | `onNewMention`（要 @）与 `onSubscribedMessage`（已订阅线程，免 @）分开 | 响应规则 `resprule`（可配）；`未确认`默认值 |
| 3 澄清/选项 | Slack/Telegram 按钮 + "Other…"转文字输入；无按钮平台回退文本 | 按钮（Telegram/Discord/Slack，单题单选）；其余平台纯文本，文字回复也能答 | 卡片按钮 + `onAction`，由你拼装 | `send_interaction` 按平台能力出卡片；`text_fallback` 回退文本编号 |
| 4 进度 | 流式编辑同一条（默认间隔 0.8s、缓冲 24 字）；Slack 原生流；Telegram 草稿；typing | Slack 原生流/预览编辑/进度卡；typing 反应；Telegram/Discord 预览 | post+edit 节流（默认 500ms，Telegram ~1 次/秒），初始占位 "..." | 适配器声明 `is_stream_output_supported`；Telegram 用 edit_message_text；处理指示器 |
| 5 长文本/格式 | Slack 39000 字拆分；Telegram MarkdownV2 转换、表格转项目符号；`rich_blocks` 原生表格 | Slack 默认 8000 字拆分（可配、`newline` 模式）；Block Kit 图表与表格 | Markdown 统一转各平台格式；Slack 用原生 `markdown_text`；Telegram `splitText`；卡片 Table | 长文本策略：`none`/转图片/转合并转发 |
| 6 运行中再发消息 | 默认 `interrupt`；可配 `queue`、`steer` | 默认 `steer`（注入运行中）；`followup`/`collect`/`interrupt` 可选；500ms 去抖 | 默认 `drop`；`queue`/`debounce`/`burst`/`concurrent` 可选 | 先尝试 steering 认领；否则进入会话信号量排队（并发数可配） |
| 7 取消 | `/stop` 命令（两道守卫都放行）；Slack 线程里用 `!stop` | `/stop`；Slack 原生 Stop 按钮 | 无内置命令；`thread.signal`，Slack 原生停止按钮触发 | `/stop` 命令；群里仅本人或有权限者可停 |
| 8 权限 | `<PLATFORM>_ALLOWED_USERS` 名单；默认拒绝；陌生私聊默认配对码，可改 ignore/decline | `dmPolicy`：pairing/allowlist/open；`allowFrom`；频道名单用频道 id；配对码 8 位 1 小时 | 无内置（文档里搜不到），自己在 handler 里查 | pipeline 访问控制 whitelist/blacklist（按会话 id） |
| 9 配置 | `~/.hermes/config.yaml` + `~/.hermes/.env`（token） | `~/.openclaw/openclaw.json`（JSON5）；token 可引用环境变量 | 代码里 `new Chat({adapters})`；token 读环境变量 | Web UI 里填，存数据库；每个平台一份 yaml 表单定义 |
| 9 多平台同开 | 同一网关进程多适配器 | 同一网关，多渠道、多账号 | 一个 `Chat` 多个适配器 | 多个 bot 实例 |
| 10 失败 | Telegram/Slack 重连阶梯与看门狗；回复 "Sorry, I encountered an error."（i18n 文案） | Socket Mode 退避 2s 到 30s；发送重试 3 次；出错回紧凑错误回复 | 抛 `RateLimitError`（带 `retryAfterMs`）等类型化错误 | `exception-handling`：show-error / show-hint（默认）/ hide |

## 2. 逐点证据

路径相对各自克隆根目录。

### 点 1：适配器与 agent 后端的关系；平台抽象接口

**Hermes**（独立网关进程，同进程调 agent）
- `gateway/AGENTS.md:5-11`："`gateway/run.py` is the facade … adapters in `platforms/<name>.py` over `platforms/base.py`"。
- 抽象基类 `gateway/platforms/base.py:1780` `class BasePlatformAdapter(ABC)`。抽象方法只有 `connect`（2593）、`disconnect`、`send`（"Send ``content`` (may be markdown) to a chat; returns SendResult"）、`get_chat_info`（4631）。
- 可选且有默认实现：`edit_message`（默认 `SendResult(success=False, error="Not supported")`，调用方失败则改发新消息）、`delete_message`、`send_typing`、`send_image`、`send_document`、`send_voice`、`send_video`、`send_clarify`、`send_exec_approval`、`send_draft`。
- 平台适配器在 `plugins/platforms/<name>/adapter.py`（slack 7087 行、telegram 6641 行），说明"一个窄接口"最后仍会长成很大的单平台文件。

**OpenClaw**（独立 Gateway 进程，渠道是插件）
- `docs/channels/channel-routing.md:1-30`：回复回到来源渠道，模型不选渠道。
- `docs/plugins/sdk-channel-outbound.md:225-262`：`defineChannelMessageAdapter({ id, durableFinal: { capabilities: { text, replyTo, thread, messageSendingHooks } }, send: { text: async ({cfg,to,text,accountId,replyToId,threadId,signal}) => ... } })`，要求"Only declare capabilities the native transport actually preserves"。
- 控制台 `http://127.0.0.1:18789`（`docs/gateway/configuration.md:77`）。

**Chat SDK**（库）
- `packages/chat/src/types.ts:247` `interface Adapter`。必需：`postMessage`、`editMessage`、`deleteMessage`、`startTyping`、`addReaction`、`removeReaction`、`fetchMessages`、`fetchThread`、`handleWebhook`、`parseMessage`、`renderFormatted`、`encodeThreadId/decodeThreadId`、`initialize`。可选：`stream`、`postEphemeral`、`openModal`、`openDM`、`endTyping`、`editObject`、`postObject` 等。
- 状态（订阅、锁、去重）通过 `StateAdapter`（内存/Redis/pg）（`apps/docs/content/docs/state-adapters.mdx:13-19`）。
- Slack 适配器内含 Socket Mode（`packages/adapter-slack/src/index.ts:15` 引入 `SocketModeClient`；1115 行起 "Socket mode support"）。Telegram 有 `webhook | polling` 两种（`adapter-telegram/src/index.ts:314`）。Discord 默认是 HTTP Interactions，收普通消息要 Gateway WebSocket，serverless 下用 cron 保活（`apps/docs/content/adapters/official/discord.mdx:190-197`）。这套设计面向 serverless（Vercel），不是常驻进程优先。

**LangBot**（独立服务，一个 bot = 适配器 + pipeline）
- `sdk/src/langbot_plugin/api/definition/abstract/platform/adapter.py:21` `AbstractMessagePlatformAdapter`。抽象：`send_message`、`reply_message`、`register_listener`、`unregister_listener`、`run_async`、`kill`。可选：`reply_message_chunk`（流式）、`is_stream_output_supported`、`edit_message`、`delete_message`、`upload_file`、`get_interaction_capabilities`/`send_interaction`/`update_interaction`（按钮卡）。
- 平台实现在 `src/langbot/pkg/platform/sources/{slack,telegram,discord}.py`。Slack 用自带 `libs/slack_api`，不是 slack_bolt；Telegram 用 `python-telegram-bot`（`telegram.py:4-6`，`telegram.py:973` `start_polling`）；Discord 用 `discord.py`（`discord.py:6`）。

### 点 2：会话映射、新话题、存储、群里是否要 @

**Hermes**
- `gateway/session.py:722-770` `build_session_key`：布局 `<ns>:<platform>:<chat_type>[:<slack scope_id>][:<chat_id>][:<thread_id>][:<user>]`。私聊按 chat_id 隔离；群里默认按参与者再细分（`group_sessions_per_user`），线程内共享（除非 `thread_sessions_per_user`）。
- 新话题：命令 `/new`（`gateway/AGENTS.md` 提到 `/new` 与 `/stop` 属"interrupt_then_dispatch"）；Discord 在频道里被 @ 会自动开线程（`auto_thread: true`，`website/docs/user-guide/messaging/discord.md:375`）。
- 群里：Slack "In channels, always @mention the bot … Once the bot is active in a thread, you can reply in that thread without mentioning it"（`slack.md:385`）；Discord `require_mention: true` 默认，`free_response_channels` 可免（`discord.md:19,371-374`）。
- 存储：`hermes_state.py` / SQLite（`未确认`具体表），重启后会话持久（TUI 文档说 durable session queue）。

**OpenClaw**
- `docs/channels/channel-routing.md:34-65`：私聊默认 `session.dmScope=main`，全部折进 `agent:<agentId>:main`；可改 `per-peer` / `per-channel-peer`。群组 `agent:<id>:<channel>:group:<id>`；Slack/Discord 线程追加 `:thread:<threadId>`；Telegram 论坛话题 `:topic:<id>`。
- `docs/channels/slack/threads-and-sessions.md:20-28`：线程回复用父级 `thread_ts` 作会话后缀；新线程会话启动时拉 20 条历史（`initialHistoryLimit`）；`implicitMentions.replyToBot`、`threadParticipation` 默认 true（回复机器人或在它回过的线程里免 @）。
- 备注：私聊折进同一个 main 会话是它的"个人助理"定位，和"一个线程一个会话"不同。

**Chat SDK**
- 线程 id 形如 `slack:C123ABC:1234567890.123456`（`threads-messages-channels.mdx:25`）。`onNewMention`（需要 @）与 `onSubscribedMessage`（已 `thread.subscribe()` 的线程，免 @）分开；订阅存进 state 适配器（`chat.ts:602-636`、`state-adapters.mdx:19`）。
- 会话映射：SDK 只给线程 id 与 `thread.setState()`，agent 会话 id 要自己存。

**LangBot**
- `src/langbot/pkg/provider/session/sessionmgr.py:36-51` `_query_session_key`：元组 `(instance_uuid, workspace_uuid, placement_generation, bot_uuid, launcher_type, conversation_scope)`；`get_session` 带最大条目与清理（200 条/工作区）；有 `reset generation` 用于重置会话。存内存加数据库（`未确认`持久化范围）。
- 群里是否响应：`pipeline/resprule/` 规则（@、前缀、随机等，`未确认`具体清单）。

### 点 3：澄清/选项

**Hermes**
- `plugins/platforms/slack/adapter.py:5509-5545` `send_clarify`：Block Kit 按钮，`action_id=hermes_clarify_choice_<idx>`，`value="<clarify_id>|<idx>"`，另加 "✏️ Other…" 按钮；选项文字长时，正文放编号列表，按钮只写数字（Slack 把长标签截断，注释 #78115）；一个 actions 块最多 5 个元素。
- Telegram：`telegram/adapter.py:3881-3901`，`callback_data=f"cl:{clarify_id}:{idx}"`，注释 "Telegram caps callback_data at 64 bytes"，另有 `cl:<id>:other`。
- 回调怎么带回：`clarify_id` 编在 `callback_data`/`value` 里，由进程内的 clarify 登记表解析（`_handle_clarify_callback`，telegram 4443）。
- 打字回复：`slack.md:346-358` 点按钮后卡片原地改写"谁选了什么"，重复点击忽略；点 Other 后下一条文字即答案；超时（`agent.clarify_timeout`）或直接打字时，卡片改写成"expired / cancelled"；网关重启后点旧卡会提示重问。按钮点击与消息同一套授权。
- 无按钮平台：基类 `send_clarify` 回退文本。

**OpenClaw**
- `docs/tools/ask-user.md:10-60`：`ask_user` 1 到 3 个结构化问题；Telegram 每个选项一个整宽原生按钮，**Other…** 切到回复输入而不结算；Discord、Slack 对"单题单选"出原生按钮；在支持的渠道上，纯文本回复也可以回答（需权限一致）；不支持提问输入的渠道（iMessage、Signal、WhatsApp 等）让 agent 直接用普通文本问。
- `docs/channels/slack/messaging.md:232-234`：问题被答、取消或过期后，原卡片更新结果，只去掉该题的答案按钮。

**Chat SDK**
- `apps/docs/content/docs/actions.mdx`：`bot.onAction("id", handler)`，事件含 `actionId`、`value`、`user`、`thread`；卡片按钮由你拼。没有内置的"澄清"概念；`approvals.mdx` 另有审批流（`未读`）。

**LangBot**
- 平台层 `get_interaction_capabilities()` 返回 `InteractionDeliveryCapabilities`（`field_types`、`action_styles`、`max_options`、`max_actions`、`supports_updates`、`text_fallback=True` 默认），`send_interaction(..., callback_token)` 把回调令牌随卡片发出（`sdk/.../runner/interaction.py:93-103`）。`pkg/platform/human_input.py` 在无卡片时生成文本版（"1. 选项"，"You can reply with one or more lines like `field_name: value`"）。
- Telegram：点选后编辑原消息显示所选（`pipeline/process/handlers/chat.py:423-429`）。

### 点 4：进度显示、编辑频率与限流

**Hermes**
- `gateway/config.py:507-523`："Tuned for Telegram's ~1 edit/s flood envelope"；`DEFAULT_STREAMING_EDIT_INTERVAL = 0.8`，`BUFFER_THRESHOLD = 24`，光标 `" ▉"`；`transport: auto` 优先原生草稿（Telegram `sendMessageDraft`）否则编辑；`streaming.enabled` 默认 False。
- Slack 原生流（`chat.startStream/appendStream/stopStream`，需要 `assistant:write`，失败时缓存并回退编辑流）（`slack.md:543-560`）。`gateway/AGENTS.md:28-52` 记了四条"流即最终消息"的不变量（草稿帧必须前缀稳定、最终文本由消费者声明、中间发送带标记、补发用编辑而不是新发），每条来自一次"重复最终消息"事故。
- typing：基类 `send_typing`；Slack 用 "is thinking..." 状态行（`typing_status_text`，需 `assistant:write`，`slack.md:493-504`）。
- 限流：Slack 发送遇 429 读 `Retry-After`（`slack/adapter.py:2280`），其他重试指数退避（5989）。

**OpenClaw**
- `docs/channels/slack/messaging.md:62-140`：`streaming.mode` 为 off / partial（替换预览）/ block（追加）/ progress（默认，2026.8.1 起）；原生任务卡，每次工具调用一行（默认关）。
- 收到消息立刻在队列里发 typing（`docs/concepts/queue.md:30`）；Slack 在没有回复线程时只用临时 typing 反应；确认反应 `ackReaction`（`messaging.md:17`）。
- 限流：显式 429 按 `Retry-After` 重试最多 2 次（`messaging.md:139`）；通用发送重试 3 次，最小延迟 400ms（Discord 500ms），上限 30s，抖动 10%（`docs/concepts/retry.md:17-30`）。

**Chat SDK**
- `apps/docs/content/docs/streaming.mdx:116-144`：Slack 原生；Telegram、Discord、Google Chat "Post + Edit"，Telegram 节流约 1 次/秒；`streamingUpdateIntervalMs` 默认 500（`chat.ts:380`）；占位 "..." 可设为 null 或自定义。`startTyping` 见 `handling-events.mdx:45`。

**LangBot**
- `platform/sources/telegram.py:674-766` `reply_message_chunk`：流式用 `edit_message_text` 编辑持久消息；媒体不能编辑进文本流，终态附件另发（693）。`platform/processing_indicator.py` 给适配器一个"处理中"钩子（具体平台实现 `未确认`）。

### 点 5：长答案、Markdown、表格、图片

**Hermes**
- Slack：`MAX_MESSAGE_LENGTH = 39000`（`slack/adapter.py:999`，留出 40000 的余量），`truncate_message` 拆分，Markdown 转 mrkdwn；可选 `rich_blocks` 出原生 Block Kit 表格，超过 100 行/20 列/1 万字符时降级为等宽对齐（`slack.md:420-432`）。
- Telegram：Markdown 转 MarkdownV2（`telegram/telegram_markdown.py`），表格转项目符号（`convert_table_to_bullets`，`gateway/platforms/helpers.py`）；`telegram_send_errors.py` 处理解析失败。
- 文件/图片：`send_image`、`send_document`、`send_multiple_images`；本地路径要过 `validate_media_delivery_path`（拒绝 .env、~/.ssh 等，严格模式只允许缓存/白名单目录）（`base.py` 约 1330 行以后）。

**OpenClaw**
- Slack 文本块 `textChunkLimit` 默认 8000，`chunkMode="newline"` 按段落拆分（`docs/channels/slack/media.md:37-38`）；长文件说明用第一块作上传评论，余下续发。`docs/channels/slack/rich-messages.md`：原生 Block Kit 图表与表格；`docs/concepts/markdown-formatting.md` 另有。

**Chat SDK**
- `posting-messages.mdx:27-35`：传 `{markdown}`，由 SDK 解析成 mdast，各适配器转成平台格式，Slack 用原生 `markdown_text`；卡片 Table 在 Slack/Teams/GitHub/Linear 原生，Discord 卡片里是 GFM，其余补空格对齐（`cards.mdx:254-256`）；Discord 单条 2000 字（`adapter-discord/src/index.ts:111`）；Telegram `splitMarkdownAst/splitText`（`adapter-telegram/src/index.ts:69`）。上传：`files`，Telegram 上 `files` 总以文档上传，`attachments` 保留媒体类型（`files.mdx:48-50`）。

**LangBot**
- `pipeline/longtext/longtext.py`：`strategy` 为 `none` / `image`（渲染成图片，缺字体时降级为 `forward`）/ `forward`（合并转发）。表格与代码块的转换：`未确认`（各平台适配器内）。

### 点 6：运行中用户再发消息

**Hermes**：基类 `_busy_text_mode` 默认 `interrupt`（`base.py:1864`）；配置 `display.busy_input_mode`：`interrupt`（默认）/ `queue` / `steer`（`run_config_loaders.py:421-424`；`cli.md:471-482`）。两道守卫：基类把消息放进 `_pending_messages`，运行器拦截 `/stop /new /queue /status /approve /deny`（`gateway/AGENTS.md:19-26`）。有文字去抖与上限（`DEFAULT_BUSY_TEXT_DEBOUNCE_SECONDS`），队列在 TUI 文档里称"durable session queue"。

**OpenClaw**：`docs/concepts/queue.md:30-52`：默认 `mode: "steer"`，`debounce` 500ms，`cap: 20`，`drop: "summarize"`。四种模式：`steer`（注入当前运行，包括工具执行中）、`followup`（排队，当前运行结束后再跑）、`collect`（合并成一次后续）、`interrupt`（中止当前并跑最新）。Discord guild 里不同人的消息各自排队，不互相 steer（`queue.md:41`）。`docs/concepts/queue-steering.md:118`："Stopping already-running work is a different intent from redirecting future work."

**Chat SDK**：`concurrency.mdx:13-30`：默认 `drop`（直接丢弃，抛 `LockError`）；`queue`（只派发最新一条，中间的放进 `context.skipped`）、`debounce`、`burst`、`concurrent`。

**LangBot**：`pipeline/process/handlers/chat.py:128-145`：先 `try_claim_steering_from_query`（有正在跑的 agent 就认领为插话），找不到 runner 则正常新开一轮；否则按会话信号量排队（`sessionmgr.py:309,339`，并发数 `concurrency.session`）。

### 点 7：取消

**Hermes**：`/stop` 属 `interrupt_then_dispatch`，绕过两道守卫即时生效（`hermes_cli/commands.py:59`；`gateway/AGENTS.md:19-26`）。Slack 线程里原生斜杠命令被 Slack 拦截，用 `!stop` 前缀代替（`slack.md:311-322`）。无专门的"停止按钮"（`未确认`；有 `send_exec_approval` 审批按钮）。

**OpenClaw**：`/stop`（`docs/tools/slash-commands.md:285,712`："Abort the current run" 且停掉该运行的命令）；Slack 原生 Stop 按钮需要 `agent_session_stopped` 事件（`manifest-and-scopes.md:157`）。

**Chat SDK**：无命令；`thread.signal`（AbortSignal）传给模型调用，Slack Agent 消息里点原生停止按钮即中止（`streaming.mdx:30-40`；`adapter-slack/README.md:588-617` `onAgentSessionStopped`）。

**LangBot**：`/stop` 命令；`pipeline/controller.py:40-60,98-106` `_handle_stop_command` 调 `agent_run_orchestrator.cancel_active_from_query`；群里默认只能停自己的，权限等级 >= 2 才能停别人。

### 点 8：权限

**Hermes**：`gateway/authz_mixin.py`：`<PLATFORM>_ALLOWED_USERS`、`<PLATFORM>_ALLOW_ALL_USERS`、`GATEWAY_ALLOW_ALL_USERS`，默认拒绝（860 行起注释 "default deny"）；Telegram 另有 `TELEGRAM_GROUP_ALLOWED_USERS`、`TELEGRAM_ALLOWED_CHATS`、`TELEGRAM_GROUP_ALLOWED_CHATS`（`telegram.md:301-334`）。陌生私聊行为 `unauthorized_dm_behavior`：`pair`（默认）/ `ignore` / `decline`，decline 可自定义文案（`gateway/config.py:673-674`，`slack.md:874-877`）。配对码 `gateway/pairing.py:1-40`：8 位、32 字符无歧义字母表、1 小时过期、每平台最多 3 个待批、同一用户 10 分钟 1 次、5 次失败锁定 1 小时、数据文件 chmod 0600，由主人在 CLI 批准。文档要求"Always set `SLACK_ALLOWED_USERS`"（`slack.md:1098`）。按钮点击与消息同一套授权。

**OpenClaw**：`docs/channels/pairing.md:14-60`：`dmPolicy` 为 `pairing`（默认）、`allowlist`、`open`（须在 allowFrom 里写 `"*"` 才算公开）；配对码 8 位、1 小时、每渠道账号最多 3 个待批；可在控制台或 CLI 批准；批准只给私聊权限，不给群权限。Slack 频道名单用稳定频道 id 作键（`access-control.md:220`）；`allowFrom` 用成员 id。

**Chat SDK**：文档里没有允许名单概念（grep `allowlist|authoriz|allowedUser` 在 docs 无命中）；需自己在 handler 里判断。

**LangBot**：`pipeline/bansess/bansess.py:20-40` `trigger.access-control.mode` 取 `whitelist` / `blacklist`，列表为会话 id；无配对码（`未确认`）。

### 点 9：配置

- Hermes：`~/.hermes/config.yaml`（行为）与 `~/.hermes/.env`（token、允许名单），文档要求 `.env` 权限 600（`slack.md:207-213,1103`）；各平台同时开，每平台一个适配器；`platforms.<name>.extra` 放平台专属项（`cli-config.yaml.example:1507-1560`）。Slack 需要 `SLACK_BOT_TOKEN` 与 `SLACK_APP_TOKEN`（`slack.md` Step 8，具体变量名 `未逐行核对`）。
- OpenClaw：`~/.openclaw/openclaw.json`（JSON5，`gateway/configuration.md:10`）；token 可写成 `{ source: "env", provider: "default", id: "SLACK_BOT_TOKEN" }` 引用（`channels/slack/setup.md:217`）；没有 `channels.slack` 块时不会自动从 `SLACK_*` 环境变量启动（`access-control.md:224`）；`channels.<channel>.defaultAccount`、`accounts.*` 支持同渠道多账号。
- Chat SDK：代码即配置；token 默认读环境变量；`create-chat-sdk` 脚手架生成 `.env.example`（README）。
- LangBot：每个平台一份 yaml 表单定义（`slack.yaml`：`socket_mode`、`app_token`、`webhook_url` 等，`show_if` 条件显示），值存数据库，在 Web UI 填写。

### 点 10：失败处理

- Hermes：Telegram 重连阶梯、`start_polling` 带 30 秒超时、看门狗识别 getUpdates 停滞（`telegram/adapter.py:316-343`）；Slack 去重重连后重放的事件，窗口 1 小时（`slack/adapter.py:888-901`）；agent 出错回复 `turn_error: "Sorry, I encountered an error."`（`locales/en.yaml:672`）；`edit_message` 不支持则退回新发。
- OpenClaw：Socket Mode 重启退避约 2 秒到 30 秒，永久性凭据错误不重试（`slack/transports.md:27,71`）；一轮被接受后意外失败，在私聊和被明确点名的会话里回一条紧凑错误回复，且不重放（`concepts/messages.md:149`）；尾部有 `observeSuppression` 等回执记账。
- Chat SDK：类型化错误 `ChatError` / `RateLimitError`（`retryAfterMs`）/ `NotImplementedError` / `LockError`（`error-handling.mdx:9-52`）。
- LangBot：`pipeline.output.misc.exception-handling`：`show-error`（回显异常文本）/ `show-hint`（默认，通用提示 "Request failed."）/ `hide`（`chat.py:289-300`）。

### 附：Python 库当前版本与许可证（PyPI JSON、GitHub API、LICENSE 原文，2026-11-23）

| 库 | 最新版（上传日） | 许可证 | 备注 |
|---|---|---|---|
| slack_bolt | 1.30.0（2026-07-15） | MIT | GitHub bolt-python 最近推送 2026-11-18；`requires_python >=3.7` |
| slack_sdk | 3.48.0（2026-11-20） | MIT | Socket Mode 客户端在此 |
| aiogram | 3.34.0（2026-11-22） | MIT | `requires_python <3.16,>=3.11`；仓库最近推送 2026-11-23 |
| discord.py | 2.7.1（2026-03-03） | MIT | 仓库最近推送 2026-09-07；`requires_python >=3.8` |
| python-telegram-bot | 22.8（2026-06-12） | LGPL-3.0-only | 见下 |

python-telegram-bot：`pyproject.toml:12` `license = "LGPL-3.0-only"`，`license-files = ["LICENSE","LICENSE.dual","LICENSE.lesser"]`；PyPI 元数据 `license_expression = LGPL-3.0-only`，classifier 为 LGPLv3；仓库 `LICENSE` 文件开头是 GPL-3 全文，`LICENSE.lesser` 是 LGPL-3 全文，GitHub API 探测显示 GPL-3.0（`LICENSE.dual` 存在，内容 `未读`）。结论：声明（PyPI、pyproject）是 LGPL-3.0-only；仓库根 `LICENSE` 是 GPL-3 文本的不一致仍在。LangBot（Apache-2.0）直接依赖它，说明有项目接受这种用法；这不是法律意见。

## 3. 多数做法

按 4 个实现里至少 3 个一致算多数（Hermes 与 OpenClaw 同源，下面标注哪些只有两票）。

1. 机器人跑在独立的常驻进程里，agent 就在同一进程内调用，不走网络调用 agent（Hermes、OpenClaw、LangBot；Chat SDK 是库，同样同进程）。
2. 平台抽象窄：必需的只有"连接/断开/发送/（取会话信息）"，编辑、typing、上传、按钮、流式都是可选能力，缺了就降级（Hermes `edit_message` 默认失败则新发；OpenClaw 只声明真正支持的 capability；LangBot `text_fallback=True`；Chat SDK 多个可选方法）。
3. 会话键由"平台 + 会话类型 + 频道/聊天 id + 线程 id（+ 用户 id）"拼成；有线程的平台线程一个会话（Hermes、OpenClaw、Chat SDK、LangBot 都含线程 id）。
4. 群/频道里要 @ 才触发；在机器人已参与的线程里可免 @（Hermes、OpenClaw、Chat SDK 的 subscribe 模型；LangBot 靠可配规则）。
5. 澄清：支持按钮的平台用原生按钮，且都带"自己输入/Other"的出口，不支持的平台回退成文本编号；按钮的回调标识里编入提问 id 与选项序号（Hermes、OpenClaw、LangBot；Chat SDK 靠自己拼）。点选后原地改写卡片。
6. 进度：先发一条占位消息，再节流编辑同一条；Telegram 约 1 次/秒；Slack 优先原生流式；默认编辑间隔在 0.5 到 0.8 秒（Hermes 0.8、Chat SDK 0.5、OpenClaw/LangBot 未确认具体数）。
7. 长文本按平台上限拆分，Markdown 转各平台方言，表格在不支持的平台降级（项目符号或等宽对齐）（Hermes、OpenClaw、Chat SDK）。LangBot 不同：拆成图片或合并转发。
8. 取消是 `/stop` 命令，且在"运行中"也要能生效（Hermes、OpenClaw、LangBot；Chat SDK 靠 AbortSignal）。Slack 线程里斜杠命令用不了，Hermes 的解法是 `!stop`。
9. 权限：默认拒绝或配对，允许名单按平台用户 id 写（Hermes、OpenClaw 两票，同源；LangBot 用会话 id 名单；Chat SDK 没有）。频道名单用频道 id 而不是名字。
10. token 放配置文件旁的 env/独立 secrets，不进代码；多平台在同一进程里同时启用（Hermes、OpenClaw、Chat SDK、LangBot 都是）。
11. 平台断线靠库自动重连加自己的退避/看门狗，对 Slack 重连重放的事件做去重（Hermes 1 小时窗口，Chat SDK 有消息去重，OpenClaw 退避 2 到 30 秒）；发送遇 429 读 `Retry-After` 重试。
12. agent 出错时 IM 里给一条简短通用提示，不回显完整堆栈（Hermes、OpenClaw、LangBot 默认 show-hint；LangBot 另可选回显）。

## 4. 各实现分歧很大、需要人定的点

1. **运行中再发消息**：四个实现四种默认——Hermes 默认打断（interrupt）、OpenClaw 默认插话注入（steer）、Chat SDK 默认丢弃（drop）、LangBot 先尝试插话否则排队。本仓库后端已有"插话（202 steer）"，但有一个已知窗口：`result` 已写而进程未退出时 202 但送不到（`im-entry.md` 题 4）。这决定 IM 里发第二条消息是"插话""新一轮""丢弃"还是"打断"。
2. **私聊要不要一人一个长会话**：OpenClaw 默认所有私聊折进一个 main 会话（个人助理）；Hermes、LangBot 按聊天 id 隔离；本仓库是 data agent，会话与数据源绑定，更像后者，但"新话题何时新开"没有共识（Hermes 靠 `/new`，OpenClaw 靠 `/new` 与线程，Discord 靠自动建线程）。
3. **要不要流式编辑**：Hermes 默认关闭流式（`streaming.enabled=False`），OpenClaw 默认 `progress`（进度卡），Chat SDK 默认开且带占位。data agent 的一轮往往很长（SQL、多个工具），"实时刷文字"、"进度卡/状态行"、"只发最终答案"的取舍无多数。
4. **澄清是否一律按钮**：OpenClaw 只给"单题单选"出按钮；Hermes 对 `clarify` 出按钮；Chat SDK 没有内置。本仓库澄清带 2 到 5 个选项，Slack 一个 actions 块 5 个元素上限、按钮标签约 30 字被截断（Hermes 注释 #78115，其解法是正文放编号列表、按钮只写数字）。Telegram `callback_data` 64 字节。是否沿用"点选 = 发一条普通消息"（本仓库契约）需要定。
5. **授权模型**：配对码（Hermes、OpenClaw 默认）还是纯允许名单（LangBot、本仓库单用户场景可能够用），以及 Slack 要不要按邮箱匹配（nao）。单用户本机场景下配对码的价值有限，但"未授权的人 @ 到机器人时回什么"（pair/ignore/decline）没有共识，Chat SDK 与 LangBot 无配对。
6. **适配器与后端的边界**：同进程调 `Conversations`（Hermes、LangBot）还是走本机 HTTP（本仓库的 API 只绑 127.0.0.1，nao 也是同进程）。四个实现没有一个把适配器做成"HTTP 客户端 + 独立进程"，这是本仓库的新选择；Chat SDK 是库形态，最接近"在你的服务里调后端"。
7. **配置存哪**：YAML + `.env`（Hermes）、JSON5 + env 引用（OpenClaw）、代码（Chat SDK）、数据库 + Web UI（LangBot）。
8. **长答案**：拆成多条（Hermes、OpenClaw、Chat SDK）还是转图片/文件（LangBot 的 `image`/`forward`，Slack 可用上传文件）；表格是否用 Slack 原生表格（Hermes `rich_blocks`、OpenClaw、Chat SDK 卡片 Table 都支持，但默认关或需要额外 scope），没有一致默认。
9. **取消的入口**：只有命令（Hermes、LangBot）还是加原生按钮（OpenClaw 的 Slack Stop、Chat SDK 的 `thread.signal`）。本仓库有取消 API，"运行中显示一个停止按钮"是否值得加，没有多数。
10. **群聊支持范围**：四个都支持群/频道，但本仓库是单用户本机，是否只做私聊先上线，各实现不能回答（它们都面向多人）。

## 5. 未确认项

- Hermes：agent 出错时在 Slack/Telegram 的具体展示（只确认了文案键 `turn_error`）；会话持久化的具体存储；停止按钮是否存在。
- OpenClaw：Telegram、Discord 的细节只读了通用文档，没读各渠道实现；`approvals` 流。
- Chat SDK：`approvals.mdx`、`modals.mdx` 没读；Discord Gateway 保活细节。
- LangBot：群里响应规则清单、会话持久化范围、`processing_indicator` 的各平台实现、错误文案（只读到 `exception-handling` 三档）。
- AstrBot：AGPL-3.0，未读。OpenHands：开源仓库内无 Slack 集成，云/企业版实现未核实。
- 各 IM 平台自身限制（字符数、回调时限）沿用 `im-entry.md` 的取证，这里只引了各实现里写的数字。
