# 43 — 诊断：同一轮发出的多个 MCP 工具调用为什么一个接一个执行

Status: needs-triage
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
