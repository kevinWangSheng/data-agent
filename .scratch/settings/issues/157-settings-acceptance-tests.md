# 157 — 设置界面：验收测试（生效设置解析、权限、登录、播种与不一致、IM、会话查看、评测隔离）

Status: ready-for-human
Blocked by: product-config 143
Spec: ../spec.md（Testing Decisions"验收测试接缝"）；决定：`docs/adr/0012-settings-ui-and-platform-identity.md`、`.scratch/product-config/decisions.md`"设置界面 + Slack 权限"S1–S9 与 Q8
Claimed by: claude-opus-5-5 (eng:implement, 2026-11-28)
Branch: worktree-157-settings-acceptance-tests

## Goal

spec"验收测试接缝"的七组行为都有验收测试，写在约定的接缝上。还没实现的行为标预期失败（pytest `xfail(strict=True)`），按转绿的工单分标记：
- 生效设置解析、播种与不一致、改动记录：`TICKET_158`；
- 权限、登录、浏览器会话、管理令牌：`TICKET_159`；
- 设置页的后端接口（分层读写、来源、锁定、"用文件的值覆盖"、改动历史）、`/api/options` 的联网默认值：`TICKET_160`；
- IM 里的设置（`!configure`、频道改动公告、临时覆盖、"在网页打开"链接、Telegram/Discord 的作用域映射）：`TICKET_161`；
- 运行详情页和运行列表的查看权限：`TICKET_162`。

现在就成立的行为写成守门测试，不标记：评测入口在数据库里有设置时运行参数不变；local 模式（只监听本机）不登录、不发 Cookie、本机用户即组织管理员（今天网页没有任何鉴权）。

本工单不改 `src/`；由人审过才合并，之后的工单不能改这些测试。原 cloud-deploy 151 里标 `TICKET_156` 的 Cookie 与联网默认值测试移到本工单（151 已按 ADR-0012 调整）。

## Authority

- May edit（都是新建）：
  - `tests/acceptance/settings_support.py`：几个文件共用的辅助（取模块和属性的辅助、替身 Slack 客户端、替身 OpenID 提供方、替身平台适配器、假 HOME、建库辅助）。已有 support 文件里要用的辅助照抄进来；已有 support 文件和夹具只读。
  - `tests/acceptance/test_settings_resolve.py`：生效设置解析（`TICKET_158`）。
  - `tests/acceptance/test_settings_seed.py`：播种、不一致、"用文件的值覆盖"、改动记录（`TICKET_158`；覆盖按钮的接口 `TICKET_160`）。
  - `tests/acceptance/test_settings_permissions.py`：权限与 Slack 角色缓存（`TICKET_159`）。
  - `tests/acceptance/test_settings_login.py`：Sign in with Slack、管理令牌、浏览器会话 Cookie（`TICKET_159`），local 不登录（守门）。
  - `tests/acceptance/test_settings_api.py`：设置页的后端接口、`/api/options` 的联网默认值（`TICKET_160`）。
  - `tests/acceptance/test_settings_im.py`：IM 里的设置（`TICKET_161`）。
  - `tests/acceptance/test_settings_sessions.py`：运行详情页的查看权限（`TICKET_162`）。
  - `tests/acceptance/test_settings_eval_isolation.py`：评测隔离（守门，加一条 `TICKET_158`，见 Context）。
  - `tests/fixtures/settings/`：新建，示例配置文件（只带 `${VAR}` 引用和 `.example` 主机名）、替身事件文件。
- Must not edit：
  - `src/`：不加空壳，见 Context"接缝"。
  - `tests/acceptance/` 里已有的任何文件（`conftest.py`、`seam_support.py`、`im_support.py`、`product_config_support.py`、141 和 151 的测试文件等）。要用的辅助照抄进 `settings_support.py`。
  - `tests/fixtures/` 里已有的文件（141 录的快照只读引用，不改）。
  - `web/`（前端测试由 160 自己写）、`evals/`、`skills/`。
  - `Makefile`、`pyproject.toml`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。不调模型、不联网、不连真的 Slack。

## Context

行号取自 main `30a18e9`。140（in-review）、141、143–148、151、153 会移动 `cli.py`、`web/app.py`、`web/launcher.py`、`web/conversations.py`、`im/adapter.py`；认领时按符号名，在 143 合入后的 main 上重新核对。

