# 外部 MCP 接入与对外暴露 MCP：现状取证（2026-11-23）

日期：2026-11-23。只取证，不改 `src/`，不做设计决定。仓库提交 `834a0c3`。SDK：`claude-agent-sdk==0.2.168`（`uv.lock:537-538`、`uv.lock:678`），`mcp 2.3.0`（`.venv/.../mcp-2.3.0.dist-info`；`pyproject` 约束 `>=2.2,<3`，`uv.lock:681`）。上游本地 clone 的提交：nao `5bde830`（2026-10-09）、SignalPilot `f5eee22`（2026-09-28）、ktx `49a4ae6`、dash `6d86fca`、signalpilot-plugin `f7b0c09`。官方文档访问日期均为 2026-11-23。

## 结论摘要（只列事实）

**(A) 外部 MCP**

1. 本仓库今天只挂一个 stdio 服务器 `data`（`agent.py:1649` `mcp_servers={MCP_SERVER: server}`），并且 `strict_mcp_config=True`（`agent.py:1650`），所以用户机器上的 `.mcp.json`、`~/.claude.json` 里的 MCP 配置一律不加载。`setting_sources=["user"]`（`agent.py:1652`）。
2. 权限现状：`permission_mode="dontAsk"`（`agent.py:1648` 附近），`allowed_tools=[*tools, "mcp__data"]`（`agent.py:1643`），`disallowed_tools=denied`（`agent.py:1644`）；没有 `can_use_tool` 回调（全仓库 `src/` 无 `can_use_tool`）；`PreToolUse` 钩子只挂在文件工具上（`agent.py:1584`，`matcher="|".join(FILE_TOOLS)`）。因此：外部 MCP 工具只要不在 `allowed_tools` 里，在 `dontAsk` 下默认被拒（官方文档，见 Q2）；工作区守卫不会管外部 MCP 工具的入参。
3. 配置没有"用户自定义 MCP"的位置：`RunConfig` 只有 `skill/verifiers/context/skills_dir/max_turns/max_budget_usd`（`run_config.py:27-39`），运行配置是随包提交的 TOML（`configs/*.toml`），评测和产品共用。`--network` 是 `run` 子命令的运行级开关（`cli.py:290-295`），不是配置层；评测入口不带它。
4. SDK 0.2.168 的 `mcp_servers` 支持 stdio / SSE / HTTP / SDK 进程内四种配置（`types.py:857-908`），也可以是 `.mcp.json` 路径或 JSON 字符串（`types.py:2365-2371`，传给 `--mcp-config`）。工具名 `mcp__<server>__<tool>`，允许规则可写 `mcp__server__*`（官方文档）。
5. 外部 MCP 工具的事件会自动进事件流：`tool_start`/`tool_end` 对任何 `ToolUseBlock` 生成（`agent.py:1776-1806`），前端按 `tool` 字符串渲染，未知工具显示为 `TOOL` 类、完整名字（`web/src/run-page/tools.ts:21-37,48-49`，`shortTool` 只剥 `mcp__data__` 前缀）。
6. 与"只读"原则的冲突：MCP 规范的 `readOnlyHint`/`destructiveHint` 只是提示（规范原文："all properties in ToolAnnotations are hints ... Clients should never make tool use decisions based on ToolAnnotations received from untrusted servers"）；`readOnlyHint` 缺省 false，`destructiveHint` 缺省 true。SignalPilot 的外部 MCP 连接器已有一套按注解分默认开关的做法（`mcp_connectors/tools.py:1-52`）。

**(B) 对外暴露为 MCP server**

7. 已有 `data-agent mcp-server`（`cli.py:229`，stdio，`cli.py:195`、`cli.py:366` 调 `.run("stdio")`），但暴露的是数据工具（查询、表结构、验证工具等），不是"整个 agent"。没有 `ask`/`run` 类工具，也没有 HTTP 传输入口。
8. 把整个 agent 包成工具的入口函数是 `agent.run(task, config, events_dir, env, ...) -> Outcome`（`agent.py:2061-2074`），CLI 调用点 `cli.py:395-409`；`print(outcome.answer)` 是答案（`cli.py:411`）。网页后端是用子进程跑 `data-agent run`（`web/launcher.py:141-168`、`web/conversations.py:123-143`），不是进程内调用。
9. MCP Python SDK 2.x：`FastMCP` 已改名 `MCPServer`，`mcp.server.fastmcp` 导入直接抛 `ModuleNotFoundError`（`.venv/.../mcp/server/fastmcp.py:1-17`）。`MCPServer.run("stdio")`、`streamable_http_app(...)`、`run_streamable_http_async` 都在（`mcpserver/server.py:371-404,1074,1113,1288`）。
10. 上游先例：nao 有 `ask_nao` + `get_nao_answer`（把整个子 agent 暴露成 MCP 工具，含异步轮询与澄清）；ktx 有 stdio + 带 token 的本地 HTTP 两种 MCP server；SignalPilot 的 gateway 是 MCP server（工具粒度，不是整个 agent）；dash 没有。

