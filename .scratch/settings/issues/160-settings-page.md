# 160 — 设置页（前端为主）：分层编辑、来源标注、锁定灰显、不一致提示与覆盖、改动历史；登录页；联网勾选跟随生效设置

Status: ready-for-agent
Blocked by: 158, 159
Spec: ../spec.md（Implementation Decisions"设置的两类键（S7）""作用域与权限""网页登录"；"存储与审计"的改动记录）；cloud-deploy 156 的联网默认值部分（156 已作废，并入本工单）
Claimed by: —
Branch: —

## Goal

网页有一个设置页：
- 按层（组织、工作区、频道、个人）查看和编辑设置类的键；每个值标来源（代码默认 / 文件 / 数据库的哪层 / 锁定）；`[policy]` 锁定的键灰显不可改；当前身份不能改的键（S1）只读；部署层的键（数据源连接、模型列表、`[server]`、`[policy]` 等）可看不可改，数据源只显示名字、类型、是否只读，提示新增找部署管理员（S3）。
- 文件与数据库不一致时提示，组织管理员有"用文件的值覆盖"按钮。
- 每层可看改动历史。
- 打开 `!configure` 给的链接直接到该频道的设置。
- 未登录时（API 401）显示登录页：cloud 有 Sign in with Slack 按钮；没接 Slack 的部署显示管理令牌输入框（159 的接口）。local 不出现登录页。
- 网页表单和对话页的"联网"勾选默认值取自 `/api/options` 给的生效联网设置，不再写死 `true`；409 的 `detail` 显示给使用者（原 cloud-deploy 156 的第二部分）。

157 里标 `TICKET_160` 的测试全部转绿。

## Authority

- May edit：
  - `web/src/`：
    - 新的设置页目录（如 `web/src/settings-page/`）和登录页组件；
    - `App.tsx`：设置页和登录页的路由、401 时显示登录页；
    - `data/source.ts`：设置接口、登录接口、401 的识别、`RunOptions` 加联网默认值；
    - `local/NewRun.tsx`、`chat-page/Conversation.tsx`：联网勾选的默认值；
    - 以及这些文件旁边的 vitest 测试（不是 `acceptance/` 目录）。
  - `src/data_agent/web/app.py`，只做设置页要的后端接口：按层读写、来源与锁定、"用文件的值覆盖"、改动历史（157 契约 6），`/api/options` 加联网默认值字段。存储调 158、权限调 159，不在这里另写一套。
  - `tests/acceptance/`：唯一例外，删掉 157 文件里的 `TICKET_160` 标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩）。
  - `tests/`（除 `tests/acceptance/`）：后端接口的单元测试。
- Must not edit：
  - `tests/acceptance/` 里除 `TICKET_160` 标记以外的一切；`web/src/*/acceptance/`；`tests/fixtures/`。
  - `src/data_agent/` 里 `web/app.py` 以外的文件：158 的存储、159 的身份与权限、161 的 IM、`launcher.py`/`conversations.py` 的联网上限与 409（153）。
  - `web/e2e/`、`web/package.json`、`web/pnpm-lock.yaml`（不加依赖）。
  - `evals/`、`skills/`、`docs/`（文档归 155）、`Dockerfile`、`deploy/`。
  - `Makefile`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。不调模型。

## Context

行号取自 main `30a18e9`。product-config 144（表单不列消融配置、显示生效配置摘要）、149（运行详情显示配置），cloud-deploy 153，以及 158、159 都会改这些文件。认领时按符号名在当前 main 上核对。

- **决定**：spec"设置的两类键（S7）""作用域与权限"的表"网页登录"；ADR-0012 决定 1、3、5；`decisions.md` S1、S3、S7、S9 原文；Q8"人定的 5 个"第 4 条（联网默认关、单次运行不能超过配置、409、表单的联网勾选默认值跟服务器配置走）。ADR-0012 后，`[sandbox].network` 是设置类键，"服务器配置"读作当前身份、当前作用域的生效设置。
- **验收测试**：157 的 `test_settings_api.py`、`test_settings_seed.py` 里标 `TICKET_160` 的（接口路径与形状、`/api/options` 联网默认值的字段名，见 157 契约 6 和人审结论）。前端没有验收测试，由本工单在 `web/src/` 旁写 vitest。
- **后端现状**：
  - API 路由在 `src/data_agent/web/app.py:107-198`；`/api/options` 在 `:107-114`；UI 兜底路由在 `:204-224`，新接口注册在 `serve_ui`（`:200`）之前。`create_app` 签名（`:73-79`）被钉住，只往后加带默认值的参数。
  - 鉴权与会话由 159 接进 app；存储与解析由 158 提供。
