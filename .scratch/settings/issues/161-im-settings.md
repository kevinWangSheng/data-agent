# 161 — IM 里的设置：`!configure`、频道改动公告、临时覆盖、"在网页打开"链接、作用域映射

Status: ready-for-agent
Blocked by: 158
Spec: ../spec.md（Implementation Decisions"IM（S4、S8、S1）""临时覆盖（S4）""作用域与权限"的作用域部分）
Claimed by: —
Branch: —

## Goal

- `@bot !configure`（Slack）/ `/configure`（Telegram、Discord）：回一个只有本人可见（Slack ephemeral；Telegram、Discord 私信）的链接，打开本频道的设置页。
- 频道层的任何设置改动，bot 在该频道发一条"谁改了什么"（S1）。IM 里没有写频道设置的命令，改动来自设置页（160）或别的写入方；公告挂在 158 存储的频道层写路径上。
- 临时覆盖（S4）：频道或线程里任何人可在消息里写覆盖（语法照 157 契约 4）。模型、effort、数据源、MCP 一律对整个线程生效直到再换（定的细节第 9 条）；模型和 effort 在 `[policy]` 范围内，数据源和 MCP 只能从本频道已开放的里挑；Skill 和说明在线程开始时固定。越界：bot 回复不允许，不悄悄替换。用了什么覆盖记进 146 的 `config` 事件（覆盖是生效配置的一部分，不另加事件，不改 `agent.py`；定的细节第 10 条）。
- bot 每次回复带"在网页打开"链接，指向这次运行的详情页（S8），地址由 `[server].public_url` 拼出（定的细节第 7 条）；链接不带权限。
- 会话索引由本工单写：每个 IM 回合起运行后记 线程 → 运行 id、所属频道（私聊记本人）（定的细节第 3 条）。`im/threads.json` 照旧是 IM 自己的线程映射。
- "是否要 @ 才回复"（行为类设置）接到 adapter 的触发规则上；默认值保持现有触发规则（定的细节第 8 条）。
- 多个 Slack 工作区：配置写 `[im.slack.workspaces.<名字>]`，各写 token 的环境变量名；每个工作区一个 Socket Mode 连接（定的细节第 6 条、S5）。
- Telegram 私信发不出（用户没先和 bot 私聊过）时，在群里回一句提示"先私聊 bot 一次"，不带链接（定的细节第 14 条）。
- 作用域映射：Slack 消息带工作区 id；Discord 服务器、Telegram 每个 bot 一个工作区；频道 = Slack 频道 / Discord 频道 / Telegram 群；私聊 = 个人层（S6）。每个回合按这个作用域经 158 解析生效设置。

157 里标 `TICKET_161` 的测试全部转绿。

## Authority

- May edit：
  - `src/data_agent/im/`：`platform.py`（`Incoming` 加工作区 id、`Platform` 加只本人可见的发送，新字段和方法带默认值或默认实现，157 契约 7）、`adapter.py`（`!configure`、覆盖的解析与拒绝、链接、公告、作用域）、`slack.py`、`telegram.py`、`discord_bot.py`（各自的工作区 id、只本人可见的发送、`/configure` 命令注册）、`format.py`（链接的格式）。
  - `src/data_agent/web/conversations.py`、`web/launcher.py`：只在把作用域和单次覆盖传给运行时改；`Conversations.send` 的现有签名照旧能用（新参数带默认值）。
  - `src/data_agent/web/app.py`：只在频道层写入后触发公告时，接一个回调（设置页的写接口是 160 的；若 160 先合入，在它的写路径上挂公告）。
  - `src/data_agent/im/config.py`、`im/service.py` 和 143 建的配置模块里 `[im.*]` 的类型：`[im.slack.workspaces.<名字>]` 的形状和每个工作区一个平台实例。
  - 158 的模块：只加会话索引里"线程开始时的快照""线程的当前覆盖"的读写和写入调用（若 158 没提供），不改表结构以外的东西。
  - `tests/acceptance/`：唯一例外，删掉 157 文件里的 `TICKET_161` 标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩）。
  - `tests/`（除 `tests/acceptance/`）：单元测试。
  - `docs/setup.md`：IM 一节加 `!configure`、覆盖语法、所需的 Slack scope。