**需要改动的文件清单（接外部 MCP，按接缝列，仅供评估，不是设计）**

| 接缝 | 文件 | 事实依据 |
|---|---|---|
| 工具选项装配 | `src/data_agent/agent.py` `build_options`（约 1515-1675） | `mcp_servers`、`allowed_tools`、`disallowed_tools`、`hooks` 都在这里设；`strict_mcp_config=True` 目前挡住一切外部配置 |
| 用户配置从哪来 | `src/data_agent/run_config.py`、`configs/*.toml`；或 `cli.py` 新参数；或 `web/sources.py`（已有 `--data-sources` TOML，`cli.py:327-331`） | `RunConfig` 无 MCP 字段；`web/sources.py` 是现成的"用户 TOML"先例（只读了 CLI 参数处，内部未读，未确认） |
| CLI 透传 | `cli.py` `run` 子命令；`web/launcher.py:141-168`、`web/conversations.py:123-143` | 网页走子进程拼命令，`--network` 就是这样传的（`launcher.py:168`、`conversations.py:143`） |
| 权限与守卫 | `agent.py:1584`（PreToolUse 只匹配文件工具）、`agent.py:1643-1648`（allow/deny/dontAsk） | 无 `can_use_tool`，外部工具入参不被检查 |
| 事件与展示 | `agent.py:1776-1806`（已通用）；`web/src/run-page/tools.ts`（`toolKind`/`shortTool`）、`web/src/run-view/model.ts:183`（`DATA_TOOL_PREFIX`，`tool_end` 里 `table` 只解析 `mcp__data__` 工具） | 外部工具显示为 `TOOL` 类，结果当文本 |
| 评测不受影响的保证 | `evals/harbor_agent.py:310-326`、`evals/dab.py:445-465` | Harbor 拼 `data-agent run` 不带 `--network`；DAB 在进程内调 `build_options` 再替换 `mcp_servers`（`dab.py:462-463`）。若外部 MCP 在 `build_options` 里按默认值挂载，这两条路径都会经过，需要默认关闭 |
| 沙箱/网络 | `sandbox.py:249-275,323-339`、`cli.py:257-259` | `--network` 只管 docker 沙箱容器和 Web 工具；外部 stdio MCP 进程是 SDK 在宿主机上拉起的，不在沙箱里（见 Q1.5） |
| 文档与 spec | `.scratch/agent-tools/spec.md:154`、`.scratch/phase1/spec.md:133` | 这两处明确把 MCP 连接器、对外 MCP、Slack/Notion 列为 Out of Scope |

**需要改动的文件清单（对外暴露 agent，仅评估）**：`cli.py`（新子命令或给 `mcp-server` 加模式）、新模块包一层 `agent.run`、`mcp_server.py`/`tool_annotations.py`（注解）、`pyproject`（已依赖 `mcp`）。

## Q1 本仓库现状

### 1.1 `build_options` 里的设置（`src/data_agent/agent.py`）

