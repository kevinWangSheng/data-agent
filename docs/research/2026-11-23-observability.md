# 可观测性 / trace 看板接入：取证（Langfuse、Claude Agent SDK 的 OpenTelemetry、事件流）

日期：2026-11-23。只取证，不做设计决定，不改 `src/`、`evals/`、`skills/`。没有调用模型，没有往任何外部服务写数据；网页只做读取（WebFetch、一次 PyPI JSON 的 GET）。外部文档的访问日期都是 2026-11-23。没读 `.env*`、`~/.config/data-agent/`、密钥文件。

背景（已定，`docs/plan.md:24,118,213`）：Langfuse 作为可选导出，同一份事件多写一份，开关控制；不用 LangSmith；评测容器里不发，只在本地运行和网页界面里接；"SDK 能否直接导出 OpenTelemetry 未确认"（`docs/plan.md:118`）。

## 结论摘要（只列事实）

1. Claude Agent SDK 自己不产生遥测；它起的 Claude Code CLI 子进程内置 OpenTelemetry，导出 metrics、logs/events 和 traces（traces 是 beta，要 `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`），可经 `OTEL_EXPORTER_OTLP_ENDPOINT` 指向任意 OTLP 端点，环境变量可以放进 `ClaudeAgentOptions.env`（§3）。这回答了 `docs/plan.md:118` 的"未确认"：官方文档说可以。
2. Langfuse 的 OTLP 端点是 `/api/public/otel`，只支持 HTTP/protobuf 和 HTTP/JSON，不支持 gRPC；认证是 Basic（公钥:私钥 的 base64），自托管要 v3.22.0 及以上（§2）。
3. 本仓库锁定 `claude-agent-sdk==0.2.168`（`uv.lock:537-546`，`pyproject` 约束见 `uv.lock:678`），自带 CLI 2.1.301（`.venv/.../_cli_version.py`）。文档写"`OTEL_EXPORTER_OTLP_PROTOCOL` 默认 `http/protobuf` 要 v2.1.323+"，2.1.301 低于它；2.1.301 上 traces 是否可用、默认协议是什么，未确认（§3）。
4. Langfuse 官方有 Claude Agent SDK 集成页，做法是 OpenInference 的 `openinference-instrumentation-claude-agent-sdk`（Python 包，包装 `query()`/`ClaudeSDKClient` 并注入 hook），不是 CLI 的环境变量（§4）。
5. Langfuse 自托管（v4，当前 compose）：7 个服务，其中 `langfuse-ai-gateway` 是 beta、可选，去掉后是 6 个（web、worker、clickhouse、minio、redis、postgres）；文档推荐至少 4 核 16 GiB、磁盘示例 100 GiB（"示例，不是最低值"）。许可证：除 `ee/` 目录外是 MIT Expat；核心功能不要 key，一批企业功能要 key（§1）。
6. 同类参照：Arize Phoenix（Elastic License 2.0）、OpenLLMetry（Apache 2.0，只有 instrumentation，没有自带后端）（§5）。
7. 上游 nao 已接 Langfuse（TypeScript、OTel + `@langfuse/otel`，三个变量齐了才开，带数据工具的脱敏处理器）；SignalPilot、dash 里没有 LLM trace 接入（§6）。
8. 现有事件流 `events.py` 只定义写入器（`seq`、`type`、`ts` + 任意字段，写前脱敏），事件类型不在这个文件里，散在 `agent.py` 等处共 26 种（§7）。每次模型调用的逐条 token 用量不在事件里，只有 `result` 里的整次运行总数。

## 1. Langfuse 自托管：组件、资源、许可证

**组件**（官方页 https://langfuse.com/self-hosting ，2026-11-23；页面标 Version v4）：Langfuse Web（UI 与 API）、Langfuse Worker（异步处理事件）、PostgreSQL（事务数据）、Redis/Valkey（缓存与队列）、ClickHouse（traces、observations、scores）、S3/Blob（原始事件、多模态附件、导出）；可选：LLM API（playground、评测）、Langfuse AI Gateway（beta）。

