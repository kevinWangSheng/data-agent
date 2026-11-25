# 131 — IM 入口：公共部分（接口、会话映射、澄清、进度、取消、排版、权限、配置、出错）、Slack 实现、`--im`、文档

Status: done
Blocked by: 130（done）
Spec: ../spec.md
Claimed by: claude (opus-5-5) 2026-11-24
Branch: worktree-131-im-common-and-slack

## Goal

`data-agent web --im <配置>` 在网页进程里起 Slack 机器人（slack_bolt，Socket Mode）：允许名单里的人私聊或在频道里 @ 它提问，它在消息的线程里回复，和网页对话共用同一套对话、运行目录、事件文件。130 定下、人审过的契约（`src/data_agent/im/` 的空壳和 130 Comments 的契约 1–9）全部实现，130 合入的 `TICKET_131` 验收测试全部转绿（只删标记）。slack_bolt 是可选依赖 `data-agent[im]`。README 和 `docs/setup.md` 写明用法和建 Slack app 的步骤。真实 Slack 的验收由人建好 app、主会话跑（见 Evidence required），不阻塞本工单交回。

## Authority

- May edit：
  - `src/data_agent/im/`：实现 130 的空壳（`adapter.py`、`config.py`、`format.py`、`progress.py`、`service.py`）；新增 Slack 实现模块（如 `src/data_agent/im/slack.py`）和本工单需要的内部模块。**130 定下的名字、签名、字段、常量不改**（`platform.py` 的类型、`Adapter` 的构造和方法、`load_config`、`run_env`、`service.start`、`IM_DIR`、`THREADS_FILE`、`STOP`、`POLL_SECONDS`、`TABLE_ROWS`、`DISPLAY_CHART`、`PLATFORMS`、`TOKEN_KEYS`、`PREFIXES`）；可以加私有函数、字段默认值以外的内部实现。
  - `src/data_agent/cli.py`：只改三处——`main` 里 `serve_web(...)` 的调用把 `args.im` 传下去（`cli.py:399-401`）；`serve_web`（`cli.py:534-584`）读 `--im` 文件、在 `create_app` 之后调 `service.start`（契约 7）；`start_run`（`cli.py:562-571`）在给了 `--im` 时把 `config.run_env(os.environ, configs)` 作为 `env=` 交给 `Popen`（契约 8）。
  - `src/data_agent/web/app.py`：只在 `create_app`（`app.py:64-86`）里做 `service.start` 必需的最小改动：把已有的 `runs`、`conversations`（`app.py:76-78`，现在是闭包里的局部变量）放到 app 上（如 `app.state`），并让 lifespan（`app.py:80-84`）能在开始服务后启动、在 `launcher.shutdown` 之前停掉 IM 适配器。`create_app` 的签名、路由、HTTP API 不改。（理由见 Context "接线"。）
  - `pyproject.toml`、`uv.lock`：加 `[project.optional-dependencies]` 的 `im`（本工单只放 Slack 需要的包，见 Context "库"）；为让 CI 的 `make check` 能导入和类型检查 Slack 模块所需的最小改动（见 Context "库" 的约束）。不动其他依赖的版本。
  - `tests/acceptance/test_im_*.py`：**只删** `TICKET_131` 标记（45 个 `@TICKET_131` 装饰器和 8 个 `TICKET_131 = pytest.mark.xfail(...)` 定义），以及因此没用了的 import 或定义（AGENTS.md 硬规矩的唯一例外）。断言、夹具、输入、docstring 一字不改。
  - `tests/`：新增单元测试（Slack 消息格式 ↔ 接口的转换、下面"测试没钉、本工单要做的"各条）。`tests/fixtures/im/`（新建，需要时）：Slack 事件样例，只放假 id、不放 token。
  - `README.md`、`docs/setup.md`。
- Must not edit：
  - 130 合入的验收测试的断言、夹具、输入（`tests/acceptance/test_im_*.py` 除上面的标记、`tests/acceptance/im_support.py`）；`conftest.py`、其他验收测试；`tests/fixtures/` 里已有的文件。
  - `src/data_agent/web/conversations.py`、`launcher.py`、`runs.py`（spec `:23`"不改 HTTP API，不改对话逻辑"）；`src/data_agent/agent.py`、`events.py`（spec `:79`"不用另加遮罩"）；`src/data_agent/` 里上面没列的文件。
  - `web/`（前端）、`evals/`、`Makefile`、`.github/workflows/`、`.env*`、`skills/`、`configs/`。
  - 其他工单的文件（132 Telegram、133 Discord 的实现不在本工单做）、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap：工单 agent $0（不调模型，不连真实 Slack 工作区；工单 agent 加载不了模型环境）。真实 Slack 验收由主会话跑（上限 $2，见下面"主会话定的点"第 14 条和 Evidence required）。

## Context

代码现状按 main `d7b23c0` 核对；行号以本节为准。spec 和 130 里的 `cli.py:556-565`、`:568` 已过时（现在是 `:562-571`、`:574`）。

- **spec**：`.scratch/im/spec.md`。行为来源："进程与接口"`:20-27`、"会话映射"`:29-36`、"澄清、进度、取消"`:38-43`、"答案排版"`:45-50`、"权限"`:52-56`、"配置"`:58-79`、"出错"`:81-85`、"库"`:87-89`、"验收"`:116-124`。取证笔记：`docs/research/2026-11-23-im-entry.md`（题 3 三个平台的限制）、`docs/research/2026-11-23-im-gateway-designs.md`（上游做法，只参考设计，不复制代码，spec `:138`）。

