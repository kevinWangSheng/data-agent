# 112 — 可观测性后端：开关、CLI 导出变量、父 span、`trace` 事件、评测入口、`trace-check`、SDK 升级

Status: in-review
Blocked by: 111（done）
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-23
Branch: worktree-112-observability-backend

## Goal

三个 `DATA_AGENT_LANGFUSE_*` 都设了时，每次 `data-agent run` 导出一条 trace：根 span `data-agent.run` 由我们发，CLI 的 span 经本地中转（113）挂到它下面；事件文件里有 `trace` 事件，`data-agent trace-check` 能读回这条 trace 的结构；评测入口带 `--trace` 时按题目所属划分选档、每题每个配置只导出一个 trial。缺任何一个变量时什么都不发生。`claude-agent-sdk` 升到 0.2.187，回归和 3 项实测有结论。111 合入的 `TICKET_112` 验收测试全部转绿。

## Authority

- May edit:
  - `src/data_agent/tracing.py`（实现 111 留的空壳；类型、常量和签名不改）；可以新建 `src/data_agent/trace_check.py` 放 `trace-check` 的逻辑；
  - `src/data_agent/agent.py`：`build_options`（`agent.py:1511`，env 在 `:1623-1635` 拼起来）加导出变量；`run`（`agent.py:2062`）读 `LangfuseSettings`、起停中转、建父 span、写 `trace` 和 `trace_export_error` 事件；SDK 升级要改的注释（`agent.py:437`、`:1239-1243` 附近，见 Context 第 6 项）；
  - `src/data_agent/cli.py`：`run` 加 `--trace-tier {full,structure}`（照 `--effort`，`cli.py:297`）；新增子命令 `trace-check`；
  - `src/data_agent/web/launcher.py`、`src/data_agent/web/conversations.py`：只在网页运行和对话拿不到 Langfuse 变量、或要显式传 `--trace-tier full` 时改；不改 `web/`（前端是 114）；
  - `evals/harbor_agent.py`、`evals/ablate.py`、`evals/board.py`、`evals/dab.py`、`evals/jaffle.py`：`--trace`、选档、只导出一个 trial、Harbor 传变量；
  - `pyproject.toml`、`uv.lock`：`claude-agent-sdk` 0.2.168 → 0.2.187；把 `opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http` 从 `dev` 组挪进正式依赖（版本仍钉 1.45.1）。`opentelemetry-proto` 由 113 挪，本工单不动。不升级或降级 `opentelemetry-api`、`protobuf`；
  - SDK 升级跟着改的测试和夹具：`tests/test_harbor_agent.py:626`（锁定版本号的断言）、`tests/test_eval_atif.py:16-17`（`sdk_version`、`cli_version`）、`tests/fixtures/subagent_transcript.jsonl` 的说明（引用它的测试里的注释；夹具内容不换）；
  - `tests/acceptance/test_observability_*.py`：**只**删 `TICKET_112` 标记，以及因此没人用的 `TICKET_112 = ...` 定义和 import（AGENTS.md 硬规矩的唯一例外）；断言、夹具、输入一字不改；
  - `tests/` 下新增的单元测试；已有的非验收测试里被本工单的改动弄过时的断言（照工单 100 的做法，Comments 里逐条列出改了什么、为什么）；
  - `.env.example`：只加三个变量名，不带值。它是不含值的模板，本工单明确允许读写这一个文件；其他 `.env*` 照旧不读（`.claude/settings.json` 只拦 `.env`、`.env.local`，不拦它）；
  - `README.md`（开关、两档、Cloud 和自托管只差 `BASE_URL`、官方 docker compose 链接、只把公开演示数据发往 Cloud）；`docs/setup.md`（三个变量、服务器 env 文件由人加）；`docs/adr/0008-change-acceptance.md`（末尾加 SDK 升级的 Update，见 Context 第 6 项）。
