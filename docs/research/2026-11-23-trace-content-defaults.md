# Trace 内容记录默认值调研（2026-11-23）

问题：LLM agent 的 trace 里，工具入参/出参、模型输入输出、查询结果，业界默认是"全记录"还是"默认不记、开关打开"？用于决定 data-agent（用户把 trace 发到自己的 Langfuse 项目）的默认。只读取证，未调模型、未改代码。访问日期均为 2026-11-23。

## 结论摘要（只列事实）

1. Langfuse Python `@observe`：默认捕获 input/output（环境变量未设即 True）。
2. Langfuse OpenAI 集成：文档称自动追踪"All prompts/completions"；页面未给出只关内容的开关。
3. Langfuse 遮掩：有 `mask`（旧）与 `mask_otel_spans`（新，推荐）；JS 是 `LangfuseSpanProcessor` 的 `mask`。默认不遮；文档未写默认遮掩。
4. LangSmith：文档说明靠 `LANGSMITH_HIDE_INPUTS/OUTPUTS=true` 关闭；默认值页面未明说，行文暗示默认发送。
5. OpenInference：所有 `OPENINFERENCE_HIDE_*` 默认 False，源码注释写"默认值使可观测性最大化"，即默认全记录。
6. OpenTelemetry GenAI 语义约定：`gen_ai.input.messages`、`gen_ai.output.messages`、`gen_ai.system_instructions`、`gen_ai.tool.call.arguments/result` 均为 Opt-In；规范写"SHOULD NOT capture them by default"；当前 main 规范的开关名是 `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT`，默认 `NO_CONTENT`。
7. Claude Code monitoring：`OTEL_LOG_USER_PROMPTS`、`OTEL_LOG_TOOL_DETAILS`、`OTEL_LOG_TOOL_CONTENT` 默认全部 disabled；文档未写"为什么"。
8. nao（上游 data agent）：Langfuse 导出需显式 opt-in 且必须显式给 base URL；默认导出 prompt 与回答；仅把 `execute_sql`、`execute_semantic_query`、`read_query_result` 三个工具的入参、结果、异常详情替换为 `[redacted]`。
9. SignalPilot、dash：未找到类似的内容记录/遮掩策略（见下"未确认"）。

## 逐题证据

### 1. Langfuse

- `@observe` 默认捕获。源码 `langfuse/_client/observe.py`（main，2026-11-23 经 raw.githubusercontent.com 取得）：
  - 约第 126-127 行 docstring：`capture_input ... Defaults to the LANGFUSE_OBSERVE_DECORATOR_IO_CAPTURE_ENABLED environment variable (True if unset).`
  - 约第 130-131 行：`capture_output ... Same default and override mechanism as capture_input.`
  - 约第 205 行：`LANGFUSE_OBSERVE_DECORATOR_IO_CAPTURE_ENABLED, "True"`，后接 `.lower() not in ("false", "0")`，所以只有 false/0 才关。
  - 约第 164 行示例：`@observe(capture_input=False, capture_output=False)`。
- 文档：https://langfuse.com/docs/observability/sdk/python/instrumentation （2026-11-23）列出 `capture_input=False, capture_output=False` 与 `LANGFUSE_OBSERVE_DECORATOR_IO_CAPTURE_ENABLED`，并写 "Capturing large inputs/outputs may add overhead."；该页没有针对 IO 的敏感数据建议（取证工具摘要，非逐字通读）。
- 遮掩：https://langfuse.com/docs/observability/features/masking （2026-11-23）
  - Python："For new Python SDK setups, prefer `mask_otel_spans`."；`mask` 是 legacy，只作用于 Langfuse SDK 创建的属性，不作用于第三方原始 OTel 属性。
  - JS/TS：向 `LangfuseSpanProcessor` 传 `mask`，"applied to the `input`, `output`, and `metadata` of every observation"。
  - 页面未记载默认遮掩；建议："Avoid recording sensitive attributes where possible."；多服务用 Collector 做统一策略。
- OpenAI 集成：https://langfuse.com/integrations/model-providers/openai-py （2026-11-23）称跟踪 "All prompts/completions with support for streaming, async and functions"；可 `openai.langfuse_enabled = False` 整体关闭或 `Langfuse(tracing_enabled=False)`；页面未描述只关内容的选项。
- LangChain 集成：未取证（未确认）。

### 2. LangSmith 与 OpenInference

