# MCP 演示候选、客户端超时、nao ask 实现、SDK 外部 MCP 配置：取证（2026-11-23）

日期：2026-11-23。只取证，不改代码，不装、不跑任何 MCP server，不调模型。承接 `docs/research/2026-11-23-external-mcp.md`。官方页面访问日期均为 2026-11-23；GitHub 内容用 `gh api` 读（只读公开仓库）。

## 结论摘要（只列事实）

1. MongoDB MCP（`mongodb-js/mongodb-mcp-server`）：最新 release v3.3.0（2026-11-09），Apache-2.0；`npx -y mongodb-mcp-server@latest` 或 `docker run --rm -i mongodb/mongodb-mcp-server:latest`；需要 Node >= 22.13（README）。`--readOnly` / `MDB_MCP_READ_ONLY=true` 默认 false，开启后 create/update/delete 类工具"不注册"，聚合管道里的 `$out`/`$merge` 也被拒；连接串走环境变量 `MDB_MCP_CONNECTION_STRING`（README 推荐，避免出现在进程列表）。工具带 annotations：read/metadata/connect 类 `readOnlyHint=true, destructiveHint=false`，delete/update 类 `readOnlyHint=false, destructiveHint=true`，create 类 `readOnlyHint=false, destructiveHint=false`，所有工具 `openWorldHint=true`。
2. 其他演示候选（4 个）：GitHub 官方 github-mcp-server（MIT，远程 HTTP 或本地 stdio，有 `--read-only`）；modelcontextprotocol/servers 的 fetch（MIT，stdio，工具带 `readOnlyHint:true`）、filesystem（仓库整体 Apache-2.0 过渡中，stdio，有 `--read-only`）、git（stdio，README 未见只读模式）。
3. 超时：Claude Code 单次 MCP 工具调用默认约 28 小时硬上限（`MCP_TOOL_TIMEOUT` 未设时），另有空闲超时（HTTP/SSE 5 分钟、stdio 30 分钟，进度通知可避免空闲超时，但不延长硬上限），主对话里超过 2 分钟的调用会自动转后台任务。Cursor 官方 MCP 文档未写工具超时和进度通知。MCP 规范：实现 SHOULD 设超时，MAY 在收到进度通知时重置，SHOULD 总有最大上限。Python SDK（本地 mcp 2.3.0）客户端 `read_timeout_seconds` 默认 `None`。
4. nao `ask_nao`：同步等待 45 秒（注释称 MCP 客户端默认请求超时 60 秒），超时返回 `status:'running'` + `chatId`，客户端轮询 `get_nao_answer`；澄清作为返回状态 `needs_clarification`，客户端带同一 `chatId` 再调 `ask_nao`。相关文件没有 `@license Enterprise` 标记。
5. Claude Agent SDK：stdio 配置有 `env`，http/sse 有 `headers`；文档示例用 Python 自己的 `os.environ[...]` 拼值；`${VAR}` 展开只在 `.mcp.json` 示例里写明；`allowed_tools` 写 `mcp__server__tool` 精确名官方有示例，也支持 `mcp__server__*`；工具调用超时由 `MCP_TOOL_TIMEOUT` 环境变量控制。

## Q1 MongoDB 官方 MCP server

来源：https://github.com/mongodb-js/mongodb-mcp-server （README，`gh api .../contents/README.md`，2026-11-23）；代码在 monorepo 的 `packages/core`、`packages/tools-mongodb`。

- 版本：`gh api repos/mongodb-js/mongodb-mcp-server/releases/latest` 返回 `v3.3.0`，发布 2026-11-09T12:01:53Z（https://github.com/mongodb-js/mongodb-mcp-server/releases/tag/v3.3.0）。GitHub 仓库 license 字段：Apache-2.0。
- 运行要求：README 第 83、86 行：Node 至少 v22.13.0，Node 20 已弃用。
- 安装与启动（README）：
  - `npx -y mongodb-mcp-server@latest --readOnly`（README:108-119、188 附近，示例都带 `--readOnly`，README:108 写明 "All examples below include `--readOnly` by default"）。
  - Docker（README:198-236）：`docker run --rm -i -e MDB_MCP_CONNECTION_STRING -e MDB_MCP_READ_ONLY="true" mongodb/mongodb-mcp-server:latest`。"doesn't require a local Node.js installation"（README:200）。
  - 本项目禁 npm：README 没写 `pnpm dlx`；`pnpm dlx mongodb-mcp-server@latest --readOnly` 是否可用**未确认**（未运行）。Docker 路径有官方写法，可行性不依赖 npm，但需要宿主机有 docker 与能拉镜像。注意 Docker 路径下 stdio 进程在容器里，网络能否访问数据库（localhost 需 host 网络）**未确认**。
