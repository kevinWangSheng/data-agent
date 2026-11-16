# 78 — 后端：提交和取消运行

Status: in-review
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

- Attempts: 3
- Review rounds: 4
- Model spend: $0.0082 (main-session real run, price table; the cancelled run wrote no result, cost unrecorded) (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: returned ×2
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
- 2026-11-16 claude-opus，退回第 1 轮修复（83f9ed1、431c8b3、948211c）：
  - 退回 1：`--runs-dir` 和任何 `dbt_project` 互相包含（含相同）就拒绝启动（`sources.check_layout`，`Launcher.__init__` 调；`create_app` 抛 ValueError，`serve_web` 转 `parser.error`）。测试覆盖三种重叠，另有一条 CLI 测试。
  - 退回 2：配了 `dbt_project` 的 DuckDB 源，库文件必须在项目里，否则拒绝启动；删了"项目外的文件复制到工作目录根"那条路径，`docs/setup.md` 同步，带测试。是否在项目里按写的路径判断、不跟符号链接（431c8b3），和 `copytree` 实际复制的位置一致，两种都有测试。
  - Codex 另报 2 条 P2（核实属实，948211c 修，各带测试）：`kind` 是数组或表时 TypeError 崩溃 → 先查是字符串，报配置错误；项目里的目录链接指向 runs 目录（或其上下级）时 `copytree` 会把工作区复制进自己 → 复制时发现就拒绝这次提交，400，删掉 run 目录。
  - make check：2240 passed, 1 skipped, 39 deselected, 4 xfailed；ruff、format 通过；basedpyright 0 errors；vitest 通过。主会话核对验收文件只删了 `xfail` 标记和 `TICKET_77`/`TICKET_78` 定义。
  - agent 的真实运行（$0）：runs 目录和项目重叠 → exit 2；DuckDB 在项目外 → exit 2；`kind = ["duckdb"]` → 配置错误；项目里链接指向 runs 上级时提交 dbt 运行 → HTTP 400，runs 目录为空。
  - 主会话在 948211c 上重跑 `t78-real.sh`：`GET /api/options` 正常；提交查数运行 201 → success，"Gold has the highest total amount of completed orders: 710.00"，69 条事件，$0.0067；第二次提交 201，期间第三次 409 "one run at a time"；取消 200 cancelled；再取消 409；`GET /api/runs` 显示 cancelled 和 success 两条；服务 SIGTERM 正常退出，没有残留进程。
  - eng:code-review：Spec 两条退回都满足；Standards 无硬违规，web 命令测试重复代码抽成 `web_command`。不修：`prepare_workspace` 的 assert 只写明 `check_layout` 已跑的前提；校验分在解析 TOML 和 `check_layout` 两处（后者需要 runs 目录）；`where` 标签重复、改名、测试准备代码重复属小取舍；runs 在 `proj/target/` 下也拒绝，照退回原文。
  - agent 对 948211c 的 da-review 复审跑满 60 分钟没结论，以 PR 上 Codex app 的审查为准。
  - 已知限制：链接只在提交时查，复制过程中新出现的链接查不到。
- **退回第 2 轮（PR #85 上 Codex GitHub app 对 948211c 的 P2，主会话 2026-11-16 核实属实；只是让复制和已通过的源校验一致，由主会话直接定）**：`prepare_workspace`（`launcher.py:294`）用 `shutil.copytree(..., ignore_dangling_symlinks=True)`；`shutil._copytree` 判断悬空用的是 `os.path.exists(os.readlink(src))`，相对链接按服务进程的当前目录解析，不是按链接所在目录。所以项目里有效的相对链接（如 `shop.duckdb -> real.duckdb`、指向 models 或 profiles 的相对链接）会被静默跳过，返回的 `--duckdb` 路径在工作区里不存在，运行失败。修法：
  - 去掉 `ignore_dangling_symlinks=True`；在现有的 `ignore` 回调里，对 `entry.is_symlink() and not entry.exists()`（`Path.exists` 按链接所在目录跟随）的才跳过。
  - 单元测试（经 `prepare_workspace`，并且测试里 `chdir` 到项目以外的目录）：相对的文件链接（DuckDB）和目录链接（models）都被复制且内容可读；真正悬空的链接被跳过、不报错。修前要失败。
  - 这是本 PR 最后一轮退回；修完重跑 `make check` 和两道审查，真实运行至少用一个带相对 DuckDB 链接的 dbt 源提交一次（$0，被拒或跑起来都要记下）。
- 2026-11-16 claude-opus，退回第 2 轮修复（bf810a2、943358e）：
  - 去掉 `prepare_workspace` 的 `ignore_dangling_symlinks=True`；`ignore` 回调只跳过 `is_symlink()` 且 `not exists()` 的链接（`Path.exists` 按链接所在目录解析）；两处链接检查合成一处，文档注明跳过悬空链接。
  - 测试（`tests/test_web_submit.py`，直接调 `prepare_workspace`，`monkeypatch.chdir` 到项目外）：相对文件链接 `shop.duckdb -> real.duckdb`、相对目录链接 `models/shared -> ../shared` 复制成真实内容且可读，返回的 `--duckdb` 存在；悬空链接 `gone.sql -> missing.sql` 被跳过不报错（cwd 里放同名 `missing.sql`）。换回 948211c 的 launcher 两条都失败，修后通过。
  - make check：2242 passed, 1 skipped, 39 deselected, 4 xfailed；ruff、format 通过；basedpyright 0 errors；vitest 通过。
  - 真实运行（$0，假 key 指向本机死端口）：项目里 `shop.duckdb -> real.duckdb`，服务从项目外的 cwd 启动；`GET /api/options` 正常；提交 dbt 运行 201，`workspace/shop.duckdb` 是普通文件 274432 字节（和 real.duckdb 相同）；取消 200 cancelled；服务正常退出，没有残留进程。查数路径本轮未改，主会话 948211c 上的真实运行仍适用。
  - da-review 超时没结论（10 分钟后台上限；上一轮 60 分钟也没结论），改用只读 Fable 审查本轮 diff：无 P1/P2，确认两条新测试修前失败。两条 P3（修前修后行为相同）记为已知限制：链接目标名超过 NAME_MAX 时 `Path.exists` 抛 OSError、提交返回 500；`proj/lnk/../d.duckdb`（`lnk` 指向项目外）能过 `check_layout`（abspath 按字面折叠 `..`），但工作区里没有这份拷贝。
  - eng:code-review：Spec 指出悬空链接测试修前也通过 → 943358e 在 cwd 放同名文件，现在修前失败；无越界。Standards 无硬违规；合并重复的 `is_symlink` 检查、补文档。不修：两条测试的 5 行准备代码重复（只两份）；`ignore` 回调做三件事（仍很短）。
  - 已知限制：链接成环（`models/self -> .`）原来抛 shutil.Error，现在复制约 30 层到 ELOOP 后跳过，工作区会变大。本 PR 退回已满 2 轮，之后的新意见记成已知限制。