- **130 的契约（人 2026-11-24 照 130 Comments 原样审过，已定，不再问）**：`.scratch/im/issues/130-im-acceptance-tests.md` Comments 的 "Contract 1–9" 和 "Decided beyond the item-9 list"。要点：
  - 平台接口 `src/data_agent/im/platform.py`：`Incoming`（`:34-52`，私聊还是群是 `direct: bool`）、`Button`（`:55-60`，`value` ≤64 字节 ASCII）、`Outgoing`（`:63-69`）、`Press`（`:72-83`）、`Capabilities(edit, buttons, typing)`（`:86-90`）、`Limits(max_chars, edit_interval)`（`:93-98`）、`Platform` 协议（`:105-128`：`start(on_message, on_press)` 连上后返回、之后依次 await 每个回调；`stop`、`send -> id`、`edit`、`typing`）。平台实现负责的事写在模块 docstring `:15-26`：Slack 的 `thread_id` 是消息所在线程，顶层消息用它自己的 `ts`；`text` 去掉 @ 机器人；`send`/`edit` 把公共 Markdown 转成 Slack mrkdwn；用户 id 一律字符串。
  - 公共入口 `src/data_agent/im/adapter.py`：`Adapter(platform, conversations, runs, allowed_users, clock=time.monotonic)`（`:102-113`），`start`、`on_message`、`on_press`、`poll`、`run`（`:115-133`）；行为全部写在模块 docstring `:1-72`（谁能得到回应、一轮的状态消息与节流、澄清、答案、没有答案的结束、停止、`Refused` 的回复、英文）。`ConversationsLike`（`:92-99`）。
  - 配置 `src/data_agent/im/config.py`：报错规则 `:20-30`，`PlatformConfig`（`:51-59`，`tokens` 不进 `repr`），`load_config`（`:62-64`），`run_env`（`:67-71`）。
  - 排版 `src/data_agent/im/format.py:1-20`；进度 `src/data_agent/im/progress.py:1-23`。
  - 启动 `src/data_agent/im/service.py:1-26`：`serve_web` 读文件（`load_config(path, os.environ)`，`ImConfigError` 像坏的 `--data-sources` 一样让服务起不来），`create_app` 之后、`uvicorn.run` 之前调 `start(app, configs)`；`start` 在调用时从模块上取（`from data_agent.im import service`），验收测试会换掉它；`data-agent[im]` 装没装在 `start` 里查（提示 `uv sync --extra im`），**不能**在 `serve_web` 里查（验收测试不装这些库）；没有 `--im` 时不调 `start`、不导入任何平台库。`start` 给每个平台建一个 `Adapter`，用 app 自己的 `Conversations` 和 `RunsDir`（同一个 launcher，"同时只跑一条"跨网页和所有平台），app 开始服务时启动，app 关闭时在 launcher 停掉剩下的运行之前停掉；适配器出错只写日志，不影响网页。

- **要转绿的验收测试**：`uv run pytest tests/acceptance/test_im_*.py --collect-only -q` 收集 60 个（main `d7b23c0`）；其中 59 个现在 XFAIL，1 个守门 `test_data_agent_web_without_im_starts_no_adapter`（`tests/acceptance/test_im_config.py:203-207`）现在 PASSED、必须继续 PASSED。标记分布：`test_im_config.py` 7、`test_im_format.py` 9、`test_im_steer_cancel.py` 6、`test_im_triggers.py` 6、`test_im_progress.py` 5、`test_im_sessions.py` 4、`test_im_clarification.py` 4、`test_im_errors.py` 4 个 `@TICKET_131`，8 个文件各一个定义。
  - 接线相关的三条在 `test_im_config.py`：`serve`（`:156-188`）换掉 `create_app`、`uvicorn.run`、`service.start`；`test_data_agent_web_im_starts_the_adapters_with_the_app`（`:191-200`）要 `service.start` 被调用一次、拿到 `create_app` 的返回值和只含 `slack` 的配置；`test_a_run_starts_without_the_im_tokens`（`:210-236`）调截下来的 `start_run`，要 `Popen` 收到 `env=`，里面没有 Slack 的两个变量和值，`PATH`、`DATA_AGENT_API_KEY`、文件没点名的 `DATA_AGENT_TELEGRAM_BOT_TOKEN` 原样都在。
  - token 不外泄：`test_no_token_value_reaches_the_log_the_output_or_the_platform`（`:241-287`）在 DEBUG 日志、stdout/stderr、`<runs-dir>/im/` 下所有文件、`repr(configs)`、交给对话替身的参数、假平台收到的文字和按钮、配置报错文本里都查 token 值。

- **接线（为什么 Authority 里有 `app.py`）**：
  - `create_app`（`src/data_agent/web/app.py:64-86`）在函数里建 `runs = RunsDir(...)`、`launcher = Launcher(...)`、`conversations = Conversations(...)`（`:76-78`），lifespan 只在退出时 `await to_thread.run_sync(launcher.shutdown)`（`:80-84`），返回的 `FastAPI` 上没有这几个对象（文件里没有 `app.state`）。`service.start(app, configs)` 只拿到 app，所以不改 `create_app` 就拿不到 app 自己的 `Conversations`、`RunsDir`，也排不进"在 `launcher.shutdown` 之前停"。
  - 顺序：在 `create_app` 外面包 `app.router.lifespan_context` 的话，退出时内层（`launcher.shutdown`）先跑、外层后跑，和 `service.py:14-15` 要的顺序相反；所以停适配器要在 `app.py` 的 lifespan 里、`launcher.shutdown` 之前。
  - `service.start` 是同步的、在 `uvicorn.run`（`cli.py:584`）之前被调用，那时还没有事件循环；`Platform.start` 是协程，要在 lifespan 里（uvicorn 的事件循环上）启动，`Adapter.run` 作为那个循环上的任务跑，关闭时取消。
  - `Conversations` 的方法是同步的、持有 launcher 的锁，网页路由用 `to_thread.run_sync` 调（`app.py:160`、`:166`、`:177`）；适配器同样放到工作线程里调（`adapter.py:111-112`）。
  - `start_run`（`cli.py:562-571`）现在 `subprocess.Popen([...], cwd=cwd, start_new_session=True, stdin=subprocess.PIPE)`，没有 `env=`，子进程继承网页进程的全部环境；运行的遮罩只从子进程自己的环境读（`src/data_agent/agent.py:2202-2211`，里面没有 IM token），所以去掉变量就够了（spec `:79`）。
  - `--im` 的空壳已在 `cli.py:374-379`（130 加的，`type=Path`），`main` 里 `serve_web(web, args.runs_dir, args.port, args.data_sources, args.mcp_servers)`（`cli.py:400`）还没传它。