- 服务器：stdio，`sys.executable -m data_agent mcp-server --config ... --workspace ...`（`agent.py:1565-1581`）。
- 内置工具：`BUILTIN_TOOLS = list(FILE_TOOLS)`（`agent.py:117`）；`DENIED_TOOLS = ["Bash"]`（`agent.py:119`）；`WEB_TOOLS = ["WebSearch","WebFetch"]`，只在 `network=True` 时加载，否则放进 `disallowed_tools`（`agent.py:122-125`、`agent.py:1606-1607`）。
- 选项（`agent.py:1636-1668`）：`tools=tools`、`allowed_tools=[*tools, "mcp__data"]`、`disallowed_tools=denied`、`permission_mode="dontAsk"`（注释 "Anything not allowed above is denied rather than prompted for."）、`hooks=all_hooks`、`mcp_servers={MCP_SERVER: server}`、`strict_mcp_config=True`、`setting_sources=["user"]`（ADR-0001：只读用户设置，不读本仓库 `.claude/`）、`skills=[]`。
- 钩子：`PreToolUse` 文件守卫（`agent.py:1584-1585`）；工具计时 `PostToolUse`/`PostToolUseFailure`（`agent.py:1677-1703`）；轮次预算、花费、对话轮次的澄清钩子（`agent.py:1608-1618`、`1457-1488`）；验证器钩子（`agent.py:1587-1604`）。**没有 `can_use_tool`**。
- 注：`allowed_tools` 里写的是 `mcp__data`（不带 `__*`）；官方文档给的服务器级写法是 `mcp__server__*`（Q2）。这条是否等价**未确认**（本仓库现有运行能用，但我没做对照实验）。

### 1.2 评测路径 vs 产品路径

| | 产品（CLI / 网页） | Harbor（data-eng-bench） | DAB |
|---|---|---|---|
| 入口 | `cli.py` `run`（`cli.py:395-409`）；网页是子进程拼同一条命令 | `evals/harbor_agent.py:310-326` 拼 `data-agent run ... --sandbox local`，注释"No --network" | `evals/dab.py` 进程内调 `build_options`（`dab.py:460-464`），再 `replace(..., mcp_servers={MCP_SERVER: server})` 换成 `dab_server` |
| network | 网页默认 `True`（`web/app.py:52,61`）；CLI 默认关 | 不带 | `dab_server` 恒 `network=False`（`evals/dab_server.py:571-574`） |
| 对话轮次 | 网页对话有 `turn`（澄清工具、`CLAUDE_CONFIG_DIR`） | 无 | 无 |

### 1.3 `--network` 的实现

- `run` 子命令选项：`cli.py:290-295`（help 里写"never on an evaluation run"）；`mcp-server` 子命令的同名选项只管 docker 沙箱（`cli.py:257-259`）。
- 传递：`run(... network=args.network)`（`cli.py:405`）→ `build_options(network=...)`（`agent.py:2224`）→ Web 工具加载/拒绝（`agent.py:1606`）；`SandboxRun.of_run(sandbox, events_dir, network)`（`agent.py:2132`）→ `--sandbox ... --network` 传给 mcp-server（`sandbox.py:338-339`）→ docker 容器是否 `--network none`（`sandbox.py:275`）。
- 网页：`web/launcher.py:168`、`web/conversations.py:143` 拼 `--network`。
- 事实：外部 stdio MCP server 由 SDK/CLI 在宿主机拉起（`subprocess_cli.py:667-687` 把 `mcp_servers` 序列化成 `--mcp-config` 交给 CLI），不受 docker `--network none` 约束；外部 HTTP/SSE server 需要宿主机出网。`--network` 目前并不控制它们（因为目前没有它们）。

## Q2 Claude Agent SDK 对外部 MCP 的支持（官方）

来源：https://code.claude.com/docs/en/agent-sdk/mcp 与 https://code.claude.com/docs/en/agent-sdk/permissions （访问 2026-11-23）；类型来自本地 `claude_agent_sdk/types.py`。

