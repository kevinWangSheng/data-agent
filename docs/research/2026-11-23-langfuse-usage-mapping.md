# 2026-11-23 Langfuse 的 usage 映射与 Claude Code OTel 属性

接 `2026-11-23-otel-spike.md` 的未确认项。访问日期均为 2026-11-23。Langfuse 源码取自 GitHub `langfuse/langfuse` main 分支当天快照（行号会随提交漂移）。只读取证，没调模型、没改代码。

## 结论摘要（只列事实）

1. Langfuse 的 OTel 摄入只从三处取 usage：`langfuse.observation.usage_details`（JSON 字符串）、以 `gen_ai.usage.` 开头的属性、以 `llm.token_count.` 开头的属性。CLI 的 `input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_creation_tokens` 没有这些前缀，所以不被映射。
2. 源码和官方文档里都没有 Claude Code / `claude_code.*` 的专门映射。
3. Claude Code 官方文档的 `claude_code.llm_request` 属性表里没有 `gen_ai.usage.*`；文档没提 `OTEL_SEMCONV_STABILITY_OPT_IN`。
4. 在 Langfuse 里定义自定义模型价格只在 usage 存在时生效；Langfuse 不再从文本估算 token。所以单独定价解决不了 usage 为空。
5. 官方有 `transform` processor（trace 部分 beta）可复制、删除 span 属性，能把 `input_tokens` 改成 `gen_ai.usage.input_tokens`。Langfuse 官方 Collector 示例里没有 transform 例子。
6. Hobby 档 50k units/月、30 天数据访问；官方页面没写超额后拒收还是限流。
7. `subprocess_cli.py` 的 TRACEPARENT 注入条件是：能 import `opentelemetry.propagate`，且 `propagate.inject` 得到的 carrier 里有 `traceparent`（即当前有活动 span）。
8. session id 属性：`langfuse.session.id`、`session.id`；trace name：`langfuse.trace.name`，缺省用根 span 名。

## 逐题事实与出处

### 1. Langfuse OTel 摄入的映射

出处：https://langfuse.com/integrations/native/opentelemetry （2026-11-23）；https://langfuse.com/docs/opentelemetry/get-started （2026-11-23）；源码 `packages/shared/src/server/otel/OtelIngestionProcessor.ts`。

文档表（generation 专属字段）：

| Langfuse 字段 | 认的属性 |
|---|---|
| model | `langfuse.observation.model.name`、`gen_ai.request.model`、`gen_ai.response.model`、`llm.model_name`、`model` |
| usage | `langfuse.observation.usage_details`（JSON）、`gen_ai.usage.*`、`llm.token_count.*` |
| cost | `langfuse.observation.cost_details`（JSON）、`gen_ai.usage.cost`（存为 `total`） |
| type | `langfuse.observation.type`；未设时按其他约定和 model 属性推断（有 model 则 generation） |
| sessionId | `langfuse.session.id`、`session.id` |
| trace name | `langfuse.trace.name`，缺省用根 span 名 |

源码核对（行号为当天快照）：

- model 候选键顺序（约 3051–3060 行）：`langfuse.observation.model.name`、`litellm.provider.model`、`gen_ai.response.model`、`ai.model.id`、`gen_ai.request.model`、`llm.response.model`、`llm.model_name`、`model`。
- usage：先读 `langfuse.observation.usage_details`（约 3086 行），有就直接用；否则按 scope 走 Genkit、Vercel AI SDK 等专门分支，最后落到 `extractGenericGenAiUsageDetails`（约 3345 行）。
- 通用分支（3349–3362 行）：只取键以 `gen_ai.usage.`（排除 `gen_ai.usage.cost`）或 `llm.token_count.` 开头的属性，去掉前缀作为 usage 键。
- 去前缀后的键再做归一（约 3390–3470 行）：`prompt_tokens` / `input_tokens` / `prompt` 归为 input；`completion_tokens` / `output_tokens` / `completion` 归为 output；缓存读的别名包括 `cache_read_input_tokens`、`cache_read_tokens`；缓存写的别名包括 `cache_creation_input_tokens`。归一后键名具体落成什么（例如 `input_cache_read`）我只看到了片段，细节**未确认**。
- cost（约 3540–3552 行）：`langfuse.observation.cost_details` > `gen_ai.usage.cost`（存 `total`）> `llm.cost.total`（存 `total`）。
- session（约 2793 行）：`["langfuse.session.id", "session.id"]`；Claude Managed Agents 的 span 额外认 `anthropic.session.id`。
- trace name：`LangfuseOtelSpanAttributes.TRACE_NAME`（即 `langfuse.trace.name`，约 1128、1175 行用到）。
- 源码里的 Claude 相关代码只有 Claude Managed Agents（scope `com.anthropic.claude-platform`、`anthropic.session.id`，约 2649–2675 行），不是 Claude Code CLI。`claude_code` 字符串在该文件中没有出现（grep 无结果）。
- 官方页面写明：usage 和 cost 只对 generation 生效；其余 OTel 属性放进 `metadata.attributes`，这与上次试验里 token 数只在 metadata 的现象一致。

