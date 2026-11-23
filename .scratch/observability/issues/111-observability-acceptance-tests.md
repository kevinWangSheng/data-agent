# 111 — 可观测性：验收测试（开关、CLI 环境变量、中转规则与转发、父 span、评测入口、trace-check、网页链接）

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (ticket agent)
Branch: worktree-111-observability-acceptance-tests

## Goal

spec "Testing Decisions" 列的每一组验收测试都写好，落在约定的接缝上：还没实现的行为按转绿的工单标预期失败（`xfail(strict=True)` / vitest `test.fails`），现在就该成立的"关闭时什么都不发生"一组不标，作守门。112、113 要实现的接口写成只有签名、函数体抛出未实现的空壳（Context 第 3 项）。全部不调模型、不联网。本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit:
  - `tests/acceptance/test_observability_switch.py`、`test_observability_relay.py`、`test_observability_evals.py`、`test_observability_trace_check.py`、`tests/acceptance/observability_support.py`（新建；support 放假 Langfuse、禁止联网的替身、夹具读取，照 `tests/acceptance/seam_support.py` 的做法）；
  - `tests/fixtures/observability/`（新建）：手工构造的 CLI OTLP 请求（JSON，见 Context 第 4 项）、Langfuse v2 observations 响应、项目列表响应；
  - `src/data_agent/tracing.py`、`src/data_agent/trace_relay.py`（新建，只有 Context 第 3 项列的类型、常量和抛出 `NotImplementedError` 的空壳）；
  - `src/data_agent/agent.py`：只给 `build_options`（`agent.py:1510`）加 `trace: CliTrace | None = None`，给 `run`（`agent.py:2060`）加 `trace_tier: TraceTier = "full"`，都不用；
  - `evals/harbor_agent.py`：只给 `DataAgentOptions`（`harbor_agent.py:193`）加 `trace: bool = False` 字段，加模块常量 `MANIFEST`（指向 `evals/manifest.json`），都不用；
  - `evals/dab.py`：只给 `run_query`（`dab.py:666`）加 `trace: bool = False`，不用；
  - `web/src/run-view/trace.ts`（新建：类型和抛出未实现的函数）、`web/src/run-view/acceptance/trace-link.test.ts`（新建）；
  - `pyproject.toml`、`uv.lock`：只把 `opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`、`opentelemetry-proto` 加进 `dev` 组，版本钉成已装的 `opentelemetry-api` 的版本（1.45.1），不许升级或降级 `opentelemetry-api`、`protobuf` 这些已有的正式依赖。它们是测试要用的；112、113 按 spec Q14 再把它们挪进正式依赖（112 挪 sdk 和 exporter，113 挪 proto）。dev 组不进 Harbor 容器的安装清单（`uv export --no-dev`，`tests/test_eval_wheels.py:91`）。