- 只读模式（README:553-566、`MDB_MCP_READ_ONLY`/`--readOnly` 默认 `false`，README:462）：
  - 保证范围，原文："restrict the MCP server to only use tools with "read", "connect", and "metadata" operation types. ... all tools that have "create", "update" or "delete" operation types will not be registered with the server."
  - 代码层再拦一道：`toolBase.ts:888` 当 `config.readOnly` 且 `operationType` 不在 `read/metadata/connect` 时拒绝；`mongodbTool.ts`（`packages/tools-mongodb/src/`）注释写明聚合管道含 `$out`、`$merge` 在 readOnly 下被拒，防止 `aggregate`/`export` 绕过（行内文字："In readOnly mode you can not run pipelines with $out or $merge stages."）。另有 `mqlGuards.ts` 默认拒绝服务端 JS 操作符 `$where`、`$function`、`$accumulator`。
  - README 没有声称它等价于数据库账号权限；它是服务端工具注册与参数检查。是否能被其他路径绕过**未确认**（未读完整代码）。建议同时用只读数据库用户（文档同段 README:551 也这么建议："a database user that has limited privileges"）。
- 连接串怎么传（README:403、417-425、217-221）：推荐环境变量 `MDB_MCP_CONNECTION_STRING`（README:403 "strongly recommend using environment variables for sensitive configuration"），也有 `--connectionString` 参数，README 警告参数可能出现在进程列表里；多连接用 `MDB_MCP_CONNECTION_STRINGS`（JSON，环境变量或配置文件）。WebFetch 摘要称 `MDB_MCP_CONNECTION_STRING` 已"deprecated"，但我读原始 README 表格（README:425）没有看到该字样，该点**不确认**。
- 工具列表：README 摘要为 53 个（Database 25、Atlas 22、Atlas Local 4、Assistant 2，WebFetch 摘要，未逐个核对原文）。Database 类含：`find`、`aggregate`、`aggregate-db`、`count`、`list-databases`、`list-collections`、`collection-schema`、`collection-indexes`、`db-stats`、`explain`、`export`、`mongodb-logs`、`connect`、`disconnect`、`list-connections`；写类：`insert-many`、`update-many`、`delete-many`、`create-collection`、`create-index`、`drop-collection`、`drop-database`、`drop-index`、`rename-collection`。Atlas 类需要 Atlas API 凭据，演示不需要。
- annotations（`packages/core/src/toolBase.ts`，`get annotations()`，约 478-505 行）：基础 `{title: name, openWorldHint: true}`；按 `operationType`：read/metadata/connect → `readOnlyHint=true, destructiveHint=false`；delete/update → `readOnlyHint=false, destructiveHint=true`；create → `readOnlyHint=false, destructiveHint=false`。README 本身没列注解（WebFetch 摘要说"无"，与代码不符，以代码为准）。
- 其他安全开关（README:422、529-533）：`confirmationRequiredTools` 默认含 `drop-database, drop-collection, delete-many, drop-index, atlas-*`；靠 elicitation，"If the client does not support elicitation, the tool will execute without confirmation"。SDK 客户端是否支持 elicitation **未确认**，所以不能依赖。

## Q2 其他演示用 MCP server（最多 4 个）

| server | 许可证 | 传输 | 只读模式 | 凭据 |
|---|---|---|---|---|
| GitHub 官方 `github/github-mcp-server`（最新 v2.2.0，2026-11-16） | MIT（GitHub API license 字段） | 远程 HTTP `https://api.githubcopilot.com/mcp/`；本地 stdio（`github-mcp-server stdio`，Docker `ghcr.io/github/github-mcp-server`）（README:59、230、418-434） | 有：`--read-only`，Docker 用 `GITHUB_READ_ONLY=1`（README:1749-1762）；README:497 "Read-only mode takes priority" | 一个 token：`GITHUB_PERSONAL_ACCESS_TOKEN`（README:199、230） |
| `modelcontextprotocol/servers` 的 fetch（`mcp-server-fetch`，2026.11.18） | MIT（`pyproject.toml`） | stdio（`uvx mcp-server-fetch`），也有 `--transport streamable-http`（README:349） | 本身只读：README:28 注解 `readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true` | 无；可访问任意主机，有 `--ignore-robots-txt` 选项（README:194） |
| 同仓库 filesystem | 仓库根 LICENSE：新贡献 Apache-2.0，旧代码 MIT（GitHub API 显示 NOASSERTION） | stdio；`npx -y @modelcontextprotocol/server-filesystem`（需 Node 24+，README:408-417）；也有 Docker | 有：`--read-only` 只注册 `readOnlyHint:true` 的工具；`--read-only-dir`（README:33-52） | 无；用允许目录参数限制 |
| 同仓库 git | 同上 | stdio（`uvx mcp-server-git`） | README 未见只读模式（grep `read.only` 只命中 gitconfig 挂载说明）；README:172 写明不加 `--repository` 时可读写任何可达仓库；含写工具 `git_add/commit/reset/...` | 无 |