- **配置格式（Python 类型）**：`McpStdioServerConfig{type?, command, args?, env?}`、`McpSSEServerConfig{type:"sse", url, headers?}`、`McpHttpServerConfig{type:"http", url, headers?}`、`McpSdkServerConfig{type:"sdk", name, instance}`（`types.py:857-908`）。官方文档："For the streamable HTTP transport, use `"type": "http"`"；`.mcp.json` 里 `"streamable-http"` 是 `"http"` 的别名，"The SDKs' `McpHttpServerConfig` type declares only `"http"`"。
- **命名**：`mcp__<server-name>__<tool-name>`；服务器名里的 `.` 和空格被替换成 `_`。
- **放行**：`allowed_tools=["mcp__github__*", "mcp__db__query"]`；官方："Allow rules accept tool-name globs only after a literal `mcp__<server>__` prefix ... `allowed_tools=["mcp__*"]` is ignored"。官方还写："MCP tools require explicit permission before Claude can use them"；`acceptEdits` 不自动批准 MCP 工具，`bypassPermissions` 会。
- **禁止**：`disallowed_tools` 是 deny 规则，在任何权限模式（含 bypass）下都生效；deny 规则支持工具名通配，`"mcp__*"` 匹配所有服务器的 MCP 工具。
- **评估顺序**：钩子 → deny 规则 → ask 规则 → 权限模式 → allow 规则 → `canUseTool`；`dontAsk` 模式下最后一步跳过，直接拒绝。`PreToolUse` 钩子对已被 allow 规则批准的调用同样运行（官方："To check calls that an allow rule ... approves, use a `PreToolUse` hook"）。
- **从文件读取**：`mcp_servers` 可以是 JSON 路径或字符串，原样传给 `--mcp-config`（`types.py:2365-2371`）。`.mcp.json` 在启用 `project` setting source 时自动加载（官方），但本仓库 `setting_sources=["user"]` 且 `strict_mcp_config=True`（"only use MCP servers passed via `mcp_servers`, ignoring all other MCP configurations ... project `.mcp.json`, user/global settings, plugin-provided servers"，`types.py:2373-2378`）。
- **进程内服务器**：`McpSdkServerConfig.instance` 可以是任意 `mcp.server.Server`（`types.py:882-897`）。
- **认证**：stdio 用 `env`，HTTP/SSE 用 `headers`；官方明确"The SDK doesn't open a browser or run an interactive OAuth flow"，服务器没令牌时状态为 `needs-auth`/`failed`，运行继续但没有该服务器的工具。
- **状态**：`system init` 消息里有每个服务器的 `status`（`pending/connected/failed/needs-auth/disabled`）；本仓库已把 `mcp_servers` 记进事件（`agent.py:1764`）。stdio 或无缓存的 HTTP/SSE 服务器会拖慢第一轮，默认 30 秒（`MCP_TIMEOUT`）。
- **工具搜索**：官方称 MCP tool search 默认开启，工具定义按需加载。本仓库用 DeepSeek 等非 Claude 模型的情况下是否生效**未确认**（ADR-0001:24 记录非 Claude 模型的兼容性本身就"未确认"）。
- **输出上限**：成功结果超 25,000 token 会存文件并以错误替换（官方，`MAX_MCP_OUTPUT_TOKENS` 可调）。

## Q3 上游怎么做

### 3.1 nao（`~/dev/AI/data-agent-refs/nao`，TypeScript）

我核对了下列 MCP 相关文件头部和全文 grep：`apps/backend/src/services/mcp.ts`、`mcp/server.ts`、`mcp/routes.ts`、`services/mcp-oauth.ts`、`mcp/tools/*.ts`、`agents/tools/mcp-connect.ts` 均**没有** `@license Enterprise` 标记（`grep -l "@license"` 对这些文件无输出）。标了 Enterprise 的是 `types/license.ts`、`types/backoffice.ts`、`types/member-budget.ts`、`services/sso-*.ts`、`services/member-budget.service.ts`、`services/backoffice.service.ts`、`routes/sso.ts`、`routes/branding.ts`、`utils/sso-session.ts`、`services/oidc-auth.service.ts`、`services/excluded-columns.service.ts` 等（`grep -rl "@license Enterprise"` 输出，仅列出前 30 个）。**以下 MCP 文件无 Enterprise 标记，但项目整体许可证与可复制性仍以 AGENTS.md 规则为准：不确定则只参考思路**（nao 自身 LICENSE 未在本次核对，未确认）。

**用户配置外部 MCP（客户端侧）**
- 配置文件 `agent/mcps/mcp.json`，格式 `{"mcpServers": {name: {transport: "streamable-http"|"sse"|"http"|"stdio", url|command,args,env, headers}}}`（`example/agent/mcps/mcp.json` 示例连 `mcp.notion.com`、`mcp.linear.app`；schema 在 `apps/shared/src/mcp.ts:1-60`）。
- 做法是**不把工具塞进上下文**：`McpService` 发现每个服务器的工具，写成每工具一个 OpenAPI 文件到磁盘 `agent/mcps/<server>/<tool>.json`，agent 用文件工具读、再用一个通用工具 `mcp_call` 调用（`services/mcp.ts:97-110` 类注释；`agents/tools/index.ts:109` 注册 `mcp_call: createMcpCallTool(options.mcpServers ?? null)`）；另有 `mcp_connect` 工具（`agents/tools/mcp-connect.ts`）。
- 底层用 `mcporter` 运行时 + 官方 TS SDK 的 SSE/Streamable HTTP 客户端（`services/mcp.ts:1-12,401-403,722-727`）。
- 参数校验：按发现到的 input schema 用 Ajv 校验后才调用（`services/mcp.ts:42-50,124-125`）。
- 管控：管理员可禁用整个服务器或单个工具（`services/mcp.ts:196-200,286-321`，`getDisabledMcpServers/Tools`）。云模式只允许 HTTP 传输，因为 stdio 会在共享主机上执行任意命令（`services/mcp.ts:54-64`、`901-908`）。OAuth 服务器按用户令牌调用（`services/mcp-oauth.ts`，`services/mcp.ts:385`）。
- 我没有查到 nao 用 `readOnlyHint` 之类注解做过滤：`grep readOnly` 对 `services/mcp.ts`、`agents/tools/mcp-call.ts` 无命中（该 grep 命令输出为空）。