- **对话模块与事件（只读调用，不改）**：
  - `Conversations.create`（`src/data_agent/web/conversations.py:57`）、`send(conversation, text, network=True)`（`:97`）、`cancel`（`:112`）；一轮的参数（`:136-146`）总带 `--charts`、`--sandbox`，`network` 为真时 `--network`，有 `--mcp-servers` 时也带，所以 IM 只调 `send()` 就和网页对话的运行选项一样（spec `:27`）。
  - `Refused(status, detail)`（`src/data_agent/web/launcher.py:67`）；"同时只跑一条"在 `:214`。
  - `RunsDir`（`src/data_agent/web/runs.py:40`）：`read(run_id, offset)`（`:103`）、`ended`（`:74`）、`has_result`（`:79`）、`record`（`:61`）。没有 `result` 的运行由 `Launcher.finish` 写 `web-run.json` 的 `status`，`cancelled` 或 `failure`（`launcher.py:309-320`）。
  - 事件字段见 130 Context "事件"一节（`130-im-acceptance-tests.md:54-68`）。`result.ended_ts` 是工单 122 加的，122 现在 `in-review`、没合入，`src/` 里还没有这个字段；`progress.py:17-20` 已定"有就用，没有用 `result.ts`"。
  - 步骤分组照网页 `web/src/run-page/tools.ts` 的 `shortTool`（`:24`）、`toolKind`（`:30`），Python 端照抄逻辑（不改 `web/`）。

- **库（已核实）**：
  - `slack-bolt` 1.30.0 存在，PyPI 当前最新版，2026-07-15 发布，License MIT（PyPI 元数据 `license: MIT`、分类 `License :: OSI Approved :: MIT License`，wheel 的 `METADATA` 同；`https://pypi.org/pypi/slack-bolt/1.30.0/json`，2026-11-24 读）。`requires_dist` 只有 `slack_sdk<4,>=3.38.0`；`slack-sdk` 当前 3.48.0，MIT。
  - **aiohttp 要显式写进 extra**：`slack_bolt/app/async_app.py:8` 顶层 `from aiohttp import web`，`AsyncSocketModeHandler` 的默认实现是 aiohttp 版（`slack_bolt/adapter/socket_mode/async_handler.py:1-3`），而 slack-bolt 不声明 aiohttp。本仓库的 `uv.lock` 里 aiohttp 3.14.5 只因 dev 组的 litellm 而在（`uv.lock:40-41`），`uv export --frozen --no-dev --no-emit-project` 里没有它（2026-11-24 实测 0 行）。aiohttp 3.14.5 的 license 是 `Apache-2.0 AND MIT`（PyPI `license_expression`）。
  - Socket Mode 的 asyncio API（读的是 1.30.0 wheel 源码）：
    - `AsyncSocketModeHandler(app: AsyncApp, app_token=None, logger=None, web_client=None, proxy=None, ping_interval=10, loop=None)`（`slack_bolt/adapter/socket_mode/aiohttp/__init__.py:65-90`）。
    - `connect_async()` 连上就返回；`close_async()` 断开并释放资源；`start_async()` 连上后 `await asyncio.sleep(float("inf"))`，永不返回（`slack_bolt/adapter/socket_mode/async_base_handler.py:28-50`）。`Platform.start` 要"连上就返回"，所以用 `connect_async`，`stop` 用 `close_async`。
    - **token 显式传入**：`AsyncApp` 的 `token` 缺省时读环境变量 `SLACK_BOT_TOKEN`（`slack_bolt/app/async_app.py:219`），handler 的 `app_token` 缺省时读 `SLACK_APP_TOKEN`（`aiohttp/__init__.py:81`）。都从 `PlatformConfig.tokens` 显式传，不依赖这两个环境变量。
    - 监听器注册：`AsyncApp.event`（`async_app.py:835`）、`message`（`:881`）、`action`（`:1115`）；Web API 客户端 `AsyncApp.client`（`:479`）。
  - 约束：`make check` 和 CI 都不装 extra——CI 是 `uv sync --locked`（`.github/workflows/check.yml:18`），basedpyright 的 `include` 有 `src` 和 `tests`（`pyproject.toml:70`），`typeCheckingMode = "standard"`（`:74`）。Slack 模块和它的转换测试在 CI 里要能导入、能类型检查；同时 `uv export --frozen --no-dev --no-emit-project`（Harbor 离线安装用的那条，`evals/harbor_agent.py:207-212`）里不能出现 slack-bolt、slack-sdk、aiohttp（spec `:89`）。
  - spec 要求不用 python-telegram-bot；aiogram、discord.py 由 132、133 加。