- Must not edit：
  - `tests/acceptance/` 里除 `TICKET_161` 标记以外的一切（含 `test_im_*.py`、`im_support.py`）；`tests/fixtures/`。
  - 159 的身份与权限（只调用）；`web/src/`（160）；`src/data_agent/agent.py`（覆盖经生效配置文件传给运行，进 146 的 `config` 事件，不改 agent）。
  - `pyproject.toml`、`evals/`、`skills/`、`Dockerfile`、`deploy/`。
  - `Makefile`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。工单 agent 不能 source 模型的 env 文件，不调模型、不连真的 IM 平台；真实回合由主会话在部署后跑。

## Context

行号取自 main `30a18e9`。140（in-review）、143、144、153、158 会移动这些行，认领时按符号名在当前 main 上核对。

- **决定**：spec"IM""临时覆盖"；ADR-0012 决定 2、4、6；`decisions.md` S1、S2、S4、S5、S6、S8 原文；`decisions.md`"建 settings 工单时主会话定的细节"第 3、6–10、14 条。
- **验收测试**：157 的 `test_settings_im.py` 里标 `TICKET_161` 的；契约 3、4、7、8、9（作用域标识、覆盖语法、平台新方法与工作区配置的键、链接格式、`config` 事件里覆盖的字段与会话索引的形状）见 157 的测试 docstring 和人审结论。已有的 `tests/acceptance/test_im_*.py` 不改就通过。
- **现状**：
  - `Incoming`（`src/data_agent/im/platform.py:35-52`）没有工作区 id；`Platform`（`platform.py:105-130`）只有 `send`、`edit`、`typing`。
  - `Adapter.handle_message`（`src/data_agent/im/adapter.py:238-275`）：触发规则（私聊、@、bot 的线程）、允许名单、`/new`、`stop`、空消息，然后 `send_turn`（`:298-316`）经 `conversations.send(conversation, text)`（`:302`）。`!configure` 和覆盖的解析接在允许名单之后、`send_turn` 之前。被拒时的回复照 `Refused` 的做法（`:303-305`）。
  - 线程映射 `<runs_dir>/im/threads.json`（`adapter.py:96-97`、模块 docstring `:16-25`）；会话索引（158 建的表）由本工单写，`threads.json` 不变。
  - IM 配置今天是每个平台一张表（`src/data_agent/im/config.py:64-124` `load_config`、`read_table`，token 键 `:41-45`）；143 把它并进 `config.toml` 的 `[im.*]`。`[im.slack.workspaces.<名字>]` 在 143 合入后的形状上加。
  - Slack：`to_incoming`（`im/slack.py:176-196`）没读事件的 `team`；发送经 `chat_postMessage`（`slack.py:131-146`）；只本人可见用 `chat_postEphemeral`（slack_sdk `async_client.py:3562`）。`/new` 在 Slack 上没有对应命令，Slack 用 `@bot !configure`（spec）。
  - Telegram：`start` 里 `set_my_commands` 注册 `/new`（`im/telegram.py:157-158`），`/configure` 照这个加；`to_incoming`（`telegram.py:284`）。私信要用户先和 bot 私聊过，发不出时在群里回一句提示"先私聊 bot 一次"，不带链接（定的细节第 14 条）。
  - Discord：`/new` 是全局斜杠命令，已用只本人可见的回复（`im/discord_bot.py:34-37`、`:235-240`），`/configure` 照这个做。
  - 触发规则由 `tests/acceptance/test_im_triggers.py:29-62` 钉住。
