# 156 — 网页：输令牌换 HttpOnly Cookie；联网勾选跟随服务器配置

Status: wontfix
Blocked by: 151, 153
Spec: ../spec.md（Implementation Decisions"网页"）；决定：`.scratch/product-config/decisions.md` Q8"人定的 5 个"第 3、4 条
Claimed by: —
Branch: —

## Goal

服务器设了 `DATA_AGENT_WEB_TOKEN` 时，浏览器能用网页。
- 打开网页时，先输一次令牌；服务器校验后回一个 HttpOnly Cookie。
- 之后所有 `/api/*` 请求和 `EventSource` 事件流都靠这个 Cookie 通过。接口仍接受 bearer 请求头（153）。
- 网页表单和对话的"联网"勾选，默认值跟服务器配置的联网设置走，不再写死 `true`。

151 里标 `TICKET_156` 的测试全部转绿。

## Authority

- May edit：
  - `web/src/`：
    - `data/source.ts`：401 的识别，以及读 `/api/options` 的联网默认值。
    - 新的输令牌页面组件。
    - `App.tsx`：401 时显示输令牌页面。
    - `local/NewRun.tsx`、`chat-page/Conversation.tsx`：联网勾选的默认值。
    - 以及这些文件旁边的 vitest 测试。
  - `src/data_agent/web/app.py`，只做最小的后端部分：
    - 令牌换 Cookie 的接口（151 契约 3）；
    - 在 153 写的鉴权函数里加认 Cookie；
    - `/api/options` 加联网默认值字段（151 契约 4）。
  - `tests/`（除 `tests/acceptance/`）：后端部分的单元测试。
- Must not edit：
  - `tests/acceptance/`。唯一例外：删掉 151 文件里的 `TICKET_156` 标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩）。
  - `src/data_agent/` 里 `web/app.py` 以外的文件：`cli.py`、`launcher.py`、`conversations.py` 的联网上限和 409 是 153 的。
  - `web/e2e/`、`web/package.json`、`web/pnpm-lock.yaml`（不加依赖）。
  - `evals/`、`skills/`、`docs/`（文档归 155）、`Dockerfile`、`deploy/`。
  - `Makefile`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。不调模型。

## Context

行号取自 main `e494e49`。product-config 144（"表单不列消融配置"）、149（运行详情显示配置），以及 153，都会改这些文件。认领时按符号名在当前 main 上核对。

- **决定**：
  - `.scratch/product-config/decisions.md` Q8"人定的 5 个"：
    - 第 3 条：打开网页先输一次令牌，服务器回 HttpOnly Cookie，接口接受 Cookie 或 bearer 请求头。
    - 第 4 条：联网默认关，配置文件可打开；单次运行不能超过配置，超过得到 409；表单的联网勾选默认值跟服务器配置走。
  - spec `.scratch/cloud-deploy/spec.md`"网页"。
- **验收测试**：151 的 `tests/acceptance/test_cloud_web_token.py`（以及放 `/api/options` 那条的文件）里标 `TICKET_156` 的那些。契约 2–4（哪些路由要凭据、Cookie 换取接口、Cookie 名与属性、`/api/options` 的字段名）见 151 的测试 docstring 和人审结论。前端没有验收测试，由本工单在 `web/src/` 旁写 vitest。
- **后端现状**：
  - 153 写的 bearer 鉴权函数在 `src/data_agent/web/app.py`，认领时看合入的样子。本工单在同一处加 Cookie，不另写一套。
  - `/api/options` 在 `app.py:107-114`。服务器的联网设置由 140/153 接进 app，认领时看合入的样子。
  - UI 页面和 `/assets/*` 不要凭据（151 契约 2），所以输令牌页面能打开。
  - Cookie 属性：`HttpOnly`、`SameSite=Strict`、`Path=/`，照 151 契约 3。值不是令牌原文时，用服务器启动时生成的签名密钥（`hmac`）签出来，令牌原文不落进 Cookie。