- Must not edit: `src/data_agent/trace_relay.py`（113）；`tests/acceptance/` 里除删 `TICKET_112` 以外的任何内容，`TICKET_113` 标记也不动；`tests/fixtures/observability/` 里已有的夹具；`web/`（114）；`.claude/`（`trace-check` 的权限规则只在 Comments 里提，见 Context 第 8 项）；`.github/workflows/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access: 工单 agent 没有。主会话用 `da-remote` 跑开发集 3 题初筛。
- Spend cap: 工单 agent $0（加载不了模型环境）。真实运行由主会话做，下面这些是小额运行、已预先批准，不用再问：jaffle-shop 7 题一次；开发集 3 题初筛（`evals board`，服务器，`da-remote`）一次；Context 第 6 项的实测 2、3；spec"验收"一节的几次真实运行。范围不能再扩大。

## Context

1. 行为来源：spec `.scratch/observability/spec.md` 的"开关与配置""覆盖范围""父 span 与 trace 组织""传给 CLI 的环境变量""读回 trace""SDK 升级""新增依赖"各节。中转（113）只通过 `trace_relay.TraceRelay` 的构造、`start()`、`stop()` 用到。取证笔记：`docs/research/2026-11-23-langfuse-usage-mapping.md`（§1 属性名，§5 TRACEPARENT 注入条件）、`docs/research/2026-11-23-sdk-upgrade-0.2.187.md`、`docs/research/2026-11-23-observability.md`。

2. 111 定下的契约（`.scratch/observability/issues/111-observability-acceptance-tests.md` Context 第 3 项和最后的 Comments；代码里的空壳在 `src/data_agent/tracing.py`、`src/data_agent/trace_relay.py`），本工单照它实现，不改签名：
   - `LangfuseSettings.from_env(env)`：三个都非空才返回设置。`run` 用和 `ModelSettings.from_env` 同一个 `env` 读它。
   - `build_options(..., trace: CliTrace | None = None)`：拿到 `CliTrace` 就按 spec"传给 CLI 的环境变量"设导出变量，并覆盖 shell 里继承来的 `OTEL_*`（先例是工单 104 的 `EFFORT_ENV`，`agent.py:136`）；`None` 就一个都不设。SDK 会把继承的 `os.environ` 和 `options.env` 合并（0.2.187 的 `subprocess_cli.py:786-806`），所以"没有密钥"要对合并后的环境成立。
   - `run(..., trace_tier="full")`：每次调用从模块上取 `trace_relay.TraceRelay` 和 `tracing.parent_exporter`（测试会换成假中转和 `InMemorySpanExporter`）；每次运行建自己的 TracerProvider，`run` 返回前 flush；父 span 在 SDK 起 CLI 之前激活。`src` 在导出关闭时不导入 OpenTelemetry（`tracing.py` 只在 `TYPE_CHECKING` 下导入）。
   - 事件：`trace`（`trace_id` 32 位十六进制、`url`、`forwarded_spans`、`forwarded_llm_requests`），中转停下后写一次；`RelayReport.errors` 的每一条写成一个 `trace_export_error`（只有 `error_type`、`status`）。**中转不写事件，由 `run` 写**（111 把这件事从 113 移到了 112）。
   - 父 span 的档属性名叫 `trace_tier`；`langfuse.trace.tags` 是字符串序列。
   - `DataAgentOptions.trace`（`harbor_agent.py:209-211`）、`harbor_agent.MANIFEST`（`:92`）、`dab.run_query(..., trace=False)`（`dab.py:666`）已经有了，只是没用。
   - Harbor：三个 Langfuse 变量和 `MODEL_ENV`（`harbor_agent.py:89`）一样，用 `_get_env` 从宿主环境读（`_model_env` 在 `:305`），只在要导出的 trial 里放进 `exec_as_agent` 的 `env`（`:351`）。题名从 Harbor 在 agent 运行前写好的 `<trial>/config.json` 读（111 Context 第 3 项）。
   - 评测入口：`ablate.py:438`、`board.py:163` 的 `harbor_run_args` 带 `--trace` 时加 `--ak trace=true`；DAB 在 `run_once`（`dab.py:405`，调 `build_options` 在 `:460`）里只给每题第 1 次运行导出，走 `full`。jaffle-shop（`evals/jaffle.py`，它直接起 `data-agent run`）也加 `--trace`，档见"待人定"第 2 条。
   - 111 Comments 提到的参考实现分支 `ref-111-throwaway` 本机已经没有（2026-11-23 `git branch -a` 查过），只能按 111 Comments 的文字描述。

3. 要转绿的验收测试（只删 `TICKET_112`）：
   - `tests/acceptance/test_observability_switch.py`：`from_env`、两档的 CLI 环境变量、`--trace-tier`、父 span、`trace` 事件、`trace_export_error`、对话两轮同一个 session、单次运行各自一个 session、留出集档父 span 不带运行 id 等。不标的"导出关闭"守门组本来就通过，必须继续通过。
   - `tests/acceptance/test_observability_evals.py`：Harbor 传变量、按划分选档、每题每 job 恰好一个 trial、`ablate`/`board` 的 `--ak trace=true`、DAB 第 1 次运行导出。
   - `tests/acceptance/test_observability_trace_check.py`：只输出结构；没有 `trace` 事件时非零退出。
   - `tests/acceptance/test_observability_relay.py` 里同时挂 `TICKET_112` 和 `TICKET_113` 的两条端到端用例（`test_the_trace_event_counts_what_langfuse_received`、`test_a_langfuse_error_leaves_the_run_to_finish_with_an_error_event`）：本工单删掉它们的 `TICKET_112`，以及那个文件里 `TICKET_112 = ...` 的定义。113 没合入时，它们靠 `TICKET_113` 仍然是 XFAIL；两张工单都合入后才转绿。**后合并的一方** rebase 后要跑这两条，贴出它们 PASSED。

4. spec 里没有测试覆盖、本工单也要做的：
   - 开发集档里模型输出由哪个内容开关带出（`OTEL_LOG_RAW_API_BODIES` 或别的）：查 CLI 官方文档定一个，见"待人定"第 6 条。
   - `trace` 事件的 URL：查 Langfuse 官方文档；需要 project id 时启动时调一次 `GET /api/public/projects`。见"待人定"第 5 条。
   - 网页的表单运行和对话（`web/launcher.py`、`web/conversations.py`）变量齐了就按开发集档导出，对话每一轮一条 trace、同一个 session。
   - Harbor 容器：服务器 env 文件里的三个变量由人加，`docs/setup.md` 写明。
   - `.env.example` 加三个变量名；README、`docs/setup.md` 按 spec 写。
   - jaffle-shop 不带 `--trace` 时，它起的 `data-agent run` 即使三个变量都在环境里也不导出（spec 表格：评测入口要显式 `--trace`）；Harbor、DAB 同理（已有测试覆盖），jaffle 没有测试，本工单补。
   - `trace-check <run_id>` 的形式：spec 写了，111 没测（spec 没说按 run id 去哪个目录找）。见"待人定"第 8 条。
   - 新行为都配单元测试。

5. 依赖：
   - 只挪 `opentelemetry-sdk` 和 `opentelemetry-exporter-otlp-proto-http`（111 Authority 定的分工：112 挪 sdk 和 exporter，113 挪 proto）。挪进正式依赖会改 `uv export --frozen --no-dev --no-emit-project --format requirements.txt` 的输出，也就是 Harbor 容器的离线安装清单（`tests/test_eval_wheels.py:91`）。Comments 里贴新旧输出的包名差异。
   - 113 也改 `pyproject.toml`、`uv.lock`，后合并的一方 rebase 后用 `uv lock` 重新生成，不手工合 `uv.lock`。

6. SDK 升级（spec"SDK 升级"一节、`docs/research/2026-11-23-sdk-upgrade-0.2.187.md`）：
   - 改版本号、`tests/test_harbor_agent.py:626`、`tests/test_eval_atif.py:16-17`；改 `agent.py:437` 附近的子 agent transcript 注释（2.1.307 起中间行也带最终 usage，取最后一行的逻辑不变）和 `agent.py:1239-1243` 附近的窗口与压缩注释（不认识的模型默认窗口 2.1.323 起是 1M；`AUTO_COMPACT_WINDOW` 2.1.304 起接受 `400k`），两个设置本身不变。spike worktree 实测：现有测试在新版下只有 `test_harbor_agent.py:626` 失败；其他测试也失败就照 Stop conditions 处理。
   - Harbor 离线安装：内置 CLI 约 250 MB → 364 MB。工单 agent 做不调模型的部分：`python -m evals.wheels <镜像>` 预热，记下新 wheel 的大小和 wheel 总数；在任务镜像的容器里离线装一次（照工单 20 的做法，`.scratch/phase1/issues/20-offline-agent-install.md:68`），记用时。Harbor 的 agent setup 上限 360 秒不能改（同一工单 :25）。真实 trial 的 setup 用时由主会话在开发集 3 题初筛里从每个 trial 的 `agent/setup/install.log` 取。
   - 3 项实测：
     1. **不设 effort 时 CLI 发什么**：不需要模型环境，**工单 agent 自己做**。照工单 104 的做法（`.scratch/dab-gap/issues/104-effort-option.md` Comments：`DATA_AGENT_API_KEY=sk-local-fake DATA_AGENT_BASE_URL=http://127.0.0.1:<端口>` 指向本地记请求体的假服务器，用 `tests/fixtures/shop.sql` 建的 DuckDB），抓未设、`--effort max`、`CLAUDE_CODE_EFFORT_LEVEL=low` 三种情况下的 `output_config.effort`。假服务器脚本放在 worktree 的 `runs/112/` 下，不提交。
     2. **fork 子 agent 默认打开后，验证子 agent 是否照常在前台跑完**：要真实模型，主会话做。工单 agent 写好命令（一次 `full` 配置的 dbt 运行，会调验证子 agent），说明从事件文件和 `subagents/agent-*.jsonl` 看什么（验证子 agent 跑完、`transcript_usage` 读到它的 usage）。需要时设 `CLAUDE_CODE_FORK_SUBAGENT=0`。
     3. **工单 92 的插话探针重跑**：要真实模型，主会话做。工单 agent 照 `.scratch/chat-ui/issues/92-steer-stdin.md:117` 的步骤写好脚本（放 worktree 的 `runs/112/`，不提交），用 `receive_messages()` 读，记下 `ResultMessage` 的个数和 `user_message_uuid(s)`。
   - 结果记进 ADR-0008 的 Update：工单 agent 先写已确认的部分（静态改动、实测 1、离线安装用时）。实测 2、3 由主会话跑，结论由主会话补进 ADR-0008（文档提交）；如果结论要求改代码（例如设 `CLAUDE_CODE_FORK_SUBAGENT=0`），按工作流程第 7 步退回本工单。