- **"在网页打开"链接**：运行详情页地址 `<base>runs/<id>`（`web/src/run-page/route.ts` `routeUrl`）；公开地址是 `[server].public_url`（定的细节第 7 条），链接格式见 157 契约 8。
- **公告**：频道层改动的公告文字写"谁（平台用户的显示方式）改了哪个键、改前改后"；密钥类只写"改过"（S9）。
- **覆盖记进运行事件**：写进 146 的 `config` 事件（定的细节第 10 条）。覆盖经生效配置文件传进运行（158 的那条路），子进程照旧写 `config` 事件，所以不改 `agent.py`。
- **"是否要 @ 才回复"**：归本工单（定的细节第 8 条）。在作用域解析之后读它，接在 `handle_message` 的触发判断（`adapter.py:243`）上；默认值保持现在的触发规则。
- **取证**：`docs/research/2026-11-27-chatbot-agent-config.md` 第 3 条结论（Dust `+Agent`、Cursor `repo=`/`model=`、Devin `!org` 的消息内覆盖）；线程内换模型照 Claude in Slack（`decisions.md` S4 原文）；`docs/research/2026-11-23-im-gateway-designs.md`（Hermes、OpenClaw 的命令与权限）。
- 只用 uv。

## Acceptance

- [ ] 157 里标 `TICKET_161` 的测试去掉标记后通过；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行
- [ ] 已有的 `tests/acceptance/test_im_*.py` 不改就通过
- [ ] 单元测试：覆盖语法的解析（各种写法、和正文分开、越界）；三个平台的工作区 id；`[im.slack.workspaces.<名字>]` 的加载与报错；会话索引的写入；Telegram 私信发不出时的提示；公告里密钥类只写"改过"
- [ ] `docs/setup.md` 写了 `!configure`、`/configure`、覆盖语法、Slack 应用要加的 scope（`users:read`、`channels:read` 等，照实际用到的 API 核实）
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 输出。
- `uv run pytest tests/acceptance/test_settings_im.py tests/acceptance/test_im_*.py -v`。

交给主会话（部署后，最终回复写出步骤）：
1. 在 Slack 频道 `@bot !configure`，确认只有自己看到链接、链接打开本频道设置。
2. 在设置页改频道说明，频道里出现公告。
3. 在线程里写一条带模型覆盖的问题，确认运行用了该模型（运行详情或事件），同一线程下一问仍用它；写一个 `[policy]` 不允许的模型，bot 回复不允许。
4. 点回复里的"在网页打开"，看到运行详情。
5. Telegram 或 Discord 跑一次 `/configure`。花费小，按小规模运行处理。

## Stop conditions

- 157 的某个验收测试看起来不对，或要改它才能转绿。
- 要改已有的 `test_im_*.py` 或 `ConversationsLike.send` 的现有签名才能做到。
- 覆盖进不了 146 的 `config` 事件，要改 `agent.py`。
- 覆盖语法在 157 人审结论里没定。
- 第二轮审查修复后仍不过。
- 不停止、不重启本机的 Docker/colima 守护进程。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-27（主会话建票）：IM 里的设置。等 158 合入。改动在后端，照常跑 `da-review` 和 `eng:code-review`。`!configure` 的链接按 157 契约的地址格式拼，不依赖 160 已合入；IM 里只有覆盖（任何人可用，S4），没有写频道设置的命令，所以不需要 159 的权限检查。公告挂在频道层的写路径上：160 没合入时，挂在 158 存储的写函数上（或留一个回调），最终回复写明。
- 2026-11-27（主会话）：按"建 settings 工单时主会话定的细节"第 3、6–10、14 条改票：本工单写会话索引；`[im.slack.workspaces.<名字>]`；"是否要 @"归本工单；覆盖都对整个线程生效、记进 `config` 事件；链接用 `[server].public_url`；Telegram 私信回退。去掉"公开地址没定""多工作区形状没定""记覆盖要改 `agent.py`"三条停止条件（最后一条改成"进不了 `config` 事件"）。
