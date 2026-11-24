# 可观测性：运行 trace 导出到 Langfuse

日期：2026-11-23。决策来自当天的 grilling（Q1–Q15，人逐条确认）。取证与试验笔记：

- `docs/research/2026-11-23-observability.md`：Langfuse、CLI OTel、上游做法的文档事实
- `docs/research/2026-11-23-otel-spike.md`：A（CLI 自带 OTel）对比 B（openinference）的真实运行
- `docs/research/2026-11-23-langfuse-usage-mapping.md`：Langfuse 认哪些属性、免费档额度、TRACEPARENT 注入
- `docs/research/2026-11-23-sdk-upgrade-0.2.187.md`：SDK 0.2.168 → 0.2.187 对 8 处已实测行为的影响
- `docs/research/2026-11-23-trace-content-defaults.md`：trace 内容业界默认记不记（Update 2026-11-23 的依据）

**Update 2026-11-23（MCP grilling 的 Q8，人定）**：原 Q4 的"数据行脱敏、只保留 SQL"改为开发集档**完整记录**：prompt、模型输出、所有工具（含外部 MCP 工具）的入参和输出、查询结果都导出，只去掉凭据和模型端点地址。依据：Langfuse 自己的 SDK 和 OpenInference 默认全记；OTel GenAI 规范默认不记是针对不知道数据去向的埋点库，我们是用户显式配置自己的 Langfuse 项目。和 nao 的差别：nao 遮掉 3 个读数据工具的 SQL 和结果，我们不遮。留出集档不变。下文已按此改写。

## Problem Statement

agent 的过程现在只能在我们自己的事件文件和网页回放里看。plan §2、§5 定过"另接 Langfuse 作为可选的 trace 看板"，一直没做（ADR-0005 挪到二期）。缺的是一个通用的 LLM 可观测性工具里才有的东西：每一次模型请求一个 span（带 token、花费、首字延迟），主 agent、验证子 agent、工具调用嵌套成一棵树，能按对话、配置、模型筛选。

## Solution

- **主体用 Claude Code CLI 自带的 OpenTelemetry**（traces beta）。试验里它是唯一给出"每次请求一个 span"的方案，而且默认不发内容。
- **外面挂一个我们自己的父 span `data-agent.run`**：带运行 id、对话 id、任务类型、配置、模型，和 `result` 事件里的总花费；总 token 放在 metadata（Update 2026-11-23：不进 usage，免得和每次请求的 GENERATION 重复计数）。SDK 在有活动 span 时自动把 `TRACEPARENT` 传给 CLI，CLI 的 span 挂到它下面。
- **CLI 的 span 先进本地中转**：data-agent 进程在 `127.0.0.1` 上收 CLI 的 OTLP 请求，按档过滤、改名、匿名化，再带上密钥转发给 Langfuse。CLI 拿不到 Langfuse 密钥。
- **默认关**：三个 `DATA_AGENT_LANGFUSE_*` 变量都设了才导出；评测还要显式 `--trace`。
- **两档**：开发集档（完整记录，只去掉凭据、模型端点地址，以及能认出机器的 `user.id`、`host.*`、`os.*`；Update 2026-11-24，工单 121）；留出集档（只放行白名单里的结构字段，id 匿名）。
- SDK 升到 0.2.187（CLI 2.1.330），并入本 feature。

## Implementation Decisions

### 开关与配置（Q4、Q2）

- 变量：`DATA_AGENT_LANGFUSE_PUBLIC_KEY`、`DATA_AGENT_LANGFUSE_SECRET_KEY`、`DATA_AGENT_LANGFUSE_BASE_URL`。照 `.env.example` 的约定用 `DATA_AGENT_` 前缀：裸的 `LANGFUSE_*` 会被本机其他用 Langfuse 的工具读到。三个变量名（不带值）加进 `.env.example`。
- 三个都非空才打开；缺任何一个，包括缺地址，都保持关闭，不默认发到 `cloud.langfuse.com`（照 nao `apps/backend/src/instrumentation.ts:8-22`）。
- 关闭时：不建 exporter，不开中转端口，不设任何 `OTEL_*` 变量，没有任何网络请求。
- 代码只认一个地址：用 Langfuse Cloud（开发和演示，US 区）还是自托管，只差 `BASE_URL`。README 写明这一点，附官方 docker compose 链接。只用公开演示数据（jaffle-shop、仓库夹具、公开榜单）发往 Cloud。