- 官方说明这些是"reference implementations"，"not production-ready"（servers README:9）。GitHub 的远程 HTTP 地址与 Agent SDK 文档的示例一致（https://code.claude.com/docs/en/agent-sdk/mcp 的 "List issues from a repository"）。
- 本项目禁 npm：fetch/git 官方用 `uvx`，与项目用 uv 的约束一致；filesystem 官方只给 npx/docker，`pnpm dlx` **未确认**。
- 以上来源 URL：https://github.com/github/github-mcp-server 、https://github.com/modelcontextprotocol/servers （2026-11-23）。

## Q3 MCP 客户端单次工具调用超时

- **Claude Code**（https://code.claude.com/docs/en/mcp ，2026-11-23；WebFetch 只读到该页 158629 字符中的前 100000，后 58629 未读，`MAX_MCP_OUTPUT_TOKENS` 一节未读到）：
  - 单服务器 `timeout`（毫秒，写在 `.mcp.json` 条目里）覆盖 `MCP_TOOL_TIMEOUT`；"Values below 1000 are ignored and fall through to `MCP_TOOL_TIMEOUT`, or to its default of about 28 hours when that variable is unset."
  - "The per-server `timeout` is a hard wall-clock limit per tool call, and progress notifications from the server don't extend it."
  - 空闲超时："A tool call ... that sends no response and no progress notification for the idle window aborts"；默认 HTTP/SSE/WebSocket/connector 5 分钟，stdio 30 分钟；`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` 可改，`0` 关闭。也就是进度通知能避免空闲超时，不延长硬上限。
  - 自动后台化："A main-conversation call still running after two minutes moves to a background task"；`CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS` 可改，`0` 关闭。
  - HTTP 等的 per-request 计时取"60 秒、服务器工具超时、`MCP_TIMEOUT` 三者最大值"（页面摘要原话，未读上下文）。
  - 启动连接超时 `MCP_TIMEOUT` 默认 30 秒（https://code.claude.com/docs/en/agent-sdk/mcp 的 Connection timing 与 Connection timeouts 两节）。
- **Cursor**（https://cursor.com/docs/context/mcp ，2026-11-23，WebFetch 摘要）：页面没有工具调用超时设置，也没有进度通知说明；有 `${env:NAME}` 变量解析，适用字段 `command, args, env, url, headers`。Cursor 论坛有一帖报告 `notifications/progress` 没有重置超时、出现 -32001（https://forum.cursor.com/t/acp-mcp-client-should-reset-json-rpc-tool-call-timeout-on-notifications-progress-or-honour-resettimeoutonprogress/160548 ，只见搜索结果摘要，未读全帖；是论坛帖，不是官方保证；该帖针对 ACP MCP client，与 Cursor 编辑器是否同一实现**未确认**）。Cursor 实际默认超时：**未确认**。
- **MCP 规范**（https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle#timeouts ，2026-11-23）原文："Implementations **SHOULD** establish timeouts for all sent requests ... SDKs ... **SHOULD** allow these timeouts to be configured on a per-request basis. Implementations **MAY** choose to reset the timeout clock when receiving a progress notification ... However, implementations **SHOULD** always enforce a maximum timeout, regardless of progress notifications". 超时后发送方 SHOULD 发 `notifications/cancelled`（https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/cancellation）。
- **Python SDK**：本地 mcp 2.3.0 `client/session.py:408` `read_timeout_seconds: float | None = None`，`session.py:572-578` 单次请求 `request_read_timeout_seconds` 优先，否则用会话值，`None` 时不设超时。进度通知是否重置：本地只读了 `client/session.py`，`shared/session.py` 路径在 2.3.0 里不存在（grep 报 No such file），没查到重置逻辑，**未确认**。搜索结果里 TS SDK 的 PR 称 TS 的默认 60 秒（并有 `resetTimeoutOnProgress`），这是第三方镜像/PR 文字，不是对 Python 的保证。nao 注释写"MCP 客户端默认请求超时 60 秒"（`sub-agent.ts:25`）与此一致，但属于"某些客户端（如 Cowork）"（同注释）。
- **判断依据**（只列事实）：客户端的默认值差异大：Claude Code 实际能等很久（硬上限约 28 小时，空闲 5 分钟，需进度通知；2 分钟后转后台），部分客户端是 60 秒且不一定能被进度通知延长。"暴露成 MCP 的 ask 工具同步等几分钟"在 Claude Code 可行（要发进度通知才能越过 5 分钟空闲窗口，HTTP 传输），在 60 秒默认的客户端不可行；Cursor 未确认。