- **决定**：spec `.scratch/settings/spec.md` 全文；ADR-0012；`decisions.md` S1–S9（原文）、"后续"清单（不测）、Q8 的"网页 Cookie：值是签过名的会话值，不是令牌原文"和"`[policy]` 和命令行参数冲突：报错拒绝"；`decisions.md`"建 settings 工单时主会话定的细节"第 1–14 条（和 Q8 不一致时以它为准：local 不发 Cookie，Q8"local 不带 Secure"作废）。
- **配置层**：加载器、`EffectiveConfig` 的形状、来源的写法（141 契约 1），以 141/143 合入的为准。设置类键与部署层键的划分见 spec"设置的两类键（S7）"；类型定义和 `config.toml` 同一套（143 的 schema）。
- **现状**（`30a18e9`）：
  - 网页没有鉴权；`create_app(runs_dir, data_sources, start_run, ui_dir, mcp_servers)`（`src/data_agent/web/app.py:73-79`）被之前的验收测试钉住，只能往后加带默认值的参数。UI 兜底路由在 `app.py:204-224`。
  - `/api/options`（`app.py:107-114`）给数据源、任务类型、配置名；请求体的 `network` 默认 `True`（`app.py:52`、`:61`）。
  - 运行详情页的数据来自 `GET /api/runs/{run_id}/events`（`app.py:120-138`）和 `GET /api/runs`（`app.py:116-118`）；前端地址是 `<base>runs/<id>`（`web/src/run-page/route.ts` `routeUrl`）。
  - IM：`Incoming`（`src/data_agent/im/platform.py:35-52`）有 `platform`、`chat_id`、`thread_id`、`user_id`、`direct`、`mentioned`、`text`、`new_conversation`，没有工作区 id；`Platform` 协议（`platform.py:105-130`）只有 `send`、`edit`、`typing`，没有只本人可见的发送。线程映射在 `<runs_dir>/im/threads.json`（`im/adapter.py:96-97`）；回合经 `conversations.send(conversation, text)`（`adapter.py:302`），`ConversationsLike.send` 的签名是 `(conversation, text, network=True)`（`adapter.py:130`）。Slack 的 `to_incoming`（`im/slack.py:176-196`）没读事件的 `team`。Discord 已有只本人可见的 `/new` 回复（`im/discord_bot.py:34-37`、`:239-240`）；Telegram 在 `start` 注册 `/new`（`im/telegram.py:157-158`）。
  - 触发规则（私聊总答；频道里要 @ 或在 bot 的线程里）由 `tests/acceptance/test_im_triggers.py:29-62` 钉住，"是否要 @"设置的默认值必须保持它。
  - slack_sdk 的异步客户端有 `users_info`、`conversations_members`、`chat_postEphemeral`、`openid_connect_token`、`openid_connect_userInfo`（`.venv/.../slack_sdk/web/async_client.py:6795`、`:4274`、`:3562`、`:5507`、`:5535`）；slack_sdk 只在 `im` extra 里（`pyproject.toml:29-34`），`httpx` 只在 dev（`pyproject.toml:53`）。
- **接缝**（不加 `src/` 空壳，照 151）：
  - 设置存储、解析、权限检查、登录的模块和函数今天都不存在。由本工单定模块名和函数名（契约，人审），测试经 `settings_support.py` 的辅助去取：先 `assert` 模块和属性存在，再调用，这样今天的失败是 `AssertionError`，不是 `ImportError`。
  - 网页一侧：用 `create_app` 建 `TestClient`（照 `tests/acceptance/test_web_api.py:117`），替身 `start_run` 记参数；cloud 的情形照 151 的做法走整条 `data-agent web --config F`、monkeypatch `uvicorn.run` 截下 `app`。带 `Secure` 的 Cookie：`TestClient` 默认 `http://testserver`，httpx 的 Cookie 罐不会把 `Secure` Cookie 发回去，cloud 的测试用 `TestClient(app, base_url="https://testserver")`。
  - 替身 Slack 客户端：记下每次 `users.info`、`conversations.members`、`chat.postEphemeral`、`chat.postMessage` 调用；时钟可注入（5 分钟缓存要能快进）。
  - 替身 OpenID 提供方：`openid.connect.token` / `openid.connect.userInfo` 的替身，给出 `https://slack.com/team_id`、`https://slack.com/user_id` 这类声明（字段名照 Slack 文档，本工单核实后写进 docstring）。
  - IM：替身平台适配器照 `tests/acceptance/im_support.py:85` `FakePlatform` 的做法照抄扩展（加只本人可见的发送和工作区 id），经 `Adapter` 驱动。