**把 nao 暴露成 MCP server（服务端侧）**
- `apps/backend/src/mcp/server.ts:1-57`：官方 TS SDK `McpServer`，按项目设置注册子 agent 工具、上下文层工具、资产工具；路由 `mcp/routes.ts:1-60`：Fastify，`POST /mcp[/:projectId]`，Streamable HTTP，需认证（`requireAuthenticatedMcpUser`）。
- 把整个 agent 包成 `ask_nao`（`mcp/tools/sub-agent.ts:24-48`）：同步等待预算 45 秒，超时返回 `status:'running'` + `chatId`，客户端用 `get_nao_answer` 轮询（因为 MCP 客户端默认请求超时 60 秒，注释写明）；歧义时返回 `needs_clarification`，客户端带同一 `chatId` 再调。对话保存在 nao 界面里。
- `mcp/tools/register-mcp-tool.ts:35-46` 的 `registerTool` 没有传 `annotations`（该文件无 `annotations` 字样，grep 输出为空）。

### 3.2 SignalPilot（`~/dev/AI/data-agent-refs/SignalPilot`，Apache-2.0，Python）

- **作为 MCP server**：`signalpilot/gateway/gateway/mcp/server.py:1-124`，用 `mcp.server.MCPServer`（mcp 2.x 同名类），`stateless_http=True` 的 Streamable HTTP（`server.py:75-84`），DNS 重绑定保护的主机白名单（`server.py:14-33`）；工具用 `@audited_tool(mcp, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))` 注册（`mcp/tools/agent.py:84,109,122,128`）。聊天身份有工具白名单 `STANDALONE_CHAT_TOOL_ALLOWLIST`（`mcp/audit.py:180-195`）并在 `tools/list` 中间件里过滤（`server.py:55-70`）。研究笔记已有评估：`docs/research/2026-09-21-signalpilot-mcp-reuse.md`（gateway 要 Postgres 元数据库，MCP 不是独立进程）。
- **作为外部 MCP 客户端（"connectors"）**：`db/models/mcp.py:1-50` 注释 "Connectors: external MCP servers the chat agent can use (org or personal scope)"；工具以 `mcp__<slug>__<tool>` 暴露给 agent；传输 `http`/`sse`/`stdio(sandbox)`；密钥 Fernet 加密。`standalone_chat/execution.py:243-252` 在特性开关 `mcp_connectors` 下把连接器注入，经 gateway 内的代理（`mcp_connectors/proxy_server.py:1-25`：每次 `tools/call` 重新鉴权并写审计）。
- **按注解分默认开关（与本任务 Q4 直接相关）**：`mcp_connectors/tools.py:1-52`：默认 `readOnlyHint: true` → 开/auto；`destructiveHint: true` 或没有 `readOnlyHint` → 关；连接后新发现的工具一律先关并标记 `is_new`（`tools.py:4-7,46-52`）；上游描述当数据处理，去控制字符并截断到 2000 字符（`tools.py:12-30`）。
- **安全辅助**：`mcp_connectors/ssrf.py:1-30`（URL 只允许 https，localhost 例外；解析 DNS 后拒绝私网、链路本地、元数据地址；逐跳重查重定向；10 秒探测超时；1 MB 响应上限）；`mcp_connectors/policy.py:21-26,67-70`（组织级主机白名单、个人连接器开关）。
- **Notion**：不是外部 MCP，而是 gateway 自带的 API 工具（`mcp/tools/notion.py:1-50`：`list_notion_integrations`、`notion_search`、`notion_fetch_page`、`notion_create_page`，按集成存加密 API key）。Slack 在 SignalPilot 的 `mcp/tools` 目录里没有对应文件（`ls` 输出里无 slack）。
- **signalpilot-plugin**：`.claude-plugin/plugin.json` 只是 Claude Code 插件清单（name/version/license 等，无 MCP 配置字段）；`benchmark/mcp_config.json:1-12` 是评测用 stdio 配置 `python3 -m gateway.mcp_server`。插件本身不暴露额外 MCP。

