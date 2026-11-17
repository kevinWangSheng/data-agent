# 93 — 对话后端：存储、接口、子进程编排

Status: ready-for-agent
Blocked by: 89, 90, 91, 92
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`data-agent web` 有对话接口：列出对话、新建对话、发消息（空闲时开新一轮，运行中送进子进程的 stdin）、取消。对话按目录和索引文件存；单次运行显示成一轮对话、不能追问。后端接口验收测试和真模型端到端 a–d、g 转绿。

## Authority

- May edit: `src/data_agent/web/`（`app.py`、`launcher.py`、`runs.py`、`sources.py`，可新增对话模块）、`cli.py`（只 `web` 子命令）；`tests/`（新增单元测试）；`docs/setup.md` 或 README 的本地启动一节
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`；agent 的运行逻辑（`agent.py`、验证者、守卫、网关，归 89–92）
- Server access: none
- Spend cap: $0

## Context

- spec 第 6、9、13、17 条，"Testing Decisions"后端接口一组。契约（对话目录、索引、接口、`data-agent run` 参数、stdin 字段）以工单 88 合入的为准。
- 先例：`src/data_agent/web/launcher.py:40` `StartRun`、`:263-330`（工作目录）、工单 78 的取消和"同时只跑一条"、工单 85 的退出时不留子进程。
- 子进程以 stdin 管道启动；第二轮起传上一轮事件文件里的 session id（`agent.py:907` 写的那个）。
- 数据源：后端把 `--data-sources` 配置里的全部数据源交给子进程（第 20 条），连接串不出现在参数、接口输出、事件里。
- 一条用户消息一次运行、一个事件文件；现有的运行列表、读事件、SSE、三栏运行页照常能用（每条回答的"完整视图"）。
- 本工单删掉 `TICKET_93` 标记。合并前主会话跑 `make test-integration`，a–d、g 每个 3 次都过才合并（spec "判定"）。

## Acceptance

- [ ] 后端接口验收测试全部通过，`TICKET_93` 标记删掉，测试内容一字未改
- [ ] 真模型端到端 a–d、g 每个 3 次都过（主会话跑）；失败时读轨迹判定是实现问题还是模型偶发，写进 Comments
- [ ] 服务退出时不留下它启动的子进程（沿用工单 85 的测试）
- [ ] 评测配置不变一组（`tests/acceptance/test_chat_eval_unchanged.py`）一直通过
- [ ] `make check` 通过

## Evidence required

`make check` 和后端接口验收测试的输出。真实运行由主会话做（工单 agent 加载不了模型环境）：最终回复写好要跑的命令，主会话跑完把命令和真实输出抄进 Comments。主会话跑 `make test-integration`，贴每个场景 3 次的结果和花费。

## Stop conditions

- 某条接口行为要改 agent 的运行逻辑才能做到。
- 端到端某个场景反复不过，读轨迹判定是 89–92 的实现问题（退回对应工单）。
- spec 自相矛盾，或缺一个需要人定的决定。
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