### 覆盖范围（Q3、Q13）

| 入口 | 何时导出 | 档 |
|---|---|---|
| `data-agent run`（CLI） | 变量齐了 | 开发集档 |
| 网页的表单运行与对话（`web/launcher.py`、`web/conversations.py`） | 变量齐了 | 开发集档 |
| `evals ablate`、`evals board`、`evals dab`、jaffle-shop | 变量齐了且带 `--trace`（默认不带） | 按题目所属划分自动选 |

- 选档按 `evals/manifest.json`：题目在 dev 列表里走开发集档；在 holdout 列表里，或者判断不了，一律走留出集档（和 `da-remote pull` 的保守口径一致）。DAB 没有开发集、留出集之分，逐题轨迹本来就可看（ADR-0005），走开发集档。
- 评测里每题每个配置只导出第 1 个 trial，其余 trial 不设 trace 变量。理由：Langfuse Cloud 免费档每月 50k 个 unit（trace、observation、评分各算一个），一个 6 轮的查询运行约 20 个 observation，dbt 任务长得多。
- `data-agent run` 新增 `--trace-tier {full,structure}`，评测入口按上面的规则传；不传时是 `full`。档由调用方定，`run` 自己不读 manifest。
- Harbor 容器：`evals/harbor_agent.py` 现在用 `MODEL_ENV` 把模型变量传进容器（`harbor_agent.py:89`、`:299-302`）。三个 Langfuse 变量只在带 `--trace`、且这个 trial 要导出时，用同样的方式传进去。服务器 env 文件的三个变量由人加。
- 不把历史评测轨迹事后导入 Langfuse。

### 父 span 与 trace 组织（Q6、Q9、Q10）

- 每次 `data-agent run`（对话的每一轮也是一次 run）一条 trace，根 span 名 `data-agent.run`。
- 属性：
  - `langfuse.session.id`：对话 id；单次运行用运行 id
  - `langfuse.trace.name`：`data-agent.run`
  - tag：任务类型（`query` / `dbt`）、配置名、模型
  - 运行 id、`skill_version`、代码 commit（取自 `version` 事件）
  - 运行结束时写 `langfuse.observation.cost_details` 和 metadata 里的总 token（Update 2026-11-23：不再写 `usage_details`，工单 121），数值取自 `result` 事件（`cost_usd` 口径，`agent.py:1818` `result_fields`），和报告同一个口径
- 父 span 在 SDK 起 CLI 之前就要激活，才能让 SDK 注入 `TRACEPARENT`（`claude_agent_sdk/_internal/transport/subprocess_cli.py:786-806`，条件是有活动 span）。
- 事件文件新增一个 `trace` 事件：trace id，以及 Langfuse 里打开这条 trace 的 URL，供网页链接用。URL 的格式实现时核实（是否需要 project id；需要的话启动时用 `GET /api/public/projects` 取一次）。
- 每次请求的 token 留在 span 属性里，由中转改名后进 Langfuse 的 usage 列（见下）。不加独立的 OTel Collector。

### 传给 CLI 的环境变量（Q6、Q4）

导出打开时，`build_options` 的 env（`agent.py:1633`）加上：