- **Slack 平台事实（官方文档，2026-11-24 读）**：
  - Socket Mode：在 app 设置的 Socket Mode 页打开开关；要先在 Basic Information 的 App-level tokens 生成 app-level token；Socket Mode 下事件和交互都不需要、也不能填 Request URL；Socket Mode 的 app 不能上公开的 Slack Marketplace（`https://docs.slack.dev/apis/events-api/using-socket-mode`）。
  - app-level token 的 scope `connections:write`："Grants permission to generate websocket URIs and connect to Socket Mode"（`https://docs.slack.dev/reference/scopes/connections.write`）；`apps.connections.open` 用 app-level token（`https://docs.slack.dev/reference/methods/apps.connections.open`）。
  - `chat.postMessage`：bot scope `chat:write`；`text` 建议不超过 4,000 字，超过 40,000 字截断；`thread_ts` 给父消息的 `ts` 就是在线程里回复（"use its parent instead"）（`https://docs.slack.dev/reference/methods/chat.postMessage`）。
  - Block Kit：section block 的 `text` 最长 3,000 字（`https://docs.slack.dev/reference/block-kit/blocks/section-block`）；按钮 `text` 最长 75 字、显示约 30 字截断，`action_id` 255，`value` 2,000（`https://docs.slack.dev/reference/block-kit/block-elements/button-element`）。按钮要用 blocks 发，所以带按钮的消息受 3,000 字的限制，不是 4,000。
  - 事件 scope：`message.channels` 要 `channels:history`；`message.im` 要 `im:history`（`https://docs.slack.dev/reference/events/message.channels`、`.../message.im`）。`message.channels` 页写 "To receive only messages sent to your app, subscribe to app_mention events instead"。
  - **未确认**（实现时查官方文档或用 slack_bolt 的事件样例核实，`docs/setup.md` 写到的每一条附链接和访问日期，查不到的标 `未确认`）：`app_mention` 和 `message.channels` 对同一条 @ 消息是否都会推送；机器人自己发的消息是否也作为 `message.*` 事件推回来（`bot_id`、`subtype` 字段）；私有频道 `message.groups`/`groups:history`、多人私聊 `message.mpim`/`mpim:history` 是否需要；App Home 的 Messages Tab 要打开才能私聊机器人；Slack bot 有没有可用的 typing 指示；bolt 的 async 监听器是否并发执行（`Platform.start` 要求依次 await 每个回调）；按钮回调 3 秒内要 `ack()`。

- **上游**：只参考设计，不复制代码（spec `:138`）。nao 的澄清是纯文本编号列表（`apps/backend/src/utils/messaging-provider.ts:456-462`，spec 引用，本工单未重核）。

- 只用 uv；每个 worktree 用自己的 `runs/` 目录。

## 测试没钉、本工单要做的

能写单元测试的都写（新文件，不改验收测试）：

- **Slack 转换**（spec `:114`）：用 Slack 事件 / `block_actions` 载荷的样例（照官方文档或 slack_bolt 自带测试里的形状手写，假 id），测：顶层消息 → `thread_id` 是它的 `ts`；线程回复 → `thread_ts`；私聊 → `direct` 为真；@ 机器人 → `mentioned` 为真且 `text` 去掉了 `<@机器人 id>`；机器人自己的消息、编辑、删除等带 `subtype`/`bot_id` 的事件不交给适配器；同一条 @ 消息只交给适配器一次；按钮点击 → `Press`（`message_id` 是按钮所在消息的 `ts`，`value` 原样）；`Outgoing` → `chat.postMessage`/`chat.update` 的参数（`thread_ts`、blocks 里的按钮只写编号、Markdown 转 mrkdwn）。用假的 web client 记录调用，不连网。
- **Markdown → mrkdwn**：粗体、链接、标题、代码块、`&<>` 转义各有用例。
- **限制**：Slack 的 `Limits.max_chars` 留出 mrkdwn 转换的余量，带按钮的消息不超过 section 的 3,000 字；`edit_interval` 约 2 秒（spec `:41`）。
- **发送失败重试一次，再失败写日志**（spec `:85`）；**遇到平台限流就跳过这次编辑**（spec `:41`）；**适配器出错只写日志、不影响网页**（spec `:84`）：各一个测试。
- **缺库提示**：没装 `data-agent[im]` 时 `--im` 报错并提示 `uv sync --extra im`（spec `:89`）；查库在 `service.start` 里（`service.py:9-10`）。测试用 monkeypatch 让导入失败。
- **没有 `--im` 不导入平台库**：`data-agent web` 不带 `--im` 时 `sys.modules` 里没有 `slack_bolt`（`service.py:8`）。
- **生命周期**：`create_app` 改动后，适配器在 lifespan 开始时启动、退出时在 `launcher.shutdown` 之前停（用假平台和 FastAPI 的 `TestClient`/lifespan 测）；没有 `--im` 时 `create_app` 的行为不变（已有 `tests/` 里的网页测试照旧通过）。
- **token 不外泄**：Slack 实现自己的日志（包括 slack_bolt、slack_sdk 的 logger 在 DEBUG 级）里没有 token 值；`--im` 文件写错时 `data-agent web` 的 stderr 没有 token 值。
- **README**：在 "Web UI" 一节后加一节（如 "Slack"）：用法 `data-agent web --im FILE`、示例 TOML（只写变量名）、`uv sync --extra im`、允许名单、频道可见性（spec `:56`）、已知限制（spec `:140`：插话的送不到窗口、频道里回答所有成员可见、没有对话总花费上限；图表只在网页看；Socket Mode app 不能上 Marketplace）。
- **`docs/setup.md`**：加一节"Slack 机器人（工单 131）"：建 app、打开 Socket Mode、生成 app-level token（`connections:write`）、bot scopes 和事件订阅、打开交互（按钮）、装进工作区、拿 bot token、两个 token 写进 `~/.config/data-agent/env`（`DATA_AGENT_SLACK_BOT_TOKEN`、`DATA_AGENT_SLACK_APP_TOKEN`）、找自己的 Slack 用户 id 填进名单、把机器人拉进频道。每一步附官方文档链接和访问日期，没核实的标 `未确认`；不写任何真实 token、工作区名、用户 id。

