# 78 — 后端：提交和取消运行

Status: ready-for-agent
Blocked by: 77
Spec: ../spec.md
Claimed by: claude (opus-5-5)
Branch: worktree-78-web-backend-submit-cancel

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
- 配置文件可选（2026-11-15 人定，spec "本地运行的数据源"）：
  - 只在命令行明确给了配置文件参数时才读；没有默认路径，不自动读 `~/.config/data-agent/`。参数名由本工单定，格式用 TOML（照 `src/data_agent/configs/*.toml`），写法写进 `docs/setup.md` 或 README 的本地启动一节。
  - 不给配置文件：`/api/options` 的数据源列表为空，列出运行、读事件、SSE 照常可用（`tests/acceptance/test_web_api.py:494-496` 就这样启动）；提交运行返回明确的 4xx（`detail` 说明没有配置数据源），不启动任何进程。
  - 启动时校验 TOML：种类合法；DuckDB 要有文件路径，Postgres 要有环境变量名；dbt 项目目录给了就要存在；dbt 任务提交时数据源必须配了 dbt 项目目录。
  - 连接串不出现在 TOML、HTTP 响应、子进程参数和事件里。每条规则都要有单元测试。
- `tests/acceptance/test_web_api.py` 里工单 77 留下的 `TICKET_77` 定义已没用，按 AGENTS.md 的机械清理例外可以顺手删；不删也行。
- 工作目录：每次运行一个目录。dbt 运行先复制数据源的 dbt 项目目录（不复制 `target/` 和 `logs/`），DuckDB 数据源连库文件一起复制；查数运行用空目录。
- 取消：只对本服务启动、还在跑的子进程，按记录的确切进程号发终止信号；不按名字找进程，不发给进程组以外的东西。取消后的状态要能被列出运行的接口看到（子进程没写 `result` 事件就退出时，由服务记下"已取消"）；读事件的接口只返回事件，不另带状态（2026-11-15 主会话按 spec"被取消的读后端给的状态"定：前端的 `RunNotes.cancelled` 从运行列表取）。
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

