# 对话界面：多轮追问、澄清、运行中插话（查数）

日期：2026-11-17。Status: 已对齐，待排期（实现时间另定）。测试接缝 2026-11-17 已定（第 5、6 轮 grilling，人确认）；2026-11-17 晚人改定第 3 条：对话只做查数，不选类型、不选数据源，agent 自己选库；随之删掉 dbt 相关条目和场景 e，加跨库场景 g。验收测试的原则人确认过（只测外部行为、尽量走高层接口，不写无效单测）；工单 88 照此写，测试代码合并前仍由人审；实现工单 89–96 已拆（`issues/`），开工时间由人另定。

决策来源：2026-11-17 与人的 grilling（4 轮，结论逐条经人确认；第 5 轮定测试接缝和插话通道，第 6 轮改成按效果测、主验收用真模型）。上游取证：SignalPilot `~/dev/AI/data-agent-refs/SignalPilot/signalpilot`、nao `~/dev/AI/data-agent-refs/nao/apps`（下文简称 SP、nao），以及本仓库代码。前一版界面的 spec：`.scratch/web-ui/spec.md`。

## Problem Statement

本地版 `data-agent web`（工单 79）是一个提交表单加一个 trace 查看器：填一个任务、跑完、看过程。它适合看 agent 怎么干活，但不是用户日常提问用的界面：

- 不能追问。agent 是一次性的：一次 `data-agent run` 就是一次 SDK `query()`（`src/data_agent/agent.py:1`、`:1420`），`build_options` 从不设 `resume`；`session_id` 只写进事件（`agent.py:907`）。
- 不能澄清。代码里没有澄清工具，也没有澄清事件；运行结果只有完成和基础设施错误两种（`agent.py:1456-1468`）。
- 运行中不能插话。

求职展示也受影响：README 动图和博客视频需要一个面试官一眼能看懂的对话形态。

## Solution

在本地版上做一个对话界面，作为主入口，形态照常见 AI 对话和上游（SP 的 `web/app/chats/`、nao 的 `components/tool-calls/`）：

- 用户发消息，agent 的每一步工具调用以卡片形式实时出现在对话里，最后给出答案。
- 能追问，agent 能反问（澄清），运行中能插话。
- 对话只做查数。agent 能看到配置里的全部数据源，自己决定查哪个库。dbt 任务仍走现有的提交表单和三栏运行页。
- 现有三栏运行页保留，作为每条回答的"完整视图"。
- 在线版加一段录好的真实对话回放。

## 决定

### 范围

1. 对话界面是本地版的主入口。README 动图和博客视频等它做完再录。
2. 做：多轮追问、澄清、运行中插话。不做：Memory、答案里的图表、编辑旧消息或重新生成。
3. 对话只做查数（2026-11-17 晚人改定，取代原来的"查数和 dbt 都能在对话里做、新建时选类型和数据源"）：
   - 新建对话不选类型，也不选数据源。
   - 配置里的数据源全交给 agent，每次查询由它自己选库，每条查询的事件记下查的是哪个库；要用到几个库时分别查再合并，不做一条 SQL 跨库 join。只配了一个库时可以不指定。
   - 先例：nao `execute_sql` 的 `database_id`（`apps/shared/src/tools/execute-sql.ts:29-34`，配了多个库时必填，库清单写进系统提示 `apps/backend/src/components/ai/system-prompt.tsx:335-337`）；SP `query_database(sql, connection_name)`；Hex Threads（`learn.hex.tech/docs/explore-data/threads`："searches across all connections… can query multiple connections within a single conversation"）、Fabric data agent、Power BI 独立 Copilot 也是自己选源（官方文档，2026-11-17 核）。没有一家说支持一条 SQL 跨两个源 join；Looker 明写一次查询只能查一个 Explore。
   - dbt 不进对话：dbt 任务仍从现有提交表单发起，在三栏运行页看过程；现有功能不变。
   - 评测每题只给一个库，工具名和行为不变（第 19 条）。
4. 在线版（GitHub Pages，只读）加一段录好的真实对话回放，访客不能发消息。演示对话（在线版和视频共用）在 data-eng-bench 的零售库上录（Apache-2.0，803 张表，带 dbt 项目；本机 clone 里是 Git LFS 指针，约 489 MB，要先拉下来），问题自己写，不用榜单题目，不碰留出集（ADR-0004、ADR-0005）。

### 显示