### 2. 属性名不匹配时的官方办法

(a) OTel Collector：
- Langfuse 官方 Collector 示例（https://langfuse.com/docs/opentelemetry/get-started ，2026-11-23）有 `otlp` receiver、`batch`、`memory_limiter`、`otlphttp/langfuse` exporter（带 Basic 认证头和 `x-langfuse-ingestion-version: "4"`），以及一个 `filter` processor 示例；没有 `transform` 或 `attributes` processor 的示例。
- `transform` processor 官方 README（https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/transformprocessor/README.md ，2026-11-23）：trace 部分状态为 beta；改名写法是先复制再删除：
  `set(span.attributes["new.key"], span.attributes["old.key"])`、`delete_key(span.attributes, "old.key")`。
- 据此可行的映射（推论，未实测）：`input_tokens` -> `gen_ai.usage.input_tokens`，`output_tokens` -> `gen_ai.usage.output_tokens`，`cache_read_tokens` -> `gen_ai.usage.cache_read_tokens`，`cache_creation_tokens` -> `gen_ai.usage.cache_creation_tokens`。依据是源码的通用分支和别名表。注意 CLI 文档写 `input_tokens` 不含缓存读写，Langfuse 对 input 与缓存桶是否再扣减，**未确认**。

(b) Langfuse 端自定义映射：官方 OTel 文档里没找到按属性名自定义映射的设置（**未确认**是否存在）。能做的是让发送端直接写 `langfuse.observation.usage_details` 或 `langfuse.observation.cost_details`（JSON 字符串），它们优先于其他约定；Collector 里写 JSON 字符串比较绕，是否可行**未验证**。

(c) 自定义模型价格：
- 出处：https://langfuse.com/docs/model-usage-and-cost （2026-11-23）。
- 入口：Project Settings > Models，或 Models API（`POST /api/public/models`，另有 GET、GET by id、DELETE）。用户自定义模型优先于 Langfuse 维护的模型。
- 匹配字段是 `match_pattern`（正则，例：`(?i)^(gpt-4-0125-preview)$`）。我没找到 `matchPattern` 这个驼峰写法；API 请求体的完整字段表我没读到，**未确认**。
- 文档写明 Langfuse 不再从输入输出估算 token，也不对任何模型估算；没有 usage 也没有 cost 就不算 cost。入库时若同时有 usage 和命中的模型价格则计算 cost；若直接上报 `cost_details`，则按上报值，不再套价格。`usage_details` 的键必须与模型定义里的价格键完全一致；`input`、`output` 是互斥桶；`total` 可选。
- 结论：自定义价格只在 usage 已存在时才产生 cost，不能补 usage。

### 3. Claude Code 官方文档

出处：https://code.claude.com/docs/en/monitoring-usage （2026-11-23；该页约 18 万字符，我只读了前 10 万字符，后面未读）。

- `claude_code.llm_request` 属性：`model`、`gen_ai.system`（恒为 `anthropic`）、`gen_ai.request.model`、`input_tokens`（不含缓存读写）、`output_tokens`、`cache_read_tokens`、`cache_creation_tokens`、`duration_ms`、`ttft_ms`、`first_content_ms`、`stop_reason`、`gen_ai.response.finish_reasons`、`gen_ai.response.id`、`request_id`、`attempt`、`success`、`speed`、`effort`、`agent_id`、`parent_agent_id` 等。没有 `gen_ai.usage.*`。
- 页面没有提 `OTEL_SEMCONV_STABILITY_OPT_IN`，也没提切换到 GenAI 约定的开关；部分属性名遵循 GenAI 命名（`gen_ai.system`、`gen_ai.request.model`、`gen_ai.response.id`、`gen_ai.response.finish_reasons`、`gen_ai.tool.call.id`），文档未说它们受任何开关控制。
- 追踪需要 `CLAUDE_CODE_ENABLE_TELEMETRY=1`、`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`（别名 `ENABLE_ENHANCED_TELEMETRY_BETA`）和 `OTEL_TRACES_EXPORTER`。span 默认遮掉 prompt、工具输入、工具内容；`OTEL_LOG_USER_PROMPTS=1`、`OTEL_LOG_TOOL_DETAILS=1`、`OTEL_LOG_TOOL_CONTENT=1` 逐项打开。
- 入站上下文：Agent SDK 和 `claude -p` 会话在开始每个 `claude_code.interaction` span 时读环境里的 `TRACEPARENT`、`TRACESTATE`；`parent.source` 属性为 `env` 表示挂上了，`none` 表示没有。交互式会话忽略入站 `TRACEPARENT`。
- 出站：默认只在 `ANTHROPIC_BASE_URL` 未设或指向 Anthropic API 时向外发 `traceparent`；自定义代理要设 `CLAUDE_CODE_PROPAGATE_TRACEPARENT=1`。这只关系出站，不影响入站读取。我们的 deepseek 端点属于第三方，不会收到 `traceparent`（文档写明从不发给第三方提供方）。