## Q4 nao 的 ask_nao / get_nao_answer

文件：`~/dev/AI/data-agent-refs/nao/apps/backend/src/mcp/tools/sub-agent.ts`（579 行）、`ask-nao-runs.ts`（91 行）。两个文件头部与全文 `grep "@license"` 无命中，**没有 Enterprise 标记**；nao 整体许可证未核对，复制前仍按 AGENTS.md 规则，下面只作思路参考。

- 同步等待：`sub-agent.ts:23-28` `ASK_NAO_SYNC_BUDGET_MS = 45_000`，注释："Kept safely below the MCP client default request timeout (60s) so clients that cannot extend it (e.g. Cowork) get a response in time and switch to polling."
- 后台运行：`sub-agent.ts:222-250` `runAskNaoInBackground` 把 agent 流跑完并把结果记进内存登记表，"Runs detached from the MCP request so it survives an early `ask_nao` response"（`sub-agent.ts:218-221`）。`ask_nao` handler（`sub-agent.ts:158-180`）：建/续对话，`askNaoRuns.start(chat.id)`，`waitForResultOrBudget(runPromise, 45000)`（`sub-agent.ts:254-268`，`Promise.race` 加定时器），出现三种结果：complete → `answerCompletePayload`；error → 抛错；pending → `runningPayload`（`sub-agent.ts:328` 起）。
- 轮询：`get_nao_answer`（`sub-agent.ts:183-206`）入参 `chatId`；先 `assertChatAccess`（`sub-agent.ts:320`，校验对话属于当前用户与项目），再 `resolveAnswerPayload`（`sub-agent.ts:270`）：登记表里有则按状态返回 complete/error/running；没有（过期或重启）则 `reconstructAnswerFromDb` 从持久化对话重建答案（`sub-agent.ts:289`，不重建 queries）。工具描述要求"polling every few seconds"（`sub-agent.ts:63-67`，由客户端 LLM 自己轮询，服务端不控制间隔）。登记表：`ask-nao-runs.ts:37` `Map`，完成后保留 `FINISHED_RUN_TTL_MS = 5 * 60 * 1000`（`ask-nao-runs.ts:41`）。
- 澄清：agent 的 `clarification` 工具调用被提取为答案的一部分（`extractClarification`，`sub-agent.ts:534-550`，按工具名 `clarification` 找 tool part，选项 `extractClarificationOptions` `sub-agent.ts:562`）；`answerCompletePayload`（`sub-agent.ts:351-353`）遇到澄清走 `clarificationPayload`（`sub-agent.ts:376-405`）：`status:'needs_clarification'`，`clarification:{question, options?}`，文字里写"Ask the user, then call ask_nao again with the SAME chatId ... as the question"。续答靠对话上下文（`chatId` 入参，`sub-agent.ts:122-129`；`buildChatContext` `sub-agent.ts:478`）。
- 返回内容：`outputSchema`（`sub-agent.ts:131-156`）：`status`（running/complete/needs_clarification，`get_nao_answer` 还有 error）、`chatId`、`chatUrl`（在 nao 界面打开）、`text`（最终回答，running 时为空）、`clarification`、`queries`（每个含 `id`、`columns`、`row_count`、前 3 行 `preview`，`sub-agent.ts:69-85`）、`stories`、`story_ids`；`content` 同时给文字块（`${text}\n\n[chatId: ...]\n[chatUrl: ...]`）与 JSON 块（`sub-agent.ts:351-375` 的 `answerCompletePayload`）。
- 描述文字里写明何时用、长任务怎么办、澄清怎么办（`sub-agent.ts:30-48`）。
- 没有看到 `ask_nao` 发送 MCP 进度通知的代码：`grep` 本文件无 `progress` 相关实现（本次只读了这两个文件，其他文件**未确认**）。

## Q5 Claude Agent SDK（Python）外部 MCP

来源：https://code.claude.com/docs/en/agent-sdk/mcp （2026-11-23，整页已读）；类型来自上一份笔记已引用的 `claude_agent_sdk/types.py:857-908`。