- LangSmith：https://docs.langchain.com/langsmith/mask-inputs-outputs （2026-11-23）："LANGSMITH_HIDE_INPUTS=true" 与 "LANGSMITH_HIDE_OUTPUTS=true"，对 SDK 与 LangChain 均有效；也可在 Client 上设 `hide_inputs`/`hide_outputs`。默认值页面未明说。合规要求某些操作完全不能追踪时，文档建议 "conditional tracing" 而非遮掩。
- OpenInference：`python/openinference-instrumentation/src/openinference/instrumentation/config.py`（main）
  - 第 85/87 行 `OPENINFERENCE_HIDE_INPUTS` / `OPENINFERENCE_HIDE_OUTPUTS`，另有 HIDE_INPUT_MESSAGES、HIDE_OUTPUT_MESSAGES、HIDE_LLM_TOOLS 等。
  - 第 123-141 行：所有 `DEFAULT_HIDE_* = False`。
  - 约第 150-154 行 docstring：未传的属性从环境变量读取，"if not found, has default values that maximize observability."

### 3. OpenTelemetry GenAI

规范已迁到 https://github.com/open-telemetry/semantic-conventions-genai （opentelemetry.io 旧页面显示 "This page has moved"）。取自 `docs/gen-ai/gen-ai-spans.md`（main，2026-11-23）：

- 第 470-473 行："Model instructions, user messages, and model outputs are considered sensitive and are often large in size. Recording large or sensitive content in telemetry may be problematic due to high storage costs, regulatory requirements, or the need to enforce different access models for operational and user data."
- 第 475-476 行："OpenTelemetry instrumentations SHOULD NOT capture them by default, but SHOULD provide an option for users to opt in."
- 第 478-497 行三种用法：1. "[Default] Don't record instructions, inputs, or outputs."；2. 记录在 span 属性上，适合"pre-production environments"或存储合规的场景；3. 内容外存、span 只存引用，"recommended in production environments where telemetry volume is a concern or sensitive data needs to be handled securely"。
- 第 531-556 行："gated by an explicit user opt-in"；环境变量 "MUST be named `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT`"；值 `NO_CONTENT`（默认）、`SPAN_ONLY`、`EVENT_ONLY`、`SPAN_AND_EVENT`；非法值回落到 `NO_CONTENT`。受控属性含 `gen_ai.tool.call.arguments`、`gen_ai.tool.call.result`（见 `execute-tool.md` 第 82-83 行 Opt-In）。
- 旧名：`opentelemetry-python-contrib` 的 `opentelemetry-instrumentation-openai-v2/README.rst` 第 106-108 行："Message content ... are not captured by default. ... set the environment variable `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`"，值 true / span_only 等。即问题里的 `..._CAPTURE_MESSAGE_CONTENT` 是 Python contrib 的旧名，规范 main 现名为 `..._CAPTURE_CONTENT`。

### 4. Claude Code monitoring

https://code.claude.com/docs/en/monitoring-usage （2026-11-23；原 docs.anthropic.com 路径 301 到此；页面长 182558 字符，只读了前 100000，后半未读）

- `OTEL_LOG_USER_PROMPTS`："Enable logging of user prompt content (default: disabled)"
- `OTEL_LOG_TOOL_DETAILS`："Enable logging of tool parameters and input arguments in tool events and trace span attributes: Bash commands, MCP server and tool names, skill names ... and tool input. ... (default: disabled)"
- `OTEL_LOG_TOOL_CONTENT`："Enable logging of tool content in the `tool.output` span event (default: disabled). ... Requires tracing. Content is truncated at the content limit (60 KB by default)"
- Traces 节："Spans redact user prompt text, tool input details, and tool content by default."
- `OTEL_LOG_RAW_API_BODIES`："Enabling this implies consent to everything `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS`, and `OTEL_LOG_TOOL_CONTENT` would reveal"
- 另："Set `ENABLE_BETA_TRACING_DETAILED` only for debugging, not in production. ... high export volume"
- 关于"默认关的理由原文"：已读部分没有明说理由；只有"redact by default"和"implies consent"的措辞（可推断为隐私同意，属推断）。

### 5. 上游 data agent

nao（`~/dev/AI/data-agent-refs/nao/apps/backend/src/`）

`instrumentation.ts`：
- 第 7-14 行注释："Langfuse tracing is opt-in and requires an explicit destination: it only activates when LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL are all set. Without the explicit base URL the SDK would default to cloud.langfuse.com, silently exporting prompts and responses to a third party — a misconfigured self-hosted deployment must fail closed instead."
- 第 15-18 行：三项缺一则 `langfuseSpanProcessor` 为 undefined；第 20-25 行：有 key 无 URL 时告警并保持关闭。
- 第 28-30 行：用 `DataToolRedactingSpanProcessor` 包住 `LangfuseSpanProcessor`；第 33-36 行是 flush 注释（与遮掩无关）。