- **预期失败的标记**：照 `.scratch/mcp/issues/115-mcp-acceptance-tests.md` Context"预期失败的标记"和 151：
  ```python
  TICKET_158 = pytest.mark.xfail(
      strict=True, raises=AssertionError, reason="ticket 158: the settings store is not built yet"
  )
  ```
  - `TICKET_159` … `TICKET_162` 照写。只接受 `AssertionError`；报错类的断言写成 `try/except` 再 `assert`，不用 `pytest.raises`。
  - 每个带标记的测试至少有一条正向断言，在该标记的工单合入前一直失败（不只是今天失败：例如 `TICKET_159` 的测试不能在 153 合入后就通过而 XPASS）；只有否定断言的检查放进带正向断言的测试，或写成守门测试。
  - 起子进程、建 git 仓库这类不依赖未实现模块的夹具，先确认自己成功了，失败就抛 `RuntimeError`。经还没实现的模块（如 158 的存储）做准备的，一律经"先 assert 在、再调用"的辅助，今天的失败是 `AssertionError`。
  - 转绿时 158–162 只删自己的标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩的唯一例外）。
- **各组要断言的行为**：
  - **生效设置解析**（`test_settings_resolve.py`，`TICKET_158`）：
    - 顺序：代码默认 < 文件 < 数据库（组织 < 工作区 < 频道，或 组织 < 工作区 < 个人）< 单次覆盖；`[policy]` 最后裁剪（spec"设置的两类键"末条）。每个键的来源对（代码默认 / 文件 / 数据库的哪一层 / 单次覆盖 / 锁定）。
    - 频道里个人层不参与：同一用户在个人层设了说明，频道里的解析结果不含它；私聊里含它。
    - 越界报错：数据库或单次覆盖的值超出 `[policy]`（如 `network = true` 而 `[policy].network = false`、模型在 `disabled_models` 里、`[limits]` 超过 `[policy]`）时报错，点名键，不悄悄改成锁定值（Q8）。
    - 部署层键（`[sources.*]` 的连接、`[models.*]`、`[server]`、`[policy]`）不能存进数据库覆盖：写入时报错。
    - 数据源选用只能从部署层登记的 `[sources.*]` 里选；选了没登记的报错。
    - Skill 和说明在线程开始时固定：给定线程开始时的快照，之后频道层改了说明，同一线程的解析结果仍是快照里的（S4）。
    - 网页里发起的运行（表单、网页对话）：设置用 组织 + 本人所在工作区 + 个人（同私聊；定的细节第 5 条）。
    - 单独的 `data-agent run`：配了 `[server].db_path` 且文件存在时读组织层，否则只读文件；`config show` 默认显示组织层，`--workspace`、`--channel`、`--user` 选看某个作用域（定的细节第 11 条；走整条 CLI，截停接缝照 141）。
  - **权限**（`test_settings_permissions.py`，`TICKET_159`）：
    - 照 spec"谁能改"的表逐格断言：组织层只组织管理员（`[server].org_admins`）；Slack 工作区层和频道层的访问和花费类只 `is_admin` 或 `is_owner`；频道层行为类频道成员可改、非成员不可；`[policy]` 收紧后行为类也只管理员；个人层只本人、只行为类；Discord/Telegram 的工作区层和访问类只组织管理员。
    - 缓存：5 分钟内同一用户、同一频道不重复调 `users.info`、`conversations.members`；快进过 5 分钟再查；被撤管理员后，缓存过期即失去权限。
    - 不另存角色表：断言数据库里没有角色或成员的表（或经存储接口查不到）。
    - 管理令牌作 bearer：`Authorization: Bearer <DATA_AGENT_ADMIN_TOKEN>` 的请求，权限等于组织管理员（定的细节第 1 条）。
  - **登录**（`test_settings_login.py`）：
    - `TICKET_159`，cloud：
      - 启动：cloud 缺 `DATA_AGENT_SESSION_KEY` 拒绝启动，退出码 2，stderr 点名变量，`uvicorn.run` 没被调用（照 151 的启动检查；定的细节第 7 条）；用了 Slack 登录又缺 `DATA_AGENT_SLACK_OIDC_CLIENT_ID` 或 `_CLIENT_SECRET` 同样拒绝。
      - Sign in with Slack 的回调：替身提供方给出登记过的工作区，得到会话 Cookie；没登记的工作区得到 403（或契约定的码），不设 Cookie。
      - Cookie 是签名会话：值里没有令牌或 Slack 的 access token 原文；`HttpOnly`、`Secure`、`SameSite`、`Path=/`（属性写法见契约）；伪造、篡改、过期的 Cookie 得到 401。
      - 只带这个 Cookie，`/api/*` 和 SSE 流（`GET /api/runs/{id}/events`，`Accept: text/event-stream`）不是 401（原 151 `TICKET_156` 那组）。
      - 管理令牌：没接 Slack 的部署，`DATA_AGENT_ADMIN_TOKEN` 换到的会话是组织管理员；错的令牌 401；令牌值不在响应、日志（`caplog`）、Cookie 里；比较对空令牌不放行。
      - 回调地址由 `[server].public_url` 拼出（定的细节第 7 条）。
    - 守门，不标记：local 模式、只监听本机、不设任何令牌：不带凭据照旧 200（151 已有同样的守门，本文件只为设置页再写一条 `GET /api/options`）。
    - 守门，不标记：local 模式下任何响应都没有 `Set-Cookie`（定的细节第 12 条；今天就成立）。
  - **播种与不一致**（`test_settings_seed.py`）：
    - `TICKET_158`：首次启动（库不存在）用 `config.toml` 的设置类键播种组织层，部署层键不进库；第二次启动不再播种。改文件后数据库不变，解析结果的来源仍是数据库，且标出"文件与数据库不一致"（列出键和两个值）。
    - `TICKET_158`：改动记录：每次写入记下谁（平台用户 id）、何时、哪层、哪个键、改前改后；密钥类只记"改过"、不记值（S9）。
    - `TICKET_158`：迁移：库带版本号；用一份旧版本号的库启动，自动升级到当前版本，原有数据还在。
    - `TICKET_160`：管理员调"用文件的值覆盖"的接口之后，不一致消失、来源变成数据库且值等于文件；非管理员调得到 403。
  - **设置页的后端接口**（`test_settings_api.py`，`TICKET_160`）：
    - 按层读：返回每个键的值、来源、是否锁定（`[policy]` 锁定的键标锁定）、是否可改（按当前身份和 S1）。部署层键可读不可改；数据源只显示名字、类型、是否只读，不含连接串和密码（S3）。
    - 按层写：允许的身份写成功，写入进改动记录；不允许的 403，库不变；越界的值 4xx 点名键。
    - 改动历史：按层返回改动记录。
    - `/api/options` 的联网默认值（原 151 `TICKET_156`）：带凭据的 `GET /api/options` 给出当前生效设置的联网默认值；cloud 不开联网时 `false`，local 默认 `true`。
  - **IM**（`test_settings_im.py`，`TICKET_161`）：
    - `@bot !configure`（Slack）、`/configure`（Telegram、Discord）：回一条只本人可见的消息（Slack ephemeral；Telegram、Discord 私信），含本频道设置页的链接；频道里没有别的消息。
    - 频道层任何改动，bot 在该频道发一条"谁改了什么"：经 158 存储的频道层写入驱动（不经 160 的接口，免得 161 依赖 160）。
    - 临时覆盖：消息里写覆盖（语法见契约 4），之后同一线程的回合都生效，直到再换（模型、effort、数据源、MCP 一律如此，定的细节第 9 条）；数据源和 MCP 只能从本频道已开放的里挑；越界或未开放的值：bot 回复不允许，没有回合启动，不悄悄替换；用了什么覆盖出现在运行的 `config` 事件的生效配置里（146 的事件，不另加事件；定的细节第 10 条）。
    - Skill 和说明在线程开始时固定：线程开始后改频道说明，同一线程的下一回合仍用旧说明；新线程用新说明。
    - 回复带"在网页打开"链接，指向这次运行的详情页地址（`<[server].public_url>/runs/<run_id>`）；链接里不带凭据。
    - 会话索引：每个 IM 回合起运行后，索引里有 线程 → 运行 id、所属频道（私聊记本人）（定的细节第 3 条）；`im/threads.json` 照旧。
    - 多个 Slack 工作区：配置写 `[im.slack.workspaces.<名字>]`，各写 token 的环境变量名（定的细节第 6 条）；两个工作区的消息各自解析到自己的工作区层。
    - "是否要 @ 才回复"：频道层设为不要时，频道里不 @ 的顶层消息也起回合；默认（没设）照现有触发规则（定的细节第 8 条）。
    - Telegram 私信发不出（替身平台对该用户的私信抛错）时，`/configure` 在群里回一句提示先私聊 bot 一次，不带链接（定的细节第 14 条）。
    - 作用域映射：Slack 消息带工作区 id；Discord 服务器、Telegram 每个 bot 一个工作区；私聊用个人层；频道里不用个人层。
    - 守门，不标记：没有任何设置时，现有触发规则不变（"是否要 @"默认要 @，和 `test_im_triggers.py` 一致）。只写一条代表性的，避免和已有测试重复。
  - **会话查看**（`test_settings_sessions.py`，`TICKET_162`）：
    - 准备一个频道会话的运行、一个私聊会话的运行（经会话索引登记所属频道或本人）和一个网页里发起的运行（所有者 = 登录的人，定的细节第 5 条）。网页发起的运行：本人和组织管理员可看，别人不可。`GET /api/runs/{id}/events`（JSON 和 SSE 两种）：频道成员 200、非成员 403（或 404，契约定）、私聊本人 200、别人 403、组织管理员都 200；Telegram、Discord 的会话只有组织管理员能看。
    - 运行列表：`GET /api/runs`、`GET /api/conversations` 只列出当前身份按上面规则能看的（定的细节第 4 条）。
    - 网页只看不接着聊：Slack 会话对应的网页对话，`POST /api/conversations/{id}/messages` 被拒（码由契约定）。
    - 链接不带权限：同一地址，未登录 401。
  - **评测隔离**（`test_settings_eval_isolation.py`）：
    - 守门，不标记：在 `[server].db_path` 的默认位置（契约 2）和假 HOME 下放一个 sqlite 文件（评测配置文件不写 `[server].db_path`），照 141 快照组的方式驱动各评测入口，规范化后的快照和 `tests/fixtures/product_config/` 里录下的相等（快照规范化规则照抄进 `settings_support.py`；`product_config_support.py` 和快照只读）。今天没有代码读库，所以现在就通过。
    - `TICKET_158`：经存储接口在库里写进组织层、工作区层、频道层的设置（改模型、上限、说明、联网），先读回断言库里确有这些值（正向断言，今天失败），再驱动评测入口，快照仍和录下的相等。