**compose 实际服务**（https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml ，2026-11-23）：

| 服务 | 镜像 | 端口 |
|---|---|---|
| `langfuse-worker` | `docker.langfuse.com/langfuse/langfuse-worker:${LANGFUSE_IMAGE_TAG:-4}` | 127.0.0.1:3030 |
| `langfuse-web` | `docker.langfuse.com/langfuse/langfuse:${LANGFUSE_IMAGE_TAG:-4}` | 3000（全网卡） |
| `langfuse-ai-gateway` | `docker.langfuse.com/langfuse/ai-gateway:...` | 3031（全网卡） |
| `clickhouse` | `docker.io/clickhouse/clickhouse-server`（26.3 系列，完整 tag 见官方 compose） | 127.0.0.1:8123、9000 |
| `minio` | `cgr.dev/chainguard/minio` | 9090（API，全网卡）、127.0.0.1:9091 |
| `redis` | `docker.io/redis:7` | 127.0.0.1:6379 |
| `postgres` | `docker.io/postgres:${POSTGRES_VERSION:-17}` | 127.0.0.1:5432 |

compose 文件里没有任何 `mem_limit` 或 `deploy.resources`。AI Gateway 需要 Langfuse 高于 v4.75.0（https://langfuse.com/self-hosting/deployment/docker-compose）。

**资源**：
- Docker Compose 页（https://langfuse.com/self-hosting/deployment/docker-compose ，2026-11-23）："We recommend that you use at least 4 cores and 16 GiB of memory"（示例 AWS t3.xlarge）；磁盘 "choose a sufficient amount of storage, e.g. 100GiB"，该页说观测数据"体量大"，100GiB 是示例。本机本地用的 CPU/内存没有给数字。
- 容器页（https://langfuse.com/self-hosting/deployment/infrastructure/containers ，2026-11-23）：生产环境 web 与 worker "at least 2 CPUs and 4 GB of RAM for all containers"；Node 默认堆约 1.7 GiB，要用 `NODE_OPTIONS` 的 `max-old-space-size` 对齐。ClickHouse、Redis、Postgres 没给最低值（未确认）。
- 自托管总览页说 Docker Compose 适合"Local use and testing"，"Single VM without high availability, scaling, or backups"。

**许可证**：
- 仓库 LICENSE 头（https://raw.githubusercontent.com/langfuse/langfuse/main/LICENSE ，2026-11-23）：`ee/`、`web/src/ee/`、`worker/src/ee/`、`packages/shared/src/server/ee/` 下的内容按 `ee/LICENSE`；其余是 MIT Expat；第三方组件保留各自许可证。
- 许可证 key 页（https://langfuse.com/self-hosting/license-key ，2026-11-23）："All core Langfuse features and APIs are available in the open-source edition without a license key."；需要 key（Enterprise 或 Government）的：项目级角色与受保护的 prompt 标签、数据保留策略与服务端数据脱敏、审计日志、UI 定制与限制创建组织、Organization Management API/SCIM/Instance Management API、外部媒体存储与 S3/Bedrock 的 IAM 角色认证；仅 Government：FIPS 模式、无遥测。key 通过 `LANGFUSE_EE_LICENSE_KEY` 配在 web 与 worker 上。
- 价格（付费档位和金额）这次没查，未确认。

## 2. Langfuse 接入方式