- Must not edit: `claude-agent-sdk` 的版本号和 `tests/test_harbor_agent.py:626`（SDK 升级是 112 的事）；`src/data_agent/` 和 `evals/` 里上面没列的文件和行为；`cli.py`（`--trace-tier`、`trace-check` 不加空壳，见 Context 第 3 项）；`.env.example`、README、`docs/`；已有的 `tests/acceptance/` 文件和 `tests/fixtures/` 里已有的文件；`web/` 里上面没列的文件；`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0（工单 agent 加载不了模型环境；本工单也不需要模型）

## Context

1. 行为来源：spec `.scratch/observability/spec.md` 的"开关与配置""覆盖范围""父 span 与 trace 组织""传给 CLI 的环境变量""本地中转""读回 trace""网页"各节，以及 "Testing Decisions" 的逐条清单（照它写，每条至少一个测试）；"工单"表定了 112、113、114 的分工。取证笔记：`docs/research/2026-11-23-otel-spike.md`（CLI span 的真实结构）、`docs/research/2026-11-23-langfuse-usage-mapping.md`（§1 Langfuse 认的属性名，§3 CLI 文档列的 `claude_code.llm_request` 属性，§5 TRACEPARENT 注入条件）、`docs/research/2026-11-23-observability.md` §3（span 层级、`claude_code.tool` 的属性）。

2. 先例：
   - 不调模型跑 `agent.run`：`tests/test_agent_outcomes.py:54` 的 `fake_query`，`monkeypatch.setattr(agent, "query", ...)`。
   - SDK 实际交给 CLI 子进程的环境（继承的 `os.environ` 加上 `options.env`）：`tests/test_context_window.py:51` 的 `cli_env`（替换 `anyio.open_process`，设 `CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK`）。合并规则在 SDK 0.2.168 的 `claude_agent_sdk/_internal/transport/subprocess_cli.py:819-824`，TRACEPARENT 注入在 `:831-835`（spec 引的 `:786-806` 是 0.2.187 的行号）。注意 SDK 会合并继承的环境：密钥"不在 env 里"要对合并后的环境断言，不能只看 `options.env`。
   - 只取选项、不起 CLI：`tests/test_effort_option.py:65` 的 `sessions`。
   - 本机假 HTTP 服务：`tests/fake_model.py`（`ThreadingHTTPServer`，记下每个请求）。
   - Harbor 适配器：`tests/test_harbor_agent.py:127-155` 的 `local`、`make_agent`，`:196` 用替身 `exec` 取传进容器的 env。DAB：`tests/test_dab.py:881-896` 的 `scripted`、`run_dab`；验收目录里已有一份 `make_dab`（`tests/acceptance/test_chat_eval_unchanged.py:119`），照它在本组里另写一份。
   - 现有代码的落点（112 要改的地方，测试从这里进）：
     - CLI env 在 `agent.py:1629-1633` 拼起来（`settings.sdk_env() | server_env | session_env | effort_env`），`sdk_env` 在 `agent.py:1219`。
     - 覆盖 shell 里继承的变量，先例是工单 104 的 `EFFORT_ENV`（`agent.py:132-135`）。
     - run 级别的命令行选项，先例是 `--effort`（`cli.py:296`）。
     - `result_fields` 在 `agent.py:1818`。
     - Harbor 适配器：`MODEL_ENV` 在 `harbor_agent.py:89`，`_model_env` 在 `:299-303`，`exec_as_agent(env=...)` 在 `:345-349`。
     - `harbor_run_args`：`ablate.py:436`、`board.py:163`。
     - DAB：`run_once` 在 `dab.py:405`，调 `build_options` 在 `:460`。
     - 划分清单：`evals/manifest.json` 是 `{"seed", "dev": [...], "holdout": [...]}`，各 30 题；同样结构的假清单是 `tests/fixtures/eval_manifest.json`。
   - Harbor 的 `extra_env`（job 配置的 `agent.env`）在 agent 阶段会叠加到**每一条** exec 上（`harbor/environments/base.py:471` 的 `scoped_exec_env`，`harbor/trial/trial.py:1718`）。所以三个 Langfuse 变量要像 `MODEL_ENV` 一样，从宿主环境用 `_get_env` 读（`harbor/agents/base.py:251-269`：先 `extra_env` 再 `os.environ`），只在要导出的 trial 里放进 `exec_as_agent` 的 `env`。测试用 `monkeypatch.setenv` 把它们放进宿主环境，不放进 `extra_env`。
   - 预期失败按工单分标记、转绿的工单只删自己那个：工单 88（`.scratch/chat-ui/issues/88-chat-acceptance-tests.md`）的 `TICKET_NN = pytest.mark.xfail(strict=True, reason=...)`；vitest 用 `test.fails`（工单 73 的 `web/src/run-view/acceptance/run-view.test.ts`）。

3. 本工单要定下、写进代码并在最终回复里逐项列出供人审的契约（只有签名，函数体 `raise NotImplementedError("ticket 112")` / `("ticket 113")`）：
   - `src/data_agent/tracing.py`（112 实现）：
     - `TraceTier = Literal["full", "structure"]`、`TRACE_TIERS`；`LANGFUSE_ENV = ("DATA_AGENT_LANGFUSE_PUBLIC_KEY", "DATA_AGENT_LANGFUSE_SECRET_KEY", "DATA_AGENT_LANGFUSE_BASE_URL")`。
     - `@dataclass(frozen=True) class LangfuseSettings(public_key, secret_key, base_url)`，`from_env(env: Mapping[str, str]) -> LangfuseSettings | None`：三个都非空才返回设置，否则 `None`（关闭）。
     - `@dataclass(frozen=True) class CliTrace(endpoint: str, tier: TraceTier)`：`build_options` 拿到它就给 CLI 设导出变量；`None` 就是关闭。
     - `parent_exporter(settings: LangfuseSettings) -> SpanExporter`：父 span 的 exporter。`run` 每次调用时从模块上取（`tracing.parent_exporter`），测试换成 `InMemorySpanExporter`。类型只在 `TYPE_CHECKING` 下导入，`src` 运行时不导入 OpenTelemetry。
   - `src/data_agent/trace_relay.py`（113 实现；112 只调 `TraceRelay` 的构造、`start`、`stop`）：
     - `@dataclass(frozen=True) class ExportError(error_type: str, status: int | None)`；`@dataclass(frozen=True) class RelayReport(forwarded_spans: int, forwarded_llm_requests: int, errors: tuple[ExportError, ...])`。
     - `class AnonIds`：一次运行一个，无参构造；同一个对象里同一个原 id 总是换成同一个匿名 id。
     - `rewrite(body: bytes, *, tier: TraceTier, ids: AnonIds, secrets: Sequence[str]) -> bytes`：输入、输出都是序列化后的 `ExportTraceServiceRequest`；`secrets` 是这次运行已知的密钥值（`events.known_secrets`，`src/data_agent/events.py:30`）。
     - `class TraceRelay(langfuse: LangfuseSettings, tier: TraceTier, secrets: Sequence[str] = (), drain_timeout_s: float = 10.0)`；`start() -> str` 返回 `http://127.0.0.1:<端口>/v1/traces`；`stop() -> RelayReport` 等已收到的请求转发完（不超过 `drain_timeout_s`）再关端口。出错不抛出，记进 `RelayReport.errors`；由 `run` 把每条写成 `trace_export_error` 事件（只有 `error_type`、`status` 两个字段）。中转不直接写事件文件：它在自己的线程里收请求，而 `EventLog.emit`（`events.py:92-97`）没有加锁、`seq` 自增，多线程写可能乱序或重号（按代码判断，没实测）。这把"写错误事件"从 113 移到了 112。
     - `run` 和 `dab.run_once` 都从模块上取 `trace_relay.TraceRelay` 和 `tracing.parent_exporter`。所以 112 的每个打开导出的测试（父 span、`trace` 事件、`--trace-tier`、DAB、Harbor 之外的评测入口）都装上记录调用的假中转和 `InMemorySpanExporter`，不依赖 113 是否已合入。
   - `agent.py`：`build_options(..., trace: CliTrace | None = None)`；`run(..., trace_tier: TraceTier = "full")`。`run` 自己读 `LangfuseSettings.from_env(env)`（和 `ModelSettings.from_env` 同一个 `env`）。
   - 新事件（112 写）：`trace`，字段 `trace_id`（32 位十六进制，等于父 span 的 trace id）、`url`、`forwarded_spans`、`forwarded_llm_requests`，中转停下后写一次；`trace_export_error`，字段 `error_type`、`status`。测试不锁定 `trace` 和 `result` 的先后。
   - `evals/harbor_agent.py`：`DataAgentOptions.trace: bool = False`（Harbor 的 `--ak trace=true`，`evals ablate/board --trace` 传）；模块常量 `MANIFEST`，适配器调用时从模块上读，测试换成 `tests/fixtures/eval_manifest.json`（假题名 `dbt-alpha` 等，不用真实留出题名）。题名从 Harbor 在 agent 运行前写好的 `<trial>/config.json` 读（`harbor/trial/trial.py:493` 调 `_init_result`，`:1067` 写文件；`TrialConfig.task.get_task_id().get_name()`，再用 `evals.harbor.task_id` 去掉 org），测试按这个布局造 trial 目录（`<job>/<trial>/config.json` 由 `TrialConfig(task=TaskConfig(path="tasks/<name>"))` 生成，`<job>/<trial>/agent/` 作 `logs_dir`）。
   - `evals/dab.py`：`run_query(..., trace: bool = False)`。
   - `web/src/run-view/trace.ts`（114 实现）：`export type TraceLink = { traceId: string; url: string }`；`export function traceLink(events: readonly RunEvent[]): TraceLink | null`，空壳抛出未实现。运行页和对话页（每轮的事件）都用它。网页没有 DOM 测试库，所以接缝是这个纯函数，链接的渲染由 114 自己的测试和截图证明。
   - 不加空壳的：`data-agent run --trace-tier`、`data-agent trace-check`、`evals ablate/board/dab --trace` 的命令行选项。没实现时 argparse 报 "unrecognized arguments" / "invalid choice"，这就是"功能没做"的失败原因。