7. 真实运行（AGENTS.md 工作流程第 3 步）。工单 agent 加载不了模型环境，113 没合入前中转也是空壳，所以本工单能自己做的真实运行是：
   - 导出关闭时用本地假模型端点跑一次 `data-agent run`（照实测 1）：事件文件里没有 `trace` 事件，没有任何对 Langfuse 的请求；
   - `data-agent trace-check` 对假 Langfuse 跑一次（可以复用 `tests/acceptance/observability_support.py` 的假服务器）。
   其余的写成可以直接复制执行的命令放进最终回复，由主会话跑。113 没合入前中转是空壳，打开导出会在 `TraceRelay(...)` 处抛出，所以下面 jaffle-shop、开发集初筛、实测 2、3 这几次都在**三个 Langfuse 变量不设**的环境里跑：
   - jaffle-shop 7 题（`python -m evals jaffle --postgres-env <变量> --out runs/<名字> --config full`），要求不低于 7/7（`71a5bcc` 的结果）；
   - 开发集 3 题初筛：`da-remote setup <commit>` 加 `da-remote start <名字> -- board ...`（选 3 道开发集题），要求在波动范围内；
   - 实测 2、3；
   - 112、113 都合入后 spec"验收"一节的几次运行（dbt `full` 一次，查询的开发集档、留出集档各一次，网页对话两轮，导出关闭一次），以及每次之后的 `data-agent trace-check` 命令。

