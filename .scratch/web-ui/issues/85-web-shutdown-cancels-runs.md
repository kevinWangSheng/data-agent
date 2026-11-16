# 85 — 网页服务停止时取消正在跑的运行，即使有浏览器开着 SSE

Status: done
Blocked by: 78（done）
Spec: ../spec.md
Claimed by: claude (opus-5-5), 2026-11-16
Branch: worktree-85-web-shutdown-cancels-runs

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted (PR #89; CI green, Codex no findings on 0bf080c)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-16 主会话：人定开本工单并派发。
- 2026-11-16 claude (opus-5-5)（16c8c91、0bf080c）：
  - 修法：`serve_web` 的 `uvicorn.run` 加 `timeout_graceful_shutdown=3`（`cli.py`），`web/` 没改。依据 uvicorn 0.54.0 `server.py:281-311`：收到信号先关监听，进行中的响应只设 `keep_alive=False`，然后等所有连接关闭，上限是 `timeout_graceful_shutdown`（默认 `None` = 一直等）；超时后取消剩下的请求任务，再走 `lifespan.shutdown()` → `launcher.shutdown`。SSE 只随运行结束，所以这个上限保证 cleanup 会执行。
  - 测试 `tests/test_web_shutdown.py`：子进程起真的 `data-agent web`，只把 `-m data_agent run` 换成一个写事件、再起一个工具进程的长运行替身；读到 SSE 第一帧后发信号，断言 15 秒内退出、运行和工具进程都不在、`/api/runs` 为 `cancelled`。三种：`sigint-with-stream`、`sigterm-with-stream`、`sigint-without-stream`。修前两条带流的失败（停在 `Waiting for connections to close`），不带流的通过；修后 3 passed，10.70s。
  - make check（16c8c91）：ruff、basedpyright 0 errors；pytest 2299 passed, 1 skipped, 39 deselected；vitest 64 passed。0bf080c 只改注释和测试名，之后只重跑了 ruff、format、basedpyright 和 `pytest tests/test_web_shutdown.py tests/test_web_submit.py`（31 passed）。
  - 真实运行（$0，`DATA_AGENT_BASE_URL` 指向本机一个收连接但不回的端点）：真的 `data-agent web` + 真的 `data-agent run`，提交一条，`curl -N` 开着事件流（收到 3 帧），`kill -INT` 服务的确切 PID → "server exit code 0, 3.56s after SIGINT"，curl exit 18；信号前运行的进程组有运行、模型 CLI、MCP 服务三个进程，停止后 `ps -g` 只剩表头、`ps -p` 三个都不在；重启后 `GET /api/runs` 那条为 `cancelled`。
  - da-review（Codex）：无意见。eng:code-review：Standards 无硬违规，Spec 无阻塞；0bf080c 修：`cli.py` 注释说错了"其他响应远在上限内结束"、删多余的 `took < EXIT_SECONDS` 断言、改名 `tool_pid`、`serving`。
  - 不修：`3` 没提成 `WEB_HOST` 旁的命名常量（Authority 只许动 `serve_web`）；测试 helper 和其他 web 测试文件重复（各文件都各留一份，没有共用模块）；测试里的小重复。
  - 已知限制：3 秒宽限内第二次 Ctrl+C 会让 uvicorn `force_exit`、跳过 cleanup、运行留下（原来就有，窗口从"直到运行结束"缩到约 3 秒）；超时时 uvicorn 对每个开着的流打一条 `CancelledError` 的 ERROR 日志（设计行为）；被上限截断的 submit 若还在 `check()`（拿锁之前）而 cleanup 已结束，可能在 cleanup 之后起一条运行，`check()` 只查文件和环境变量，实际极少。
- 2026-11-16 主会话：PR #89 CI 绿，Codex GitHub app 对 0bf080c 无意见，squash 合并。