- `CLAUDE_CODE_ENABLE_TELEMETRY=1`、`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`
- `OTEL_TRACES_EXPORTER=otlp`，`OTEL_METRICS_EXPORTER=none`，`OTEL_LOGS_EXPORTER=none`
- `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf`，`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:<中转端口>/v1/traces`
- `OTEL_TRACES_EXPORT_INTERVAL=1000`（文档建议短任务调到 1000，退出时 flush 的超时很短）
- 开发集档：`OTEL_LOG_USER_PROMPTS=1`、`OTEL_LOG_TOOL_DETAILS=1`、`OTEL_LOG_TOOL_CONTENT=1`；模型输出由哪个开关带出（`OTEL_LOG_RAW_API_BODIES` 或其他）实现时核实，要能在 Langfuse 里看到模型回答。超长内容按 CLI 默认在 60 KB 截断（`CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH`）
- 留出集档：四个内容开关都不设

传给 CLI 的 env 里不能有 Langfuse 的公钥和私钥，也不能有 `Authorization` 头。shell 里继承来的 `OTEL_*` 变量一律被覆盖，不能让外部设置改变导出目的地（照工单 104 对 `CLAUDE_CODE_EFFORT_LEVEL` 的处理）。

### 本地中转（Q15）

- 每个 `data-agent run` 进程里一个，导出打开时启动：监听 `127.0.0.1` 的随机端口，只收 `POST /v1/traces`（OTLP http/protobuf）。
- 收到的请求用 `opentelemetry-proto` 解析，按下面的规则改写，重新编码，带上 `Authorization: Basic <公钥:私钥>` 和 `x-langfuse-ingestion-version: 4` 转发到 `<BASE_URL>/api/public/otel/v1/traces`。
- 规则（两档共用）：
  - token 改名：`input_tokens` → `gen_ai.usage.input_tokens`，`output_tokens` → `gen_ai.usage.output_tokens`，`cache_read_tokens`、`cache_creation_tokens` 映射到 Langfuse 认的缓存字段（Langfuse 认 `gen_ai.usage.*`、`llm.token_count.*`，见 usage-mapping 笔记 §1；缓存字段的确切名字实现时核实）。
  - 删掉 `anthropic.custom_base_url`（模型端点地址）和任何凭据样的值。
- 开发集档：不再删内容，只做两档共用的规则（改名、删凭据样的值和模型端点地址），另外删掉 `user.id` 和各层的 `host.*`、`os.*`（Update 2026-11-24，工单 121）。
- 留出集档：
  - 属性按**白名单**放行：span 名、类型、层级、起止时间、`duration_ms`、`ttft_ms`、`first_content_ms`、token 数、`stop_reason`、`success`、`attempt`、`effort`、工具名（`tool_name`、`gen_ai.tool.name`、MCP 服务器名）、模型名（`model`、`gen_ai.request.model`）、`span.type`。其余属性一律删掉，包括 `user_prompt`、`user_prompt_length`、工具参数、`llm_request.context`、`user.id`、`terminal.type`、主机和系统信息。CLI 以后新增的属性默认发不出去。
  - `session.id`、`gen_ai.conversation.id`、`tool_use_id`、`gen_ai.tool.call.id` 换成这次运行随机生成的匿名 id（同一次运行内保持一致，能看出配对，看不出是哪一题）。
  - 父 span 不带运行 id、题目 id、对话 id，只带配置名、模型、任务类型、`trace_tier=structure` 和总 token（metadata）、cost。
- 出错不影响运行：解析失败、转发失败、Langfuse 返回错误，都只丢掉这批 span，写一条 `trace_export_error` 事件（只有错误类型和 HTTP 状态码），agent 照常跑完。
- 收尾：CLI 退出后，等中转把已收到的请求转发完（有上限，如 10 秒），再关端口。转发出去的 span 数写进 `trace` 事件，用来核对"请求数和 span 数对得上"。

### 读回 trace：`data-agent trace-check`（Q8）

