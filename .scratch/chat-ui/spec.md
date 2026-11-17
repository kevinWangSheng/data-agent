# 对话界面：多轮追问、澄清、运行中插话，查数和 dbt 都能用

日期：2026-11-17。Status: 已对齐，待排期（实现时间另定，现在不拆工单）。

决策来源：2026-11-17 与人的 grilling（4 轮，结论逐条经人确认）。上游取证：SignalPilot `~/dev/AI/data-agent-refs/SignalPilot/signalpilot`、nao `~/dev/AI/data-agent-refs/nao/apps`（下文简称 SP、nao），以及本仓库代码。前一版界面的 spec：`.scratch/web-ui/spec.md`。

## Problem Statement

本地版 `data-agent web`（工单 79）是一个提交表单加一个 trace 查看器：填一个任务、跑完、看过程。它适合看 agent 怎么干活，但不是用户日常提问用的界面：

- 不能追问。agent 是一次性的：一次 `data-agent run` 就是一次 SDK `query()`（`src/data_agent/agent.py:1`、`:1420`），`build_options` 从不设 `resume`；`session_id` 只写进事件（`agent.py:907`）。
- 不能澄清。代码里没有澄清工具，也没有澄清事件；运行结果只有完成和基础设施错误两种（`agent.py:1456-1468`）。
- 运行中不能插话。
- dbt 跑完，改动只留在运行目录的项目副本里（`src/data_agent/web/launcher.py:300`），用户要自己去找。

求职展示也受影响：README 动图和博客视频需要一个面试官一眼能看懂的对话形态。

## Solution

在本地版上做一个对话界面，作为主入口，形态照常见 AI 对话和上游（SP 的 `web/app/chats/`、nao 的 `components/tool-calls/`）：

- 用户发消息，agent 的每一步工具调用以卡片形式实时出现在对话里，最后给出答案。
- 能追问，agent 能反问（澄清），运行中能插话。
- 查数和 dbt 两种任务都能在对话里做。
- 现有三栏运行页保留，作为每条回答的"完整视图"。
- 在线版加一段录好的真实对话回放。

## 决定

### 范围

1. 对话界面是本地版的主入口。README 动图和博客视频等它做完再录。
2. 做：多轮追问、澄清、运行中插话。不做：Memory、答案里的图表、编辑旧消息或重新生成。
3. 查数和 dbt 都能在对话里做。新建对话时由用户选类型和数据源，整段对话不变；agent 不自己判断类型。要换就新开对话。
4. 在线版（GitHub Pages，只读）加一段录好的真实对话回放，访客不能发消息。演示对话（在线版和视频共用）在 data-eng-bench 的零售库上录（Apache-2.0，803 张表，带 dbt 项目；本机 clone 里是 Git LFS 指针，约 489 MB，要先拉下来），问题自己写，不用榜单题目，不碰留出集（ADR-0004、ADR-0005）。

### 显示

5. 调用过程直接显示在对话里，照常见 AI 对话和上游做：
   - 运行中，每一步以卡片实时出现。思考默认收起；SQL 带高亮和结果表；dbt run 带输出；验证者带 PASS/FAIL 和逐项结论；被拒绝的调用有标记。
   - 完成后卡片留在对话里，答案和结果表在最后；步骤多时按阶段分组，可以折叠和展开。
   - 每条回答可以打开现有的三栏运行页看完整视图。
   - 卡片样式、事件折叠尽量复用现有运行页的组件。具体样式做出可点的原型后由人看着定。
6. 历史列表改成对话列表。命令行跑出来的单次运行显示成只有一轮的对话（沿用 `.scratch/web-ui/spec.md` 用户故事 33）；旧事件文件照样能回放（用户故事 39）。
7. 对话里显示这段对话的累计花费。

### 行为

8. **澄清**照 nao（`docs/plan.md` §3 已定的做法）：
   - agent 调一个提问工具，给一个问题加 2–5 个选项，这一轮随即结束（nao：`shared/src/tools/clarification.ts:3-24`、`backend/src/agents/stop-conditions.ts:21-24`）。
   - 用户点的选项作为下一条普通消息发出（nao `frontend/src/components/tool-calls/clarification.tsx:51`）。不用 SP 那种挂起运行等待回答的状态。
   - 查数和 dbt 都能用。只在歧义会改变结果时才问；不问就把假设写进答案。
   - 跑榜时移除这个工具。
9. **运行中插话**照 SP：
   - agent 运行时可以继续发消息，消息直接送进正在跑的会话，不打断它，模型下一步就能读到；对话里标出"已送达 agent"。
   - SP 的做法是在活着的 SDK 客户端上再调一次 `client.query()`，用 stream-json 的 user 消息，带 uuid 用于对账（`notebook-server/signalpilot/_server/ai/claude_agent.py:355-408`；接口 `api/endpoints/standalone_chat_cancel.py:28`；网关转发 `gateway/gateway/standalone_chat/worker_events.py:328`）。
   - 取消保留。被取消的一轮标成"已取消"，之后可以接着发下一条。