- stdio `env`：官方示例 `"env": {"API_KEY": os.environ["API_KEY"]}`（Python），同时说明"Use the `env` field to pass API keys, tokens, and other credentials to the MCP server"。
- http / sse `headers`：官方 Python 示例 `{"type": "http", "url": "https://api.example.com/mcp", "headers": {"Authorization": f"Bearer {os.environ['API_TOKEN']}"}}`；sse 同理 `"type": "sse"`；代码里的 http 类型只写 `"http"`，`.mcp.json` 才接受 `"streamable-http"` 别名。
- `${VAR}` 展开：文档只在 `.mcp.json` 示例后写 "The `${API_KEY}` syntax expands environment variables at runtime"（`env` 与 `headers` 两处）。Python 代码里的 dict（`mcp_servers=`）文档示例用 `os.environ[...]` 自己取值，**没有写** Python dict 里的 `${VAR}` 是否由 SDK 或 CLI 展开。我没有实验确认，标未确认。注意：如果通过 `mcp_servers=` 传 JSON 路径或字符串（`types.py:2365-2371`，上一份笔记），由 CLI 读取，可能走 `.mcp.json` 的展开规则，**未确认**。
- 工具调用超时：文档 Connection timeouts 一节："MCP server connections time out after 30 seconds by default. To change how long a running tool call may take, set `MCP_TOOL_TIMEOUT`"；`MCP_TIMEOUT` 管启动连接（毫秒）。Python SDK 的 `ClaudeAgentOptions` 里没有单独的 per-server timeout 字段这一点**未核对**（该页写的单服务器 `timeout` 只提 `.mcp.json` 和 TypeScript 的 `createSdkMcpServer()`）。`MCP_TOOL_TIMEOUT` 要放到 SDK 的 `env` 选项里（文档在 Connection timing 一节说 `CLAUDE_CODE_MCP_STARTUP_WAIT_MS` 放在 `env` 选项，见 https://code.claude.com/docs/en/agent-sdk/configuration#set-environment-variables ，该页未读），对 `MCP_TOOL_TIMEOUT` 同样适用是推断，**未确认**。
- `allowed_tools` 精确放行：有效。官方文档 "Auto-approve with allowedTools" 的 Python 示例：`allowed_tools=["mcp__github__*", "mcp__db__query", "mcp__slack__send_message"]`，并写"Use the full tool names in `allowedTools`"；快速示例里也有 `allowed_tools=["mcp__github__list_issues"]`。服务器名中的 `.` 和空格替换为 `_`。
- 权限：文档："MCP tools require explicit permission before Claude can use them"；`acceptEdits` 不自动批准 MCP 工具，`bypassPermissions` 会。
- 连接状态：`system init` 里 `mcp_servers[].status` 为 `pending/connected/failed/needs-auth/disabled`；未连上的服务器不抛异常，只是没有工具。
- 官方文档里的只读例子：DBHub 要靠配置 `readonly = true` 才拒绝写（文档原话："DBHub's `execute_sql` tool runs whatever SQL the agent emits, including writes, unless you restrict it"）。

## 未确认项

1. `pnpm dlx mongodb-mcp-server` / `pnpm dlx @modelcontextprotocol/server-filesystem` 能否直接运行（未运行；官方只给 npx / docker）。
2. MongoDB MCP 只读模式的完整保证范围（只读了 README 与 `toolBase.ts`/`mongodbTool.ts` 片段）；`MDB_MCP_CONNECTION_STRING` 是否已被标 deprecated（README 原文未见，WebFetch 摘要说有）。
3. 工具总数 53 与逐个工具的 `operationType`（仅用 WebFetch 摘要，未逐个核对）。
4. Cursor 的默认工具超时与进度通知行为；Python SDK 对进度通知重置超时的实现（`mcp/shared/session.py` 在 2.3.0 里不存在，未找到所在文件）。
5. Claude Code 文档页后 58629 字符未读（含 `MAX_MCP_OUTPUT_TOKENS`、MCP 输出限制等）。
6. Python `mcp_servers` dict 里 `${VAR}` 是否展开；`MCP_TOOL_TIMEOUT` 经 SDK `env` 选项传入是否生效；Python SDK 有无 per-server `timeout`。
7. nao 整体许可证；nao 其他文件是否发送进度通知。
8. Docker 方式跑 MongoDB MCP 时容器到数据库的网络可达性；SDK 拉起的客户端是否支持 elicitation（决定 MongoDB 的确认机制是否生效）。