### 3.3 dash（`~/dev/AI/data-agent-refs/dash`，Agno）

- `pyproject.toml:13` 依赖了 `mcp` 包，但 `dash/`、`app/` 下 `grep -i mcp` 无任何代码引用；README 只在知识表里写"Institutional Knowledge … MCP (optional)"（`README.md:154`）。结论：**dash 没有实现外部 MCP，也没有暴露 MCP**。
- Slack 是 Agno 的接口：`dash/team.py:21-28`（`SlackTools`，只给 leader）、`app/main.py:33-40`（`agno.os.interfaces.slack.Slack`）——属于 Slack 接入，不是 MCP。

### 3.4 ktx（`~/dev/AI/data-agent-refs/ktx`，TypeScript）

- 对外 MCP server，两种传输：stdio（`packages/cli/src/mcp-stdio-server.ts:1-72`，注释"stdout is reserved for JSON-RPC, so the logger writes to stderr only"）和本地 HTTP（`mcp-http-server.ts:1-299`：回环地址、可选 token、`allowedHosts/allowedOrigins`）。工具都带注解，例如 `sql_execution: {readOnlyHint: true, openWorldHint: false}`（`context/mcp/context-tools.ts:46-57`）。`setup-agents.ts` 负责给 coding agent 写配置（具体写法未读，未确认）。
- 自己的 Claude Code 运行时：`context/llm/claude-code-runtime.ts:244-270` 用 `managedSettings{allowManagedMcpServersOnly: true, allowedMcpServers}` 加 `strictMcpConfig: true`，并校验 init 消息里的 `mcp_servers` 恰好等于预期（`claude-code-runtime.ts:122-154`）。这是"禁止用户机器上的其他 MCP 混入"的做法。

## Q4 安全相关事实

1. **规范**（https://modelcontextprotocol.io/specification/2025-06-18/schema#toolannotations ，访问 2026-11-23；页面当前是 schema 参考页）：`ToolAnnotations` 含 `title`、`readOnlyHint`（默认 false）、`destructiveHint`（默认 true，仅在 `readOnlyHint==false` 时有意义）、`idempotentHint`（默认 false）、`openWorldHint`。原文声明全部是 hints，"Clients should never make tool use decisions based on ToolAnnotations received from untrusted servers"。
2. **SDK/CLI 里怎么用**：CLI 把 `readOnlyHint` 用于并发调度判定（`mayOverlap || readOnlyHint`，见 `docs/research/2026-11-04-serial-mcp-calls.md` §2.1(a)）、`isReadOnly()`、`isDestructive()`（同文 §2.1）。官方权限文档：plan 模式下"Calls to MCP tools that their server marks read-only"会交给 auto 模式分类器（"Calls to MCP tools that their server marks read-only"）。官方还有 `_meta["anthropic/requiresUserInteraction"]`：设了的 MCP 工具即使有 allow 规则也要走回调，`dontAsk` 下被拒（需要 Claude Code v2.1.199+）。**SDK 没有"按注解自动过滤工具"的选项**——我在 `types.py` 的 `ClaudeAgentOptions` 里没有找到这类字段（`grep` 命中的 MCP 相关字段只有 `mcp_servers`、`strict_mcp_config`、`can_use_tool`、`setting_sources` 等）。要按注解过滤，需要自己连上服务器读 `tools/list`（SignalPilot 就是这么做的），或用 `PreToolUse` 钩子/`can_use_tool`——但钩子入参是否含注解**未确认**。
3. **与本仓库原则的冲突点（事实陈述）**：
   - ADR-0010 与 AGENTS.md："对数据源的写只走 `dbt_execute`"；本仓库的读保证来自网关（`gateway.py:136` 只读连接、关外部访问）和 SQL 校验（`engine/`），**不是**来自模型选择。外部 MCP 服务器（如 MongoDB、Slack、Notion）有自己的写路径，不经过这个网关；官方文档自己举的例子也提醒"DBHub's `execute_sql` tool runs whatever SQL the agent emits, including writes, unless you restrict it"。
   - 沙箱无网络（`sandbox.py:275`）不覆盖外部 MCP：它们的进程在宿主机上（Q1.3）。
   - AGENTS.md 硬规矩："沙箱和 agent 拿不到写库凭据"。外部 MCP 的 `env`/`headers` 里放凭据，会进入 `--mcp-config` 命令行参数（`subprocess_cli.py:683-687`，`json.dumps({"mcpServers": ...})` 作为 CLI argv）；本仓库对数据库凭据的现有做法是走 SDK 子进程环境变量而不是 argv（`agent.py:1621-1629` 注释"never argv or the MCP config"）。
   - 事件文件会记录 `tool_start.input` 与 `tool_end.content`（`agent.py:1776-1806`）；`EventLog(secrets=...)` 有密钥脱敏（`agent.py` 里 `known_secrets/url_secrets` 导入，`agent.py:59`），对外部 MCP 返回的内容是否会泄漏第三方数据到运行产出（`runs/` 不进 git）——本仓库没有对这类内容的处理，**未确认**需要什么。
   - 评测约束：评测入口"一律不开 --network"（AGENTS.md）；若 `build_options` 默认挂外部 MCP，Harbor 与 DAB 两条路径都会受影响（见改动清单）。