10. **dbt 按后台作业处理**：一轮 13–50 分钟，用户可以关掉页面，回来时看到当前阶段或结果（断线续看现有版本已支持）。不做浏览器通知或邮件。同一时间仍只跑一个任务。
11. **dbt 的验证者**核对整段对话到目前为止的全部要求（每一轮的消息和插话），检查的改动范围是这一轮开始以来的改动。
12. **dbt 结果的交付**：
    - 每轮回答列出改了哪些文件，每个文件可以看前后对比（diff）。
    - 对话里可以下载补丁：整段对话相对原项目的全部改动，一个 `.patch` 文件，用户在自己的项目里 `git apply`。
    - 不做"一键写回原项目"。
13. **花费**：每轮保持现在的轮数上限和花费上限；不设对话总上限。

### 技术

14. **多轮用 SDK 的 `resume`**，照 SP（`notebook-server/signalpilot/_server/ai/claude_agent_options.py:269-273`）：
    - 每段对话有自己的 `CLAUDE_CONFIG_DIR` 作为会话存档目录，不写进用户本人的 `~/.claude`。现在 `sdk_env()` 没设它（`agent.py:708-735`），推断记录落在 `~/.claude`，未验证。
    - SDK 把记录存在 `<config>/projects/<按工作目录转换的名字>/<session_id>.jsonl`（`claude_agent_sdk/_internal/sessions.py:104-145`）。
    - SP 的经验：CLI 拒绝对已存在的 id 用 `--session-id`、对没见过的 id 用 `--resume`，要按情况选 flag（`claude_agent.py:127-160`）。
    - 后台任务保持关闭（`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`，`agent.py:726`），SP 也因为它会污染下一次 resume 而关掉。
15. **agent 从一次性 `query()` 换成 `ClaudeSDKClient`**（`claude_agent_sdk/client.py:272` `query()`、`:308` `interrupt()`），用于插话。改动碰到 agent 的核心循环；按 ADR-0008 Update 2026-11-04 的规则，确定性修改用单元测试加单题真实运行验证，评测行为不能变。
16. **一段对话一个工作目录**：
    - 查数是一个空目录。
    - dbt 在对话开始时复制一次项目（DuckDB 数据源连库文件一起复制，沿用 `launcher.py:263-330`），后续每一轮在这份副本上改。
    - Postgres 数据源仍直接写配置的库，记为已知限制：多段对话会在同一个库里留下状态。
    - 首条提示词、MCP 服务参数、快照和工具账本的临时目录都写死了本次运行的路径（`agent.py:988-1039`、`:1359-1366`）。工作目录按对话固定后，要确认每一轮的这些路径和会话历史里的一致。
17. **存储用文件，不引入数据库**：
    - 一条用户消息对应一次运行、一个事件文件，沿用现有格式。
    - 一段对话一个目录，加一个索引文件把各轮串起来。
    - 事件新增：澄清事件、插话送达事件；每个事件文件记下所属对话和第几轮。
18. **每轮重建的状态要按对话处理**：轮数预算、花费、验证者的派发计数和 Stop hook 状态现在都按单次运行新建（`agent.py:1342-1347`，`src/data_agent/verifier_v2/hooks.py`）。resume 后这些归零，但会话历史里还留着上一轮的记录。实现时逐项定哪些按轮、哪些按对话。
19. **评测和跑榜仍走单次运行**：Harbor 和 DAB 的入口不变；跑榜时关闭澄清和插话。

## 写工单前先验证

- 我们的 CLI 2.1.301 能否在新进程、同一工作目录下 `resume` 原会话，以及 MCP 服务配置和系统提示词重新传入后的行为。SP 的注释说 2.1.280 跨目录也能找到会话（`claude_session_archive.py` 的 `_find_session_file`），我们的版本没有验证。
- 结论不成立时，退回 nao 的做法（每轮把历史重放给模型，`backend/src/services/agent.ts:629-657`），并回来改第 14 条。

## Out of Scope

- Memory；答案里的图表；编辑旧消息、重新生成、对话分叉。
- 浏览器通知、邮件提醒。
- 一键把 dbt 改动写回原项目。
- 对话总花费上限。
- 登录、多用户、权限；公网部署；在线版真跑任务（沿用 `.scratch/web-ui/spec.md`）。
- 让查数任务读 dbt 项目的模型说明当业务语义（目前只有验证者读 `target/manifest.json`，`src/data_agent/verifier_tools.py:454-456`）。
- 中文界面。
