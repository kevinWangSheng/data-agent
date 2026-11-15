# 77 — 后端：`data-agent web`，列出运行、读事件、实时推送

Status: ready-for-agent
Blocked by: 73
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