4. CLI 的 OTLP 夹具：**手工构造**，不抓真实请求。理由：本工单花费上限 $0，工单 agent 加载不了模型环境（113 也一样，所以 spec 写的"113 先抓一份"做不到）；试验时内容开关全关，内容属性叫什么本来就没确认（otel-spike 笔记"未确认"一节）。做法：
   - 存成 protobuf 标准 JSON 映射（`google.protobuf.json_format`，bytes 字段是 base64），测试里 `Parse` 成 `ExportTraceServiceRequest` 再序列化，人审时能直接读。
   - 结构照试验读回的树（otel-spike 笔记"A"一节）：`claude_code.interaction` → 多个 `claude_code.llm_request`、`claude_code.tool`（内置工具、本仓库 MCP 工具 `mcp__data__query_database`、一个外部 MCP 工具）→ `claude_code.tool.blocked_on_user`、`claude_code.tool.execution`；再加一个验证子 agent：`Agent` 工具 span 下挂子 agent 的 `llm_request`（observability 笔记 §3："子 agent 的 span 挂在父 agent 的 `claude_code.tool` 之下"）。interaction 的父 span id 是一个固定值，模拟 TRACEPARENT。
   - 属性名分两类，在测试模块的文档字符串里逐个标明：有出处的（usage-mapping 笔记 §3 的 `llm_request` 属性表和 `parent.source`、§5 的 `session.id`；observability 笔记 §3 的 `tool_name`、`tool_use_id`、`duration_ms`；otel-spike 笔记读回的 `gen_ai.conversation.id`、`user_prompt`、`user_prompt_length`；spec "本地中转"一节点名的 `gen_ai.tool.call.id`、`anthropic.custom_base_url`、`user.id`、`terminal.type`、`llm_request.context`）；占位的（内容属性：工具入参、工具输出、模型输出的名字，MCP 服务器名的名字，scope 名，resource 里的主机和系统属性）。开发集档的测试只断言"原样保留"，留出集档只断言"输出在白名单里"，所以占位名猜错也不影响测试的意义。
   - 白名单以外的属性要放在每一层：resource、scope、span、span event。另放一个"CLI 将来新增的属性"（如 `claude_code.future_field`）。
   - 形如密钥的值（`sk-` 加 40 位）和模型端点地址（如 `https://llm.example/anthropic`）**在测试运行时拼出来再塞进请求**，不写进夹具文件：否则 pre-commit 的密钥扫描和 `evals scrub` 都会报。
   - 113 合入前，主会话可以按 spec"验收"一节真跑一次，抓一份真实请求，去掉内容后作 113 自己单元测试的补充夹具。它不替换本工单的夹具，也不进 `tests/acceptance/`。

