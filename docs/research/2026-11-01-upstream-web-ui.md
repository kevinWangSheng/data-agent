# 上游网页界面取证（2026-11-01）

这是做网页界面前的上游核查（plan §2 要求"真正动手时再核一次"）。

- 来源：SignalPilot `f5eee22`（2026-09-28）、nao `5bde830`（2026-10-09），都在 `~/dev/AI/data-agent-refs/`。
- 读法：只读子 agent 读源码，下面的路径和行号来自它的报告。
- 标"未确认"的项没有核实。

## 与 plan 不符的三点

1. **SignalPilot 没有用 `@assistant-ui/react`。** `web/package.json:19` 声明了 0.15.1，但源码里没有任何 import，聊天界面是手写的。plan §2、§5 写的"聊天组件用 `@assistant-ui/react`"不成立。
2. **nao 的澄清和图表代码不在 plan 写的目录里。** 不在 `chat-messages/`，而在 `apps/frontend/src/components/tool-calls/`（`clarification.tsx`、`display-chart.tsx`）。
3. **我们的 agent 没有澄清事件。** 澄清的工具和交互在 spec 里写的是"预留给二期界面"。

## 我们的事件

- 写入方式：`src/data_agent/events.py:93` 每行写一条 `{seq, type, ts, ...字段}`，字段平铺，没有 payload 包装。
- 事件类型：
  - `version`、`status`（started）
  - `text`、`thinking`、`tool_start`、`sql`、`tool_end`
  - `error`（来源分 model、sdk、spend、dab_load）
  - `result`（subtype、num_turns、cost_usd、sdk_cost_usd、errors）
  - `verifier_start`，`verifier_end`（含解析出的 checks 和 report）
  - `dab_load`
- 文本按整块写入，不是逐字增量（delta）。

## SignalPilot web（`signalpilot/web`）

- **许可**：仓库 `LICENSE` 是 Apache-2.0，但版权行是没填的模板（第 189 行）。web 下的文件都没有版权头，也没有 NOTICE。
  - 复制时要在我们的 `NOTICE` 里注明 SignalPilot-Labs。
  - 避开 `web/dashboard-renderer/`，它是 MIT 的 Lightdash 代码。
- **技术栈**：next 16.3.5、react ^19.2.4、tailwindcss ^4、zustand 5.0.14、swr ^2.4.1、recharts ^2.15、vitest ^4.1。
- **页面**：
  - `app/chats/[conversationId]/page.tsx` 包着 `components/chat/standalone-data-chat-loader`。
  - `app/evals/page.tsx` 由 RunsList、RunDetail、TaskDetail 组成，用 SWR 取数。
  - `app/evals/accuracy/page.tsx` 用 recharts 画准确率历史（:24、:90-156）。
- **聊天传输**：
  - 前端 `lib/api/standalone-chat.ts:405-438` 用 fetch 请求 `GET /api/chat/runs/{id}/events?after=N`，手动解析 SSE。
  - 事件结构是 `{run_id, sequence, type, payload, created_at}`（:135-176），约 35 种。
- **渲染**：
  - `lib/chat-run-steps/fold-steps.ts:99-230` 把事件折叠成 RunStep：按 `tool_call_id` 配对工具调用，按 `parent_tool_call_id` 嵌套子 agent。
  - 折叠结果由 `components/chat/run-timeline/*` 和 `components/chat/tool-cards/*` 渲染（`registry.ts:1-60`，校验结果用 `validation-card.tsx`）。
  - `sql-trace-panel.tsx` 列出所有 SQL 执行。
- **回放**：`lib/chat-replay.ts`（2/5/10 倍速，:25；`buildReplaySchedule` 在 :109），配合 `chat-replay-view.tsx`、`replay-controls.tsx`。
- **评测轨迹**：
  - `app/evals/_components/TranscriptView.tsx:30-80` 把 Claude stream-json 解析成若干轮。
  - 每一轮由 `turns.tsx` 渲染：SqlBlock :29、SessionCard :95、AgentTurn :119、ThinkingTurn :128、ToolCallTurn :143、ToolResultTurn :191、ResultFooter :206。
  - `badges.tsx` 有 VerdictBadge、CheckChips（:18-48）。

## nao（`apps/frontend`）

