---
status: proposed
---

# Agent 骨架：Claude Agent SDK 独立 CLI，经 Harbor 接榜

需要在 (a) Claude Agent SDK 独立 CLI 与 (b) Claude Code/Codex 插件之间选一个骨架。取证（`docs/research/2026-09-21-harbor-signalpilot-dab.md`）表明：Harbor 接自定义 agent 只需继承 `BaseInstalledAgent` 实现 `install()`/`run()`，不需要 agent 镜像；SignalPilot 自己的 `benchmark/` 也正是 `claude-agent-sdk` 的 `query()` + MCP stdio + 插件 skills 这个形态，只是绑死了它的网关且不可插拔。选 (a)：它就是对标物的形态，且 skills 不依赖宿主，消融与"脱离榜单接任意 Postgres + dbt"都能干净成立。

## Consequences

- 一期榜单 = data-eng-bench（Harbor 原生，`DB_TYPE=duckdb`）+ DAB（自带 runner，提交 JSON + 轨迹）。
- LiveSQLBench-CLI 降二期：它 vendor 旧版 Harbor 0.1.45、API 不同、需打镜像、提交走 Google Doc。
- SignalPilot `benchmark/` 不复用，只借 `evaluation/comparator.py`。

## Open question（待议，2026-09-21）

模型厂商与 harness 未定：Claude Agent SDK 官方只支持 Claude（Anthropic API / Bedrock / Vertex / Foundry），`ANTHROPIC_BASE_URL` 接非 Claude 模型明确不受支持。若成本不可接受，备选是厂商无关 harness（如 BIRD-Interact-ADK 的 Google ADK + LiteLLM）。在第一条 tracer bullet 前、拿到 data-eng-bench 子集的实测 token 数后再定。受影响的只有 skill/子 agent 的加载方式与 Memory/session；内容层不受影响。

## Update 2026-10-05：骨架不换，模型可换

不换 harness，把模型做成配置项：Claude Agent SDK 支持用 `ANTHROPIC_BASE_URL` 指向兼容 Anthropic 接口的端点（DeepSeek `https://api.deepseek.com/anthropic`、智谱 `https://api.z.ai/api/anthropic`，或 LiteLLM 转发 GPT 等模型）。默认：开发集迭代与留出集消融用 DeepSeek（`deepseek-flash`；不够强再换 `deepseek-v4-pro`），Claude 只在预算允许时用于最终全量。消融结论只在同一模型内比较，换模型不影响口径。

- 价格（2026-10-05 官方页面）：Sonnet 5.5 $2 / $10，缓存读 $0.20；deepseek-flash $0.15–0.30 / $0.6–1.2，缓存读 $0.003–0.006；deepseek-v4-pro $0.66–1.32 / $1.98–3.96（单位：每百万 token，依次为输入 / 输出；DeepSeek 闲时半价）。
- 风险：Anthropic 官方不支持非 Claude 模型。DeepSeek 文档写明忽略服务端 `mcp_servers` 参数；本项目的 MCP server 由 SDK 在本地运行、以普通工具定义发给模型，推断不受影响，但**未确认**，由工单 01 验证。Skill 与子 agent 由 SDK 在本地处理，同样待 01 实测。
- 不选 pi：它支持多家模型，但 MCP 2026-10-01 才进核心，子 agent 要自己写扩展，也没有任何对标 data agent 用它，违背"照上游抄"。
- DAB 的 `common_scaffold` 本身就是 OpenAI 兼容接口，跑 DAB 不受影响。

## Update 2026-11-17：本地对话改用 ClaudeSDKClient，评测仍是单次运行

本地版要做对话界面（`.scratch/chat-ui/spec.md`），需要多轮追问和运行中插话，一次性的 `query()` 两样都做不到。

- 多轮：用 SDK 的 `resume` 续接会话，照 SignalPilot（`notebook-server/signalpilot/_server/ai/claude_agent_options.py:269-273`）。每段对话一个 `CLAUDE_CONFIG_DIR`，不写进用户的 `~/.claude`。
- 插话：agent 改用 `ClaudeSDKClient`，运行中在同一个客户端上再调 `query()` 送入用户消息，照 SignalPilot（`claude_agent.py:355-408`）。
- 评测和跑榜的入口不变，仍是一次任务一次运行，关闭澄清和插话。换调用方式按 ADR-0008 Update 2026-11-04 验证（单元测试加单题真实运行），评测行为不能变。
- 写工单前先实测：CLI 2.1.301 能否在新进程里 `resume` 原会话。不成立就改用 nao 的做法（每轮重放历史），回来更新本节。

### 实现时发现的事实（工单 90，SDK 0.2.168 / CLI 2.1.301）

- 所有运行（单次运行、Harbor、DAB、对话的每一轮）都走同一个 `agent.query()`，底下换成 `ClaudeSDKClient`：连上、发一条提示、读到这一轮的 `ResultMessage` 为止，然后断开。原来的一次性 `query()` 在结果之后还会读到 CLI 报"idle"为止；后台任务关着时一条提示只出一个结果（探针已验证），所以推断两种读法得到的消息相同（由主会话的单题真实运行核对）。断开时 SDK 先关 stdin、等 CLI 写完会话记录（最多 5 秒）再终止它。
- 续接后的花费：CLI 在会话记录里写 `cost-state` 条目（后写的为准），`resume` 时把它恢复成会话的起点，所以续接那一轮 `ResultMessage` 的 `total_cost_usd` 和 `modelUsage` 是从第一轮起的累计；CLI 自己的 `--max-budget-usd` 检查会减掉恢复的部分（读自 CLI 2.1.301 的程序包，未用真实运行核对）。所以每轮的 `result` 事件减掉开跑前从会话记录读到的那份，只记本轮；减掉的数记成 `restored_sdk_cost_usd`；结果比恢复的还少时（说明 CLI 没恢复）原样记下，另写一条 `source: "spend"` 的 error 事件。
- 设了 `CLAUDE_CONFIG_DIR` 后，`setting_sources=["user"]` 读的是对话自己目录里的设置（一开始是空的），不再读用户 `~/.claude` 里的；评测和单次运行不设它，照旧。

## Update 2026-11-27：产品打开 Skill 工具、使用者的 MCP 和本地插件

见 ADR-0011。"Skills come from the prompt"（`skills=[]`、`strict_mcp_config=True`、不加载插件）只留给评测迁移第一步；产品运行经 SDK 的 Skill 工具按需加载，`preload` 的 Skill 照旧进系统提示词，使用者的 Skill 目录、MCP、本地插件都能加载。不读本仓库 `.claude/` 不变。
