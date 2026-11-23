# 2026-11-23 Langfuse 接入试验：CLI 自带 OTel 对比 openinference

可观测性 grilling 的 Q5（先试验再定接入方式）。依据的文档事实见 `2026-11-23-observability.md`。

## 做法

- 一次性 worktree `../data-agent-spike-otel`（分支 `spike/otel`，不合入），`claude-agent-sdk` 从 0.2.168 升到 0.2.187；另装 `openinference-instrumentation-claude-agent-sdk` 0.1.32、`opentelemetry-sdk` 1.45.2、`opentelemetry-exporter-otlp-proto-http` 1.45.2。仓库代码没改。
- 数据：`tests/fixtures/shop.sql` 建的 DuckDB。题目：按会员等级数客户（等级写法不统一，先归一）。`data-agent run --type query --config full --sandbox local`，模型是产品默认的 deepseek-flash。
- A：只设环境变量，用 Claude Code CLI 自带的 OTel（`CLAUDE_CODE_ENABLE_TELEMETRY=1`、`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`、`OTEL_TRACES_EXPORTER=otlp`、`http/protobuf`，端点 `<base>/api/public/otel/v1/traces`，头 `Authorization=Basic …` 和 `x-langfuse-ingestion-version=4`）。内容开关（`OTEL_LOG_USER_PROMPTS`、`OTEL_LOG_TOOL_DETAILS`、`OTEL_LOG_TOOL_CONTENT`）都没开，即默认值。
- B：包装脚本在本进程里建 `TracerProvider` 和 OTLP HTTP exporter，调 `ClaudeAgentSDKInstrumentor().instrument()`，再调 `data_agent.cli.main`。
- 读回：新建的 Langfuse Cloud 项目拒绝旧接口 `GET /api/public/traces`（HTTP 410），改用 `GET /api/public/v2/observations`（`fields=core,basic,model,usage,metadata,io,trace_context`）。只打印名字、类型、层级、字段名和内容长度，不打印内容。
- 脚本在会话临时目录（`otel_spike.sh`、`otel_spike_b.py`、`lf_inspect.py`），没进仓库。

## 结果

两次运行都成功：各 6 轮，答案一致（Bronze 4、Gold 4、Silver 3、Platinum 1），花费 $0.0047（A）、$0.0045（B）。Langfuse 共收到 26 个 observation。

### A：CLI 自带 OTel（20 个 observation）

```
AGENT claude_code.interaction
  GENERATION claude_code.llm_request   model=deepseek-flash   ×4
  TOOL list_tables / query_database ×3   （MCP 工具）
    SPAN claude_code.tool.blocked_on_user   （权限判定）
    TOOL claude_code.tool.execution
```

- 每次模型请求一个 GENERATION span，属性里有 `input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_creation_tokens`、`ttft_ms`、`duration_ms`、`stop_reason`、`effort`、`gen_ai.request.model`。
- **但 Langfuse 的 usage 和 cost 列是空的**：token 数只在 metadata 里，属性名不是 Langfuse 识别的那一套，所以没被映射成 usage。cost 也没有（deepseek-flash 不在 Langfuse 的价目表里）。
- 默认不发内容：所有 observation 的 input、output 长度都是 0。interaction span 带 `user_prompt` 和 `user_prompt_length` 两个属性，`user_prompt` 的值是不是被遮掉了，**未确认**（读回脚本只打印字段名）。
- trace 带 `session.id`、`gen_ai.conversation.id`（CLI 的会话 id）。
- 运行是 6 轮，但只收到 4 个 llm_request。原因**未确认**：可能是轮数和请求数的计法不同，也可能是进程退出时最后一批 span 没发出去（文档写退出时 flush 超时很短，见 `2026-11-23-observability.md`）。

### B：openinference（6 个 observation）

```
AGENT ClaudeAgentSDK.ClaudeSDKClient.receive_response   model=deepseek-flash
  TOOL mcp__data__list_tables / dictionary_search / query_database ×3
```

- **没有每次请求的 span**：整次运行只有一个 AGENT span，usage 是全程合计（input 11,700、缓存读 33,664、output 0）。output 0 显然不对，原因未查。
- Langfuse 能识别它的 usage 字段（usage 列有值），cost 同样为空。
- 默认发全部内容：工具 span 的 input 是 SQL，output 是查询结果（长度 17–1,344），AGENT span 带题面和答案。
- 它不经过 CLI，没有权限判定和工具执行的子 span。

### 对照 Q1 的两条标准

| 标准 | A | B |
|---|---|---|
| 每次模型请求一个 span，带 token 和花费 | span 有；token 在 metadata 里，没映射到 Langfuse 的 usage；没有 cost | 没有，只有全程合计 |
| 主 agent、工具嵌套成一棵树 | 有 | 有，层级更浅 |
| 子 agent 嵌套 | 未验证（query 任务不调用验证子 agent） | 未验证 |
| 默认不发内容 | 是，内容开关逐项可开 | 否，默认全发 |

## 未确认

- 怎么把 A 的 token 映射成 Langfuse 的 usage 和 cost：Langfuse 是否支持按属性名映射，或者要不要在中间加一层 OTel Collector 改属性名，或者在 Langfuse 里给 deepseek-flash 定价。
- 怎么在 A 外面挂一个我们自己的父 span，带上 data-agent 的运行 id、配置、`result` 里的总花费、对话 id：文档说 SDK 在有活动 span 时会自动注入 `TRACEPARENT`（`subprocess_cli.py` 0.2.168 里已有注入代码），这次没有测。
- `user_prompt` 属性的值、`OTEL_LOG_TOOL_DETAILS` 打开后 SQL 会放在哪个字段、4 对 6 的差异。
- 0.2.168（CLI 2.1.301）上 A 能不能用：这次只测了 0.2.187。
- Langfuse Cloud 免费档的额度。
