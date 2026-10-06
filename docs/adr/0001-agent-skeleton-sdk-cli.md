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