5. 调用过程直接显示在对话里，照常见 AI 对话和上游做：
   - 运行中，每一步以卡片实时出现。思考默认收起；SQL 带高亮和结果表，标出查的是哪个库；验证者带 PASS/FAIL 和逐项结论；被拒绝的调用有标记。
   - 完成后卡片留在对话里，答案和结果表在最后；步骤多时按阶段分组，可以折叠和展开。
   - 每条回答可以打开现有的三栏运行页看完整视图。
   - 卡片样式、事件折叠尽量复用现有运行页的组件。具体样式做出可点的原型后由人看着定。
   - 2026-11-17 人看过可点原型，选定 A「对话流」：单栏对话，每轮的步骤按阶段折叠成卡片嵌在回答里，展开看 SQL 和结果表，答案和结果表在最后；完整过程点"完整视图"去三栏运行页。没选 B（对话加右侧过程栏，和三栏运行页重叠）和 C（工作日志）。原型在分支 `prototype/chat-ui`（提交 `566e353`，`web/src/prototype-chat/`，`/prototype/chat?variant=A`），只作参考，不合入 main；实现时重写，不直接搬原型代码。
6. 历史列表改成对话列表。单次运行（命令行或提交表单跑的，包括 dbt）显示成只有一轮的对话，不能追问（沿用 `.scratch/web-ui/spec.md` 用户故事 33）；旧事件文件照样能回放（用户故事 39）。
7. 对话里显示这段对话的累计花费。

### 行为

8. **澄清**照 nao（`docs/plan.md` §3 已定的做法）：
   - agent 调一个提问工具，给一个问题加 2–5 个选项，这一轮随即结束（nao：`shared/src/tools/clarification.ts:3-24`、`backend/src/agents/stop-conditions.ts:21-24`）。
   - 用户点的选项作为下一条普通消息发出（nao `frontend/src/components/tool-calls/clarification.tsx:51`）。不用 SP 那种挂起运行等待回答的状态。
   - 只在歧义会改变结果时才问；不问就把假设写进答案。
   - 跑榜时移除这个工具。
9. **运行中插话**照 SP：
   - agent 运行时可以继续发消息，消息直接送进正在跑的会话，不打断它，模型下一步就能读到；对话里标出"已送达 agent"。
   - SP 的做法是在活着的 SDK 客户端上再调一次 `client.query()`，用 stream-json 的 user 消息，带 uuid 用于对账（`notebook-server/signalpilot/_server/ai/claude_agent.py:355-408`；接口 `api/endpoints/standalone_chat_cancel.py:28`；网关转发 `gateway/gateway/standalone_chat/worker_events.py:328`）。
   - 后端到运行中子进程的通道（2026-11-17 第 5 轮 Q3，人确认）：本地版后端启动的 `data-agent run` 子进程从 stdin 按行读 JSON 消息，每条转成一次 `client.query()`，送进后写一个插话送达事件。消息字段由验收测试工单定。
   - 取消保留。被取消的一轮标成"已取消"，之后可以接着发下一条。
10–12. （2026-11-17 晚删：dbt 不进对话。原来是 dbt 后台作业、dbt 验证者核对整段对话、dbt 改动的 diff 和补丁下载。）
13. **花费**：每轮保持现在的轮数上限和花费上限；不设对话总上限。

### 技术

14. **多轮用 SDK 的 `resume`**，照 SP（`notebook-server/signalpilot/_server/ai/claude_agent_options.py:269-273`）：
    - 每段对话有自己的 `CLAUDE_CONFIG_DIR` 作为会话存档目录，不写进用户本人的 `~/.claude`。现在 `sdk_env()` 没设它（`agent.py:708-735`），推断记录落在 `~/.claude`，未验证。
    - SDK 把记录存在 `<config>/projects/<按工作目录转换的名字>/<session_id>.jsonl`（`claude_agent_sdk/_internal/sessions.py:104-145`）。
    - SP 的经验：CLI 拒绝对已存在的 id 用 `--session-id`、对没见过的 id 用 `--resume`，要按情况选 flag（`claude_agent.py:127-160`）。
    - 后台任务保持关闭（`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`，`agent.py:726`），SP 也因为它会污染下一次 resume 而关掉。
15. **agent 从一次性 `query()` 换成 `ClaudeSDKClient`**（`claude_agent_sdk/client.py:272` `query()`、`:308` `interrupt()`），用于插话。改动碰到 agent 的核心循环；按 ADR-0008 Update 2026-11-04 的规则，确定性修改用单元测试加单题真实运行验证，评测行为不能变。
16. **一段对话一个工作目录**（空目录，各轮共用）：
    - 首条提示词、MCP 服务参数、快照和工具账本的临时目录都写死了本次运行的路径（`agent.py:988-1039`、`:1359-1366`）。工作目录按对话固定后，要确认每一轮的这些路径和会话历史里的一致。
