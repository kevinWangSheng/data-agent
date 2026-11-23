# 113 — 本地中转：接收 CLI 的 OTLP、按档改写、转发 Langfuse

Status: ready-for-agent
Blocked by: 111（done）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`src/data_agent/trace_relay.py` 的空壳实现完：`TraceRelay` 在 `127.0.0.1` 的随机端口收 CLI 的 `POST /v1/traces`，用 `rewrite` 按档改写（token 改名、删凭据样的值和模型端点地址；留出集档按白名单放行、id 匿名），带上 Langfuse 的认证头转发；出错不抛出，记进 `RelayReport`；`stop()` 等已收到的请求转发完再关端口。111 合入的 `TICKET_113` 验收测试全部转绿。另外补一份真实抓到、去掉敏感信息的 CLI OTLP 请求作补充夹具。

## Authority

- May edit:
  - `src/data_agent/trace_relay.py`（实现 111 留的空壳；类、函数的签名和 `ExportError`、`RelayReport` 的字段不改）；
  - `pyproject.toml`、`uv.lock`：只把 `opentelemetry-proto` 从 `dev` 组挪进正式依赖（版本仍钉 1.45.1）。`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http` 由 112 挪，本工单不动；不升级或降级 `opentelemetry-api`、`protobuf`；
  - `tests/acceptance/test_observability_relay.py`：**只**删 `TICKET_113` 标记，以及因此没人用的 `TICKET_113 = ...` 定义和 import（AGENTS.md 硬规矩的唯一例外）；断言、夹具、输入一字不改；`TICKET_112` 标记和定义不动；
  - `tests/` 下新增的单元测试（如 `tests/test_trace_relay.py`）；已有的非验收测试里被本工单的改动弄过时的断言（Comments 里逐条列出）；
  - `tests/fixtures/observability/captured/`（新建）：去掉敏感信息的真实抓包夹具，见 Context 第 5 项。
- Must not edit: `src/data_agent/tracing.py`、`agent.py`、`cli.py`、`evals/`（112）；`tests/acceptance/` 里除删 `TICKET_113` 以外的任何内容；`tests/fixtures/observability/` 里 111 的三个夹具（`cli-request.json`、`observations.json`、`projects.json`）；`web/`、`.claude/`、`.github/workflows/`、`CODING_STANDARDS.md`、`AGENTS.md`、`.env*`、其他工单的文件。
- Server access: none
- Spend cap: 工单 agent $0（加载不了模型环境）。抓包由主会话在派工前做完，本工单不跑真实模型。

## Context

1. 行为来源：spec `.scratch/observability/spec.md` 的"本地中转"一节和"Testing Decisions"里"中转规则""中转转发"两条。取证笔记：`docs/research/2026-11-23-otel-spike.md`（CLI span 的真实结构），`docs/research/2026-11-23-langfuse-usage-mapping.md`（§1 Langfuse 认的属性名，含 `gen_ai.usage.*`、`llm.token_count.*`；§3 `llm_request` 的属性），`docs/research/2026-11-23-observability.md` §3（span 层级、`claude_code.tool` 的属性）。上游 nao 的脱敏是进程内的 span 处理器（`nao/apps/backend/src/utils/langfuse-redaction.ts`，路径相对 `~/dev/AI/data-agent-refs/`），只能参考思路；我们多一个本地接收端，内容上按 spec Update 2026-11-23 开发集档不遮。

2. 111 定下的契约（`.scratch/observability/issues/111-observability-acceptance-tests.md` Context 第 3 项和最后的 Comments；空壳在 `src/data_agent/trace_relay.py`），照它实现，不改签名：
   - `ExportError(error_type, status)`：`error_type` 只有三种值，`http_error`（Langfuse 返回错误状态码）、`connection_error`（没有响应：连接被拒、断开、超时；`status` 为 `None`）、`parse_error`（CLI 的请求解析不了，什么都没发；`status` 为 `None`）。
   - `RelayReport(forwarded_spans, forwarded_llm_requests, errors)`：`forwarded_llm_requests` 是转发出去的 `claude_code.llm_request` span 数。
   - `AnonIds()`：一次运行一个；同一个对象里同一个原 id 总是换成同一个匿名 id，换一个对象就不同。
   - `rewrite(body, *, tier, ids, secrets) -> bytes`：输入、输出都是序列化后的 `ExportTraceServiceRequest`；`secrets` 是 `events.known_secrets`（`src/data_agent/events.py:30`）给的已知密钥值，不能出现在输出里。密钥和端点删属性还是遮值都行（测试只要求它们不在转发出去的字节里、相邻的内容还在）。除了按名字删，还要扫属性值（111 的反向检查之一：只按属性名删会失败）。
   - `TraceRelay(langfuse, tier, secrets=(), drain_timeout_s=10.0)`：`start()` 返回 `http://127.0.0.1:<端口>/v1/traces`，只收 `POST /v1/traces`，别的路径和方法不转发；转发到 `<base_url>/api/public/otel/v1/traces`，带 `Authorization: Basic base64(公钥:私钥)` 和 `x-langfuse-ingestion-version: 4`；`stop()` 等已收到的请求转发完（不超过 `drain_timeout_s`）再关端口，Langfuse 一直不回时也要在时限内返回。
   - **中转不写事件文件**：它在自己的线程里收请求，`EventLog.emit`（`events.py:92-97`）没有加锁。错误只进 `RelayReport.errors`，由 `run`（112）写成 `trace_export_error` 事件。
   - 白名单（spec"本地中转"一节）：span 名、类型、层级、起止时间、`duration_ms`、`ttft_ms`、`first_content_ms`、token 数、`stop_reason`、`success`、`attempt`、`effort`、工具名（`tool_name`、`gen_ai.tool.name`、MCP 服务器名）、模型名（`model`、`gen_ai.request.model`）、`span.type`，加上 `gen_ai.usage.`、`llm.token_count.` 前缀。resource、scope、span、span event 每一层都只放行白名单。夹具里占位的属性名（MCP 服务器名等）列在 `tests/acceptance/test_observability_relay.py` 的文档字符串里。白名单写成一个常量，方便以后按真实抓包补。
   - 匿名化：`session.id`、`gen_ai.conversation.id`、`tool_use_id`、`gen_ai.tool.call.id`。
   - span 名、数量、trace / span / 父 span id、起止时间两档都不变。