- Attempts: 1
- Review rounds: 2
- Model spend: $0.0082 (main-session real run, price table; the cancelled run wrote no result, cost unrecorded) (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: returned ×1
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-15 claude-opus（worktree-78-web-backend-submit-cancel：6854ac1、f23d768、2866f60；PR #85）：
  - 设计：`data-agent web --data-sources FILE`（TOML，`[sources.<name>]`，kind = duckdb|postgres，path / env，可选 dbt_project；只在给了时读，无默认路径），写进 `docs/setup.md`。每条运行 `<runs-dir>/<id>/`（事件文件 + `web-run.json`，服务自己的记录）和 `<id>/workspace/`（查数为空目录；dbt 复制项目，不含 target/、logs/，符号链接复制成目标内容，连同 DuckDB 文件）。运行在自己的 session 里启动；取消对记录的 pid 及其领头的进程组发信号，10 s 后 SIGKILL。没写 result 就退出的记成 cancelled（本服务停的）或 failure。
  - make check：2231 passed, 1 skipped, 39 deselected, 4 xfailed；ruff、format、basedpyright 0 errors；web vitest 通过。
  - agent 自己的无模型检查（假的 `DATA_AGENT_API_KEY` 指向不应答的本地 socket，$0）：提交 201、再提交 409、dbt 任务配了没 dbt_project 的数据源 400；取消前进程组 3 个进程（data-agent run、claude CLI、MCP server），取消 200 后组立即为空，再取消 409；服务 SIGINT 时在跑的组为空、web-run.json 记 cancelled。真实运行发现并修好：起初 claude CLI 活过了取消响应，现在取消等整组退出。
  - 审查 1 da-review（Codex）5 条，f23d768 全修且各带测试：[P1] 没 result 的运行卡在 running / 没事件的运行从列表消失 → web-run.json 记录；[P1] 运行退出后组里残留进程 → 退出时和关服务时停掉；[P2] 复制时保留符号链接 → 复制目标；[P2] target/ 下的 DuckDB 文件没复制 → 复制；[P2] 取消和自然结束竞争 → 取消返回结束时的那行。
  - 审查 2 eng:code-review：Standards 无硬违规，2866f60 修（重复的数据库参数分支；给验收测试要的 DataSource 再导出加注释）；不修：status 用字符串、记录用 dict（JSON 行就是接口契约）；四个参数分开传（只有一条调用路径）；launcher.py 290 行。Spec 无阻塞项。
  - 工单外的增加（主会话接受）：没 result 的运行记 failure；提交时再查 DuckDB 文件存在、环境变量已设置；拒绝未知的 TOML 表和键；SIGKILL 兜底。
  - 已知限制：服务被强杀时不更新 web-run.json，那条一直显示 running；验收夹具不在自己的 session 里起运行，进程组清理只由 `tests/test_web_submit.py` 覆盖；macOS 上全量测试时 `killpg(pgid, 0)` 在组员还活着时报过 EPERM（原因未确认），只把"no such process"当作组已结束。
  - 按 AGENTS.md 机械清理例外删了 `TICKET_77` 定义。
- 读事件的接口不带 cancelled（agent 交主会话）：主会话判定接受。spec"被取消的读后端给的状态"由运行列表满足，前端 `RunNotes.cancelled` 从列表取；Context 第 31 行已改成这样，工单 79 加了说明。
- 真实运行（主会话，2026-11-15，分支 2866f60，脚本 scratchpad `t78-real.sh`）：`data-agent web --runs-dir runs/t78-real/web --port 18778 --data-sources runs/t78-real/sources.toml`（一个 DuckDB 源 shop）。
  - `GET /api/options` → `{"data_sources":["shop"],"task_types":["query","dbt"],"configs":[…6 个]}`
  - `POST /api/runs`（README 快速上手查数，no-verifiers）→ `{"id":"20261116T054032Z-3fd17f"} HTTP 201`；轮询到 `success`，24.9 s，$0.0082，79 条事件，answer "Gold — 710.00 …"
  - 再提交 → 201 `20261116T054102Z-dd666a`；8 s 后第三次提交 → 409 "run … is still going; one run at a time"
  - `POST …/dd666a/cancel` → 200，`"status":"cancelled"`；再取消 → 409 "… is not a run this server is running"
  - `GET /api/runs` → dd666a cancelled，3fd17f success 0.00817692；`web-run.json` 记 cancelled；SIGTERM 停服务 "Application shutdown complete"；之后没有残留进程。
- **退回第 1 轮（PR #85 上 Codex GitHub app 的两条 P2，主会话 2026-11-15 核实属实）**：
  1. `--runs-dir` 在某个 `dbt_project` 里面时，`prepare_workspace` 的 `copytree`（`src/data_agent/web/launcher.py:272-277`）只跳过 `target/`、`logs/`，新建的工作区就在被复制的树里，会一层层复制进去，直到递归或路径过长报错。修法：启动时校验，`--runs-dir` 和任何一个数据源的 `dbt_project` 互相包含就拒绝启动并说明原因；加测试。
  2. DuckDB 文件在 `dbt_project` 外面时，副本放到工作区根下（`launcher.py:285-291`），但 dbt 从工作区按 `profiles.yml` 的相对路径找库：例如 `path: ../shop.duckdb` 指向 `<run-dir>/shop.duckdb`，网关拿到的是 `<run-dir>/workspace/shop.duckdb`，dbt 和只读工具连的不是同一份（dbt 还可能新建一个空库）。保留相对位置又可能跑出运行目录（`../../data/x.duckdb`）。修法（主会话定，按"原目录不动、每条运行用自己的副本"）：配了 `dbt_project` 的 DuckDB 数据源，库文件必须在 `dbt_project` 里面，否则启动时拒绝并说明原因；去掉"在项目外的复制到工作目录根下"这条路径；`docs/setup.md` 同步改；加测试。
  - 在原分支 `worktree-78-web-backend-submit-cancel` 上接着修；修完重跑 `make check` 和两道审查。真实运行由主会话复跑。