5. 标记（每个用例只属于一个工单；跨两个工单的用例同时挂两个标记，各自删自己那个，两个都删掉时转绿）：
   - `TICKET_112`：`from_env`、CLI 环境变量、`--trace-tier`、父 span、`trace` 和 `trace_export_error` 事件（换成假中转）、评测入口、`trace-check`。
   - `TICKET_113`：`rewrite` 的两档规则、`TraceRelay` 的接收、转发、出错、收尾计数（直接驱动中转，不经过 `run`）。
   - `TICKET_112` 加 `TICKET_113`：一条端到端用例。`run` 用真中转和假 Langfuse，假 `query` 把夹具 POST 到 `options.env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]`。断言 `trace` 事件的计数等于假 Langfuse 收到的 span 数；假 Langfuse 返回 500 时 run 照常结束，事件里有 `trace_export_error`。
   - `TICKET_114`：vitest `test.fails`。
   - 不标（守门，现在就通过）：关闭的几种情形；夹具能解析、假 Langfuse 能应答的自检。

6. 写法上的要求：
   - **strict 预期失败不能 XPASS**：许多"没有 X"的断言今天就成立（关闭时没有 `OTEL_*`、不带 `--trace` 时没有 Langfuse 变量、env 里没有密钥、留出集档没有内容开关）。这些要么归进不标的守门组，要么和"打开时 X 有值"的正向断言写在同一个用例里，正向断言放在前面。
   - **不锁定 spec 留给实现时核实的东西**：
     - 缓存 token 只断言值出现在某个 `gen_ai.usage.` 或 `llm.token_count.` 开头的键下，原键没了。
     - 模型输出的内容开关只断言开发集档的三个开关（`OTEL_LOG_USER_PROMPTS`、`OTEL_LOG_TOOL_DETAILS`、`OTEL_LOG_TOOL_CONTENT`）为 `1`；留出集档断言没有任何 `OTEL_LOG_` 开头的变量为真（按 CLI 的真值规则 `1/true/yes/on`，见 `agent.py:1243` 的注释）。
     - trace URL 只断言以 `BASE_URL` 开头、含 trace id。
     - 密钥和端点只断言它们不出现在转发出去的字节里、相邻的内容还在，不管是删属性还是遮值。
     - `usage_details` 只断言是 JSON、`output` 等于 `result.output_tokens`、各值之和等于 `result` 的三项 token 之和；`cost_details` 的 `total` 等于 `result.cost_usd`（`agent.py:1818`）。
     - 单次运行的 `langfuse.session.id` 只断言非空、两次运行不同。取值的建议（事件文件名去掉 `.jsonl`）写进最终回复供人定；不能用 `--events-dir` 的目录名，Harbor 下它总是 `events`（`harbor_agent.py:222`）。
   - tag 的属性名（`langfuse.trace.tags`）和 v2 observations、项目列表的响应结构，从 Langfuse 官方文档或 API 参考取，测试里注明出处和访问日期；查不到就停下交回。
   - 父 span：测试在一个进程里跑很多次 `run`，OTel 的全局 TracerProvider 只能设一次，所以 112 要每次运行建自己的 provider，并在 `run` 返回前 flush。测试在 `run` 返回后读 `InMemorySpanExporter`。是否激活由假 `query` 断言：它被调用时 `opentelemetry.trace.get_current_span()` 有效，trace id 等于 `trace` 事件的 `trace_id`。
   - 禁止联网的替身只拦 `AF_INET` / `AF_INET6` 的 `connect` 和 `bind`。asyncio 的 self-pipe 用 `socketpair`，不能拦。
   - 假 Langfuse（`observability_support.py`）：只监听 `127.0.0.1`。接 `POST /api/public/otel/v1/traces`（可设返回码、延迟、一直不回），`GET /api/public/projects`（112 可能要用它拼 URL），`GET /api/public/v2/observations`（返回夹具）。每个请求的方法、路径、查询串、头、body 都记下来。