3. 要转绿的验收测试（只删 `TICKET_113`）：`tests/acceptance/test_observability_relay.py` 里 token 改名、开发集档原样保留、留出集档白名单、两档保留树结构、id 匿名、转发头、只收 `POST /v1/traces`、三种失败都记进报告、`stop()` 转发完、Langfuse 不回时 `stop()` 按时返回。不标的自检（夹具能解析、夹具里没有密钥和端点、假 Langfuse 能应答）继续通过。
   - 同时挂 `TICKET_112` 和 `TICKET_113` 的两条端到端用例（`test_the_trace_event_counts_what_langfuse_received`、`test_a_langfuse_error_leaves_the_run_to_finish_with_an_error_event`）：本工单删掉它们的 `TICKET_113` 和 `TICKET_113 = ...` 的定义（没有别的用处时）。112 没合入时，它们靠 `TICKET_112` 仍然是 XFAIL；两张工单都合入后才转绿。**后合并的一方** rebase 后要跑这两条，贴出它们 PASSED。两张工单在这两个用例的装饰器上改的是相邻的行，rebase 时按"两个标记都删掉"解决。

4. 真实运行（AGENTS.md 工作流程第 3 步），工单 agent 自己做、不花钱：照工单 104 的做法（`.scratch/dab-gap/issues/104-effort-option.md` Comments：`DATA_AGENT_API_KEY=sk-local-fake DATA_AGENT_BASE_URL=http://127.0.0.1:<端口>` 指向本地假模型服务器，用 `tests/fixtures/shop.sql` 建的 DuckDB）跑一次 `data-agent run`，在 shell 里设好 spec"传给 CLI 的环境变量"列的变量（112 没合入前 `build_options` 不覆盖它们，CLI 会继承），endpoint 指向一个真的 `TraceRelay`，`TraceRelay` 再转发给本地假 Langfuse（可以复用 `tests/acceptance/observability_support.py` 的假服务器）。记下 CLI 版本（`--version`）、收到和转发的 span 名与数量、`RelayReport`。这次的请求内容是假模型给的，不作夹具。脚本放 worktree 的 `runs/113/`，不提交。

5. 真实抓包的补充夹具。111 的夹具是手工构造的，内容属性名是占位的。主会话在派工前已做好真实抓包和去敏（CLI 2.1.330，即 0.2.187 自带的版本；一次开发集档内容开关全开、一次全关的查询运行，仓库夹具 `tests/fixtures/shop.sql`），去敏后的 protobuf 标准 JSON 放在主 checkout 的 `/Users/shenghuikevin/dev/AI/data-agent/runs/otel-capture/scrubbed/`（不进 git；目录里的 `README.md` 记了 CLI 版本、两次运行的开关和替换掉的字段）。本工单 agent 不读原始抓包。
   - 本工单：把 `scrubbed/` 的文件复制进 `tests/fixtures/observability/captured/`（文件名可以改），补一组非验收的单元测试：两档改写后的结果符合 Context 第 2 项的规则（token 改名、留出集档只有白名单属性、id 匿名、开发集档内容还在）。同时把真实抓包里出现、但白名单和 111 的占位名不一致的属性名（例如 MCP 服务器名、内容属性）列进最终回复。真实抓包不替换 111 的夹具，不进 `tests/acceptance/`。
   - 复制前再检查一遍：发现密钥样的值、模型端点、IP、主机名、邮箱，停下交回，不自己改。
   - 夹具里不能有密钥、模型端点、服务器 IP、主机名：`uv run python -m evals scrub` 对 `tests/fixtures/observability/captured/` 是 0 处改动（对一份副本跑，照 111 的做法），pre-commit 的密钥与公网 IP 扫描通过。