- `data-agent trace-check <run_id 或 events 文件>`：读 `DATA_AGENT_LANGFUSE_*`，按 `trace` 事件里的 trace id 调 `GET /api/public/v2/observations`（新建的 Cloud 项目拒绝旧的 `/api/public/traces`，HTTP 410），输出这条 trace 的结构：span 名、类型、层级、数量、哪些有 usage 和 cost、属性名。不输出属性值、内容和密钥。
- 用于实现和验收时 agent 自己核对，不用人手动跑。工单里给它加一条精确的权限规则（只放行这一条命令），由人审。

### SDK 升级（Q7）

- `claude-agent-sdk` 0.2.168 → 0.2.187，自带 CLI 2.1.301 → 2.1.330。现有 2,590 个测试在新版下只有 `tests/test_harbor_agent.py:626`（断言锁定版本号）失败（spike worktree 实测，2026-11-23）。
- 要改的地方：版本号和这个测试；`agent.py:1238-1240` 注释（不认识的模型默认窗口 2.1.323 起是 1M；`AUTO_COMPACT_WINDOW` 2.1.304 起接受 `400k`），两个设置本身不变；`agent.py:432` 注释和 `tests/fixtures/subagent_transcript.jsonl` 的说明（2.1.307 起中间行也带最终 usage，取最后一行的逻辑不变）。
- 要实测的 3 项（sdk-upgrade 笔记的未确认项）：不设 effort 时 CLI 发什么（照工单 104 用本地假端点抓 `output_config.effort`）；2.1.302 起 fork 子 agent 默认打开，验证子 agent 是否照常在前台跑完（需要时设 `CLAUDE_CODE_FORK_SUBAGENT=0`）；工单 92 的插话探针重跑一次（`ResultMessage` 新增 `user_message_uuid(s)`）。
- Harbor 离线安装包：内置 CLI 约 250 MB → 364 MB，确认 `evals/harbor_agent.py` 的安装仍在时限内。
- 结果记进 ADR-0008 的 Update。

### 新增依赖（Q14）

`opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`（父 span），`opentelemetry-proto`（中转）。正式依赖，不做可选安装。都是 OpenTelemetry 官方包，Apache-2.0。不加 openinference。

### 网页（Q11）

运行页和对话页：这次运行的事件里有 `trace` 事件时，显示"在 Langfuse 打开"的链接；没有时不显示。只改 `web/`，单独一张前端工单（不跑 Codex，见 AGENTS.md）。

## 工单

| 工单 | 内容 | 依赖 |
|---|---|---|
| 111 | 验收测试：在下面 Testing Decisions 列的接缝上写测试，标 `xfail`；人审后合入 main | 无 |
| 112 | 后端：开关与配置、CLI 环境变量、父 span、`trace` 事件、`--trace-tier`、评测入口的 `--trace` 与选档与抽样、Harbor 传变量、`trace-check`、SDK 升级与 3 项实测、`.env.example`、README、`docs/setup.md` | 111 |
| 113 | 本地中转：接收、解析、两档规则、转发、出错处理、收尾计数 | 111（和 112 并行；112 只调用它的启动、停止接口） |
| 114 | 前端：运行页和对话页的 Langfuse 链接 | 112 |
| 121 | 开发集档 trace 补上工具入参和模型文字（从事件里取）；父 span 只留 cost；开发集档删机器属性 | 112、113 |
| 122 | `result` 加 `ended_ts`，运行页墙钟、回放、历史页时长不算 trace 导出的等待（Update 2026-11-24：扩到后端） | 121 |
| 134 | `evals dab --trace` 也接上 121 的入参和文字 | 121 |

112、113 都合入后，主会话做下面的"验收"。

## Testing Decisions

单元测试不调模型、不联网：Langfuse 用本地假 HTTP 服务器替身，CLI 发来的 span 用试验里抓到的真实 OTLP 请求做夹具（113 先抓一份，去掉内容后提交）。

