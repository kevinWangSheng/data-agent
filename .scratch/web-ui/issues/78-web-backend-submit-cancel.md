# 78 — 后端：提交和取消运行

Status: ready-for-agent
Blocked by: 77
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

通过接口提交一条运行会启动 `data-agent run` 子进程并返回运行 id；可以取消；数据源按名字从配置文件解析；每次运行有自己的工作目录；同时只跑一条。后端验收测试全绿。

## Authority

- May edit: `src/data_agent/` 的 web 服务模块和 `cli.py`；`tests/`（新增的单元测试）；`docs/setup.md` 或 README 的本地启动一节（数据源配置文件的写法）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、agent 的运行逻辑（`agent.py`、验证者、守卫、网关）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- spec "后端接口"的提交和取消，以及"本地运行的数据源、工作目录和并发"（Q38–Q40）。
- 数据源配置文件：每个数据源有名字、种类（DuckDB 文件路径，或存着 Postgres 连接串的环境变量名）、可选的 dbt 项目目录。连接串不进文件；子进程通过 `--postgres-env` 拿到环境变量名，不是连接串本身。
- 工作目录：每次运行一个目录。dbt 运行先复制数据源的 dbt 项目目录（不含 `target/`、`logs/`、`dbt_packages/` 以外的排除由实现者按现有快照代码的范围定），DuckDB 数据源连库文件一起复制；查数运行用空目录。
- 取消：只对本服务启动、还在跑的子进程，按记录的确切进程号发终止信号；不按名字找进程，不发给进程组以外的东西。取消后的状态要能被列出运行和读事件的接口看到（子进程没写 `result` 事件就退出时，由服务记下"已取消"）。
- 同时只跑一条：已有运行在跑时再提交，拒绝并说明原因。
- 提交的校验：数据源名字存在、类型和配置合法、dbt 类型的任务要求数据源配了 dbt 项目目录。
- 只用 uv。

## Acceptance

- [ ] 后端验收测试全部通过，`xfail` 标记全部删掉，测试内容一字未改
- [ ] 接口的输入输出和事件文件里都没有连接串（有测试）
- [ ] 服务退出时不留下它启动的子进程
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实运行由主会话做：用接口提交一条 README 快速上手的查数运行，再提交一条并取消，贴出命令和输出。

## Stop conditions

- 取消没法在不改 agent 运行逻辑的前提下得到一致的状态。
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
