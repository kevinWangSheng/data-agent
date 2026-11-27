# 159 — 平台身份：Sign in with Slack、Slack 角色与频道成员、管理令牌、按 S1 的权限检查、浏览器会话

Status: ready-for-agent
Blocked by: cloud-deploy 153, 157, 158
Spec: ../spec.md（Implementation Decisions"作用域与权限（S1、S2、S5、S6）""网页登录（S5、S6）"）
Claimed by: —
Branch: —

## Goal

- cloud 模式的网页用 Sign in with Slack（OpenID Connect）登录，只接受配置里登记的工作区；登录后只看、只改自己所在工作区和自己的个人层；组织管理员（`[server].org_admins`）看全部。
- 没接 Slack 的部署：组织管理员用 `DATA_AGENT_ADMIN_TOKEN` 登录设置页。这个令牌同时是脚本调 API 的 bearer（153 接的），权限 = 组织管理员；没有单独的 `DATA_AGENT_WEB_TOKEN`（定的细节第 1 条）。
- 浏览器会话是 HttpOnly、`Secure` 的 Cookie，值是用 `DATA_AGENT_SESSION_KEY` 签名的会话；cloud 缺这个变量拒绝启动。OIDC 用 `DATA_AGENT_SLACK_OIDC_CLIENT_ID`、`DATA_AGENT_SLACK_OIDC_CLIENT_SECRET`，回调地址由 `[server].public_url` 拼出（定的细节第 7 条）。`/api/*` 和 SSE 事件流都认这个 Cookie。
- local 模式（只监听本机）不登录、不发 Cookie，本机用户即组织管理员（沿用现在的行为；定的细节第 12 条）。
- 网页里发起的运行（表单、网页对话）按登录的人解析设置：组织 + 本人所在工作区 + 个人（定的细节第 5 条），把身份交给 158 的解析函数。
- 权限检查按 spec"谁能改"的表：一个函数回答"这个身份能不能改这一层的这个键"。Slack 角色每次请求查 `users.info`（`is_admin`、`is_owner`），频道成员查 `conversations.members`，缓存 5 分钟，不另存角色表（S9）。

157 里标 `TICKET_159` 的测试全部转绿。设置页的读写接口和页面是 160 的，会话查看是 162 的，本工单提供它们要用的身份和权限函数。

## Authority

- May edit：
  - 新建身份与权限模块（157 契约 1 定的路径，如 `src/data_agent/settings/access.py`、`src/data_agent/web/auth.py`）。
  - `src/data_agent/web/app.py`：登录路由（OIDC 回调、管理令牌换会话）、在 153 写的鉴权函数里加认 Cookie 会话、把登录的身份交给网页运行的设置解析；`create_app` 只往后加带默认值的参数。
  - `src/data_agent/web/launcher.py`、`web/conversations.py`：只在把登录的身份（作用域）传给 158 的解析时改；新参数带默认值。
  - `src/data_agent/cli.py`：`web` 启动时读部署层的登录配置（`DATA_AGENT_SLACK_OIDC_CLIENT_ID`、`DATA_AGENT_SLACK_OIDC_CLIENT_SECRET`、`DATA_AGENT_SESSION_KEY`、`[server].public_url`），缺了的拒绝启动规则照 153 的启动检查；运行子进程的环境去掉 OIDC 密钥和 `DATA_AGENT_SESSION_KEY`（照 153 去掉 `DATA_AGENT_ADMIN_TOKEN` 的做法，在同一处）。
  - 143 建的配置模块：加 `[server].public_url` 等登录相关的部署层键的类型（157 契约 5、定的细节第 7 条）。
  - `tests/acceptance/`：唯一例外，删掉 157 文件里的 `TICKET_159` 标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩）。
  - `tests/`（除 `tests/acceptance/`）：单元测试。
- Must not edit：
  - `tests/acceptance/` 里除 `TICKET_159` 标记以外的一切；`tests/fixtures/`。
  - `web/src/`：登录按钮、401 时的跳转由 160 做。
  - 158 的存储与解析（只调用；Blocked by 158，不另存角色表的断言和键的分类都用 158 的）；`src/data_agent/im/`（161）。
  - `pyproject.toml`：Slack 的 OpenID 调用用 slack_sdk（`im` extra 里已有）或标准库；要加运行时依赖（如 OIDC、JWT、签名库）就停下。
  - `evals/`、`skills/`、`docs/`（部署文档归 155）、`Dockerfile`、`deploy/`。
  - `Makefile`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。不调模型、不连真的 Slack；真实登录由主会话在部署后做。

## Context

行号取自 main `30a18e9`。140（in-review）、143、144、153 会移动这些行，认领时按符号名在当前 main 上核对。