- **本工单定下、最终回复里逐项列出供人审的契约**（下面是建议）：
  1. 模块和函数：存储（如 `data_agent.settings.store`：打开库、迁移、读写某层某键、改动记录）、解析（如 `resolve(effective_config, store, scope, overrides, pinned) -> 带来源的生效设置`）、权限（如 `data_agent.settings.access.can_edit(identity, scope, key) -> bool`，Slack 角色查询带可注入的客户端和时钟）、登录（OIDC 回调、管理令牌换会话的路由）。
  2. `[server].db_path` 的默认值（spec：和 `runs_dir` 同卷，提议 `<runs_dir>/settings.sqlite`）、`[server].org_admins` 的写法（各平台用户 id，提议 `"slack:U…"`、`"telegram:…"`、`"discord:…"`）。
  3. 作用域的标识：组织、工作区（Slack `team_id`、Discord 服务器 id、Telegram bot）、频道、个人的 id 写法；单次覆盖和线程开始时快照的形状。
  4. **临时覆盖的语法**（spec 留给 157，候选 `model=`、`effort=`、`source=`、`mcp=`）：写在消息的哪里（开头、任意位置）、多个怎么分隔、怎么撤销、和正文怎么分开。生效范围已定：四种覆盖都对整个线程生效直到再换（定的细节第 9 条）。
  5. 登录：OIDC 回调路径、只接受登记工作区时的响应码；Cookie 名、属性（`HttpOnly`、`Secure`、`SameSite`、`Path=/`；local 不发 Cookie）、会话有效期；管理令牌换会话的接口。已定（定的细节第 1、7 条）：签名密钥是 `DATA_AGENT_SESSION_KEY`（cloud 缺它拒绝启动）；OIDC 的变量是 `DATA_AGENT_SLACK_OIDC_CLIENT_ID`、`DATA_AGENT_SLACK_OIDC_CLIENT_SECRET`；`DATA_AGENT_ADMIN_TOKEN` 同时是脚本调 API 的 bearer（权限 = 组织管理员），没有 `DATA_AGENT_WEB_TOKEN`。
  6. 设置页接口的路径与形状（按层读、写、"用文件的值覆盖"、改动历史）、`/api/options` 联网默认值的字段名（原 151 契约 4，提议 `network_default: bool`）。设置 API 由 160 实现（定的细节第 2 条）。
  7. 平台协议新增的方法：只本人可见的发送（Slack `chat.postEphemeral`；Telegram、Discord 私信）、`Incoming` 的工作区 id 字段、`[im.slack.workspaces.<名字>]` 的键（定的细节第 6 条）；新参数一律带默认值，`ConversationsLike.send`、`Conversations.send` 现有签名不变（已有验收测试钉住）。
  8. "在网页打开"链接和 `!configure` 链接的格式；公开地址已定为 `[server].public_url`（定的细节第 7 条）。
  9. 覆盖在 `config` 事件里怎么看出来（已定写进 146 的 `config` 事件的生效配置，不另加事件，定的细节第 10 条；本工单定断言读哪个字段）；会话查看被拒时的码（403 还是 404）；会话索引的形状（线程 → 运行 id、所属频道或本人、网页运行的所有者）。
