# 43 — 诊断：同一轮发出的多个 MCP 工具调用为什么一个接一个执行

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (fable)
Branch: worktree-43-diagnose-serial-mcp-calls

## Goal

查清 agent 在同一轮里并行发出的多个 MCP 调用，为什么实际上是串行执行的，给出有证据的根因和修法建议。**只诊断，不修。**

## 已和用户定下的（2026-11-04）

- 只查原因、不改代码。查清后由人决定修不修：修就另开工单；查不清就记成已知问题，不阻塞统一评估。
- 用最小复现定位，不在服务器上跑评测。

## Authority

- May edit：`docs/research/2026-11-xx-serial-mcp-calls.md`（新建，日期用实际日期），scratchpad 里的复现脚本（不提交）
- Must not edit：`src/`、`skills/`、`evals/`、`tests/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none
- Spend cap：$0.2（只用于本地复现里必须调模型的情况；能不调就不调）

## Context

- 现象（`runs/accept-2/reading/design-audit/audit-harness.md` 第 2 条）：同一轮的一批 MCP 调用一个一个完成，间隔约 15 秒，例如 21 个调用用了 340 秒。
- 待验证的假设，至少逐条排除或证实：
  1. Claude Code 只并行执行标了只读的工具：我们的工具都没标 `readOnlyHint`。mcp 2.3.0 支持这个注解，`server.tool(..., annotations=...)`，`server.py:674,730`。
  2. MCP Python 服务端是否按顺序处理 stdio 上的请求。
  3. 同步工具都用 `anyio.to_thread.run_sync` 跑（`func_metadata.py:162-164`），线程数上限或者全局锁会不会让它们排队。
  4. 网关的锁：Postgres 网关有 `threading.Lock`（`gateway.py:112-125`）；DuckDB 网关每条语句开关一个连接。
- 打包的 Claude Code CLI 在 venv 里的 `claude_agent_sdk` 包中，是压缩过的 JS，可以用 `grep -o` 查。

## Acceptance

- [ ] 研究笔记写明：复现方法（本地起 MCP 服务，构造同一轮的多个调用）；每条假设的证据（命令和输出、源码 `file:line`）；结论（根因，或"查不清"加上已经排除了哪些）；修法建议和它的代价。
- [ ] 如果修法是加 `readOnlyHint` 这类小改动，在笔记里给出改动草稿和预期效果，但不提交到 `src/`。

## Evidence required

复现脚本的命令和输出，贴在笔记和 Comments 里。

## Stop conditions

- 要定位就必须改 `src/` 或者在服务器上跑：停下，说明需要什么。
- 本地复现不出串行。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (复现全用假 Messages API，没有调模型)
- Outcome: 根因已查明；修法（给全部工具加 `readOnlyHint`）另开工单，由人定
- Human review minutes: —

## Comments

- 2026-11-04 工单会话（worktree 43-diagnose-serial-mcp-calls，commits 2c8ce42、fa45a42）：诊断完成，笔记 `docs/research/2026-11-04-serial-mcp-calls.md`，`src/` 未动。
  - 根因：Claude Code 2.1.301 的工具队列只在工具和所有正在执行的工具都"并发安全"时才并行；对用户配置的 stdio MCP 服务器，判据是 `mayOverlap || readOnlyHint`，`mayOverlap` 对我们的 stdio 服务器为 false。我们 11 个工具都没有 annotations，于是同一条 assistant 消息里的一批调用严格串行。CLI 的调度代码和判据在笔记里从打包的 CLI 里原文引出。
  - 服务端（mcp 2.3.0 分发、anyio 40 线程 limiter、DuckDB 网关）都是并行的，已用本地复现排除：复现 A，Python MCP 客户端直连真实 `data-agent mcp-server`，4 个并发 `query_database` 墙钟 0.43 s，串行约 2.28 s。
  - 复现 B（打包 CLI + 假 Messages API + 假 MCP 服务器，3 个 3 秒 sleep）：不带 annotation 0.36→3.37 / 3.45→6.45 / 6.47→9.48；带 `readOnlyHint=True` 三个都是 0.21→3.21。
  - 复现 C（CLI + 真实服务器，4 个 `query_database`）：原样 0.30→2.87 / 2.93→4.39 / 4.40→6.04 / 6.06→7.45；内存里补上 `readOnlyHint` 后四个都在 0.14 s 开始、2.50–2.70 s 结束。
  - Postgres 网关的 `threading.Lock` 是次级限制（只看了源码，没有测）。
  - 修法草稿在笔记 §4，未应用：新建 `tool_annotations.py` 放 `READ_ONLY = ToolAnnotations(readOnlyHint=True)`，11 个装饰器改成 `@server.tool(annotations=READ_ONLY)`。注意事项：并发长扫描会提高 `dbt run` 文件锁冲突风险，建议工单 42 先合或一起合；`readOnlyHint` 也参与 CLI 的 plan/auto 模式权限判断（我们用 `dontAsk`，无影响）。
  - `make check` 通过（1445 passed）。复现脚本在 worktree 的 `.scratch/43-serial-mcp/`，按工单不提交，笔记里写了每个脚本做什么。
  - 审查第 1 轮：`da-review main` 一条 P2，标题把判据说过头（漏了 `mayOverlap`），fa45a42 修。`eng:code-review main` 无硬性违规，引用全部核实正确；判断项（草稿位置矛盾、未标注的推断、重复的风险条目、标题过度声明）都在 fa45a42 修。
- 2026-11-04 主会话：PR #38 CI 绿，Codex app 审查无意见，squash 合入 main。修不修 `readOnlyHint` 交人定（笔记 §4 有草稿；建议与工单 42 一起或之后）。