7. 每组要覆盖的断言（spec "Testing Decisions"，逐条）：
   - **开关**（守门，加 `TICKET_112` 的 `from_env`）：三个变量缺任一个或为空（参数化 7 种）时 `from_env` 返回 `None`、三个都有时返回设置（`TICKET_112`）。关闭时 `run` 走完（假 `query`），按同样 7 种不全的设置参数化（含公钥、私钥都在只缺 `BASE_URL`；变量放在传给 `run` 的 `env` 里，也放进 shell 环境）：`build_options` 的 env 里没有 `OTEL_` 开头的键，也没有 `CLAUDE_CODE_ENABLE_TELEMETRY`；`tracing.parent_exporter` 和 `trace_relay.TraceRelay` 换成一调用就失败的替身，证明没建；禁止联网的替身证明没有建连接、没有监听；事件文件里没有 `trace` 事件（守门）。
   - **CLI 环境变量**（`TICKET_112`）：对 `build_options(..., trace=CliTrace(...))` 和 `cli_env` 拿到的合并后环境都要断言。shell 里预设 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`、`OTEL_EXPORTER_OTLP_HEADERS=Authorization=...`、`OTEL_LOG_USER_PROMPTS=1`、`OTEL_LOG_RAW_API_BODIES=1` 和三个 `DATA_AGENT_LANGFUSE_*`。打开时：
     - endpoint 指向 `http://127.0.0.1:` 加 `/v1/traces`，协议 `http/protobuf`；
     - spec 列的 telemetry 开关和 exporter 开关都在：`CLAUDE_CODE_ENABLE_TELEMETRY`、`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`、`OTEL_TRACES_EXPORTER=otlp`、metrics 和 logs 为 `none`、`OTEL_TRACES_EXPORT_INTERVAL=1000`；
     - 合并后的环境里没有一个值含公钥、私钥或 `Authorization`；
     - 开发集档三个内容开关为 `1`；留出集档没有为真的 `OTEL_LOG_*`；shell 的 endpoint 被覆盖。
     - `data-agent run --trace-tier structure` 让这次 session 走留出集档，不传时是开发集档（照 `sessions` 先例）。
   - **中转规则**（`TICKET_113`，直接调 `rewrite`）：
     - token 改名为 `gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens`，缓存两项按上面的宽松断言；`anthropic.custom_base_url` 没了。
     - 开发集档：除了改名的 token 键和含密钥、端点的属性，每一层的每个属性原样保留。prompt、SQL、查询结果、外部 MCP 工具的入参和输出都还在；密钥、已知密钥、端点不在输出字节里。
     - 留出集档：resource、scope、span、span event 每一层的属性名都在白名单里（spec 列的名字，加上 `gen_ai.usage.` 和 `llm.token_count.` 前缀，加上夹具里 MCP 服务器名的占位名）；`claude_code.future_field`、`user_prompt`、`user.id`、`terminal.type`、主机信息都没了。
     - span 名、数量、trace / span / 父 span id、起止时间两档都不变。
     - `session.id`、`gen_ai.conversation.id`、`tool_use_id`、`gen_ai.tool.call.id` 换成匿名 id：原值不在输出里；同一个 `AnonIds` 跨 span、跨两次 `rewrite` 一致（工具 span 和执行 span 还能配对）；换一个 `AnonIds` 就不同。
   - **中转转发**（`TICKET_113`，直接驱动 `TraceRelay`）：
     - `start()` 返回 `127.0.0.1` 的地址；只收 `POST /v1/traces`，别的路径和方法不转发。
     - 假 Langfuse 收到的请求：路径 `/api/public/otel/v1/traces`、`Authorization: Basic base64(公钥:私钥)`、`x-langfuse-ingestion-version: 4`，body 能解析且已按档改写。
     - 返回 500、连接被拒（`BASE_URL` 指向关掉的端口）、收到无法解析的请求，这三种情况下 `stop()` 都正常返回，`errors` 里有对应的 `error_type` 和 `status`（连接被拒和无法解析时为 `None`）。
     - 假 Langfuse 每次延迟应答时，连发几次请求后立刻 `stop()`：全部转发完，`forwarded_spans` 等于收到的 span 数，`forwarded_llm_requests` 等于其中 `claude_code.llm_request` 的数量。
     - 假 Langfuse 一直不回时，`stop(drain_timeout_s=1)` 在几秒内返回。
   - **父 span**（`TICKET_112`，`InMemorySpanExporter`，假中转）：
     - 恰好一个名为 `data-agent.run` 的根 span；`langfuse.trace.name` 为 `data-agent.run`；tag 含任务类型、配置名、模型。
     - 属性值里有 `version` 事件的 `skill_version` 和 `git_commit`；`usage_details`、`cost_details` 按上面的规则。
     - 对话两轮（`ConversationTurn(dir/<id>, 1)`、`(..., 2)`）的 `langfuse.session.id` 都是对话 id，两条 trace id 不同。
     - 留出集档：没有 `langfuse.session.id`；任何属性值里都没有事件文件名、`--events-dir` 目录名、对话 id 和题面；有 `trace_tier=structure` 和 usage、cost。
     - `trace` 事件的 `trace_id` 等于父 span 的，`forwarded_*` 等于假中转 `stop()` 返回的数；假中转返回 `errors` 时每条一个 `trace_export_error` 事件，除 `seq`、`type`、`ts` 外只有 `error_type`、`status`，run 正常结束。
   - **评测入口**（`TICKET_112`）：
     - Harbor（三个变量用 `monkeypatch.setenv` 放进宿主环境，见 Context 第 2 项）：不带 `trace` 时传进容器的 env 里没有 `DATA_AGENT_LANGFUSE_*`；带 `trace` 时，dev 题（`dbt-alpha`）的命令带 `--trace-tier full`，holdout 题（`dbt-delta`）和不在清单里的题带 `structure`，三个变量传进去。
     - 同一个 job 里同一题的 3 个 trial **恰好一个**带 Langfuse 变量；另一个 job（另一个配置）里同一题的 trial 也会导出。Harbor 不给适配器 trial 序号，所以断言"恰好一个"，不断言"第一个"。
     - `ablate`、`board` 的 `harbor_run_args`：带 `--trace` 时有 `--ak trace=true`，不带时没有。
     - DAB：`run_query(..., runs=2, trace=True)` 时第 1 次 session 的 env 是开发集档（三个内容开关为 `1`、endpoint 指向 `127.0.0.1`），第 2 次没有 `OTEL_` 开头的键；不带 `trace` 时两次都没有。
   - **`trace-check`**（`TICKET_112`）：对写有 `trace` 事件的事件文件跑 `data-agent trace-check <文件>`。假 Langfuse 收到 `GET /api/public/v2/observations`，带这个 trace id 和 Basic 认证。输出里有每个 observation 的名字、类型、属性名；夹具里的内容字符串、运行时拼出的形如密钥的字符串、属性值、私钥都不在输出里。事件文件里没有 `trace` 事件时，以非零码退出。`run_id` 形式不测（spec 没说按 run id 去哪个目录找）。
   - **网页**（`TICKET_114`，vitest `test.fails`）：有 `trace` 事件时 `traceLink` 返回它的 `trace_id` 和 `url`；没有时返回 `null`（空壳抛出，所以这条也是预期失败）；对话两轮各自的事件给出各自的链接。