- **开关**：三个变量缺任何一个都是关闭；关闭时 `build_options` 的 env 里没有 `OTEL_*`、`CLAUDE_CODE_ENABLE_TELEMETRY`，不开端口，不建 exporter；用禁止建连接的替身证明没有网络请求。
- **CLI 环境变量**：打开时 endpoint 指向 `127.0.0.1`；env 里没有公钥、私钥、`Authorization`；开发集档打开内容开关（prompt、工具入参、工具输出、模型输出）；留出集档一个内容开关都没有；shell 里预设的 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 被覆盖。
- **中转规则**（输入夹具，断言输出）：
  - token 属性被改名为 Langfuse 认的名字；`anthropic.custom_base_url` 被删
  - 开发集档：prompt、SQL、工具输出、外部 MCP 工具的入参和输出都保留；夹具里放一个形如密钥的值和模型端点地址，它们被删
  - 留出集档：输出里每个属性名都在白名单里；夹具里加一个"CLI 将来新增的属性"，它被删掉；`user_prompt` 被删；会话 id、对话 id、`tool_use_id` 被换成匿名 id，同一次运行内一致，两次运行不同
- **中转转发**：假 Langfuse 收到的请求带正确的 Basic 认证和 `x-langfuse-ingestion-version: 4`；假 Langfuse 返回 500、连接被拒、收到无法解析的请求时，`run` 照常结束，事件里有 `trace_export_error`；CLI 退出后已收到的请求都转发完，`trace` 事件里的计数等于转发数。
- **父 span**：用内存 exporter，断言名字、session id、tag、metadata 里的总 token、`cost_details` 取自 `result` 事件，且不带 `usage_details`（Update 2026-11-23，人授权工单 121 改 `assert_usage_and_cost`）；留出集档的父 span 没有运行 id、题目 id、对话 id。
- **评测入口**：不带 `--trace` 时，`harbor_agent` 拼出的 env 里没有 Langfuse 变量；带 `--trace` 时，dev 题走 `--trace-tier full`，holdout 题和不在 manifest 里的题走 `structure`；同一题的第 2、3 个 trial 不带 Langfuse 变量；DAB 走 `full`。
- **`trace-check`**：对假 Langfuse 的 v2 observations 响应，输出里只有结构；响应里放一个内容字符串和一个形如密钥的字符串，断言它们都不出现在输出里。
- **网页**（114）：有 `trace` 事件显示链接，没有不显示。

## 验收

新能力，不套 ADR-0008 的"目标题必须涨"，只要求不退步，且下面几条都在真实运行里看到：

- 一次真实的 dbt 运行（`full` 配置，会调用验证子 agent）：用 `trace-check` 看到父 span → CLI 的 interaction → 每次请求的 GENERATION（usage 有值）和工具 span，**验证子 agent 的请求挂在这棵树里**；`trace` 事件里转发的 `llm_request` 数等于这次运行的模型请求数（试验里 6 轮只收到 4 个，要查清是计法不同还是丢失）。
- 一次查询运行在开发集档、一次在留出集档（用 `--trace-tier structure` 手动指定，用仓库夹具，不用真的留出题）：`trace-check` 显示开发集档有 prompt、SQL、查询结果和模型回答，没有凭据；留出集档只有白名单字段、id 是匿名的。
- 网页对话里问两轮：Langfuse 里同一个 session 下有两条 trace，运行页的链接能打开对应 trace。
- 导出关闭时跑一次，事件文件里没有 `trace` 事件，没有任何对 Langfuse 的请求。
- SDK 升级回归（Q7）：`make check`；jaffle-shop 7 题不低于 7/7（`71a5bcc` 的结果）；开发集 3 题初筛（`evals board`，服务器，小额运行）在波动范围内；3 项实测有结论。

## Out of Scope

- 自托管 Langfuse 的部署（本机磁盘和 Docker 内存目前不够；README 给链接，等资源够了再验证一次）。
- OTel metrics 和 logs 导出（只导出 traces）。
- openinference、独立的 OTel Collector。
- 把历史评测轨迹导入 Langfuse；Langfuse 的评分、数据集、提示词管理功能。
- 网页里直接嵌 Langfuse 的界面（只给链接）。
- 用 Langfuse 的数据代替我们自己的事件文件和报告（报告仍从事件文件算）。