8. `trace-check` 的权限规则：spec 要求"给它加一条精确的权限规则（只放行这一条命令），由人审"。本工单**不改** `.claude/`，在最终回复里写出建议的规则原文（写进哪个 settings 文件、匹配哪条命令、为什么不会放行别的命令），由主会话交给人审。

9. 并行的工单：113 和本工单都改 `pyproject.toml`、`uv.lock`；116（外部 MCP）和 119（图表）也改 `agent.py` 的 `build_options` 和 `cli.py`。后合并的一方 rebase；冲突在 `build_options` 的 env 拼接、`cli.py` 的 `run` 选项和子命令列表上最可能。

10. **待人定**（人 2026-11-23 说"以后再定"）。规则：测试已经锁定的照测试做；没锁定的，照 spec 原文或 111 的建议做一个，做成容易改的样子（集中在一个常量或一个函数里），在最终回复的"待人定"标题下逐条列出（选了什么、依据、改起来要动哪里），由主会话在合并前交给人定。**中途不问。**
    1. 导出关闭时 shell 里继承来的 `OTEL_*`、`CLAUDE_CODE_ENABLE_TELEMETRY` 要不要也清掉。测试只锁定 `options.env` 里没有这些键。先照 spec 原文"关闭时不设任何 `OTEL_*`"：不设、也不清。
    2. jaffle-shop 的档：它不在 `evals/manifest.json` 里，照选档规则走留出集档；spec 又把它算作可以发往 Cloud 的公开演示数据。没有测试锁定。先照选档规则走 `structure`。
    3. Harbor 下"第 1 个 trial"怎么认：测试只锁定"每题每 job 恰好一个"。先照 111 的建议：在 job 目录里按题名用 `O_EXCL` 原子地占一个文件，谁先占到谁导出。代价：`harbor job resume` 重跑出错的 trial 时，这题不会再导出。
    4. 单次运行的 `langfuse.session.id`：测试只锁定非空、两次运行不同。先照 111 的建议用事件文件名去掉 `.jsonl`（不能用 `--events-dir` 的目录名，Harbor 下它总是 `events`）。
    5. trace URL 的格式：测试只锁定以 `BASE_URL` 开头、含 trace id。照 Langfuse 官方文档定，注明出处和访问日期；查不到就用 `<BASE_URL>/project/<project id>/traces/<trace id>`，在验收的真实运行里打开核对。
    6. 开发集档里带出模型输出的内容开关：测试只锁定三个开关为 `1`、留出集档没有为真的 `OTEL_LOG_*`。照 CLI 官方文档选一个（候选 `OTEL_LOG_RAW_API_BODIES`），写明它还会带出什么（例如完整请求体），在验收的真实运行里确认 Langfuse 能看到模型回答。
    7. `trace-check` 读的 Langfuse 响应里 resource 属性的字段名（111 夹具用了猜的 `metadata.resourceAttributes`）：测试按夹具锁定。照夹具实现；验收的真实运行里对照真实响应，不一致就记下来交回。
    8. `trace-check <run_id>`：先只认事件文件路径；给的不是存在的文件时，按 `runs/` 下的运行目录找名为 `<run_id>.jsonl` 的事件文件，找不到或找到多个就非零退出并说明。查找逻辑放在一个函数里。

11. 审查（工作流程第 5 步）：`da-review` 在这个账号上跑不了。第一道审查换成一个只读的 `model: fable` 子 agent（给它 worktree 路径、本工单、spec、`CODING_STANDARDS.md`，只报问题、带 `file:line` 和严重程度），结果和 Codex 的一样处理（修，或写明不修的理由）；然后开新上下文跑 `eng:code-review main`。

## Acceptance

- [ ] `tests/acceptance/` 里所有 `TICKET_112` 标记都已删掉，只删了标记和因此没用的定义、import；这些用例都 PASSED，只挂 `TICKET_113` 的两条端到端用例仍是 XFAIL；"导出关闭"守门组仍通过；`git diff main -- tests/acceptance/` 只有删掉的标记行
- [ ] 导出关闭时 `data-agent run` 不导入 OpenTelemetry、不建中转和 exporter、不设 `OTEL_*`（守门测试，加上本地假模型端点的真实运行）
- [ ] 网页运行和对话在变量齐了时按开发集档导出，对话每轮一条 trace、同一个 session（单元测试）
- [ ] jaffle-shop 加了 `--trace`，按"待人定"第 2 条的暂定做法选档；不带 `--trace` 时即使三个变量都在也不导出（单元测试）
- [ ] `opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http` 在正式依赖里，`opentelemetry-proto` 没动；`opentelemetry-api`、`protobuf` 版本不变
- [ ] `claude-agent-sdk==0.2.187`；版本号的测试和 Context 第 6 项的注释已改；实测 1 有结论，ADR-0008 Update 写了已确认的部分
- [ ] Harbor 离线安装：新 wheel 大小、wheel 总数和容器内离线安装用时记在 Comments，明显低于 360 秒
- [ ] `.env.example`、README、`docs/setup.md` 按 Context 第 4 项改好；不含任何值、密钥和服务器地址
- [ ] 最终回复里有：主会话要跑的全部命令（Context 第 7 项）；`trace-check` 的权限规则建议（Context 第 8 项）；"待人定"清单（Context 第 10 项）
- [ ] 主会话做（不阻塞本工单交回）：jaffle-shop 7 题不低于 7/7；开发集 3 题初筛在波动范围内，每个 trial 的 agent setup 低于 360 秒；实测 2、3 有结论，主会话补进 ADR-0008；112、113 都合入后 spec"验收"一节逐条看到
- [ ] `make check` 通过