8. 只用 uv、pnpm。

## Acceptance

- [ ] spec "Testing Decisions" 每一组的每条行为都有断言（Context 第 7 项逐条对照）
- [ ] 预期失败的用例当前都以预期失败的方式通过，没有被跳过的，也没有 XPASS；守门组当前通过
- [ ] 每个预期失败的用例，失败原因都是功能还没做：`NotImplementedError("ticket 11N")`；argparse 不认 `--trace-tier` / `trace-check` / `--trace`；或者打开状态下的正向断言不成立（缺 `OTEL_*`、容器 env 里没有 Langfuse 变量、`harbor_run_args` 里没有 `trace=true`、`traceLink` 抛出未实现）。不能是夹具解析失败、假 Langfuse 没起来或导入出错。最终回复每组贴一个用 `--runxfail` 跑出的真实失败原因
- [ ] 每组都临时写过参考实现（不提交），证明去掉预期失败标记后能通过；每组至少一个反向检查会失败。例如：留着 `anthropic.custom_base_url`；不按白名单（`claude_code.future_field` 漏出去）；第 2 个 trial 也导出；shell 的 endpoint 没被覆盖；留出集档的父 span 带了运行 id；`trace-check` 打印属性值；`traceLink` 在没有 `trace` 事件时返回链接
- [ ] 夹具里没有密钥、地址、主机名（`uv run python -m evals scrub` 对 `tests/fixtures/observability/` 是 0 处改动）；pre-commit 的密钥与公网 IP 扫描通过
- [ ] Context 第 3 项的契约都写在代码里，并在最终回复里逐项列出，供人审；Context 第 6 项的会话 id 取值建议、下面 Comments 要列的未决项也在最终回复里
- [ ] `claude-agent-sdk` 的版本号和 `tests/test_harbor_agent.py` 没动；`uv export --frozen --no-dev --no-emit-project --format requirements.txt` 的输出和 main 一字不差（正式依赖和 Harbor 容器的安装清单都没变）
- [ ] `make check` 通过

## Evidence required

`make check` 的完整输出；`uv run pytest tests/acceptance/test_observability_*.py -v` 的输出（每个用例的名字和 XFAIL / PASSED）；每组一个 `--runxfail` 的失败原因；`pnpm --dir web test` 里 trace-link 用例的输出；参考实现和反向检查的结果；脱敏命令的输出；`git diff main -- uv.lock` 的包名清单和上面 `uv export --no-dev` 的对比结果；最终回复里的契约清单和未决项。

## Stop conditions

- spec 的某条行为没法在约定的接缝上测，或者要改 Authority 以外的实现代码才能测。
- 要测某条行为，得先定一个 spec 没定、也不属于 Context 第 3 项契约细节的决定（字段名、签名、假服务的写法不算，由本工单定、人审）。
- spec 自相矛盾。
- Langfuse 官方文档里查不到 tag 属性名或 v2 observations 的响应结构。
- 加三个 dev 依赖后 `make check` 里已有的测试失败（例如 `opentelemetry-api` 版本被迫变化，影响了 SDK 的 TRACEPARENT 注入）。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0
- Outcome: accepted

## Comments

- 2026-11-23（写工单时）：写工单的人替 spec 补的决定，等人审：
  - 三个 OpenTelemetry 包在 111 进 `dev` 组，由 112、113 挪进正式依赖。spec Q14 只说是正式依赖，没说哪张工单加。
  - OTLP 夹具手工构造，替代 spec 的"113 先抓一份"（工单 agent 都加载不了模型环境）。真实抓包作为 113 单元测试的补充，由主会话做。
  - `trace_export_error` 由 `run` 按 `RelayReport.errors` 写，不由中转写。spec 写在"本地中转"一节，等于把这件事从 113 移到 112。
  - 模型输出由哪个内容开关带出不断言（spec 写的是"实现时核实"），留给 spec"验收"一节的真实运行。