## 主会话 2026-11-24 定的点（实现时照做，不是停下的理由）

都是由人已批准的 130 契约或 spec 推出来的实现细节，主会话对照代码定：

1. `app.py` 的窄改动照 Authority 那条做（`app.py:76-78` 三个对象是闭包局部变量，没有 `app.state`，契约 7 不改就接不上；停适配器要排在 `launcher.shutdown` 之前）。
2. CI：`slack-bolt==1.30.0` 和 `aiohttp` 同时写进 `im` extra 和 `dev` 组。CI 只跑 `uv sync --locked`（`.github/workflows/check.yml:18`），dev 组里有它们，basedpyright 和转换测试在 CI 里照常跑；`uv export --no-dev` 仍然没有它们，Harbor 离线安装不变（人 2026-11-24 定的"可选依赖"的目的）。
3. 本工单的 `im` extra 只放 slack-bolt 和 aiohttp；aiogram、discord.py 由 132、133 加。
4. 配置里写了 `[telegram]`/`[discord]` 但实现还没有（132、133 之前）：`service.start` 报错并点名平台（"not implemented yet"），`serve_web` 转成 exit 2；不静默跳过。
5. 缺 extra：`service.start` 抛清楚的错误（带 `uv sync --extra im` 的提示），`serve_web` 转成 `parser.error`（exit 2），不出 traceback。
6. 多个平台共用 `threads.json`：进程内一份映射，各适配器共用，加锁写盘，键里带平台；不让各平台整份覆盖彼此。
7. 只订阅 `message.*`，用 `<@bot_user_id>`（启动时 `auth.test` 取）判断 @；不订阅 `app_mention`；带 `bot_id` 或 `subtype` 的事件丢掉。
8. Slack `Limits`：`max_chars` 约 2900（section block 3000 字上限留出 mrkdwn 转换的余量），顶层 `text` 作回退；`edit_interval` 2.0 s。
9. Markdown → mrkdwn：手写一个小转换（粗体、链接、标题、转义、保留代码块），不加依赖。
10. `Capabilities.typing` 在 Slack 上为 False。
11. `Press.direct`：按频道 id 前缀 `D` 判断（未确认，真实验收时核对）；不为此加 `conversations.info` 和额外 scope。
12. action 监听器先 `ack()`，再把按钮事件经 `asyncio.Lock` 一个个交给适配器（满足"上一个处理完再处理下一个"）。
13. 不带 `--im` 时 `Popen` 不变（不传 `env=`）。
14. 主会话的真实 Slack 验收是默认放行的小额运行，上限 $2。
15. `docs/setup.md` 里 Slack app 的 scope、Messages 标签页、interactivity 等每一步，实现时对照 Slack 官方文档核对并写明查阅日期。

## Acceptance

- [ ] `TICKET_131` 标记全部删掉（45 个装饰器、8 个定义），以及因此没用的 import；`git diff main -- tests/acceptance tests/fixtures` 只有删除行（新建的 `tests/fixtures/im/` 除外）。
- [ ] `uv run pytest tests/acceptance/test_im_*.py -v`：60 个全部 PASSED，0 xfail、0 skip。
- [ ] "测试没钉、本工单要做的"每条都做了，能单测的有新测试；bug 修复配一个修复前会失败的测试。
- [ ] `pyproject.toml` 有 `[project.optional-dependencies]` 的 `im`，含 slack-bolt（钉 1.30.0）和 aiohttp；`uv sync --extra im` 能装；`uv export --frozen --no-dev --no-emit-project` 的输出里没有 slack-bolt、slack-sdk、aiohttp（贴命令和 `grep` 结果）；`uv.lock` 只有本工单的包变化。
- [ ] CI 的 `uv sync --locked` 环境下 `make check` 能过（Slack 模块和测试能导入、能类型检查）。
- [ ] `git diff main --stat` 只有 Authority 列的文件；`git diff main -- src/data_agent/web/` 只有 `app.py` 里暴露 `runs`/`conversations` 和 lifespan 启停的改动。
- [ ] README、`docs/setup.md` 写好；Slack 的每一步有官方链接和访问日期或标 `未确认`；没有密钥、工作区名、用户 id、服务器地址；pre-commit 的密钥与公网 IP 扫描通过。
- [ ] `make check` 通过。
- [ ] 主会话做（不阻塞本工单交回）：人建好 Slack app 后的真实验收，spec "验收"`:119-123` 逐条看到（见 Evidence required）。

## Evidence required

工单 agent 贴到最终回复的 Comments（$0，不连真实 Slack 工作区、不调模型）：

- `make check` 的输出（`tee` 存文件）。
- `uv run pytest tests/acceptance/test_im_*.py -v` 的输出；`git diff main --stat -- tests/` 和 `git diff main -- tests/acceptance | grep '^[-+]' | grep -v '^[-+][-+]'`，证明只删了标记。
- `uv export --frozen --no-dev --no-emit-project | grep -iE 'slack|aiohttp'`（应为空）。
- 不连 Slack 的真实运行：
  - 坏的 `--im` 文件（如变量名不是 `DATA_AGENT_SLACK_` 前缀）：`uv run data-agent web --runs-dir runs/131/r --im runs/131/bad.toml` 的退出码（2）和 stderr（点名 `slack`，没有 token 值）。
  - 没装 extra：用不含 extra 的环境（例如 `uv run --no-dev --isolated ...` 或等效办法，照实写用了什么）跑 `data-agent web --im <合法文件>`，贴出提示 `uv sync --extra im` 的报错。
  - 装了 extra、token 是假值（运行时拼出来，不写进文件）：`data-agent web --im <合法文件>` 起来后，适配器连 Slack 失败只写日志（贴日志里的错误类型，不含 token 值），网页照常服务（`curl -s http://127.0.0.1:<port>/api/options` 返回 200），Ctrl-C 后进程正常退出。