## Evidence required

`make check` 的完整输出；`uv run pytest tests/acceptance/test_observability_*.py -v`（每个用例的 PASSED / XFAIL）；`git diff main -- tests/acceptance/`；`git diff main -- pyproject.toml` 和新旧 `uv export --no-dev` 的包名差异；实测 1 的请求体摘录（只有 `output_config` 等结构字段）；离线安装的 wheel 大小和用时；导出关闭的真实运行命令和输出；交给主会话的命令清单、权限规则建议、"待人定"清单。

## Stop conditions

- 要改 111 的验收测试（除了删 `TICKET_112`）才能转绿，或者觉得某条验收测试错了。
- 要改 `trace_relay.py` 的契约（构造、`start`、`stop`、`RelayReport`）才能接上中转。
- 升级到 0.2.187 后，除了 Context 第 6 项列的测试，还有已有测试失败，而且不是改注释、改版本号能解决的。
- 挪依赖迫使 `opentelemetry-api` 或 `protobuf` 换版本。
- 容器内离线安装接近或超过 360 秒（时限不能改）。
- spec 自相矛盾，或缺一个"待人定"以外、测试和 spec 都没给方向的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: —
- `03225e8`：修复本身。父 span 改为显式导出；`ParentExporter` 记下 HTTP 状态码；失败时写 `trace_export_error`；配 500 和连接被拒两个测试。
- `6ae0645`：Fable 审查第 1 轮。3xx 记成 `http_error`，配测试。
- `d45693e`：`eng:code-review` 第 2 轮。状态码按最后一次尝试算，归类挪进 `ParentExporter.send`，测试改用关闭的端口，加 503 后断连的测试。
- 改动文件：`src/data_agent/tracing.py`、`tests/test_tracing.py`。真实运行的脚本和输出在 `runs/112/return1/`（不进 git）。

## Comments

- 2026-11-23（工单 agent 最终回复，主会话照抄）：

**Commits**
- `4adcd0f`: SDK upgrade to 0.2.187 and the dependency move.
- `4ee1d8e`: the feature itself.
- `2fb56aa`: fixes from the Fable review.
- `d69448b`: fixes from `eng:code-review`.

**Evidence**

- **Baseline before any change:** 2613 passed, 2 skipped, 97 xfailed.
- **After the SDK upgrade, with only the version tests changed:** 2613 passed, 2 skipped, 97 xfailed. No other test failed.
- **Final `make check` (`d69448b`):**
  - ruff passed, 401 files formatted, basedpyright 0 errors.
  - pytest: 2684 passed, 2 skipped, 79 deselected, 63 xfailed in 610 s.
  - web: 322 passed, 52 expected fail.
- **`pytest tests/acceptance/test_observability_*.py -v`:** 45 passed, 17 xfailed.
  - All 17 xfails are in `test_observability_relay.py` and still carry `TICKET_113`.
  - They include the two end-to-end cases that had both marks.
  - The "export off" guard group passes.
