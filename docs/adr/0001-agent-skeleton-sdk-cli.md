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