- 写好交给主会话的真实验收步骤（下面），确认命令、路径、worktree 名。

交给主会话和人（工单 agent 不做、不等，**没有 Slack app 和 token 不是停下的理由**）：

1. 人：照 `docs/setup.md` 新加的一节建 Slack app，把 `DATA_AGENT_SLACK_BOT_TOKEN`、`DATA_AGENT_SLACK_APP_TOKEN` 写进 `~/.config/data-agent/env`，准备一个仓库外的 `im.toml`（`allowed_users` 填自己的 Slack 用户 id），把机器人拉进一个测试频道；另备一个不在名单里的账号。
2. 主会话：在本工单的 worktree 里 `uv sync --extra im`，确认网页已构建（`pnpm --dir web build:local`），然后
   ```bash
   mkdir -p runs/131-live && set -a && . ~/.config/data-agent/env && set +a && \
     uv run data-agent web --runs-dir runs/131-live --data-sources <仓库外的 sources.toml> --im <仓库外的 im.toml> 2>&1 | tee runs/131-live/web.log
   ```
3. 人在 Slack 里做 spec "验收"`:119-123` 的每一条：私聊问一个需要澄清的问题、点按钮回答、拿到答案；在线程里追问一轮（同一对话）；运行中插一句话；点"停止"取消一次；频道里 @ 机器人问一次；名单外的账号私聊，收到"未授权"和自己的 id。
4. 主会话：在网页对话列表里看到这几次对话、能打开回放；`grep` 运行目录和 `web.log` 确认没有 token 值；从事件文件读这几轮的花费，把命令、结果、花费抄进 Comments。不跑分（spec `:124`）。失败就按"退回：原因"交回本工单。

## Stop conditions

- 要改验收测试的断言、夹具、输入才能转绿，或者觉得某个验收测试错了（AGENTS.md 硬规矩）。
- 要改 130 定下的名字、签名、字段、常量，或者改 Authority 以外的文件（包括 `conversations.py`、`launcher.py`、`runs.py`、`agent.py`）才能做到。
- `make check` 在 CI 的 `uv sync --locked` 环境下过不了，又不能在 `pyproject.toml` 的范围内解决（例如必须改 `.github/workflows/`）。
- 加 extra 迫使已有依赖换版本，或 slack-bolt、slack-sdk、aiohttp 进了 `uv export --no-dev`。
- spec 和 130 的契约自相矛盾，或和 Context 列的代码现状对不上。
- 第二轮审查修复后仍不过。