- 只用 uv。每个 worktree 用自己的 `runs/` 目录。

## Acceptance

- [ ] spec"验收测试接缝"七组和本票 Context 列的每一条都有断言，落在 Authority 列的测试文件里；契约 1–9 写在相应测试文件的 docstring 里
- [ ] `TICKET_158` … `TICKET_162` 标记的测试当前都以预期失败通过检查，没有被跳过的；守门测试（评测隔离的守门、local 不登录、IM 触发规则不变）当前都通过，且不带标记
- [ ] 每个预期失败的测试，失败原因是功能没做（如模块不存在、没凭据也是 200、没有只本人可见的消息），不是夹具起不来或 import 失败。最终回复给每个标记贴一个 `--runxfail` 跑出的真实失败原因
- [ ] 临时写一个参考实现（不提交，结束后 `git status src/` 干净），证明去掉标记后至少 `TICKET_158` 一组能通过；每组至少一个反向检查会失败，例如：
  - 频道里把个人层也叠进去了；
  - `[policy]` 越界时悄悄改成锁定值；
  - 改文件后数据库被覆盖；
  - 缓存不过期，被撤的管理员仍能改；
  - 没登记的工作区也拿到 Cookie；
  - Cookie 值是令牌原文；
  - 越界的覆盖被悄悄替换；
  - 非成员能看频道会话；
  - 评测入口读了库里的设置。