**Python SDK**（https://langfuse.com/docs/observability/sdk/python/overview ，2026-11-23；PyPI https://pypi.org/pypi/langfuse/json 查到最新版 5.2.0，`requires_python <4.0,>=3.11`，PyPI 的 license 字段为空）：
- v5，`pip install langfuse`，建立在 OpenTelemetry 之上；自托管要求 Langfuse 服务端 v4.59.0 及以上。
- 初始化：`get_client()`（单例，读 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL`；该页没提 `LANGFUSE_HOST`），`langfuse.auth_check()` 验连通。
- 手动打点：`langfuse.start_as_current_observation(as_type="span" | "generation", name=..., model=...)`，generation 可带 `model_parameters`、`usage_details`、`cost_details`；短生命周期程序要调 `langfuse.flush()`。
- 默认只导出 Langfuse 和 GenAI/LLM 的 span，可用 `should_export_span` 改。

**OTLP**（https://langfuse.com/integrations/native/opentelemetry ，2026-11-23）：
- 基础路径 `/api/public/otel`；traces 专用路径 `/api/public/otel/v1/traces`；本地示例 `http://localhost:3000/api/public/otel`；自托管 v3.22.0 起有这个端点。
- 认证头：`Authorization: Basic <base64(public_key:secret_key)>`。
- 协议：HTTP/JSON 和 HTTP/protobuf；"`gRPC` is not supported yet"。
- 建议加 `x-langfuse-ingestion-version: 4`：不加时，直接用 OTLP 摄入的数据"最多延迟 10 分钟"出现（页面没把它标成必需）。
- 属性映射：输入 `gen_ai.prompt`，输出 `gen_ai.completion`；模型 `gen_ai.request.model` 或 `gen_ai.response.model`；模型参数 `gen_ai.request.*`；用量 `gen_ai.usage.*`，成本 `gen_ai.usage.cost`；工具 span 的 `gen_ai.tool.call.arguments`/`gen_ai.tool.call.result` 仅在没有其他输入输出属性时用作输入输出；有模型属性才兜底推断为 `generation`，`langfuse.observation.type` 优先；`langfuse.*` 命名空间优先于通用 GenAI 约定。

## 3. Claude Agent SDK / Claude Code CLI 的 OpenTelemetry

来源：https://code.claude.com/docs/en/agent-sdk/observability 与 https://code.claude.com/docs/en/monitoring-usage （2026-11-23；后者全文约 18 万字符，只读到前 10 万字符，之后的事件属性等部分未读）。