17. **存储用文件，不引入数据库**：
    - 一条用户消息对应一次运行、一个事件文件，沿用现有格式。
    - 一段对话一个目录，加一个索引文件把各轮串起来。
    - 事件新增：澄清事件、插话送达事件；每个事件文件记下所属对话和第几轮。
18. **每轮重建的状态要按对话处理**：轮数预算、花费、验证者的派发计数和 Stop hook 状态现在都按单次运行新建（`agent.py:1342-1347`，`src/data_agent/verifier_v2/hooks.py`）。resume 后这些归零，但会话历史里还留着上一轮的记录。实现时逐项定哪些按轮、哪些按对话。
19. **评测和跑榜仍走单次运行**：Harbor 和 DAB 的入口不变；跑榜时关闭澄清和插话。
20. **多数据源**（第 3 条）：
    - `data-agent run` 新增一个参数接收数据源清单，格式沿用 `data-agent web --data-sources` 的 TOML（`src/data_agent/web/sources.py`）；Postgres 仍只传环境变量名。现有的 `--duckdb` / `--postgres-env` 单库参数保留，评测照旧用它们。
    - 一个 MCP 服务连多个库；查数和上下文工具（查询、执行计划、表目录、数据库事实、标签查询等，`src/data_agent/mcp_server.py`）加一个库名参数，照 nao `database_id`，配了多个库时必填。只配了一个库时不注册这个参数，工具名（`mcp__data__*`）和参数都和现在完全一样，评测看到的工具定义不变。
    - 系统提示列出可用的库名和种类，照 nao `ConfiguredDatabasesBlock`（`system-prompt.tsx:371-388`，两个库以上才列）。
    - 每条查询的事件记下库名；只读网关的规则对每个库都一样。

## Testing Decisions

2026-11-17 第 5 轮 grilling 定接缝，第 6 轮（Q1–Q6，人确认）改成按效果测：主验收用真模型端到端，不为省钱或省时间压缩。好的测试只测外部行为。

### 主验收：真模型端到端（pytest，`integration`，`make test-integration` 跑）

起真的 `data-agent web` 后端、真 agent、真模型，经 HTTP 接口走完整段对话。先例：`tests/acceptance/test_seam_a_run_cli.py`（真调模型）。夹具库和问题自己写，不用榜单题目（ADR-0005）。后端配两个夹具库（如订单库和客户库），a–d、g 都在这个配置下跑，不指定库。

- a **追问**：先问一个数，再问一个依赖上文的问题（如"其中 2024 年的有多少"）。第二轮数值对、session id 不变、会话记录在对话自己的配置目录里而不在 `~/.claude`。
- b **澄清**：夹具库里有一个会改变结果的歧义（如收入含不含退款）。第一轮以澄清结束、2–5 个选项；选一个后答案按那个口径算对。另一个没有歧义的问题不澄清。
- c **插话**：运行中发"只要前 3 名"。有送达事件，最终答案照做。
- d **取消后续问**：取消一轮，下一轮正常答完。
- f **评测路径**：`data-agent run` 单次跑一个有歧义的问题，不出澄清事件，直接回答并写出假设。
- g **自己选库、跨库**：一个要两个库的数据才能答对的问题（如按客户所在地区汇总订单金额，订单和客户在不同库）。数值对，两个库都有查询事件且事件记下了库名；另一个只涉及一个库的问题只查那个库。

### 浏览器（Playwright，`@playwright/test`，pnpm；真后端加真模型）

一条主流程：在页面上发消息、卡片实时出现、点澄清选项、追问。不断言样式。

### 判定

- 每个场景（a–d、f、g）跑 3 次，3 次都过才算过。失败时读轨迹，判定是实现问题还是模型偶发。
- 工单 agent 加载不了模型环境，功能没做时也写不出这些测试的参考实现：工单 88 写好、标 `xfail`；对应实现工单合并前由主会话跑 `make test-integration`，转绿才合并。

### 不调模型的三组（保留，覆盖真跑造不出来的情形）

照上一版界面的做法（`.scratch/web-ui/spec.md` "Testing Decisions"），不为省时间压数量：