`utils/langfuse-redaction.ts`：
- 第 4 行：`REDACTED_DATA_TOOL_NAMES = {'execute_sql','execute_semantic_query','read_query_result'}`；第 9 行占位符 `[redacted]`。
- 第 5-8 行：处理的属性 `ai.prompt`、`ai.prompt.messages`、`ai.response.toolCalls`（JSON 内 `toolName` 属于上述三工具时，把 `input/args/output/result` 换成占位符，第 6、58-72 行）；工具 span 的 `ai.toolCall.args`、`ai.toolCall.result`（第 7、13-16 行）；异常事件的 `exception.message`、`exception.stacktrace`（第 8、74-84 行）。
- 非 JSON 属性值若包含这三个工具名子串则整体替换（第 51-54 行）。
- 保留：其他工具的入参出参；模型调用的文本 prompt 与回答（`ai.prompt.messages` 中非三工具的部分）；工具名、span 结构、时间与 token 等其余属性。
- 该文件无注释说明遮掩理由；理由只能从文件名/命名推断（数据类工具的结果可能含用户数据行，属推断）。另：`execute_sql` 的入参也被遮（SQL 文本不保留）。
- 注意：`ai.prompt.messages` 里若有前几轮的 tool-result 消息，按 `toolName` 字段匹配；若消息格式里工具结果以其他键出现则不会被遮（未确认，未跑测试）。

SignalPilot（`~/dev/AI/data-agent-refs/SignalPilot`）：
- `signalpilot/notebook-server/signalpilot/_tracer.py` 是 OTLP 导出封装（第 83-137 行读 `OTEL_EXPORTER_OTLP_*`）；`middleware.py:228` 只设 `http.status_code`。未发现 LLM 内容记录或遮掩逻辑。
- `benchmark/core/audit.py` 是评测结果的落盘存储（保存 TaskResult、transcript、网关 query 到本地），与可观测性后端无关。
- 全仓 grep `langfuse|OTEL_LOG|hide_inputs` 无结果。

dash（`~/dev/AI/data-agent-refs/dash`）：`app/main.py:77` 只有 `tracing=True`（Agno AgentOS 自带）；README:360 称 Agno control plane 处理 traces；仓库内无内容记录策略代码。Agno 自身的默认未取证（未确认）。

## 归纳（事实与推断分开）

事实：
- 规范层与"安全网关型"产品默认不记：OTel GenAI 规范 SHOULD NOT；Claude Code 默认 redact。
- 面向"可观测平台"的埋点库默认记：Langfuse `@observe`（True）、OpenInference（`hide_*` 全 False，注释写"maximize observability"）；LangSmith 行文暗示默认发送。
- 遮掩的颗粒度：Langfuse 靠 mask 函数，LangSmith/OpenInference 靠布尔开关；OTel 靠枚举 `NO_CONTENT/SPAN_ONLY/...`。
- 自托管上游 nao：应用自己写 trace，默认导出 prompt 与回答，只对"返回数据行"的三个工具遮入参、结果与异常；Langfuse 导出本身 opt-in 且 fail closed。

推断（未经本次验证）：
- 工具提供的 trace 平台以"记录全部、事后遮"为产品卖点（调试需要看内容）；通用规范与厂商 CLI 因面对不特定用户，默认不记。
- 用户自己的自托管 Langfuse 项目，数据不出用户边界，接近 OTel 所说"存储合规"的情形；nao 的做法（默认记 prompt/回答，只遮数据读取工具）是已有先例。
- data agent 的特殊点：工具结果即查询结果行，是最可能含敏感数据的部分，也是 nao 唯一遮的部分。

## 未确认项

- Langfuse LangChain 集成（CallbackHandler）的默认与遮掩选项：未取证。
- Langfuse 文档中 `mask_otel_spans` 的函数签名与示例：只读到摘要。
- LangSmith 默认是否记录：文档未明说，上文为推断。
- Claude Code 文档默认关的"理由"原文：未读到；文档后 82558 字符未读，可能有隐私说明。
- Agno（dash）tracing 默认记录什么：未取证。
- nao 的遮掩有无测试、是否覆盖 `ai.prompt.messages` 里的 tool-result 其他键：未读测试、未运行。
- OTel GenAI 规范仍标 Development，env var 名从 `..._CAPTURE_MESSAGE_CONTENT` 变为 `..._CAPTURE_CONTENT`，后续可能再变。