- 2026-11-23（写工单时）：交给人定、本工单不测的未决项：
  - 导出关闭时，shell 里继承来的 `OTEL_*` 和 `CLAUDE_CODE_ENABLE_TELEMETRY` 是否也要清空：spec 只写了"关闭时不设任何 `OTEL_*`"，测试照字面断言 `options.env` 里没有这些键。
  - jaffle-shop 的题不在 `evals/manifest.json` 里，照字面规则走留出集档；spec 又把它算作可发往 Cloud 的公开演示数据。本工单不测 jaffle-shop。
  - Harbor 下"第 1 个 trial"怎么认：建议适配器在 job 目录里原子地占一个按题名命名的文件（`O_EXCL`），谁先占到谁导出。代价：`harbor job resume` 重跑出错的 trial 时，这题就不会再导出。测试只断言"恰好一个"。
  - 留出集档 resource 上的 `service.name`、`service.version` 不在 spec 的白名单里，测试照字面会删掉它们；要留的话，合并前在 spec 里加上。

- 2026-11-23（工单 agent 最终回复，主会话照抄）：

**Step 3: the real run** (`uv run pytest tests/acceptance/test_observability_*.py -v`, output in `runs/111/acceptance-v-final.txt`): 62 cases.
- **11 PASSED, unmarked:** the 7 "export off" cases, the no-network self-check, and 3 self-checks (the fixture parses, it holds no secret or endpoint, the fake Langfuse answers).
- **51 XFAIL:** 0 XPASS, 0 skipped.
- **Web** (`pnpm exec vitest run …/trace-link.test.ts`): 3 expected fail.

**Why each group fails today** (`--runxfail`, `runs/111/runxfail-final.txt`). None of them fails on fixture parsing, the fake server or an import.

| Group | Failure today |
|---|---|
| `from_env` | `NotImplementedError: ticket 112` (14 cases) |
| CLI variables | the endpoint the CLI gets is `''`: the shell's value is not overridden and there is no relay endpoint |
| `--trace-tier` | argparse `unrecognized arguments` |
| Parent span | no `data-agent.run` span (`AssertionError: []`); the error case gets `assert [] == [('http_error', 500), …]` |
| Rules and forwarding | `NotImplementedError: ticket 113` (15 cases) |
| End to end | no `trace` event |
| Harbor | the container env has no Langfuse variables (`assert {} == {'DATA_AGENT_…'}`) |
| ablate / board | `SystemExit: 2`, because `--trace` is unknown |
| DAB | the content switches are `None` instead of `'1'` |
| `trace-check` | `invalid choice: 'trace-check'` |
| Web | `traceLink` throws "not implemented" |

**Reference implementation.** A throwaway one is on the local branch `ref-111-throwaway`. It was committed with `--no-verify`, is never to be merged or pushed, and you can delete it. With it, `--runxfail` gives 62/62 passed, three runs in a row, and vitest gives 3/3 with `.fails` removed.

**Negative checks.** Each sabotages the reference in one way; each one fails at least one test:
- `anthropic.custom_base_url` is kept.
- `claude_code.future_field` gets through on the held-out tier.
- The held-out tier drops allowlisted attributes.
- Secrets are removed only by attribute name, with no scan of the values.
- The ids are not made anonymous.
- `stop()` does not wait for forwarding to finish.
- The shell's endpoint is not overridden.
- The shell's `OTEL_LOG_RAW_API_BODIES=1` stays on in the held-out tier.
- The held-out parent span carries the run id.
- The span is not active when the session starts.
- The relay is built with the export off. This is the unmarked guard; 7 cases fail.
- Every trial of a task exports, or only one task per job does.
- ablate adds `trace=true` without `--trace`.
- A held-out task gets the dev tier.
- DAB exports every run.
- `trace-check` prints attribute values.
- `traceLink` returns a link when there is no `trace` event.

The first version of the "no drain" check passed even with draining removed, so it proved nothing. I tightened that test.

**Scrub and secret scan.** `evals scrub` on a copy of `tests/fixtures/observability/`: 0 files changed, 0 redactions of any kind. Pre-commit (gitleaks and the rest) passed on every commit. The secret-like value, the known secret and the model endpoint are put into the fixtures at test time, not stored in them.

**Dependencies.** `pyproject.toml` adds `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` and `opentelemetry-proto`, all `==1.45.1`, to the `dev` group. The `uv.lock` diff only adds packages:
- `googleapis-common-protos`
- `opentelemetry-exporter-http-transport`
- `opentelemetry-exporter-otlp-common`
- `opentelemetry-exporter-otlp-proto-common`
- `opentelemetry-exporter-otlp-proto-http`
- `opentelemetry-proto`
- `opentelemetry-sdk`
- `opentelemetry-semantic-conventions`

`opentelemetry-api` is still 1.45.1 and `protobuf` still 6.33.6. The output of `uv export --frozen --no-dev --no-emit-project` is identical to the branch's starting point. `claude-agent-sdk` and `tests/test_harbor_agent.py` are unchanged.

**Langfuse sources** (read 2026-11-23, cited in the test docstrings):
- `langfuse.trace.tags` is `string[]`; the endpoint is `/api/public/otel/v1/traces`, with Basic auth and `x-langfuse-ingestion-version: 4` (langfuse.com/integrations/native/opentelemetry).
- `GET /api/public/v2/observations` returns `{"data": [ObservationV2], "meta"}`, from the fern definitions `observations.yml` and `commons.yml`.
- `GET /api/public/projects` returns `{"data": [{id, name, organization, metadata}]}`, from `projects.yml`.
- I found no official source for the trace URL format in the UI. The tests only check that the URL starts with `BASE_URL` and contains the trace id.