- **前端现状**：
  - 路由在 `web/src/App.tsx:47-59`（`Place`、`currentPlace`）；运行详情页地址 `<base>runs/<id>`（`web/src/run-page/route.ts` `readRoute`、`routeUrl`）。
  - 所有 API 调用经 `localSource` 的 `send`（`web/src/data/source.ts:233-240`），失败时经 `reason` 取 FastAPI 的 `detail`（`source.ts:346-352`）。同源 `fetch` 默认带 Cookie；`EventSource`（`browserStream`，`source.ts:206-219`）同源时也带 Cookie。
  - `RunOptions`（`source.ts:37`）现在只有 `data_sources`、`task_types`、`configs`（144 会改）；表单在 `local/NewRun.tsx:23-34` 的 `useEffect` 读它。
  - 联网勾选写死默认 `true`：`web/src/local/NewRun.tsx:18`（勾选框 `:103`，提交 `:53`）、`web/src/chat-page/Conversation.tsx:73`（勾选组件 `:26-47`，发送 `:101`）。对话页经 `live.options()`（`source.ts:332`）拿 options。
  - 现有错误显示（如 `NewRun.tsx:33`）照旧；409 的 `detail` 用同一处显示。
  - 在线回放（`web/src/site/`，`VITE_DATA_SOURCE` 不是 `local` 时，`App.tsx:40-42`）没有后端：设置页和登录页只在 live 下出现，回放构建不受影响。
  - local 服务器上网页的样子和现在一样：没有登录页，勾选默认跟 `/api/options`（local 默认联网，ADR-0010）。
- **取证**：`docs/research/2026-11-27-chatbot-agent-config.md` 第 1、2、11 条结论（行为配置放在产品的管理面；组织 > 工作区 > 频道的继承；nao 用环境变量播种、界面存过的不覆盖）；S7 的"用文件的值覆盖"是为避开 Open WebUI 的旧值陷阱（`decisions.md` S7 原文）。
- **审查分两半**（`decisions.md`"建 settings 工单时主会话定的细节"第 2 条）：
  - 后端文件（`src/data_agent/web/app.py` 和 `tests/` 下的单元测试）：第 5 步跑 `da-review main` 和 `eng:code-review main`；第 7 步照后端工单读 Codex GitHub app 的意见（只看后端文件的条目）。
  - 前端文件（`web/src/`）：照 AGENTS.md 前端工单，不用 Codex；第 5 步的 `eng:code-review main` 覆盖它，第 7 步由主会话派一个上下文干净的 Opus 5.5 子 agent（只读）审 PR 里 `web/src/` 的 diff。
  - 最终回复分开列出后端和前端的改动，以及两半各自的审查结果。
- 只用 pnpm、uv。

## Acceptance

- [ ] 157 里标 `TICKET_160` 的测试去掉标记后通过；标 `TICKET_158`、`TICKET_159` 的照旧通过
- [ ] vitest 覆盖：
  - 每个值的来源标注；锁定的键灰显不可改；不能改的键只读；部署层的键只读、数据源不显示连接串。
  - 不一致提示；非管理员看不到"用文件的值覆盖"；管理员点了之后调覆盖接口、提示消失。
  - 保存调写接口；403、4xx 的 `detail` 显示给使用者。
  - 改动历史按层显示；密钥类只显示"改过"。
  - API 返回 401 时显示登录页；cloud 有 Slack 登录按钮；管理令牌提交成功回到原页面、失败显示错误。
  - 联网勾选的默认值取自 `/api/options`：`false` 时不勾，`true` 时勾；409 的 `detail` 显示给使用者。
- [ ] 不登录的 local 服务器上，已有的 vitest、`web/src/*/acceptance/` 和 `web/e2e/chat.spec.ts` 不改就通过（`make test-integration` 的 Playwright 部分由主会话跑）
- [ ] 后端接口的单元测试：权限拒绝时库不变；数据源接口的响应里没有连接串和密码
- [ ] `make check` 通过（含 `pnpm --dir web typecheck`、`pnpm --dir web test`）

## Evidence required

贴到 Comments：
- `make check` 输出。
- `uv run pytest tests/acceptance/test_settings_api.py tests/acceptance/test_settings_seed.py -v`。
- `pnpm --dir web test` 里本工单测试文件的输出。
- 截图（工单 agent 用 Playwright 自己拍，不调模型）：本机 local 服务器的设置页（来源标注、锁定灰显、不一致提示、改动历史），以及一个用管理令牌登录的 cloud 服务器的登录页。

交给主会话（部署后，最终回复写出步骤）：
1. 浏览器 Sign in with Slack，打开设置页，看到自己工作区的层；普通成员改频道说明成功、改模型被拒。
2. 在 Slack 里 `@bot !configure` 拿到的链接直接打开该频道的设置（161 合入后）。
3. cloud 不开联网时表单勾选默认不勾；勾上提交得到 409 的提示。截图贴进 Comments。

## Stop conditions

- 157 的某个验收测试看起来不对，或要改它才能转绿。
- 158 的存储或 159 的权限函数缺接口，要改它们的代码才能做。
- 157 契约 6 没写进测试，或人审结论和 S1、S3、S7 矛盾。
- 要加前端依赖或运行时依赖。
- 第二轮审查修复后仍不过。
- 不停止、不重启本机的 Docker/colima 守护进程。本工单不需要 Docker。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-27（主会话建票）：设置页，前端为主。等 158、159 合入。审查见 Context"审查分两半"。原 cloud-deploy 156 的"联网勾选跟随服务器配置"并入本工单（156 作废）。设置的 HTTP API（带权限检查）归本工单。
- 2026-11-27（主会话）：按"建 settings 工单时主会话定的细节"第 2 条改票：设置的 HTTP API 归本工单；审查分两半（后端文件 `da-review` + `eng:code-review` + Codex GitHub app，前端文件 Opus）。
