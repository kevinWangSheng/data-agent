# 真实 OTLP 抓包（去敏后）

- 抓包：2026-11-23，主会话。`claude-agent-sdk` 0.2.187 自带 CLI 2.1.330（spike worktree `spike/otel`），`data-agent run --type query --config full --sandbox local`，夹具 `tests/fixtures/shop.sql` 建的 DuckDB，题目"按会员等级数客户（等级写法不统一，先归一）"，模型 deepseek-flash。CLI 直接发到本机回环地址上的接收端，没经过中转，没发 Langfuse。
- `full-*.json`：`OTEL_LOG_USER_PROMPTS=1`、`OTEL_LOG_TOOL_DETAILS=1`、`OTEL_LOG_TOOL_CONTENT=1`、`OTEL_LOG_RAW_API_BODIES=1`。
- `structure-*.json`：四个内容开关都不设。
- 两次都是 `CLAUDE_CODE_ENABLE_TELEMETRY=1`、`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`、`OTEL_TRACES_EXPORTER=otlp`、`http/protobuf`、`OTEL_TRACES_EXPORT_INTERVAL=1000`。各 5 个请求。
- 格式：每个文件是一个 `ExportTraceServiceRequest` 的 protobuf 标准 JSON（`google.protobuf.json_format.MessageToDict`）。
- 替换掉的值：`user.id` → 64 个 `0`；resource 的 `host.arch`、`os.type`、`os.version` → `placeholder-*`；字符串里的本机路径 → `/placeholder/path`，本机用户名 → `placeholder-user`（只命中 1 处 `user_prompt`）。`anthropic.custom_base_url` 在这次抓包里本来就是空串，没改。其余属性名、结构、id、时间原样。
- 检查：`python -m evals scrub --hostname <本机>` 对副本 0 处改动；密钥、IP、邮箱、本机路径的 grep 0 命中。

## 观察到的事实（给 113、112 参考）

- span：`claude_code.interaction` 1、`claude_code.llm_request` 5、`claude_code.tool` 6、`claude_code.tool.execution` 6、`claude_code.tool.blocked_on_user` 6。
- 两档的属性名集合完全一样；差别只在：内容全开时 `user_prompt` 是全文（关时是 `<REDACTED>`），`claude_code.tool` 多一个 span event `tool.output`（属性 `output`）。没看到单独的工具入参属性，没看到模型输出（`OTEL_LOG_RAW_API_BODIES` 没带出任何东西）。
- token 属性名：`input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_creation_tokens`（在 `claude_code.llm_request` 上）。
- MCP 服务器名：`anthropic.mcp.server.name`（在 `claude_code.tool` 上）。
- 其他 111 白名单之外、spec 也没提的属性：`anthropic.provider`、`gen_ai.provider.name`、`gen_ai.system`、`gen_ai.operation.name`、`gen_ai.response.finish_reasons`、`query_source_safe`、`speed`、`tool_name_safe`、`decision`、`source`、`parent.source`、`queued_sends`、`interaction.sequence`、`interaction.duration_ms`。