## Further Notes

- 上游：nao 的开关和脱敏（`nao/apps/backend/src/instrumentation.ts`、`apps/backend/src/utils/langfuse-redaction.ts`）是进程内的 span 处理器；我们的 span 由 CLI 子进程发出，所以多了一个本地接收端，开关的做法照 nao；内容上 nao 遮掉读数据工具的 SQL 和结果，我们按 Update 2026-11-23 不遮。SignalPilot、dash 没有 LLM trace 接入。
- 已知限制：traces 在 CLI 里还是 beta，属性名可能随 CLI 版本变；留出集档靠白名单，CLI 改名后被删的字段要人看到才会补回白名单，不会泄漏。
- Langfuse Cloud 免费档超额后是拒收还是限流，官方没写（未确认）；超额只影响导出，不影响运行。
- 试验 worktree `../data-agent-spike-otel`（分支 `spike/otel`）留到 112、113 合入后再删。

## 验收结果 2026-11-23（主会话，main 6fadb61，固定 worktree，Langfuse Cloud）

命令：`runs/batch2/obs-acceptance.sh`、`runs/batch2/obs-web.sh`（不进 git）。`trace-check` 只取结构（`fields=core,basic,metadata,usage`，不取 io，只打印属性名和 usage/cost 有没有值），所以下面"看不到值"的几项要在 Langfuse 界面里看一次。

- dbt 运行（jaffle b2，`full` 配置，`--trace` 留出集档）：通过，$0.040。树是 `data-agent.run` → `claude_code.interaction` → 每次请求一个 GENERATION（usage 有 `input / input_cache_read / cache_creation_tokens / output / total`）加工具 span；两个验证子 agent 的请求挂在 `TOOL Agent` 下（8 个、7 个）。`trace` 事件 `forwarded_llm_requests: 30` = 主线程 15 + 验证 8 + 7，没有丢；spec 里"6 轮只收到 4 个"是计法不同（`num_turns` 按轮算，不按请求算）。查询运行 7=7、5=5。
- 开发集档查询：prompt 在（interaction 的 `user_prompt`）；SQL **没有**（CLI 不导出工具入参）；查询结果在 `tool.output` span 事件、答案在父 span 的 output，都在 io 里，`trace-check` 看不到值（未确认）。日志和事件里没有任何凭据。
- 留出集档查询：属性名只有白名单里的、匿名 id 名和 `gen_ai.usage.*`；没有 user_prompt、host.*、user.id。id 值是否匿名，`trace-check` 看不到（未确认）。
- 导出关闭：事件文件里没有 `trace`、没有 `trace_export_error`。
- 网页对话两轮（`data-agent web`，网络关）：两轮各有一条 `trace` 事件（trace id 不同），页面上每轮的 "Open in Langfuse ↗" 链到各自的 trace；两轮的 `langfuse.session.id` 都取对话 id（`agent.py` `session=turn.conversation`），Langfuse 里是否显示为同一 session 未在界面确认。
- 四次运行和网页两轮都没有 `error` / `trace_export_error` 事件。
- SDK 升级回归：见 ADR-0008 Update 2026-11-23（jaffle 7/7、开发集初筛 5/9 与基线持平、3 项实测有结论）。
- 其他发现：cost 没有重复（只有父 span 有 cost，GENERATION 都没有，DeepSeek 模型在 Langfuse 里大概没有价格，推断）；token 合计大概率重复（父 span 带全程 usage，键名 `cache_read` 和 GENERATION 的 `input_cache_read` 不同）；资源属性在 Langfuse 里是 `metadata.resourceAttributes.*`，`trace-check` 能显示；开发集档带 `user.id`、`terminal.type` 和 host/OS 资源属性（不是凭据，但能认出机器）。