没有真实 Slack app 和 token、不能真跑 Slack：**不是**停下的理由，照 Evidence required 做完不连 Slack 的部分，把真实验收交给主会话。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 2
- Model spend: $0.0366（主会话 Slack 真实验收 10 次运行；工单 agent 没有调模型）
- Outcome: accepted (PR #136, squash 30ed465)
- Human review minutes: —

## Comments

- 2026-11-24（工单 agent 最终回复，主会话照抄）：

**实现（commit `f45bad9`、`78c1c35`、`5013c61`）**
- `src/data_agent/im/`：
  - 实现了 130 的五个空壳（`config`、`format`、`progress`、`adapter`、`service`），新增 `slack.py`。130 定下的名字、签名、常量和 docstring 都没改。
  - `service.ImUnavailable` 是新加的异常。配置里有还没实现的平台（item 4）、或缺 extra（item 5）时抛出，`serve_web` 把它转成 exit 2。
- `cli.py` 改了三处：`main` 传 `args.im`；`serve_web` 读 `--im` 并调 `service.start`；`start_run` 只在给了 `--im` 时传 `env=run_env(...)`，不给时 `Popen` 不变（item 13）。
- `app.py`：`app.state` 上放 `runs`、`conversations`、`services`。lifespan 开始服务后启动 services，在 `launcher.shutdown` 之前停掉。路由、签名、HTTP API 不变。
- `pyproject.toml`：`im` extra 和 dev 组都加了 `slack-bolt==1.30.0`、`aiohttp>=3.14`。`uv.lock` 只多了 slack-bolt 1.30.0 和 slack-sdk 3.48.0，aiohttp 本来就在锁里。

**检查输出**
- `make check`：exit 0。输出存在 `runs/131/make_check_final.txt`。
  - ruff "All checks passed!"，格式 "451 files already formatted"，basedpyright "0 errors, 0 warnings, 0 notes"。
  - pytest：`2976 passed, 2 skipped, 79 deselected, 75 xfailed`。基线是 2870 passed、134 xfailed；少掉的 59 个 xfail 就是 IM 验收测试。
  - web：`Tests 442 passed`。
- `uv run pytest tests/acceptance/test_im_*.py -v`：`60 passed`，0 xfail，0 skip。输出存在 `runs/131/acceptance_im.txt`。
- 验收测试只删了标记：
  - `git diff main...HEAD -- tests/acceptance` 新增行为 0。
  - 删掉的是 45 个 `@TICKET_131`、8 个 `TICKET_131 = pytest.mark.xfail(...)` 定义（每个 5 行，带它后面的空行），以及 3 个没用了的 `import pytest`。
  - `--stat` 里 `tests/acceptance` 只有删除。新增的是 `tests/fixtures/im/slack_payloads.json`（假 id）和 `tests/test_im_{adapter,service,slack}.py`。
- `uv export --frozen --no-dev --no-emit-project | grep -iE 'slack|aiohttp'`：输出为空（grep exit 1）。
- 只装 extra 就够：`uv run --isolated --no-dev --extra im python -c "import data_agent.im.slack, slack_bolt, aiohttp"` 能导入。

**不连 Slack 的真实运行（在 `5013c61` 上，token 都是运行时拼出来的假值）**
- 坏的 `--im` 文件（`app_token_env = "DATA_AGENT_API_KEY"`）：exit 2。stderr 是 `data-agent web: error: --im file runs/131/bad.toml, [slack]: app_token_env must name a variable starting with DATA_AGENT_SLACK_ (the name, never the token)`，token 值出现 0 次。
- 没装 extra（`uv run --no-dev --isolated`，环境里没有 slack_bolt）：exit 2，提示 `--im: the slack bot needs the IM libraries (slack_bolt is not installed); run uv sync --extra im (or install data-agent[im])`。
- 只装 extra 的环境（`uv run --isolated --no-dev --extra im`）加假 token，脚本是 `runs/131/fake_token_run.sh`：
  - `/api/options` 返回 200。
  - 日志里只有 `slack: the bot could not connect; the web UI goes on without it` 和 `SlackApiError ... auth.test ... 'invalid_auth'`，网页照常服务。
  - SIGINT 后 exit 0，日志里 token 值 0 次。
  - 第一次只等了 5 秒，`auth.test` 还没返回；改成 25 秒后看到了这一行。

**新单元测试（"测试没钉"那几条）**
- Slack 消息格式 ↔ 接口的转换：
  - 顶层消息、线程回复、私聊。
  - @ 去掉 `<@id>`。
  - 丢掉 `bot_id`/`subtype` 的事件。
  - 同一条消息只交一次。
  - 按钮点击转成 `Press`，并且和开线程那条消息落在同一个键上。
  - `chat.postMessage`/`chat.update` 的参数，编辑去掉按钮。
  - 消息和按钮点击一个个交给适配器。
- Markdown → mrkdwn：11 个用例。
- 限制：section 不超过 3000 字，`edit_interval` 2.0，typing 为 False。
- 发送失败重试一次，再失败写日志。
- 编辑失败（限流）跳过，过一个间隔再补；最后那次编辑也会重试；Slack 被限流（429）时 `edit` 抛出。
- 适配器出错只写日志。
- 两个平台共用 `threads.json`。
- 缺 extra 的提示；配置里有还没实现的平台。
- 坏文件时 stderr 不带 token；把 token 填在变量名位置时也不回显。
- 不带 `--im` 时：子进程里查不到 slack 库，`Popen` 不收到 `env`。
- lifespan 顺序：platform.start → platform.stop → launcher.shutdown。
- 连不上时网页照常返回 200。
- slack_sdk、slack_bolt 在 DEBUG 级的日志里没有 token：连本机关着的端口，并清掉了代理变量，因为 slack_sdk 会读环境里的代理。

**审查**
- `da-review main`（第 1 轮）三条都属实，已修（`78c1c35`），每条配了一个修复前会失败的测试：
  - 前缀不对时报错原样带出了那个值，填错的 token 会出现在 stderr。
  - mrkdwn 转义后可能超过 section 的 3000 字，被截断。现在拆成多个 section block。
  - 澄清按钮在 `send()` 被拒（409）时就被收走了。现在被拒时问题和按钮都留着。
- `eng:code-review main`（第 2 轮）：
  - Standards：没有违反文档规定的地方。
  - Spec：没有阻塞的缺口。
  - 已修（`5013c61`）：
    - `Adapter.stop()` 改名为 `stop_buttons()`。
    - `slack.py` 改用 `format.FENCE`/`is_fence`。
    - 补了 Slack 429 的测试。
    - `docs/setup.md` 补了第 1、7 步和 typing 说明的链接，第 10 步、重装 scope 标"未确认"。
    - README 去掉了 `pip`。
  - 不修，理由如下：
    - 两个 `Popen` 分支：item 13 要求不带 `--im` 时不传 `env=`，照字面做。
    - `subtype` 一律丢掉，`file_share`、`thread_broadcast` 也在内：照 item 7 字面做。后果是带附件的消息和"同时发到频道"的回复不会有回应，列为已知限制，真实验收时看是否要放开。
    - Repeated Switches（`status_text` 和 `conclude` 走同一串判断）、`chat`/`thread` Data Clump、`Turn` 的状态字段、`make_platform` 只有一个分支、两层锁：这些都是判断题。Slack 的锁保证到达顺序，适配器的锁让 `poll` 和处理函数轮流跑，作用不同。都不影响行为，不改。
    - spec 以外的小行为：斜体、删除线、列表符号的转换，固定回复（空消息、`/new`、"问题已关闭"），重试上限（`FOLLOW_TRIES`、`FINAL_EDIT_TRIES`）。这些都是行为必需的或很小。
- 和 item 6 的字面不同（请主会话定）：没做进程内一份共用的映射。每个适配器持有自己平台的条目，写盘时在模块级锁下重读文件、保留其他平台的条目再整体替换。"不让各平台整份覆盖彼此"做到了，有测试；没做共用映射是因为构造函数签名已定，取不到共用对象。

**交给主会话的真实验收**（worktree `.claude/worktrees/131-im-common-and-slack`，上限 $2）
1. 人照 `docs/setup.md` 的"Slack 机器人（工单 131）"建 app。两个 token 写进 `~/.config/data-agent/env`；在仓库外写 `im.toml`，`allowed_users` 填自己的 member id；把机器人拉进测试频道；另备一个不在名单里的账号。
2. 在这个 worktree 里：`uv sync --extra im`，然后 `pnpm --dir web build:local`，然后：
   ```bash
   mkdir -p runs/131-live && set -a && . ~/.config/data-agent/env && set +a && \
     uv run data-agent web --runs-dir runs/131-live --data-sources <仓库外的 sources.toml> --im <仓库外的 im.toml> 2>&1 | tee runs/131-live/web.log
   ```
3. 人做 spec `:119-123` 的每一条：
   - 私聊问一个需要澄清的问题，点按钮回答，拿到答案；
   - 在线程里追问一轮；运行中插一句话；点"停止"取消一次；
   - 频道里 @ 机器人问一次；
   - 名单外的账号私聊，收到"未授权"和自己的 id。
4. 主会话：
   - 在网页对话列表里看到这几次对话，能打开回放；
   - `grep` 运行目录和 `web.log`，确认没有 token 值；
   - 从事件文件读这几轮的花费。

   另外几处要核对：
   - `Press.direct` 按 `D` 前缀判断对不对；
   - 机器人会不会收到自己发的消息（官方文档没写）；
   - `/invite` 这一步；
   - bot token 错时 `auth.test` 失败、不重试，要重启服务；app token 错时 slack_sdk 每 10 秒重试一次。

- 2026-11-24（主会话）item 6 的做法（各适配器持有自己平台的条目，写盘时在模块级锁下重读、保留其他平台条目再整体替换）接受：达到"不让各平台整份覆盖彼此"，有测试；构造函数签名由人审过的契约定，取不到共用对象。

- 2026-11-24（主会话）退回第 1 轮：原因——PR #136 上 Codex GitHub app 的 P2 属实（主会话对照 `5013c61` 核实）：在线程里打字回答澄清时（`src/data_agent/im/adapter.py:267` 的 `send_turn`），这个对话还开着的 `self.questions` 条目和它的按钮没有收掉；之后再点旧按钮仍过 `handle_press` 的检查（`:279-281`），会把第二个、可能矛盾的选项发出去。在原分支上修：打字回答被接受后，收掉这个对话所有还开着的问题、编辑掉它们的按钮（平台能编辑时）；`send()` 被拒（`Refused`）时保留问题。加回归测试：打字回答之后点旧按钮，`send()` 不再被调用、回复"问题已关闭"；被拒时问题仍开着。修完跑 `make check` 和 `da-review main`。

- 2026-11-24（工单 agent 退回第 1 轮最终回复，主会话照抄）：

- 2026-11-24 (claude opus-5-5) Bounce round 1, fixed on `worktree-131-im-common-and-slack`:
  - `b372dff`: once `send()` accepts a message (typed or pressed), the conversation's open questions are closed and their buttons edited off; on `Refused` they stay open. Regression tests: `test_a_typed_answer_closes_the_question` (fails before the fix), `test_a_refused_typed_answer_keeps_the_question_open`.
  - `da-review main`, run 1: two P2s, both real, fixed in `ac5679a`.
    - On a Slack 429, send waits for `Retry-After` (at most 30 s) and retries once; edits still raise for the adapter to skip.
    - Section cuts avoid `&…;` escapes and `<…>` links; a code block cut in two is closed and reopened in each section.
  - `da-review main`, run 2: P1, real (introduced by `ac5679a`): a single code line expanding past 3000 characters made `sections` loop forever. Fixed in `5ee63be`; regression test plus 40 randomized cases.
  - `da-review main`, run 3: "No actionable defects found".
  - `make check`: 3025 passed, 2 skipped, 75 xfailed; web 442 passed.

- 2026-11-25（主会话）Slack 真实验收，131 分支 `5ee63be`（文档另提交 `64443ff`）。人用 Paste a manifest 建 app（manifest 已写进 `docs/setup.md` 第 1 步），token 在 env 文件里，`im.toml` 放仓库外。命令：`data-agent web --runs-dir runs-web --data-sources sources.toml --port 8831 --im <仓库外 im.toml>`，数据源是 `tests/fixtures/shop.sql` 建的 DuckDB，产出在 worktree 的 `runs/131real/`（不进 git）。spec "验收" `:119-123` 逐条：
  - 澄清加按钮：私聊"帮我算一下收入"，turn 1 出 `clarification`（4 个口径选项），点第 1 项，turn 2 的输入就是该选项原文，回答 2139.25。
  - 线程追问进同一对话：私聊线程到 turn 5，`threads.json` 一个线程对应一个对话；turn 3 沿用 turn 2 的"排除 returned"口径。
  - 运行中插话：turn 5 03:31:23 开始，`steer_delivered` 在 03:31:36（"只分析 Avery Lane 一个人"），报告只写 Avery Lane；网页 turn 的 `steers` 有这一条。
  - 停止：点 Stop 后约 1 秒 `cancelled`，Slack 回 "Stopped. Run 20261125T032732Z-f44a39 in the web UI."
  - 频道 @：公开频道里 @ 机器人，`threads.json` 多一条 `C…` 频道线程，对应新对话，turn 1 success。
  - 名单外：工作区只有人一个账号，把 `allowed_users` 换成假 id 重启后，人私聊和频道各发一条：没有新运行目录，`threads.json` 不变；人确认私聊收到了提示，提示原文主会话没有看到。之后改回名单并重启。
  - 网页：`/api/conversations` 列出这 3 个对话，可回放。
  - token：在 `runs-web/` 和各次 `web.log` 里查 `xoxb-`/`xapp-`/`xoxp-`，0 处。
  - 花费：10 次运行 $0.0366（从事件文件 `result.cost_usd` 加总）。
  - 未确认项的结论：私聊判断用的 `D` 前缀对（私聊 `D…`，频道 `C…`）；`/invite` 这一步要做；机器人会不会收到自己发的消息，日志里看不出来（成功时不写日志），但验收中没有出现重复回复或自己回复自己。
  - 发现：Slack 的创建页已改版（2026-11-25），"From scratch"下面是 Paste a manifest / Pick features；`docs/setup.md` 第 1 步改成贴 manifest（`64443ff`，`make check` 通过：3025 passed，web 442 passed）。