- **许可**：Apache-2.0，带 `/* @license Enterprise */` 头的文件除外，那些不能复制。`components/chat-messages/`、`tool-calls/`、`ui/` 下没有这种头。
- **澄清**：
  - `tool-calls/clarification.tsx`（142 行）：一个问题加 2–5 个点选项。
  - schema 在 `apps/shared/src/tools/clarification.ts`。
  - 它依赖 Vercel `ai` SDK 和 nao 的 context，逻辑可以移植，但不能直接放进来用。
- **图表**：`display-chart.tsx`（28 KB）用 recharts，和 tRPC、react-query 绑在一起，只作参考。
- **技术栈**：Vite + TanStack Router + `@ai-sdk/react`，不是 Next.js。

## 后端

- **SignalPilot**：
  - `gateway/gateway/api/chat_routes/runs.py:141-213` 用 FastAPI 的 `StreamingResponse`：从库里读出 `sequence > after` 的事件，每秒轮询一次，每 15 次发一次 keepalive。
  - 每条 SSE 帧是 `id: seq / event: type / data: json`。
  - 运行结束、失败、取消或等待用户时，推送完剩余事件就停。
  - 存储用 `GatewayChatRun` / `GatewayChatRunEvent` 两张表（`db/models/chat.py:139,186`）。
  - 评测相关表在 `db/models/evals.py:56-175`，评测接口在 `api/eval_runs.py:77-496`。
- **nao**：Fastify 的 `POST /api/agent` 返回 AI SDK 的 UI message stream（基于 SSE），存储用 Drizzle，支持 SQLite 和 Postgres。
- **对我们的适用性**：SignalPilot "先按游标 `after` 补发、再持续推送"的模式，可以直接套在我们每次运行一份的 JSONL 上：按 `seq` 跟读文件。plan §5 的 SQLite 运行索引起到 `GatewayChatRun` 的作用。

## 事件与组件的对应

| 我们的事件 | 上游组件 |
|---|---|
| `version` / `status` | SignalPilot `turns.tsx` SessionCard |
| `text` | AgentTurn / Markdown。实时显示的组件按增量设计，我们是整块写入 |
| `thinking` | ThinkingTurn |
| `tool_start` + `tool_end` | ToolCallTurn / ToolResultTurn；更丰富的版本是 fold-steps → StepRow / tool-cards |
| `sql` | SqlBlock 或 `chat-code.tsx` + `lib/pretty-sql.ts`；SQL 列表用 `sql-trace-panel.tsx` |
| 查询结果 | `tool-cards/cards/data-table.tsx`、`table-card.tsx` |
| `verifier_start` / `verifier_end` | `run-timeline/subagent-row.tsx` 显示报告，`validation-card.tsx` 或 `badges.tsx` 的 CheckChips 显示各项检查 |
| `error` | `chat-error-details.tsx` |
| `result` | ResultFooter |
| 澄清 | 我们还没有这个事件。界面参考 nao `clarification.tsx`；暂停与恢复的流程参考 SignalPilot 的 `clarification_requested` + `POST /runs/{id}/clarification` |
| `dab_load` | 上游没有，用通用的步骤行显示 |

评测页的要求：
- 开发集报告（`evals/report.py:185-195`）可以对应 RunDetail、badges 和准确率图表。
- 留出集只显示汇总分数，不出现逐题行，也不出现回放链接（ADR-0004）。

## 可复制与只作参考

- **可复制**（Apache-2.0，在 NOTICE 里注明 SignalPilot）：
  - `app/evals/_components/turns.tsx`、`Markdown.tsx`、`badges.tsx`
  - `TranscriptView.tsx` 的解析逻辑，需要改成读我们的事件
  - `components/chat/chat-code.tsx`、`lib/pretty-sql.ts`、`components/chat/replay-controls.tsx`
  - `lib/chat-run-steps/` + `run-timeline/` + `tool-cards/` 也许可以整组复制，依赖链有多深未确认
- **只作参考**：
  - SignalPilot 的 `standalone-data-chat*`、`use-standalone-chat-*`、`lib/standalone-chat-state.ts`、`lib/chat-replay.ts`、`lib/api/*`，以及 gateway 的 `runs.py`（只借它的模式）
  - nao 的 `clarification.tsx`、`display-chart*`、`ui/chart.tsx`
  - 任何带 `@license Enterprise` 头的文件都不能碰

## 未确认

- `run-timeline`、`tool-cards` 通过 ChatUiContext、telemetry、artifact 牵连多少文件。
- SignalPilot 是否为这些组件写了测试。
- `GatewayChatRunEvent` 的完整列定义。
- Harbor 有没有可以借用的轨迹查看器。