- **`git diff main -- tests/acceptance/`:** only deleted lines. That is 19 `@TICKET_112` marks, 4 `TICKET_112 = ...` definitions, and the 3 blank lines that came with them (ruff's import sorting requires removing them).
- **Dependencies:**
  - `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` 1.45.1 are now in the main dependencies. `opentelemetry-proto` stays in the `dev` group.
  - `opentelemetry-api` stays at 1.45.1 and `protobuf` at 6.33.6.
  - `uv export --no-dev` gains these packages: certifi, charset-normalizer, googleapis-common-protos, opentelemetry-exporter-http-transport, opentelemetry-exporter-otlp-common, opentelemetry-exporter-otlp-proto-common, opentelemetry-exporter-otlp-proto-http, opentelemetry-proto, opentelemetry-sdk, opentelemetry-semantic-conventions, protobuf, requests, urllib3. claude-agent-sdk goes from 0.2.168 to 0.2.187.
- **Test 1 (what the CLI sends when effort is not set), against a fake local endpoint:**
  - No `--effort` and an empty `CLAUDE_CONFIG_DIR`: `{"effort":"high"}`.
  - No `--effort` with user settings that say `xhigh`: `xhigh`.
  - `--effort max`: `max`.
  - Only `CLAUDE_CODE_EFFORT_LEVEL=low`: `low`.
  - `low` plus `--effort max`: `max`.
  - This is the same as on 2.1.301. One new thing: `thinking` is now `{"type":"adaptive","display":"updates"}`. Whether DeepSeek accepts `display` is unconfirmed.
- **Harbor offline install:**
  - There is no data-eng-bench task image on this machine, so I used `python:3.12-slim` (CPython 3.12, glibc 2.41, aarch64) with `--network none`.
  - 53 wheels, 208 MB in total. The SDK wheel is 167 MB (it was 114 MB).
  - Container install time: `installed in 12 s`, far under the 360 s limit.
  - x86_64 setup time on the server is still unmeasured.
- **Real run with the export off:**
  - Command: `runs/112/export_off_run.sh` (fake model on 127.0.0.1:18912; two of the three Langfuse variables set; the base URL points at a stand-in on :18913 that logs requests). Exit 0.
  - Events: `version, task, status, text, answer, result`, with no `trace` or `trace_export_error`.
  - The Langfuse stand-in received 0 requests.
- **`trace-check` run as a real command:** `runs/112/trace_check_demo.py`, against the acceptance tests' fake Langfuse.
  - With a `trace` event: exit 0, prints a 4-level tree plus counts and only attribute names. One `GET /api/public/v2/observations?traceId=…&fields=core,basic,metadata,usage` was made. The planted secret-like value was not printed.
  - Without a `trace` event: exit 2.
- **Probe for decision 6 (export off, the shell's own `OTEL_*` pointed at a local sink):**
  - With the three content switches, and also with `OTEL_LOG_RAW_API_BODIES=1` plus `OTEL_LOG_ASSISTANT_RESPONSES=1`, no span carried the model's reply.
  - With `ENABLE_BETA_TRACING_DETAILED=1` plus `BETA_TRACING_ENDPOINT`, the CLI sends **OTLP/JSON** to the beta endpoint instead of the normal export, and `llm_request` then carries `response.model_output`.

**Changed existing (non-acceptance) tests**
- `tests/test_harbor_agent.py:626` and `tests/test_eval_atif.py:16-17`: version numbers.
- `tests/test_agent_outcomes.py`: the fixture description comment.
- `tests/test_eval_jaffle.py`: the hand-built `Namespace` gains `trace=False`, because `jaffle.run` now reads `args.trace`.
- `tests/test_postgres.py:176`: the exact set of env keys gains `LANGFUSE_ENV`, because the Langfuse keys are now blanked on every run.

**Reviews**
- **First review (Fable, standing in for `da-review`).** No P0.
  - Fixed:
    - A failed export start or finish no longer breaks the run; it writes one `error` event with `source: trace` instead.
    - `OTEL_SDK_DISABLED` leaves the run untraced.
    - `TRACEPARENT` and `TRACESTATE` are set explicitly in the options' env, so `OTEL_PROPAGATORS` cannot detach the CLI's spans.
    - Exceptions are not recorded on the parent span.
    - Host names are masked as whole words, so a host called `db` no longer turns "dbt" into `[REDACTED]t`.
    - No `cost_details` is sent when the run has no cost.
    - `http.client.HTTPException` is caught.
    - `trace-check` exits non-zero on a trace with no observations.
    - The Langfuse keys are blanked in the CLI env and masked in the event file on every run.
  - Not fixed:
    - Parent-span usage may be double-counted: the spec requires it, so it goes in the decisions below.
    - User-settings `env` blocks may override our variables: unconfirmed, also in the decisions below.
    - `CLAUDE_CODE_OTEL_*` variables are not blanked: they only change content length, not the destination.
    - The Harbor claim is taken before the trial runs: this is the known cost of decision 3.
    - The public export may include the `trace` URL: `evals/export.py` is outside my Authority; handle it before 114.
    - The README uses the public Cloud URL: it is a vendor address, not a secret.
- **Second review (`eng:code-review main`).**
  - Standards:
    - Fixed: one hard violation. A DAB run whose export failed wrote the `error` before the `version` header; a new test covers it.
    - Fixed: DAB's logs now use one secret list.
    - Fixed: `active()` now has a real return type.
    - Fixed: reworded the jaffle comment.
    - Not fixed: duplication between `agent.run` and `dab.run_once`. The two loops already differ (event file, database load, result); the risky part sits in `RunExport`, and the ordering is covered by a test.
    - Not fixed: the two `--trace` arguments in ablate and board. They are two lines each.
    - Not fixed: the size of `tracing.py`. It does one job, the export.
  - Spec: "keys blanked when the export is off", "a new `error` event", "explicit TRACEPARENT" and "the URL fallback differs from the ticket's wording" are all written up in the decisions below.


### Commands for the main session

Leave the three Langfuse variables unset until 113 merges.

1. **jaffle-shop, 7 cases (needs 7/7):**
   `uv run python -m evals jaffle --postgres-env <VAR> --out runs/112-jaffle --config full`
2. **Dev-set screen, 3 tasks:** run `da-remote setup <branch HEAD after push>`, then:
   `da-remote start 112-screen -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest evals/manifest.json --out runs/112-screen --budget 3 --split dev --task <t1> --task <t2> --task <t3> --trials 1 --config full`
   - The ticket says `evals board`; I used the dev-set form documented in `docs/setup.md`.
   - Then read each trial's setup time with `da-remote exec -- bash -c 'grep -h "installed in" runs/112-screen/**/agent/setup/install.log'`. The exact path is unconfirmed.
3. **Test 2 (verifier subagent after the fork default changed):**
   `uv run python -m evals jaffle --postgres-env <VAR> --out runs/112-fork --config full --case b1_monthly_revenue`
   - Check in the event file:
     - `verifier_start` and `verifier_end` both appear before the single `result`.
     - There is no `error` with `source: spend`, which would mean the transcript read failed.
     - The result's `cost_basis` is `price_table`.
   - Check that `subagents/agent-*.jsonl` exists under the session.
   - If it fails, retry with `CLAUDE_CODE_FORK_SUBAGENT=0` and send the ticket back.
4. **Test 3 (steer probe):**
   `uv run python <worktree>/runs/112/steer_probe.py`, then again with `--replay`.
   - Record the number of `ResultMessage`s, each one's `num_turns`, whether BANANA appears, and `user_message_uuid(s)`.
   - The script prints P for the prompt and S for the steer.
5. **After both 112 and 113 merge**, with the three variables set, run `data-agent trace-check <events file>` after each of these:
   - one dbt run on `full`;
   - one query run on the dev tier and one with `--trace-tier structure` (on repo fixtures);
   - two turns of a web conversation;
   - one run with the export off.
   - Rebase 113 and confirm the two relay end-to-end cases PASS.

### `trace-check` permission rule (for human review; I did not touch `.claude/`)

The repo already allows `Bash(uv run *)`. What actually blocks agents is that the keys live in `~/.config/data-agent/env`, which agents may not read. The suggestion is a one-argument wrapper at `~/.local/bin/da-trace-check`:

```bash
#!/bin/bash
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: da-trace-check <events file | run id>" >&2; exit 2; }
set -a; . ~/.config/data-agent/env; set +a
exec uv run data-agent trace-check "$1"
```

Then add `"Bash(da-trace-check *)"` to `permissions.allow` in `.claude/settings.json`. This lets nothing else through:
- The wrapper takes one argument and runs only `trace-check`, which prints only structure.
- Claude Code checks each part of a compound command separately.
- `Edit(~/.local/bin/da-*)` is already denied, so an agent cannot change the wrapper.

### Decisions for you (待人定)

1. **Inherited `OTEL_*` when the export is off:** not set and not cleared (spec wording). Two related points:
   - The review added blanking the three Langfuse keys on every run. Change it in `tracing.cli_env`.
   - The parent span's exporter in our own process still merges `OTEL_EXPORTER_OTLP_HEADERS` (ours win on the same key).
2. **jaffle-shop tier:** `structure` (`evals/jaffle.py` `TRACE_TIER`).
3. **The first Harbor trial:** an `O_EXCL` claim file at the job's top level (`harbor_agent.claim_trace`). Costs:
   - A resumed trial does not export.
   - A first trial that breaks on infrastructure uses up its task's export.
   - Smoke runs are traced too, one trial per config.
4. **Session id of a single run:** the event file name without `.jsonl`. DAB uses `dab-<dataset>-query<id>-run<k>-<random>`.
5. **Trace URL:** `<BASE_URL>/project/<id>/traces/<trace id>`, as langfuse-python `get_trace_url` builds it (2cea520, read 2026-11-23). When the project id is missing it falls back to `<BASE_URL>/trace/<id>`, the web app's redirect page (langfuse 67b035c). Change it in `tracing.trace_url`.
6. **Model output: no documented switch does it.**
   - The three switches and `RAW_API_BODIES` put no model text on standard spans; `RAW_API_BODIES` emits log events, which the export turns off.
   - Only the detailed beta tracing does, and it switches the export to JSON sent to a different endpoint, which the relay would also have to parse.
   - What I did: only the final answer goes in the dev-tier parent span's `langfuse.observation.output`. The text of each model request does **not** reach Langfuse.
   - This falls short of the spec's "能看到模型回答". Change it in `tracing.CONTENT_SWITCHES` and `result_attributes`.
7. **Resource attribute field name:** implemented as the fixture has it (`metadata.<group>` keys). Compare against the real response in the acceptance run.
8. **`trace-check <run_id>`:** a file path first; otherwise the single `runs/**/<run_id>.jsonl`. Change it in `trace_check.find_events`.

Further points for you:
- The parent span carries the run's total usage and cost while each request span carries its own, so Langfuse's trace total may be double. Check in the acceptance run.
- If the user settings' `env` block sets `OTEL_*` and it takes precedence (unconfirmed), it would bypass the relay.
- A failed export writes an `error` event with `source: trace`. The spec only defines `trace_export_error`.

The local Langfuse stand-in (port 18913) was stopped when it hit its background time limit. It was only for the export-off run, which already finished with 0 requests to it, so nothing is affected.

The fake model endpoint on port 18912 will stop the same way when its limit is reached. The main-session commands don't use it: they call the real model endpoint.

The fake model endpoint on port 18912 has now also stopped at its background time limit. It was only for test 1, the probe on which switch carries model output, and the export-off run, and all three had already finished, so no result is affected.

To re-run any of those, start it again with `uv run python runs/112/fake_model_server.py 18912 runs/112/m1/requests.jsonl runs/112/m1/otlp`.

- 2026-11-23（主会话，第 7 步）：PR #125 CI 绿。Codex GitHub app 1 条意见，主会话对照代码核实属实：
  - 退回：父 span 导出失败不报。`tracing.py:303,314` 用 `SimpleSpanProcessor`，它吞掉 exporter 的异常、不看导出结果；`_finish`（`:367` `self._span.end()`）之后照常写 `trace` 事件。Langfuse 拒收或超时时，父 span（会话 id、答案、总 usage 和 cost）静默丢失，事件里没有任何错误。要显式拿到父 span 的导出结果（例如直接调 exporter 的 `export()` 看 `SpanExportResult`，或包一层记下失败），失败时写一条 `trace_export_error`（只有错误类型和 HTTP 状态码，和中转同一口径），run 照常结束。配一个修复前会失败的测试（假 Langfuse 对父 span 返回 500 / 连接被拒）。
  - 派修复要等主会话的真实运行（`runs/batch2/live-checks.sh` 在本工单 worktree 里跑 jaffle 和实测 2、3）结束，不在运行中改 worktree。
- 2026-11-23（主会话，真实运行，`runs/batch2/live-checks.sh`，本分支 d69448b，Langfuse 变量未设）：
  - jaffle 7 题 7/7 通过，$0.0926。
  - 实测 2（fork 子 agent 默认开，`--case b1_monthly_revenue`）：通过，$0.0230。事件里 `verifier_dispatch`→`verifier_start`→`verifier_end`（turns 6、tool_calls 9、checks 全 PASS、hit_max_turns false）都在 `answer`、`result` 之前；无 `error` 事件；`result.cost_basis=price_table`，`errors: []`。验证子 agent 照常在前台跑完，不需要 `CLAUDE_CODE_FORK_SUBAGENT=0`。`subagents/agent-*.jsonl` 在本次运行的临时配置目录里，运行后已清掉，没直接看到文件；`verifier_end` 的 turns/tool_calls 来自它，说明读到了。
  - 实测 3（steer）：中途插话得到 2 个 ResultMessage，各自 `num_turns=1`，`user_message_uuid` 分别为 P、S（`user_message_uuids` 各一个），第二轮含 BANANA；`--replay` 结果一致。
- 2026-11-23（工单 agent，退回 1 修复）：父 span 导出失败不再被吞掉。
  - **改法**
    - 去掉 `SimpleSpanProcessor`。`RunExport._end_span` 结束父 span 后自己调导出，并看导出结果。
    - 新增 `ParentExporter`，包着 OTLP 导出器。它用一个 `requests.Session` 子类，每次尝试（含重试）开头清空状态码，响应回来后记下。
    - 由 `ParentExporter.send` 归类，口径和中转的 `ExportError` 一样：
      - 最后一次尝试的回答不是 2xx：`http_error` 加状态码。3xx 也算，因为导出器把它当成功，但不跟随跳转，父 span 实际没到。
      - 没收到回答：`connection_error`，状态码为 None。
    - 这条错误事件排在中转的错误事件之后、`trace` 事件之前。`failure` 仍是 None，run 照常结束。
    - `parent_exporter(settings)` 仍然只收一个参数，111 的验收测试一字未改。
  - **修复前会失败的测试**（`tests/test_tracing.py`）
    - 父 span 被回 500；
    - 连接被拒（用一个绑定后关闭的端口）；
    - 回 302；
    - 先 503 重试，之后连接断开。

    前两条在修复前的代码上跑过，确实失败：只有 `parse_error` 和 `trace` 两条事件，没有父 span 的错误。
  - **`make check`**：exit 0，pytest 2688 passed、2 skipped、63 xfailed；web 322 passed、52 expected fail。
  - **真实运行**：`uv run python runs/112/return1/probe.py`。真实的 `RunExport` 和 OTLP 导出器对着本地一个回 500 的 Langfuse 跑，再对着一个被拒的端口跑。中转用替身，因为中转是工单 113 的部分，还没实现。输出：
    ```
    ('trace_export_error', {'error_type': 'http_error', 'status': 500})
    ('trace', {'trace_id': 'f83cb6dd…', 'url': 'http://127.0.0.1:50994/trace/f83cb6dd…', 'forwarded_spans': 0, 'forwarded_llm_requests': 0})
    failure: None
    ('trace_export_error', {'error_type': 'connection_error', 'status': None})
    ('trace', {'trace_id': 'cfc79a5b…', 'url': 'http://127.0.0.1:9/trace/cfc79a5b…', …})
    failure: None
    ```
  - **审查第 1 轮：Fable 只读子 agent**（da-review 不可用，用它代替）
    - 修了：3xx 被当成功，父 span 照样静默丢失（`6ae0645`）。
    - 不修，理由如下：
      - 503 重试后连接断开，被报成过时的 `http_error 503`：第 2 轮已修（`d45693e`）。
      - 编码失败、请求超过 64 MiB、导出器已关闭这类没收到回答的失败，都记成 `connection_error`：中转的口径里没有更合适的类型，已写进 `send` 的 docstring。
      - 导出器自己抛异常时，`finish` 改为写 `error` 事件（`source: trace`）：真实的 OTLP 导出器会自己接住异常，只有会抛异常的测试替身受影响，run 仍照常结束。
      - 取消 run 时 `finish` 和 `_close` 并发：只发生在已经中止的 run 上，导出到一半被关掉的情况修复前就有。
      - `_shutdown` 里 `exporter.shutdown()` 抛错时，会跳过 `provider.shutdown()`：provider 没有挂任何 processor，只剩一个 atexit 钩子。
      - `requests` 没有直接声明为依赖：它随导出器而来（导出器公开的 `session` 参数就是 `requests.Session`，钉住的 1.45.1 依赖 `requests~=2.7`）；已在代码里注释，不动 lockfile。
  - **审查第 2 轮：`eng:code-review main`**（新上下文）：没有硬性违规，没有阻塞的 spec 问题。
    - 修了（`d45693e`）：
      - 状态码按最后一次尝试算，不再按最后收到的回答算；
      - 归类从 `RunExport` 挪进 `ParentExporter.send`，原先在 `RunExport` 里用 `isinstance` 判断再读 `statuses` 列表；
      - 只保留一个 `last_status`；
      - 连接错误的测试不再依赖固定的 9 号端口。
    - 不修，理由如下：
      - run 异常退出或被取消时，`_close` 不报父 span 的导出错误：这条路径本来就不写 trace 相关事件，阻塞时长和修复前一样，不在退回说明要求的"run 照常结束"路径里。
      - `connection_error` 比中转的定义宽：同第 1 轮。
      - `parent_exporter` 返回类型是联合类型：为了让测试替身通过类型检查，docstring 已注明。
  - 两轮审查都用完了，没有剩下要修的问题。
