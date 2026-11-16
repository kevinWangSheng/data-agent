# 85 — 网页服务停止时取消正在跑的运行，即使有浏览器开着 SSE

Status: ready-for-agent
Blocked by: 78（done）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

Ctrl+C（SIGINT）或 SIGTERM 停 `data-agent web` 时，它启动的运行一定被取消、子进程不留下，不管有没有浏览器开着那条运行的 SSE。

## Authority

- May edit: `src/data_agent/cli.py`（只动 `serve_web`）、`src/data_agent/web/`（只在必须时，例如让 `stream()` 在关闭时结束）、`tests/test_web_submit.py` 或新的 `tests/test_web_shutdown.py`
- Must not edit: 验收测试（`tests/acceptance/`、`web/src/**/acceptance/`、`tests/fixtures/web/`）、`web/`、`evals/`、`src/data_agent/` 里 web 以外的模块、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0（测试和真实运行用假的模型地址或假的 run 命令）

## Context

- 起因：PR #85（工单 78）Codex GitHub app 对 943358e 的 P2，主会话核实属实，记在工单 78 的 Comments。uvicorn 先等活着的响应结束再发 lifespan shutdown；`stream()` 要等运行结束才关；`serve_web` 调 `uvicorn.run(app, host=WEB_HOST, port=port)`（`cli.py:182`）没给 `timeout_graceful_shutdown`，所以 `launcher.shutdown`（`app.py` 的 lifespan）要等运行跑完才执行，期间继续花模型费；第二次 Ctrl+C 强退不走 cleanup，单独 session 的子进程留下。
- 修法由本工单定，二选一或都做：给一个短的 `timeout_graceful_shutdown`（几秒）保证 lifespan cleanup 会执行；或在连接排空前先取消运行（例如关闭信号让 `stream()` 结束）。照 uvicorn 文档或源码选，写明依据。
- 只停本服务启动的运行，不动别的进程（`launcher.shutdown` 现有规则）。

## Acceptance

- [ ] 测试：起真的服务（子进程），提交一条用假 run 命令的长运行，开着它的 SSE 发 SIGINT；服务在限定时间内退出，运行被记为取消，子进程不在了。修前失败
- [ ] 没有 SSE 时的停止行为不变（现有测试通过）
- [ ] `make check` 通过

## Evidence required

`make check` 输出；真实运行一次：`data-agent web` 起服务、提交一条运行、`curl -N` 开着它的事件流、Ctrl+C（或 SIGINT 到确切 PID），贴出退出用时、`GET /api/runs`（重启后）里那条的状态，以及确认子进程不在的命令输出。

## Stop conditions

- 要改验收测试或网页前端才做得到。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-16 主会话：人定开本工单并派发。