**Review 1** (a read-only Fable subagent in place of `da-review`, which this account can't run): 13 findings.
- Fixed:
  - The observations fixture now nests attributes under `metadata.attributes`, and the fake answers only the `fields` groups asked for.
  - Allowlisted attributes must keep their values on the held-out tier.
  - Tool-span pairing is now required.
  - Secrets are planted inside content strings.
  - The tag test uses a config whose name is not `full`.
  - The refused port is held for the whole test.
  - The hang test asserts that the relay forwarded something.
  - The `parent_exporter` stand-in takes any arguments.
  - The self-check requires the future attribute on every kind of layer.
  - The fixture's host fields are now placeholders.
- The "exactly one trial" check is now per task: two tasks per job.
- `tier_flag` is no longer pinned when tracing is off.
- Not fixed, with reason: the `Authorization` check now covers only `OTEL_*`, `DATA_AGENT_*` and `CLAUDE_CODE_*` variables. Any unrelated variable on a developer or CI machine could contain that word. The public and secret keys are still checked in every variable.

**Review 2** (`eng:code-review main`):
- Fixed:
  - The drain test calls `stop()` as soon as Langfuse has the requests, before it answers.
  - "A new `AnonIds` gives different ids" now covers all four id kinds.
  - Comparing ids replaces comparing bytes, which depended on protobuf serialization order.
  - The second job really uses `run_config=no-skill`.
  - `result_message` is shared instead of copied three times.
  - Names: `ExportStandIns`, `Planted.carried_by`.
  - A comment says why `LANGFUSE_ENV` is spelled out in the tests.
- Not fixed, with reasons:
  - The `langfuse` fixture is still defined in 4 test files. Authority doesn't include a `conftest.py`, and importing fixtures from a module trips ruff F401/F811; each copy is 4 lines.
  - `trace-check` value checks still skip strings shorter than 6 characters. Values like `"data"` match parts of span names.
  - Whether `error_type` should be a `Literal` is left for you to decide, as part of the contract.

Each review led to one fix round; the reference was re-checked after both.

**Contract for you to review** (all as the ticket specified, unless noted):
- `tracing.py`:
  - `TraceTier = Literal["full", "structure"]`, `TRACE_TIERS`, `LANGFUSE_ENV`.
  - `LangfuseSettings(public_key, secret_key, base_url)` with `from_env(env) -> LangfuseSettings | None`.
  - `CliTrace(endpoint, tier)`.
  - `parent_exporter(settings) -> SpanExporter`. OpenTelemetry is imported only under `TYPE_CHECKING`.
- `trace_relay.py`:
  - `ExportError(error_type, status)`.
  - `RelayReport(forwarded_spans, forwarded_llm_requests, errors)`.
  - `AnonIds()`.
  - `rewrite(body, *, tier, ids, secrets) -> bytes`.
  - `TraceRelay(langfuse, tier, secrets=(), drain_timeout_s=10.0)`, with `start() -> str` and `stop() -> RelayReport`.
- Signatures only: `build_options(…, trace: CliTrace | None = None)`, `run(…, trace_tier: TraceTier = "full")`, `DataAgentOptions.trace: bool = False`, `harbor_agent.MANIFEST`, `dab.run_query(…, trace: bool = False)`.
- `web/src/run-view/trace.ts`: `TraceLink = { traceId, url }` and `traceLink(events) -> TraceLink | null`.
- Decided in this ticket:
  - `error_type` takes three values: `http_error`, `connection_error`, `parse_error`.
  - `drain_timeout_s` is a constructor argument. Context item 7 wrote `stop(drain_timeout_s=1)`; I followed item 3.
  - The parent span's attribute is named `trace_tier`.
  - `langfuse.trace.tags` must be a sequence of strings, not one string.
  - The fixture's placeholder attribute names are listed in the docstring of `test_observability_relay.py`.

**Open items for you to decide:**
- **Single-run `langfuse.session.id`:** I recommend the event file's name without `.jsonl`. It is unique per run, and Harbor's `--events-dir` is always `events`. The reference uses it; the tests only require it to be non-empty and different between two runs.
- **Inherited `OTEL_*` with the export off:** whether to clear them too. The tests only check `options.env`.
- **jaffle-shop:** it is not in the manifest, so by the rule it gets the held-out tier, yet it is public demo data. It is not tested.
- **Picking Harbor's "first trial":** I suggest an `O_EXCL` claim file per task in the job directory; the reference does this. The cost is that a `harbor job resume` that reruns a failed trial won't export that task again. The tests check "exactly one".
- **`service.name` / `service.version`:** they are not in the held-out allowlist, so the tests expect them removed. To keep them, add them to the spec before merging.
- **Langfuse's `metadata.resourceAttributes`:** the fixture uses this name as a guess; it is unconfirmed. Real responses should be checked in the acceptance run.

- 2026-11-23（主会话）：PR CI 绿；Codex GitHub app 审查（`@codex review` 触发）完成，无意见。等人审验收测试后合并。
- 2026-11-23（主会话）：人选"先合并，待定项留给后端工单"；PR #120 squash 合入 main。待定项转进后端工单的"待人定"。