4. **`src/data_agent/tool_annotations.py` 现在做什么**（`tool_annotations.py:1-79`）：这是**服务端**声明文件，不过滤外部工具。`READ_ONLY = ToolAnnotations(read_only_hint=True)`（所有数据工具），`SANDBOX = ToolAnnotations(read_only_hint=False)`（`sandbox_exec`），目的是让 CLI 并发执行只读工具（注释与 `docs/research/2026-11-04-serial-mcp-calls.md`）；另有 `DATABASE_ARGUMENT` / `database_tools`（多数据库时给每个工具加 `database` 参数，对标 nao `execute_sql`）和 `CHAT_ENV`（对话模式开关澄清工具）。
5. **上游对外部 MCP 的做法**：nao 按服务器/工具由管理员禁用，云模式禁 stdio，调用前校验入参（Q3.1）；SignalPilot 按注解设默认开关、新工具默认关、SSRF 防护、每次调用审计（Q3.2）；ktx 用 `strictMcpConfig` 禁外部混入（Q3.4）。

## Q5 (B) 暴露成 MCP server

- **MCP Python SDK 当前用法（本地已装 mcp 2.3.0）**：`from mcp.server import MCPServer`（本仓库 `tool_annotations.py:11` 已用 `from mcp.server import MCPServer`，`mcp_server.py:291` `server = MCPServer("data-agent")`）；`@server.tool(...)` 注册；`server.run("stdio")`（`cli.py:195,366`）；Streamable HTTP 用 `streamable_http_app(...)`/`run_streamable_http_async`（`mcpserver/server.py:1113,1288`）；SignalPilot 的用法示例：`mcp.streamable_http_app(stateless_http=True, transport_security=TransportSecuritySettings(...))`（`SignalPilot/.../mcp/server.py:75-84`）。官方 README（https://github.com/modelcontextprotocol/python-sdk ，访问 2026-11-23）的快速开始就是 `from mcp.server import MCPServer; mcp = MCPServer("Demo"); @mcp.tool()`，页面显示 `main` 分支且有 v2 迁移指引；README 里没有 stdio 运行命令和注解的示例（页面抓取结果，未再核对其他分支）。
- **能否把 `data-agent run` 包成 ask/run 工具**（事实）：
  - 函数入口 `agent.run(...)`（`agent.py:2061-2074`）是 async，返回 `Outcome`，有 `answer`、`finished`、`errors`、`events_path`、`infra_error`（`cli.py:411-420` 使用）；需要的输入：`Task(text, type, workspace, duckdb|postgres_env, data_source)`（`cli.py:380-390`）、`RunConfig`、`events_dir`、环境变量。
  - 一次运行要启动 SDK 子进程 + 本仓库的 stdio 数据 MCP 服务器 + 可选 docker 沙箱，耗时分钟级（`cli.py:395-409`；运行有 `--agent-timeout-sec`）。nao 的注释说 MCP 客户端默认请求超时 60 秒，因此 nao 做了 45 秒同步预算+轮询（Q3.1）。本仓库对应处理**未确认**，需要设计时决定。
  - 对话支持：`ConversationTurn` 与 `stdin_lines` 的 steering（`agent.py`、`cli.py:380-395`）是网页对话的机制；澄清工具仅对话轮次可用（`CHAT_ENV`）。
  - 数据源与工作区由调用方提供（`--duckdb`/`--postgres-env`/`--data-sources` TOML，`cli.py:327-331`），对外暴露时由谁选择数据源、凭据放哪里：**未确认**（本次只取证）。
