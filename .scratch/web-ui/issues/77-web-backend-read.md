# 77 — 后端：`data-agent web`，列出运行、读事件、实时推送

Status: done
Blocked by: 73
Spec: ../spec.md
Claimed by: Claude Code (opus) 2026-11-15 (filled in by the main session; the worktree session could not write the main checkout)
Branch: worktree-77-web-backend-read

## Goal

`data-agent web` 启动一个只监听本机地址的服务，能列出运行目录里的运行（包括命令行跑出来的），按游标读事件，并用 SSE 实时推送。

## Authority

- May edit: `src/data_agent/`（新增 web 服务的模块、`cli.py` 的子命令）；`pyproject.toml`（依赖）；`tests/`（新增的单元测试）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、agent 的运行逻辑（`agent.py`、验证者、守卫、网关）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "后端接口"一节的前两类接口，加返回数据源和配置列表的只读接口（数据源配置文件的读取在工单 78，这里先返回空列表也可以）。
- 应用入口的签名由工单 73 定。
- SSE 的模式照 SignalPilot（只借模式，不复制）：先补发游标之后的事件，再持续推送，运行结束后推完剩余的就关闭，空闲时发 keepalive，每帧带序号。见 `docs/research/2026-11-01-upstream-web-ui.md` 的"后端"。
- 事件文件是只追加的 JSONL，可能读到写了一半的最后一行：只返回完整的行。
- 运行概况从事件文件的头部事件和 `result` 事件取；不建库，每次扫描。
- 只监听本机地址，不提供改监听地址的参数。
- 只用 uv。

## Acceptance

- [ ] 后端验收测试里"列出运行、按游标读事件、SSE、只监听本机地址、输出里没有连接串"这几条通过，对应的 `xfail` 标记已删
- [ ] 提交和取消的验收测试仍是 `xfail`（留给工单 78）
- [ ] `make check` 通过

## Evidence required

`make check` 输出；真实运行一次：`data-agent web` 启动后用 `curl` 列出运行、读一条运行的事件、开一个 SSE 连接，贴出命令和输出（运行目录用夹具或主会话给的目录）。启动的服务按确切的进程号停掉。

## Stop conditions

- 验收测试要求的行为和 spec 矛盾。
- spec 自相矛盾或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1 (Codex run again after the fixes: clean)
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted (PR #83 → 3511df0; CI green, Codex no findings)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 实现（分支 worktree-77-web-backend-read，提交 cf19c68、c94961e、8675944；PR #83）：
  - `src/data_agent/web/runs.py`（新）：只算完整的行；列表行取前 8 行加从文件尾读的最后一行；读不了的文件只跳过那一条并记 warning；会跑出运行目录的 id 返回 404。
  - `src/data_agent/web/app.py`：`GET /api/options`、`GET /api/runs`、`GET /api/runs/{id}/events`（`?after=` 返回 JSON；`Accept: text/event-stream` 时 SSE：先补发 `Last-Event-ID` 之后的，再每 0.25 s 推新事件，静默 15 s 发 keepalive，`result` 后关闭）。数据源列表暂为空（工单 78）。
  - `cli.py`：`data-agent web --runs-dir DIR [--port N]`，无 `--host`。`pyproject.toml`/`uv.lock` 把 `uvicorn` 加成直接依赖（锁文件 2 行；原本经 `mcp` 已装）。
  - 测试：`tests/acceptance/test_web_api.py` 只删了 7 个 `@TICKET_77` 标记；新增 `tests/test_web_read.py` 8 条，同一 HTTP 接缝。
- `make check`：ruff 通过，basedpyright 0 errors，"2195 passed, 1 skipped, 39 deselected, 10 xfailed"（基线 2180 passed、17 xfailed），vitest 43 expected fail。7 条 77 验收测试通过，6 条 78 的仍 xfail。
- 真实运行（运行目录用夹具 `query-shop`、`new-fields` 搭）：`data-agent web --runs-dir runs/77-demo --port 18777` → "Uvicorn running on http://127.0.0.1:18777"；`curl /api/runs` 列出两条（query-shop 是旧文件，`task` 为 null）；`curl ".../events?after=41"` → seq 42 answer、43 text、44 result；`curl -sN -H "Accept: text/event-stream" -H "Last-Event-ID: 55"` → `id: 56`…`id: 58` result 后服务端关闭（curl exit 0）；`/api/options` → `data_sources: []`、task_types、6 个 configs；`kill -TERM 97642` → "Application shutdown complete"。
- 审查：
  - da-review 第 1 轮：P2 跟随符号链接的运行目录。不修：服务只听本机、单用户，放符号链接要本地写权限；docstring 已写明 id 检查防什么。
  - eng:code-review（Standards + Spec）第 1 轮已修：一个坏文件让整个列表失败 → 跳过那条（加测试）；列表读整个文件 → 只读头和最后一行（加测试）；事件读取重复 → `RunsDir.read`；改名 `summarize_run`、`first_by_type`。
  - 不修：`serve_web` 已带真实 `start_run`（`create_app` 要求，78 加 POST 前不会被调用）；`TASK_TYPES` 是任务种类第三份拷贝（另一份在 `agent.py`，本工单不能改）；列表行是 dict 不是类型化记录（验收测试已定形状）；验收文件里的 `TICKET_77` 常量已无用（只允许删标记，由 78 或主会话删）。
  - da-review 修复后复跑："No actionable correctness issues."
- 已知限制（给 78）：没有 `result` 事件的文件显示 `running`，SSE 不关闭；按 spec 如此，进程退出或取消时关闭属于 78。