- 机制：Agent SDK 把 Claude Code CLI 当子进程跑，CLI 内置 OTel；"The SDK does not produce telemetry of its own"，只把配置透传给 CLI，CLI 直接导出到 collector。
- 三种信号，各自开关：metrics（`OTEL_METRICS_EXPORTER`）、log events（`OTEL_LOGS_EXPORTER`）、traces（beta，`OTEL_TRACES_EXPORTER` + `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`，另需 `CLAUDE_CODE_ENABLE_TELEMETRY=1`）。
- 配置位置：进程环境，或 `ClaudeAgentOptions.env`（Python 里合并在继承的环境之上）。本仓库传 `env=settings.sdk_env() | server_env | session_env | effort_env`（`src/data_agent/agent.py:1633`）。
- 端点与协议：`OTEL_EXPORTER_OTLP_ENDPOINT`，可按信号覆盖（`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`、`..._TRACES_PROTOCOL`、`..._TRACES_HEADERS` 等）；协议 `grpc`、`http/json`、`http/protobuf`；头用 `OTEL_EXPORTER_OTLP_HEADERS`。页面没点名任何必须的后端，"any OTLP-compatible collector"。
- 官方示例（SDK 页）用 `http/protobuf`、`http://collector.example.com:4318`、`Authorization=Bearer ...`。
- SDK 页明确：不要在 SDK 里把 exporter 设为 `console`（stdout 是 SDK 的消息通道）。
- 导出失败默认静默；`CLAUDE_CODE_OTEL_DIAG_STDERR=1`（要 v2.1.179+）可把诊断经 SDK 的 `stderr` 回调拿到。
- 短任务：批量导出，默认 metrics 60 秒、traces 与 logs 5 秒；退出时 flush 有短超时，进程被杀会丢数据；页面建议把 `OTEL_*_EXPORT_INTERVAL` 调到 1000。
- span 层级（beta，"Span names and attributes may change between releases"）：`claude_code.interaction`（每个用户 prompt 一个根 span）→ `claude_code.llm_request`（记 `input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_creation_tokens`、`ttft_ms`、`stop_reason`、`request_id`）、`claude_code.tool`（`tool_name`、`duration_ms`、`tool_use_id`、`result_tokens`；子 span `claude_code.tool.blocked_on_user`、`claude_code.tool.execution`）、`claude_code.hook`（需要 `ENABLE_BETA_TRACING_DETAILED` 加 `BETA_TRACING_ENDPOINT`，页面警告不要用于生产，且这对变量会改变日志与 traces 的去向）。子 agent 的 `llm_request`、`tool` span 挂在父 agent 的 `claude_code.tool` 之下。span 默认带 `session.id`。
- 内容默认不导出：prompt 文本、工具入参、工具输出都要各自的开关（`OTEL_LOG_USER_PROMPTS`、`OTEL_LOG_TOOL_DETAILS`、`OTEL_LOG_TOOL_CONTENT`，原始 API 请求体 `OTEL_LOG_RAW_API_BODIES`），默认截断 60 KB（`CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH`，v2.1.214+）。
- 事件名（logs）有 `claude_code.user_prompt`、`claude_code.assistant_response`、`claude_code.tool_result`、`claude_code.tool_decision`、`claude_code.api_request`、`claude_code.api_error`、`claude_code.api_refusal` 等。
- 链接到应用自己的 trace：在 `query()` 时若应用里有活动的 OTel span，SDK 自动把 `TRACEPARENT`/`TRACESTATE` 注入子进程，CLI 的 `claude_code.interaction` 成为它的子 span；只有 SDK 和 `claude -p` 认入站 `TRACEPARENT`。自定义 `ANTHROPIC_BASE_URL` 代理时用 `CLAUDE_CODE_PROPAGATE_TRACEPARENT=1`。
- 受管设置里设了 `OTEL_EXPORTER_OTLP_ENDPOINT` 会移除开发者设的按信号端点；仓库级 `.claude/settings*.json` 不能设 OTel exporter 变量（monitoring 页）。
- 本仓库版本：`claude-agent-sdk==0.2.168`（`uv.lock:537-546`，上传时间 2026-10-15），自带 CLI `2.1.301`（`.venv/lib/python*/site-packages/claude_agent_sdk/_cli_version.py`，`agent.py:52,2167` 也把它记进 `version` 事件）。文档标的版本门槛：OTLP 默认 `http/protobuf` 要 v2.1.323+，`CLAUDE_CODE_OTEL_DIAG_STDERR` 要 v2.1.179+，内容截断变量要 v2.1.214+，事件带 trace_id 要 v2.1.212+。2.1.301 上 traces 是否可用没有实测，未确认。
- 本项目用的模型端点可能是非 Claude 模型（`agent.py` 的 `result_fields` 里有 `sdk_cost_usd`/`price_table` 的区别）；CLI 的 `claude_code.llm_request` span 里模型名和成本在这种情形下的内容，未确认。

## 4. Langfuse 官方的 Claude Agent SDK 集成

- 有：https://langfuse.com/integrations/frameworks/claude-agent-sdk （2026-11-23）。做法：OpenTelemetry 经 Python 包 `openinference-instrumentation-claude-agent-sdk`，代码是 `ClaudeAgentSDKInstrumentor().instrument()`，要在跑 agent 之前调；`get_client()` + `auth_check()`；可选 `@observe()` 与 `start_as_current_observation()` 加 user id、session id、tags、metadata；结束时 `langfuse.flush()`。页面写的排障项：无关 span 也计入计费单位要自己过滤；属性有的会落在 observation 的 metadata。
- 该包（https://github.com/Arize-ai/openinference/tree/main/python/instrumentation/openinference-instrumentation-claude-agent-sdk ，2026-11-23）：把 `query()` 与 `ClaudeSDKClient` 的每次 `receive_response()` 包成 AGENT span（`ClaudeAgentSDK.query`），工具调用的子 TOOL span 来自 PreToolUse/PostToolUse/PostToolUseFailure hook 注入；`receive_messages()` 不包。PyPI（https://pypi.org/pypi/openinference-instrumentation-claude-agent-sdk/json）最新 0.1.32，license 字段为空（页面也没写，未确认；OpenInference 仓库整体的许可证没查）。
- 与本仓库的关系：本仓库自己用 hook（`verifier_hooks`，`src/data_agent/verifiers.py:112-125`；`verifier_v2/hooks.py`），该包注入 hook 与现有 hook 是否冲突，未确认。Langfuse 页没给版本约束，与 0.2.168 是否兼容未确认。
- 官方文档里没有查到 Langfuse 对 Claude Code CLI 原生 OTel（metrics/logs/traces）的专页；这次没搜到，未确认。