1. **对话视图模型**（vitest，纯函数）：输入一段对话的索引文件加各轮的事件文件（全部或运行中的前缀），输出对话视图。
   - 要断言的行为：轮次顺序，每轮的卡片复用现有运行视图模型；澄清的问题和选项，所选选项作为下一条用户消息；插话出现在哪一步之后、"已送达"标记；已取消的轮；累计花费等于各轮 `cost_usd` 之和；每条查询查的是哪个库；单次运行（含 dbt）显示成一轮对话；没有对话字段的旧事件文件照样能回放；只给前缀（运行中）时的结果。
   - 夹具：现在还没有多轮的真实事件文件，用手写的小夹具；单轮部分复用 `tests/fixtures/web/real/`。实现做完后，可以把端到端跑出来的真实事件文件脱敏后补进夹具。
2. **后端接口**（pytest，FastAPI 测试客户端）：`data-agent run` 子进程换成假的，按脚本写事件、读 stdin、改工作目录里的文件。先例：`tests/acceptance/test_web_api.py`、`tests/fixtures/web/fake_run.py`。
   - 要断言的行为：新建对话不带类型和数据源；子进程拿到配置里的全部数据源（只有名字和种类，没有连接串）；第二轮起子进程拿到上一轮的 session id 和同一个对话目录；运行中发的消息送进正在跑的子进程并有送达事件，不开新一轮；取消后能接着发下一条；同一时间只跑一个；单次运行出现在对话列表里且不能追问；接口输出里没有连接串。
3. **评测配置不变**（pytest，和 `tests/acceptance/test_seam_c_eval_cli.py` 同一层，另起新文件）：评测和跑榜的入口（Harbor 走 `data-agent run`，DAB 直接调 `build_options`，`evals/dab.py:417`）给 agent 的选项里没有澄清工具、不带 `resume`、不设对话的 `CLAUDE_CONFIG_DIR`，工具和权限设置和现在一致，MCP 工具清单和每个工具的参数也和现在一致（第 15、19、20 条）。这一组现在就通过，以后也必须一直通过。

### 不写验收测试的

只有卡片样式：做出原型后由人看着定。新增事件的字段测试写在现有的事件测试里，归各自的实现工单。

第一张工单（88）写上面全部验收测试，人审过合并后才开始实现工单。

## 写工单前先验证

- 我们的 CLI 2.1.301 能否在新进程、同一工作目录下 `resume` 原会话，以及 MCP 服务配置和系统提示词重新传入后的行为。SP 的注释说 2.1.280 跨目录也能找到会话（`claude_session_archive.py` 的 `_find_session_file`），我们的版本没有验证。
- 结论不成立时，退回 nao 的做法（每轮把历史重放给模型，`backend/src/services/agent.ts:629-657`），并回来改第 14 条。
- **2026-11-17 已验证，成立**（一次性探针，SDK 0.2.168 自带 CLI 2.1.301，DeepSeek，实付约 $0.08；脚本不进仓库）：
  - 设 `CLAUDE_CONFIG_DIR` 后，会话记录落在 `<config>/projects/<工作目录转换名>/<session_id>.jsonl`，不进 `~/.claude`。
  - 新进程、同一工作目录、`resume=<id>`，stdio MCP 服务和系统提示词重新传入：MCP 连上，模型不调工具就答出上一轮工具返回的值，`session_id` 不变。
  - 换一个工作目录 `resume` 同一 id 也能找到会话（记录仍只在原目录名下）。
  - 没见过的 id 用 `resume`：报 `No conversation found with session ID`，退出码 1；已存在的 id 用 `--session-id`：报 `Session ID ... is already in use`，退出码 1。和 SP 的注释一致。
  - 插话（第 15 条）：`ClaudeSDKClient` 在第一次工具调用后再调 `query()` 发第二条消息，模型在同一轮里读到并照做，只出一个 `ResultMessage`。
  - 没验证：真实 agent 的 hooks、验证者子 agent、按运行写死的路径（第 16 条）在 resume 后的表现，这些归实现工单。

## Out of Scope

- Memory；答案里的图表；编辑旧消息、重新生成、对话分叉。
- 浏览器通知、邮件提醒。
- 在对话里做 dbt（dbt 仍走现有提交表单）；新建对话时手选数据源。
- 对话总花费上限。
- 登录、多用户、权限；公网部署；在线版真跑任务（沿用 `.scratch/web-ui/spec.md`）。
- 让查数任务读 dbt 项目的模型说明当业务语义（目前只有验证者读 `target/manifest.json`，`src/data_agent/verifier_tools.py:454-456`）。
- 中文界面。