### 4. Langfuse Cloud Hobby 额度

出处：https://langfuse.com/pricing （2026-11-23）；https://langfuse.com/faq/all/api-limits （2026-11-23）。

- Hobby：50k units/月，30 天 data access（页面把它与"数据保留管理"区分，后者是 Pro 和 Enterprise 的另选功能），2 用户（用户数来自搜索摘要，以官方页面为准未单独核对）。
- unit 的定义（pricing 页）：“A billable unit in Langfuse is any tracing data point sent to the platform.” 包括 trace、observation（span、event、generation）和 score。
- 超额行为：pricing 页没写；比较表里 Hobby 的 Additional usage 为 "--"，即没有付费超额价。我没找到官方说明超过 50k 后拒收还是限流，**未确认**（第三方文章的说法不作依据）。
- 速率限制（官方，与 unit 额度无关）：Hobby 的 tracing（`/ingestion`、`/otel`）为 1,000 req/min；桶耗尽返回 429，附 `Retry-After`；单请求体上限 5MB。
- 估算（推论）：上次试验一次运行产生约 20 个 observation，加上 trace，按这个量级 50k units 约够 2000 多次运行。

### 5. TRACEPARENT 注入与 Langfuse 的 session / trace name

文件：`/Users/shenghuikevin/dev/AI/data-agent-spike-otel/.venv/lib/python3.12/site-packages/claude_agent_sdk/_internal/transport/subprocess_cli.py`。

- 注入块在第 786–806 行。第 786 行 `from opentelemetry import propagate`，`propagate.inject(carrier)` 在第 789 行。
- 条件：第 790 行 `if "traceparent" in carrier:`。即 opentelemetry-api 可导入，且当前有活动 span（carrier 含 `traceparent`；仅有 baggage 不算）。注释写明：没有 opentelemetry-api 或没有活动 span 时不做事。整块被 `try/except Exception` 包住，失败只记 debug 日志，不影响 connect。
- 触发后：第 798–800 行先清掉继承来的 `TRACEPARENT`、`TRACESTATE`（除非在 `ClaudeAgentOptions.env` 里显式给了），第 801–804 行把 carrier 的每个键转大写写进子进程环境；`ClaudeAgentOptions.env` 里显式给的值始终优先。
- 要让 CLI 的 `claude_code.interaction` 成为子 span，还需要 CLI 在 SDK 或 `-p` 模式下读到它（见第 3 节入站）。这次没实测，**未确认**实际是否挂上，可看 `parent.source` 属性。
- Langfuse 侧：session id 用 `langfuse.session.id` 或 `session.id`（都认；CLI 自带 `session.id`，上次试验已看到）；trace name 用 `langfuse.trace.name`，不设则用根 span 名。这些是 trace 级属性，文档写明可设在任意 span 上。

## 未确认项

- 用 Collector 改名后，Langfuse 实际显示的 usage 键和 input 是否扣除缓存桶；cost 是否按自定义模型价格算出。需要一次实测。
- Hobby 超过 50k units 后是拒收、限流还是只提示；官方 FAQ 里可能有计费页，这次没找到。
- Langfuse 有没有按属性名自定义映射的设置；Models API 请求体的完整字段（含 `match_pattern` 之外的字段、各价格键）。
- deepseek-flash 是否已在 Langfuse 维护的模型表里（上次试验 cost 为空，说明至少没命中或没有 usage）。
- `monitoring-usage` 页面后 8 万字符未读，其中是否有 `gen_ai.usage.*` 或语义约定开关的说明。
- 在有活动 Python span 的进程里实际挂接 `claude_code.interaction` 的效果，以及 0.2.168 版本的行为。