- **决定**：spec"作用域与权限""网页登录"；ADR-0012 决定 3、5；`decisions.md` S1、S5、S6、S9 原文；Q8"网页 Cookie：值是签过名的会话值，不是令牌原文"；`decisions.md`"建 settings 工单时主会话定的细节"第 1、5、7、12、13 条（local 不发 Cookie，Q8"local 不带 Secure"作废）。ADR-0012 取代 cloud-deploy 156 的"输一次令牌换 Cookie"（156 已作废）。
- **验收测试**：157 的 `test_settings_permissions.py`、`test_settings_login.py` 里标 `TICKET_159` 的。契约 1、2、5（模块、`org_admins` 写法、OIDC 回调路径、Cookie 名与属性、管理令牌接口）见 157 的测试 docstring 和人审结论。
- **153 留下的**：bearer 鉴权写成一个函数（153 Context"令牌"末条），本工单在同一处加 Cookie 会话，不另写一套。153 已按定的细节第 1 条用 `DATA_AGENT_ADMIN_TOKEN` 作 bearer；本工单让它换到的身份是组织管理员。
- **现状**：
  - 网页没有鉴权；API 路由在 `src/data_agent/web/app.py:107-198`，UI 兜底路由 `app.py:204-224` 会吃掉没注册的路径，登录路由要注册在 `serve_ui`（`app.py:200`）之前。UI 页面和 `/assets/*` 不要凭据，浏览器才能打开登录页（151 契约 2）。
  - 事件流用浏览器的 `EventSource`（`web/src/data/source.ts:206-219`），带不了请求头，所以浏览器只能靠 Cookie。
  - 运行子进程由 `serve_web` 的 `start_run` 起（`src/data_agent/cli.py:573-589`），带 IM 时已用 `im_config.run_env` 去掉 IM 令牌的变量（`im/config.py:127`）。
- **Slack API**（slack_sdk 的异步客户端，`.venv/.../slack_sdk/web/async_client.py`）：`openid_connect_token`（`:5507`）、`openid_connect_userInfo`（`:5535`）、`users_info`（`:6795`）、`conversations_members`（`:4274`）。slack_sdk 只在 `im` extra 里（`pyproject.toml:29-34`）；Sign in with Slack 就用它，不新增依赖，云端镜像装 `data-agent[im]`（155，定的细节第 13 条）。没装 extra 却配了 Slack 登录时，启动报错点名 extra，不影响管理令牌登录。查角色和成员用的 Bot token 照 IM 配置的变量名（`im/config.py:40-44`）。
- **权限**：
  - 表格逐格照 spec"谁能改"；`[policy]` 可把行为类收紧到管理员（键名由 157 契约或 143 的 `[policy]` 定）。
  - 不用 Slack 的"频道管理者"角色（S1）。
  - Discord/Telegram 的工作区层和访问类只组织管理员（S6）；它们的网页登录排在后面（"后续"），所以它们的用户在网页上只能经管理令牌或组织管理员的 Slack 身份进来。
  - 缓存 5 分钟，按（工作区、用户）和（频道）分键；时钟可注入；被撤管理员后过期即失去权限。
- **会话签名**：用 `hmac` 和 `DATA_AGENT_SESSION_KEY` 签；比较用 `hmac.compare_digest`；Cookie、日志、错误信息、事件文件里都没有令牌或 Slack access token 原文。
- **取证**：`docs/research/2026-11-27-chatbot-agent-config.md`（同类产品的身份与管理员权限，第 1、5 条结论）、`docs/research/2026-11-23-im-gateway-designs.md`（Hermes、OpenClaw、LangBot 的权限）；具体做法以 `decisions.md` S1、S5、S6、S9 为准。
- 只用 uv。

## Acceptance

- [ ] 157 里标 `TICKET_159` 的测试去掉标记后通过；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行
- [ ] local 模式不变：已有的 `tests/acceptance/test_web_api.py`、`test_chat_api.py`、`test_im_*.py` 和 151 的守门不改就通过
- [ ] 单元测试：Cookie 签名的验证、篡改与过期；令牌常数时间比较且空令牌不放行；日志和异常文字不含令牌、access token；缓存的过期与按键隔离；运行子进程的环境里没有 OIDC 密钥和 `DATA_AGENT_SESSION_KEY`；local 下任何响应都没有 `Set-Cookie`
- [ ] 最终回复列出部署层新增的键和环境变量（名字、用途、缺了时的报错文字），供 155 写进 `docs/deploy.md`
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 输出。
- `uv run pytest tests/acceptance/test_settings_permissions.py tests/acceptance/test_settings_login.py -v`。
- 不调模型的真实运行（$0）：本机起一个 cloud 配置、设了 `DATA_AGENT_ADMIN_TOKEN` 的服务器，`curl` 不带凭据得到 401；用管理令牌换会话、带 Cookie 的 `GET /api/runs` 得到 200；贴响应头（`Set-Cookie` 的属性，值打码）。

交给主会话（部署后，最终回复写出步骤）：
1. 在 Slack 应用里配好 OpenID 的重定向地址，部署后浏览器点 Sign in with Slack，登录进本工作区。
2. 用另一个工作区的账号登录，被拒。
3. 工作区管理员和普通成员各登录一次，确认能改的键符合 S1（设置页 160 合入前用 API 验证）。

## Stop conditions

- 157 的某个验收测试看起来不对，或要改它才能转绿。
- 153 没留出可扩展的鉴权函数，加 Cookie 要改 153 的结构。
- 要加运行时依赖。
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

- 2026-11-27（主会话建票）：settings 的身份与权限。等 cloud-deploy 153 和 157 合入。改动在后端，照常跑 `da-review` 和 `eng:code-review`。取代 cloud-deploy 156 的令牌换 Cookie（ADR-0012）。
- 2026-11-27（主会话）：按"建 settings 工单时主会话定的细节"第 1、5、7、12、13 条改票：Blocked by 加 158；`DATA_AGENT_ADMIN_TOKEN` 同时是 bearer；签名密钥 `DATA_AGENT_SESSION_KEY`、OIDC 变量、`[server].public_url`；local 不发 Cookie；网页运行按登录的人解析设置；用 `im` extra 的 slack_sdk。去掉"签名密钥等没定"的停止条件。