## 5. 同类自托管替代（仅对照）

- Arize Phoenix：许可证 Elastic License 2.0（https://raw.githubusercontent.com/Arize-ai/phoenix/main/LICENSE 首行；PyPI `arize-phoenix` 22.0.1 license 字段 `Elastic-2.0`）。部署方式含 CLI、Docker/Compose、Kubernetes、Helm 等；自称"free to self-host with no feature limitations"（https://arize.com/docs/phoenix/self-hosting ，2026-11-23）；所需数据库组件该页没写，未确认。
- OpenLLMetry（Traceloop）：Apache 2.0，"a set of extensions built on top of OpenTelemetry"，只有 instrumentation/SDK，页面没提自带后端 UI，页面没列 Claude Agent SDK 的 instrumentation（https://github.com/traceloop/openllmetry ，2026-11-23）。
- 自托管成本（内存、磁盘）两者这次都没查到数字，未确认。

## 6. 上游参照（`~/dev/AI/data-agent-refs/`）

grep 关键字：langfuse、opentelemetry、otel、phoenix、langsmith（排除 node_modules、.git）。

**nao 有**（TypeScript，Vercel AI SDK，不是 Claude Agent SDK）：
- `nao/apps/backend/src/instrumentation.ts:1-60`：`LangfuseSpanProcessor`（`@langfuse/otel`）+ `NodeTracerProvider`；仅当 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL` 三个都设了才开；缺 base URL 时只警告、保持关闭，理由写在注释里：不设会默认发到 cloud.langfuse.com，把 prompt 和回复静默外发；自托管配错要"fail closed"；退出时 `forceFlush`；`DataToolRedactingSpanProcessor` 包在外层。
- `nao/apps/backend/src/utils/langfuse-redaction.ts:1-40`：在 `onEnd` 里把数据类工具（`execute_sql`、`execute_semantic_query`、`read_query_result`）的 `ai.toolCall.args/result`，以及 `ai.prompt`、`ai.prompt.messages`、`ai.response.toolCalls` 里的对应载荷换成 `[redacted]`，异常详情也处理；测试 `nao/apps/backend/tests/langfuse-redaction.test.ts`。
- `nao/apps/backend/src/agents/telemetry.ts:1-13`：`llmTelemetry(functionId, metadata)` 返回 AI SDK 的 `experimental_telemetry` 设置，`functionId` 成为 Langfuse 的 trace/span 名，`sessionId`、`userId`、`tags` 元数据被 Langfuse 原生识别；用在 `agents/subagents/run.ts:12,51` 与 `agents/memory/memory-extractor-llm.ts:13,48`。
- 配置入口：`nao/apps/backend/src/env.ts:401-413`、`nao/.env.example:300-305`、`nao/docker-compose.yml:41-45`（三变量默认空，可选）；依赖 `nao/apps/backend/package.json:69`（`@langfuse/otel ^5.9.1`）、`:75`（`@opentelemetry/sdk-trace-node ^2.9.0`）。

**SignalPilot 没有 LLM trace 接入**：命中的 `SignalPilot/signalpilot/notebook-server/signalpilot/_tracer.py` 是 notebook 服务的通用 OTel 追踪（`MockSpan` 兜底，`pyproject.toml:134-149` 有 `otel` 可选依赖：opentelemetry-api/sdk 1.28、OTLP http/grpc exporter），没看到与 LLM 或 agent 相关的 span；其余命中是 lockfile、`health.py`、`middleware.py` 等，没逐个读（未确认它们的用途）。

**dash**：只有 `dash/requirements.txt:42-45` 里 opentelemetry-api/sdk 1.40.0、instrumentation 0.61b0、semantic-conventions 0.61b0 的依赖行；代码里没有 opentelemetry、langfuse、openinference 的 `.py`/`.md`/`.toml` 命中（未确认它在运行时是否被用，可能只是传递依赖）。

## 7. 现有事件流（`src/data_agent/events.py`）

- `events.py` 本身（123 行）只定义写入器，不列事件类型：`EventLog.emit(type, **fields)`（`events.py:92-97`）每条写 `seq`（从 0 连续）、`type`、`ts`（UTC ISO）加任意字段，经 `_mask`（`events.py:113-123`）脱敏后 `json.dumps` 一行写入、立即 `flush`；文件用 `path.open("x")` 打开（`events.py:89`，每次运行一份，已存在则报错）。脱敏对象：`DATA_AGENT_API_KEY`、`DATA_AGENT_ANTHROPIC_API_KEY`、`DATA_AGENT_BASE_URL` 及其主机名（`events.py:27-36`）、数据库连接串的密码与主机（`events.py:49-70`）。
- 没有 span 的概念，没有 trace id、没有父子 id 的统一字段；配对关系靠 `tool_use_id`（`tool_start`/`tool_end`/`sql`/`tool_timing`），子 agent 靠 `parent`（`tool_start` 的 `parent_tool_use_id`）和 `agent_id`。

事件类型与关键字段（全部来自 `emit` 调用，按 `grep emit(` 加人读；每种我只列首个出处，部分类型有多处）：

| type | 关键字段 | 出处 |
|---|---|---|
| `version` | `git_commit`、`skill_version`、`skills`、`config`、`layers`、`model`、`sdk_version`、`cli_version`（可能还有更多，只读到前 10 行） | `agent.py:2158` |
| `task` | `task`、`task_type`、`data_source`、对话字段 | `agent.py:2176` |
| `dictionary` | `database`、覆盖统计、`tables_not_profiled` | `agent.py:332` |
| `status` | `state="started"`、`model`、`tools`、`mcp_servers`、`session_id` | `agent.py:1759` |
| `text` / `thinking` | `text` | `agent.py:1773,1775` |
| `tool_start` | `tool`、`tool_use_id`、`parent`、`input` | `agent.py:1778` |
| `sql` | `tool_use_id`、`sql`、`database` | `agent.py:1787` |
| `tool_end` | `tool`、`tool_use_id`、`parent`、`is_error`、`content`、`rejected_by` | `agent.py:1798` |
| `tool_timing` | `tool`、`tool_use_id`、`agent_id`、`duration_ms`、`failed`、`timed_out` | `agent.py:1690` |
| `turn_budget` | `turn`、`turns_left`、`max_turns`、`tool_use_id`、`session_id`、`text` | `agent.py:1436` |
| `clarification` | `tool_use_id`、`question`、`options` | `agent.py:1480` |
| `steer_delivered` | `id`、`text` | `agent.py:873` |
| `error` | `source`（`spend`、`steer`、`model`、`sdk`、verifier 钩子等）、`error`（`sdk` 时另有 `stderr_tail`） | `agent.py:569,838,860,931,1770,2270,2292`；`verifier_v2/hooks.py:185` |
| `answer` | `text`、`sql_tool_use_id` | `agent.py:2287` |
| `result` | `subtype`、`is_error`、`num_turns`、`terminal_reason`、`api_error_status`、`api_error`、`uncached_input_tokens`、`cached_input_tokens`、`output_tokens`、`cost_usd`、`sdk_cost_usd`、`cost_basis`、`errors`、`session_id` | `agent.py:2300`；字段构成 `agent.py:1830-1870` |
| `verifier_start` | `verifier`、`agent_id` | `verifiers.py:120` |
| `verifier_dispatch` | `n`、`models`、`source`、`tool_use_id` | `verifier_v2/hooks.py:229` |
| `verifier_end` | `verifier`、`agent_id`、`turns`、`tool_calls`、`hit_max_turns`、`checks`、`check_names`、`overall` | `verifier_v2/hooks.py:311` |
| `ended_disputed` / `ended_unverified` / `ended_with_unresolved` | 未读字段 | `verifier_v2/hooks.py:339,346,350` |

事实性观察（供后续判断映射，不是设计）：
- 一次运行有 `task` 起、`result` 止的边界，`status` 带 `session_id`，对话的下一轮 resume 同一个 SDK 会话（`agent.py:1759` 注释）。
- 工具调用有 `tool_start`/`tool_end` 配对（共用 `tool_use_id`），`tool_timing` 另给 `duration_ms`；`ts` 是写入时刻，不是模型调用的开始/结束时刻。
- 模型调用没有单独的事件：`text`/`thinking`/`tool_start` 是从 SDK 的 `AssistantMessage` 拆出来的，每条事件没有 token 用量和模型调用耗时；token、成本只出现在 `result`（整次运行合计，含子 agent，来自 `message.model_usage`，`agent.py:1845-1853`）。因此"每次 LLM 调用一个 generation 带 usage"这类映射，现有事件流里没有对应数据（CLI 的 `claude_code.llm_request` span 有按请求的 token 数，见 §3）。
- `thinking` 和 `tool_end.content`、`tool_start.input` 里可含数据库查询结果和 SQL；事件文件已做密钥脱敏，但没有对数据内容做脱敏（nao 另有数据工具载荷脱敏，见 §6）。

## 未确认项

1. Claude Code CLI 2.1.301（本仓库锁定）上 `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA` traces 是否可用、默认协议是什么、OTLP 到 Langfuse 能否真的收到 span：文档只给了几个版本门槛（2.1.323 等），没有实测，也没有查 CLI 变更日志。
2. 非 Claude 模型（本项目的 `ANTHROPIC_BASE_URL` 指向别的模型）下，CLI 的 `claude_code.llm_request` span 的模型名、token、成本字段的内容；Langfuse 能否把这些 span 识别成 generation 并给出可信的成本。
3. CLI 的 span 属性是否使用 `gen_ai.*` 命名：monitoring 页前 10 万字符里列的是 `input_tokens`、`output_tokens` 等自有名字，后 8 万字符（属性细节）没读；Langfuse 对 `claude_code.*` span 的映射因此未确认。
4. `monitoring-usage` 页后 82,558 个字符没读（事件属性、安全与隐私等）。
5. OpenInference 的 Claude Agent SDK 包的许可证、与 0.2.168 的兼容性、其 hook 注入与本仓库现有 hook 的相互作用。
6. Langfuse 企业版价格、各档位的用量限额；ClickHouse、Redis、Postgres 的最低资源；本机本地运行 compose 的实际占用。
7. `docs/plan.md:118` 说"6 个容器"：当前 compose 有 7 个服务，多出的是 beta 的 `langfuse-ai-gateway`；它是否可以不启动、compose 是否有 profile 开关，没有核对。
8. Phoenix 的所需数据库与资源；OpenLLMetry 的后端选择。
9. SignalPilot 里 `_tracer.py` 之外的 OTel 命中文件、dash 中 opentelemetry 的实际用途，没逐个读。
10. 事件类型表中的 `version`、`ended_*` 的完整字段没读全；`agent.py` 之外的 `emit` 调用点只做了 `grep emit(` 加 `src/` 全目录的正则，未覆盖通过别名调用 emit 的地方。