- [ ] `git diff main -- src/` 为空；`git diff main --stat -- tests/acceptance` 只有新文件
- [ ] 夹具和测试里没有密钥、主机地址；pre-commit 的密钥与公网 IP 扫描通过
- [ ] `make check` 通过

## Evidence required

贴到最终回复的 Comments：
- `make check` 的输出（`tee` 存文件）。
- `uv run pytest tests/acceptance/test_settings_*.py -v`：每个测试名，XFAIL 还是 PASSED。
- 每个标记的 `--runxfail` 失败原因。
- 参考实现和反向检查的结果。
- 契约 1–9。

真实运行：本工单不调模型、不连 Slack，没有要主会话跑的。

## Stop conditions

- 某条行为在约定接缝上测不了，或要加 `src/` 空壳、改已有验收文件才能测。
- 评测隔离的守门写出来现在就不通过。
- 断言需要人还没定的选择，且不属于契约里由本工单提议的细节，也不在 `decisions.md`"建 settings 工单时主会话定的细节"里。
- spec、ADR-0012、`decisions.md` S1–S9 与 143 合入的配置层互相矛盾。
- 第二轮审查修复后仍不过。
- 不停止、不重启本机的 Docker/colima 守护进程。本工单不需要 Docker。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3（中断两次后第三次完成）
- Review rounds: 2
- Model spend: $0
- Outcome: —

## Comments