6. 依赖：只挪 `opentelemetry-proto`。112 也改 `pyproject.toml`、`uv.lock`，后合并的一方 rebase 后用 `uv lock` 重新生成，不手工合 `uv.lock`。`opentelemetry-proto` 进正式依赖会改 `uv export --frozen --no-dev --no-emit-project --format requirements.txt` 的输出（Harbor 容器的离线安装清单），Comments 里贴新旧输出的包名差异；离线安装用时由 112 核对。

7. 并行的工单：112 和本工单并行，112 只调 `TraceRelay` 的构造、`start()`、`stop()`。另有 116（外部 MCP）、119（图表）改 `agent.py` 和 `cli.py`，本工单不碰这两个文件。

8. **待人定**（人 2026-11-23 说"以后再定"）。规则：测试已经锁定的照测试做；没锁定的，照 spec 原文或 111 的建议做一个，做成容易改的样子（集中在一个常量里），在最终回复的"待人定"标题下逐条列出（选了什么、依据、改起来要动哪里），由主会话在合并前交给人定。**中途不问。**
   1. 留出集档 resource 上的 `service.name`、`service.version`：不在 spec 的白名单里，测试照字面要求删掉。照测试删。要留的话得先改 spec 和验收测试，由人定。
   2. 缓存 token 改成的键名：测试只锁定值出现在某个 `gen_ai.usage.` 或 `llm.token_count.` 开头的键下、原键没了。照 Langfuse 官方文档（usage-mapping 笔记 §1 给的出处）选确切的名字，注明出处和访问日期；验收的真实运行里在 Langfuse 的 usage 列核对缓存两项是否被认出。
   3. `ExportError.error_type` 要不要改成 `Literal`：111 留给人定。改了会动 111 的契约，先保持 `str`，三个取值写成模块常量。
   4. （已由主会话定：补充夹具在 CLI 2.1.330 上抓。）

9. 审查（工作流程第 5 步）：`da-review` 在这个账号上跑不了。第一道审查换成一个只读的 `model: fable` 子 agent（给它 worktree 路径、本工单、spec、`CODING_STANDARDS.md`，只报问题、带 `file:line` 和严重程度），结果和 Codex 的一样处理（修，或写明不修的理由）；然后开新上下文跑 `eng:code-review main`。

10. 只用 uv。

## Acceptance

- [ ] `tests/acceptance/test_observability_relay.py` 里所有 `TICKET_113` 标记都已删掉，只删了标记和因此没用的定义、import；只挂 `TICKET_113` 的用例都 PASSED，同时挂 `TICKET_112` 的两条端到端用例仍是 XFAIL；自检用例仍通过；`git diff main -- tests/acceptance/` 只有删掉的标记行
- [ ] 白名单、匿名化的 id 名、三种 `error_type` 都是一处定义的常量
- [ ] `opentelemetry-proto` 在正式依赖里，另外两个 OpenTelemetry 包没动；`opentelemetry-api`、`protobuf` 版本不变
- [ ] Context 第 4 项的真实运行做了：真 CLI → 真中转 → 假 Langfuse，记下 CLI 版本、span 数和 `RelayReport`
- [ ] `tests/fixtures/observability/captured/` 里有主会话给的去敏真实抓包，记了 CLI 版本；补充单元测试通过；`evals scrub` 0 处改动；pre-commit 通过；和白名单不一致的属性名列在最终回复里
- [ ] 最终回复里有"待人定"清单（Context 第 8 项）
- [ ] `make check` 通过

## Evidence required

`make check` 的完整输出；`uv run pytest tests/acceptance/test_observability_relay.py -v`（每个用例的 PASSED / XFAIL）；`git diff main -- tests/acceptance/`；`git diff main -- pyproject.toml` 和新旧 `uv export --no-dev` 的包名差异；Context 第 4 项真实运行的命令和输出（span 名、数量、`RelayReport`，不贴内容）；补充夹具的 `evals scrub` 输出；"待人定"清单。

## Stop conditions

- 要改 111 的验收测试（除了删 `TICKET_113`）才能转绿，或者觉得某条验收测试错了。
- 要改 `trace_relay.py` 的契约（签名、`ExportError`、`RelayReport` 的字段）。
- 挪依赖迫使 `opentelemetry-api` 或 `protobuf` 换版本。
- 真实抓包的结构和 111 夹具差得太多，按白名单的规则在真实请求上会删掉 spec 要保留的字段，或放出 spec 不让放的字段：停下，列出差异交回，不自己扩白名单。
- 去敏后 `evals scrub` 仍有改动，或者 pre-commit 的密钥与公网 IP 扫描不过，而且去敏脚本改不好。
- spec 自相矛盾，或缺一个"待人定"以外、测试和 spec 都没给方向的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