- **前端现状**：
  - 所有 API 调用经 `localSource` 的 `send`（`web/src/data/source.ts:229-240`），失败时经 `reason` 取 FastAPI 的 `detail`（`source.ts:346-350`）。同源 `fetch` 默认带 Cookie。
  - 事件流用 `EventSource`（`browserStream`，`source.ts:206-219`）。同源时它也带 Cookie，但带不了请求头，所以浏览器要靠 Cookie。
  - `RunOptions` 类型（`source.ts:37`）现在只有 `data_sources`、`task_types`、`configs`。表单在 `NewRun.tsx:23-34` 的 `useEffect` 里读它。
  - 联网勾选写死默认 `true`：`web/src/local/NewRun.tsx:18`（勾选框在 `:103`，提交在 `:53`）、`web/src/chat-page/Conversation.tsx:73`。改成取 `/api/options` 的联网默认值。对话页面要能拿到 options（经 `live.options()`，`source.ts:332`）。
  - 请求被 409 拒绝时，把 `detail` 显示给使用者。现有的错误显示（如 `NewRun.tsx:33`）照旧。
  - 没设令牌的 local 服务器上，网页的样子和现在一样：不出现输令牌页面，勾选默认 `true`（local 默认联网，ADR-0010）。
- **审查**：
  - 本工单以前端为主。照 AGENTS.md 前端工单的做法，第 5 步不跑 `da-review`，只跑 `eng:code-review main`。
  - 第 7 步不等 Codex GitHub app，由主会话派一个上下文干净的 Opus 子 agent（只读）审 PR 的 diff。
  - 后端部分（`app.py`）也在这次审查里。
- 只用 pnpm、uv。

## Acceptance

- [ ] 151 里标 `TICKET_156` 的测试去掉标记后通过；标 `TICKET_153` 的照旧通过
- [ ] vitest 覆盖：
  - API 返回 401 时显示输令牌页面；提交令牌后调换取接口，成功就回到原页面，失败就显示错误。
  - 联网勾选的默认值取自 `/api/options`：`false` 时不勾，`true` 时勾。
  - 409 的 `detail` 显示给使用者。
- [ ] 不设令牌的 local 服务器上，已有的 vitest 和 `web/e2e/chat.spec.ts` 不改就通过（`make test-integration` 的 Playwright 部分由主会话跑）
- [ ] 后端单元测试：Cookie 签名的验证；伪造、篡改的 Cookie 得到 401；Cookie 和日志里没有令牌原文
- [ ] `make check` 通过（含 `pnpm --dir web typecheck`、`pnpm --dir web test`）

## Evidence required

贴到 Comments：
- `make check` 输出。
- `uv run pytest tests/acceptance/test_cloud_web_token.py -v`。
- `pnpm --dir web test` 里本工单测试文件的输出。

真实运行交给主会话（最终回复写出步骤）：
1. `pnpm --dir web build:local`，起一个设了令牌的 cloud 服务器。
2. 浏览器打开网页：先出现输令牌页面；输对之后能看历史、提交一题、实时看到事件流。
3. 联网勾选默认不勾；勾上提交得到 409 的提示。
4. 截图贴进 Comments。花费小，按小规模运行处理。

## Stop conditions

- 151 的某个验收测试看起来不对，或要改它才能转绿。
- 153 没留出可扩展的鉴权函数，加 Cookie 要改 153 的结构或 `cli.py`。
- 契约 2–4 没写进 151 的测试，或人审结论和 Q8 第 3、4 条矛盾。
- 要加前端依赖。
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

- 2026-11-27（主会话建票）：`decisions.md` Q8 人定第 3、4 条新增的工单：浏览器带不了 bearer 请求头（`EventSource`），改用输令牌换 HttpOnly Cookie；联网勾选跟随服务器配置。以前端为主，审查照前端工单（不用 Codex，主会话派干净上下文的 Opus 审 PR）。
- 2026-11-27（主会话）：由 settings 159、160 取代（ADR-0012）。令牌换 Cookie 改为 159 的 Sign in with Slack / `DATA_AGENT_ADMIN_TOKEN` 换签名会话 Cookie；本票的"联网勾选跟随服务器配置"（`/api/options` 的联网默认值、`NewRun.tsx`、`Conversation.tsx` 的勾选默认值、409 的 `detail` 显示）并入 `.scratch/settings/issues/160-settings-page.md`（Goal 末条、Authority、Acceptance 的 vitest）；151 里原标 `TICKET_156` 的测试移到 `.scratch/settings/issues/157-settings-acceptance-tests.md`（`TICKET_159`、`TICKET_160`）。