- **上游先例**：nao `ask_nao`/`get_nao_answer`（Q3.1，HTTP，需认证）；ktx stdio+HTTP（Q3.4）；SignalPilot gateway（工具粒度，Q3.2）；dash 无。
- **Claude Code 侧如何接**：官方 https://code.claude.com/docs/en/mcp （本次未抓取该页，**未确认**命令细节）；本次只确认 SDK 文档里"在 CLI 里加 MCP 服务器"指向 "MCP installation scopes"。Cursor 的配置格式**未确认**（未查官方文档）。

## Q6 web 端

- 后端事件：`tool_start`（`tool`、`tool_use_id`、`parent`、`input`）和 `tool_end`（`is_error`、`content`）对任意 `ToolUseBlock`/`ToolResultBlock` 都写（`agent.py:1776-1806`）；`tool_timing` 钩子也是全工具（`agent.py:1677-1703`）。所以外部 MCP 工具的事件**会进事件文件**。
- 前端：`web/src/run-view/model.ts:374-425` 把 `tool_start`/`tool_end` 配对成 `Step`；`tool_end` 里只有名字以 `mcp__data__` 开头的成功结果才被 `parseTable`（`model.ts:183,415`）；`web/src/run-page/tools.ts:21-37` 的 `toolKind` 对 `mcp__data__*` 之外、非文件/编辑/Agent 的工具返回 `'other'`，标签 `TOOL`，说明 "Tool"（`tools.ts:48-49,66`）；`shortTool` 只剥 `mcp__data__`（`tools.ts:19-21`），所以外部工具在对话页显示成完整的 `mcp__notion__notion-search` 之类（`chat-page/Turn.tsx:117,196,202`）。`mcp__data__sandbox_exec`/`dbt_execute` 有专用面板（`web/src/run-page/tool-views.ts:5-6`）。
- 上述是读代码得出的静态结论，没有启动网页验证（本次不跑）；渲染是否有未知工具崩溃的情况**未确认**，也没有看 `web/src/chat-page/Turn.tsx` 的全部分支。

## 未确认项

1. `allowed_tools` 里 `mcp__data`（无 `__*`）与官方 `mcp__data__*` 写法是否等价（本仓库能跑，但没做对照）。
2. 外部 MCP 服务器配置放在哪里（`RunConfig`、`--data-sources` TOML、新文件）——本次不做设计；`web/sources.py`、`web/launcher.py` 内部逻辑只读到相关行，没有通读。
3. 非 Claude 模型（DeepSeek 的 Anthropic 兼容接口）下外部 MCP 工具、tool search 是否正常（ADR-0001:24 本身就标"未确认"）。
4. SDK 钩子/`can_use_tool` 的入参里是否带 MCP 工具注解；以及 CLI 对 `readOnlyHint` 的其他用法（只在 2026-11-04 笔记里有 CLI 2.1.301 的二进制源码证据）。
5. nao 项目许可证对 MCP 部分的整体可复制性（只确认这些文件没有 `@license Enterprise` 标记）；SignalPilot 是 Apache-2.0（`docs/research/2026-09-21-signalpilot-mcp-reuse.md`）。
6. ktx `setup-agents.ts` 给 Claude Code/Cursor 写什么配置；Claude Code、Cursor 接入 MCP server 的官方配置格式（本次未查官方文档）。
7. 一次 agent 运行（分钟级）作为 MCP 工具调用时，Claude Code/Cursor 的超时行为；本次只引用了 nao 注释中的"默认 60 秒"。
8. 外部 MCP 返回内容进入 `runs/` 事件文件的脱敏需求；外部 stdio MCP 进程的凭据注入通道（argv vs 环境变量）在 SDK 0.2.168 下是否能避免 argv（从 `subprocess_cli.py:667-687` 看 `--mcp-config` 是 argv，`env` 字段随 JSON 一起；SDK 的 `env` 选项是否会被 CLI 继承给外部 stdio 服务器：未确认）。
9. 网页端未启动验证外部工具渲染。