- 2026-11-27（主会话建票）：settings spec 的验收测试工单，人审后合入；158–162 照它转绿。原 cloud-deploy 151 的 `TICKET_156` 测试（Cookie 换取与鉴权、`/api/options` 联网默认值）移到这里，分别标 `TICKET_159`、`TICKET_160`。建票时发现的问题已由主会话定（`decisions.md`"建 settings 工单时主会话定的细节"第 1–14 条），本票照它写。
- 2026-11-27（主会话）：按"建 settings 工单时主会话定的细节"改票：单一令牌 `DATA_AGENT_ADMIN_TOKEN`（也是 bearer）、`DATA_AGENT_SESSION_KEY` 与 OIDC 变量、`[server].public_url`、local 不发 Cookie、覆盖都对整个线程生效并记进 `config` 事件、网页运行的作用域与所有者、运行列表同一可见性规则、会话索引由 161 写、`[im.slack.workspaces.<名字>]`、"是否要 @"、Telegram 私信回退、单独 `run` 与 `config show` 的作用域。
- 2026-11-28（工单 agent，第二次中断后续做；分支 `worktree-157-settings-acceptance-tests`，最后 `417ad3a`；交人：`config show --workspace` 与 143 冲突；覆盖记进 `config` 事件要等 146）：
  **续做**
  - 第二次中断后，先 merge 了 origin/main（`c111624`，没有冲突）。
  - 上次的 8 个提交都是本票的测试文件。没提交的 `src/` 改动是临时参考实现：验证完、备份后已从工作区移除，`git status src/` 干净。

  **范围**
  - `git diff main...HEAD -- src/` 为空。
  - `tests/acceptance` 下只有 9 个新文件：`settings_support.py` 和 8 个 `test_settings_*.py`，没有修改任何已有文件。
  - 没有新建 `tests/fixtures/settings/`，示例配置都在测试里现写。

  **`make check`**（最后一次跑在 `3d5c90d` 上，存为 `/tmp/ref157b/make_check2.txt`；之后的 `417ad3a` 只调了一处 import 顺序，单独跑过 ruff、basedpyright 和设置测试）
  - ruff：`All checks passed!`
  - basedpyright：`0 errors, 0 warnings, 0 notes`
  - pytest：`3589 passed, 3 skipped, 81 deselected, 146 xfailed, 1 warning`
  - web：`Test Files 30 passed`，`Tests 448 passed`，exit 0

  **`pytest tests/acceptance/test_settings_*.py -v`**（在 `417ad3a` 上）
  - 结果：`9 passed, 84 xfailed`，没有跳过的。每个测试名和结果在 `/tmp/ref157b/final_verbose.txt`。
  - 9 条守门测试都通过、都不带标记：
    - 评测隔离 6 条：`test_an_evaluation_entry_ignores_a_sqlite_file_where_settings_would_be`，6 个评测入口各一条。
    - `test_settings_im.py::test_without_settings_a_channel_message_still_needs_the_mention`
    - `test_settings_login.py::test_local_mode_needs_no_credentials_for_the_settings_page_s_options`
    - `test_settings_login.py::test_local_mode_sets_no_cookie`

  **`--runxfail`**
  - `84 failed`，84 条全是 `AssertionError`。中间出现的 `ModuleNotFoundError` 只是被包在里面的原因。
  - 每个标记一条真实失败原因：
    - `TICKET_158`：`AssertionError: no module data_agent.settings.resolve yet`（生效设置解析）；评测隔离那条是 `no module data_agent.settings.store yet`。
    - `TICKET_159`：`AssertionError: no module data_agent.settings.access yet`（权限）；`stderr does not name DATA_AGENT_SESSION_KEY`（cloud 启动检查：今天的报错是 `server.public_url: unknown key`）。
    - `TICKET_160`：`the server did not start (exit 2) … server.public_url: unknown key`（`/api/options` 的联网默认值）。
    - `TICKET_161`：`AssertionError: Incoming has no field 'workspace_id'`
    - `TICKET_162`：`AssertionError: no module data_agent.settings.store yet`（会话查看要用的会话索引还不存在）。

  **参考实现与反向检查**
  - 参考实现覆盖 158 的全部和 159 的大部分：存储、解析、权限、登录。去掉标记后：
    - 158 组的测试全部通过。
    - 159 组只有 `test_a_signed_in_persons_web_run_has_their_workspace_and_personal_settings` 仍然失败，因为参考实现没按登录的人解析网页运行的设置。
    - 160–162 没有一条 XPASS，说明这几组不会在自己的工单合入前提前转绿。
    - 结果：58 XPASS(strict)，26 xfailed，没有别的失败。
  - 反向检查（对参考实现做一处改动，再用 `--runxfail` 跑对应测试）：

  | 改动 | 结果 |
  |---|---|
  | 频道里也叠了个人层 | `test_in_a_channel_the_personal_layer_is_left_out` 失败 |
  | `[policy]` 越界时悄悄改成锁定值 | `test_a_value_outside_the_policy_is_refused_naming_the_key` 6 条都失败 |
  | 改文件后数据库被覆盖 | `test_a_file_edited_after_seeding_leaves_the_store_and_is_flagged` 失败 |
  | 缓存不过期 | `test_slack_answers_are_kept_five_minutes_and_a_revoked_admin_loses_the_right` 失败 |
  | 没登记的工作区也拿到 Cookie | `test_a_registered_workspace_signs_in_and_another_does_not` 失败 |
  | Cookie 值是令牌原文 | `test_the_admin_token_signs_in_an_org_admin_and_nothing_else_does` 失败；只走 Slack 登录的 Cookie 测试抓不到这个改动 |
  | 评测入口读了默认位置的库（`run_config.load_config`） | livesqlbench 和 dab 两个入口报 `differs from the recorded snapshot in ['max_turns', 'prompt']`；6 个入口里只有这 2 个能抓到 |
  | 只有一个 schema 版本（`SCHEMA_VERSION = 1`） | 迁移测试报 `no older version to upgrade from` |

  - **160、161、162 组没做反向检查**：参考实现里没有这几组的代码。工单举的例子"越界的覆盖被悄悄替换""非成员能看频道会话"都没真跑，只靠今天的行为当反例：没凭据也是 200、谁都能看运行。这一项验收**不能算完全满足**。

  **审查（2 轮）**
  - **`da-review main`（Codex）**，3 条：
    - 修了：允许的覆盖和频道默认值相同，测不出 `source=`/`mcp=` 是否真的生效。改为选频道开放范围里的一个子集，再断言 `=default` 能撤销。
    - 修了：迁移测试在只有一个版本时也能通过。加了 `SCHEMA_VERSION > 1` 的断言，并写进契约。
    - 不修：没断言真实的 `config` 事件。理由是 146 还没建，见上面第 2 条，交人定。
  - **`eng:code-review main`**：
    - 修了：
      - 删掉 4 个没用到的辅助：`SETTINGS_FIXTURES`、`EXIT_CONFIG_ERROR`、`write_config`、`new_id`。
      - 加一条测试：真实的 Slack 消息事件经 `to_incoming`，`team` 进到 `workspace_id`。
      - 会话查看测试补了一个 Discord 运行。
      - 管理令牌补了"变量没设"的情形。
      - 登录回调断言跳转到 `/`。
      - 播种测试带上部署层键（`[server]`、`[prompts]`、`[layers]`），并断言它们不进库。
      - 改动历史断言别的层的改动不会列进来。
    - 不修，理由：
      - 迁移经 `open_store` 而不是启动整个服务：`open_store` 就是启动时调的接缝。
      - "不另存角色表"只查新库的表结构：要查服务运行之后的库，得等 159 有了真实的写路径才能写。
      - `run_reads_only_the_file…` 实际起的是守门作用：它的正向断言靠准备步骤（存储、`db_path` 键）在今天失败，符合本票规则。
      - `settings_support.py` 有 1600 行：工单要求照抄而不是 import，所以大。
      - `drive_entry` 的 if 链、`Any` 类型：属于判断项，改动小，不值得动。

  **交人审的契约**：契约 1–9 全文在各测试文件的文件说明里：

  | 契约 | 位置 |
  |---|---|
  | 1、2、3 | `test_settings_resolve.py` |
  | 1 的权限部分 | `test_settings_permissions.py` |
  | 5 | `test_settings_login.py` |
  | 6 | `test_settings_api.py`、`test_settings_seed.py` |
  | 4、7、8、9 | `test_settings_im.py` |
  | 9 的查看部分 | `test_settings_sessions.py` |

  - 按定的细节第 17 条，下面这些细节由 157 提出、人审时定：
    - 路由：`/auth/slack/login`、`/auth/slack/callback`、`/auth/token`、`GET /api/me`、`/api/settings`（含 `/history`、`/use-file`）。
    - OIDC `state` 不对时回 400；没登记的工作区回 403。
    - Cookie `data_agent_session`：`HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=604800`。
    - 覆盖语法：消息开头写 `model= / effort= / source= / mcp=a,b`，空格分隔，`=default` 撤销；只有覆盖、没有正文的消息只确认，不起回合。
    - 看不到的运行回 403；会话索引里没有的运行只有组织管理员能看（同第 18 条）。
  - 新提议的键：`chat.sources`、`chat.mcp`、`chat.require_mention`、`instructions.text`、`[server].db_path`、`[server].org_admins`、`[server].public_url`、`[policy].max_budget_usd`、`[policy].behavior_admins_only`、`[im.slack].team_id`。
