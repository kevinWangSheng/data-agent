# 23 — DAB 大数据集：先装好数据库再起 agent

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 23-dab-large-dataset-load)
Branch: worktree-23-dab-large-dataset-load

## Goal

DAB 的大数据集（`imdb` 的 1.6 GB `movies.sql` 进 Postgres、`krama` 的 530 MB `files.bson` 进 MongoDB）在服务器上能真实跑通一题：数据库在 agent 的 MCP 握手之外装好，agent 起来时数据工具可用；并量出装载与运行时的服务器内存峰值，判断全量 DAB 能否在 7 GB 机器上跑。

## Authority

- May edit: `evals/dab.py`、`evals/dab_server.py`、`tests/`（除 `tests/acceptance/`）、`docs/setup.md`
- Must not edit: `tests/acceptance/` 下的验收测试、`evals/ablate.py`、`evals/wheels.py`（工单 22 在改）、`scripts/da-remote`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: `da-remote` 现有子命令；只用 `start`/`status`/`pull` 跑 DAB。不允许 sudo、装系统包、改系统配置、改数据库容器配置。
- Spend cap: $1

## Context

- 主会话实测（服务器，main `9da3477`）：`da-remote start mem-imdb -- dab --dab-root ../da-benchmarks/DataAgentBench --dataset imdb --query 1 --config no-context --runs 1 --out runs/mem-imdb --budget 0.5` → `imdb query1 run0: infra_error`，exit 2，$0.0384。events：`mcp_servers: [{"name": "data", "status": "failed"}]`、`error: DAB tool server failed`，agent 只剩 Glob/Grep/Read。`memory.log` 显示 `da-dab-postgres` 在 agent 开始前后才从 43 MiB 涨到 150 MiB，即 dump 装载还没完成。本机已 pull 到 `runs/mem-imdb/`（主 checkout，可读）。
- 推断（未确认）：`evals/dab_server.py:205-215` 构造 `query_db`（`check_load=True`）时同步装载数据集，装载慢于 Claude Agent SDK 等 MCP 服务器就绪的时限，服务器被判 failed。小数据集（工单 21 的 bookreview、civic_unstructured）装得快所以没暴露。先查清真实原因再修。
- 工单 13/21 已知：`ExecTool` 构造失败时已装载的 PG/Mongo 数据不清理，下次装同一数据集会先 drop。
- DAB 没有许可证：只参考思路，不复制代码。
- 服务器 7.9 GB 内存，其他服务约 1 GB；DAB 不和 Harbor 并发跑。

## Acceptance

- [ ] 查清 `imdb` 失败的真实原因，写进 Comments（证据：日志、时间线）
- [ ] 修复：装载不受 agent 的 MCP 握手时限约束（例如在起 agent 之前装好、数据工具只连接已装好的库），装载失败时本次运行记为 infra_error 并给出原因；配修复前会失败的测试
- [ ] 真实运行（经 `da-remote start`/`status`/`pull`）：`imdb` q1 与 `krama` q1，各 `--config no-context --runs 1`，数据工具可用、跑完；贴结果、装载用时、内存峰值（`status` 的 memory peak 与 `memory.log` 里两个数据库容器的峰值）
- [ ] 结论：全量 DAB（54 题 × 5）在这台机器上是否需要分批或清理已装数据集；内存不够就停下交回
- [ ] `make check` 通过

## Evidence required

单元测试输出；原因分析；两次真实运行的命令、结果、装载用时与内存峰值。

## Stop conditions

内存不够放下数据集（需要人决定加机器或分批）；需要改数据库容器配置；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1（三个会话：实现与审查；网络中断后续跑；真实运行）
- Review rounds: 2（da-review：7af8b2a、b04c6c8、f1be2b6 各一次；eng:code-review：7af8b2a、b04c6c8 各一次）
- Model spend: $0.21（主会话 mem-imdb $0.0384；imdb $0.0208；krama $0.1471）
- Outcome: done，PR #25 squash 合并（0 轮退回）；imdb 装载 50.1 s、krama 8.4 s，主机峰值 1586/1981 MB；全量 DAB 不分批，每次运行装库删库
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

工单 21 的已知限制"大数据集装载内存未测"，主会话实测时暴露装载与 MCP 启动的冲突。全量 DAB 前必须解决。

### 2026-10-20 工单 agent（主会话抄录）：原因、修复、审查、真实运行（第一次）

**imdb 失败的真实原因（主会话 mem-imdb，9da3477）**
- 旧代码在 MCP 服务器启动时构造 `QueryDBTool(check_load=True)`，同步装载 1.6 GB 的 movies.sql。
- 内置 CLI 等 MCP 服务器的时限 `MCP_TIMEOUT` 默认 30000 ms；version 事件与 `mcp_servers: failed` 相隔 31 s，对得上。服务器被判失败，agent 只剩 Glob/Grep/Read。
- 小数据集装得比 30 s 快，所以工单 21 没暴露。

**修复**
- 7af8b2a：
  - runner 在起会话前用 DAB 的 query tool 装库，用时记成 `dab_load` 事件；会话后在 finally 里 `clean_up` 删库。
  - 装失败：先删掉已装上的部分，记 `error(source=dab_load)` 加 `result`，结果为 infra_error，不起会话。
  - 工具服务器改成 `check_load=False`，只连接已装好的库，也不再删库。
- b04c6c8（第 1 轮审查修复）：
  - 装载外包一层 finally 做日志打码，中断时也会执行。
  - 删库失败的报错打码后再打印。
  - 显式传 `check_load`，并用 `_query_tool` 收拢工具构造。
- f1be2b6（真实运行发现，见下）：传给 DAB 的 db_config 改为绝对路径。
  - 新增 `test_databases_are_loaded_from_an_absolute_config_path`，修复前失败（AssertionError：路径不是绝对路径），修复后通过。

**make check（f1be2b6）**
ruff：All checks passed；format：133 files already formatted；basedpyright：0 errors；pytest：1152 passed, 1 skipped, 25 deselected。

**真实运行一（b04c6c8）**
- 命令：
  - `da-remote status`：空闲，无运行中的任务
  - `da-remote setup b04c6c8`
  - `da-remote start t23-imdb -- dab --dab-root ../da-benchmarks/DataAgentBench --dataset imdb --query 1 --config no-context --runs 1 --out runs/t23-imdb --budget 0.4`
- 输出：
  Database 'movies_db' already exists. Cleaning up before loading dump.
  imdb query1 run0: infra_error
  da-remote: evals exited 2 at 2026-10-20T23:14:43Z
  memory peak: 1109 MB used (total_mb=7940)；spend $0.0000
- events：`{"source": "dab_load", "error": "loading DAB's databases failed: PostgreSQL load error (CalledProcessError): Command '['/root/da-benchmarks/bin/psql', ..., '-d', 'movies_db', '-f', '../da-benchmarks/DataAgentBench/query_imdb/query_dataset/movies.sql']' returned non-zero exit status 1."}`，随后是 result，没有起会话。
- 新的失败路径按设计工作：主会话那次留下的 movies_db 被 DAB 先删掉了；装载失败记为 infra_error，花费 $0。
- 失败原因：
  - DAB 的 `db_config.py:35` 把 sql_file 拼在 config 所在目录上。新加的 `_query_tool` 传的是未解析的相对路径，而 `server_args` 早就做了 `.resolve()`。
  - psql 经 `bin/psql` 的 `docker exec` 包装脚本在容器里执行，工作目录是容器自己的，找不到 `../da-benchmarks/...`，psql 以 exit 1 退出（psql 的致命错误码，例如文件不存在）。
  - 单测用的是假 DAB 和进程内的 sqlite，测不出来。
  - krama 的 `dump_folder` 交给 mongorestore，同样在容器里执行，推断（未确认）会以同样方式失败；f1be2b6 一并修复。
- 1109 MB 的峰值是装载开始前的值，不能代表 imdb 装载时的内存。

**真实运行二（imdb q1、krama q1，f1be2b6）**：未运行，等 f1be2b6 push。

**审查**
- da-review 第 1 轮（7af8b2a）：P2 中断装载时不打码 → b04c6c8 修复。
- da-review 第 2 轮（b04c6c8）：无问题。
- da-review（f1be2b6）：P2 独立运行 `python -m evals.dab_server` 时不再装库。不修，理由：
  - 该模块的 docstring 写明它是给 DAB runner 用的（evals/dab_server.py:1）；
  - 唯一调用方是 runner（evals/dab.py:389），runner 先装库再起它；
  - docstring 里的命令行只是参数示例。
- eng:code-review 第 1 轮（7af8b2a）：
  - Standards 1–3 已在 b04c6c8 修复：装失败时日志未打码、删库报错未打码、`check_load` 隐式默认值；工具构造重复也已收拢。
  - Spec c1（打码）同上，已修复。
- eng:code-review 第 2 轮（b04c6c8），Standards：无硬违规，第 1 轮各项都已修。剩下 5 条判断项，不修：
  - 构造函数带副作用（照搬 DAB 的 API）；
  - docstring 没写中断时也会删库；
  - `secrets_of(env)` 重复调用；
  - 测试辅助函数名 `loads`/`record`；
  - 假 `ListDBTool.clean_up` 没有直接测试（它的作用是：若有人把 `list_tool.clean_up()` 加回服务器，会被测试抓到）。
  - 理由：都是可读性层面的判断，不影响行为；修复轮数已用满。
- eng:code-review 第 2 轮（b04c6c8），Spec，不修：
  1. 装载中途的 SystemExit 或 Ctrl-C 不记为 infra_error：DAB 的 `load_db` 把所有异常包成 FatalError（postgres_utils.py，`except Exception`），不会调 `sys.exit`；Ctrl-C 本来就应该中止整批。
  2. 中断后留下的运行目录，重跑时会遇到 `FileExistsError`：会话中断时本来就是这样，不是本工单引入的。
  3. 删库失败只打到 stderr：已知限制。DAB 下次装同一数据集前会先删旧库（这次运行的 "already exists. Cleaning up" 是实证）；不同数据集的残留不会被清理，建议在全量 DAB 工单里处理。
  4. 每次运行都删库，`--runs 5` 时 imdb 要装 5 次：先保留，是否改为按数据集只装一次，等第二次真实运行量出装载用时再定。

### 2026-10-21 工单 agent（主会话抄录）：真实运行二（f1be2b6）

**命令**
- `da-remote status`：空闲，所有任务都已 finished
- `da-remote setup f1be2b6` → `setup: /root/data-agent at f1be2b6`
- `da-remote start t23b-imdb -- dab --dab-root ../da-benchmarks/DataAgentBench --dataset imdb --query 1 --config no-context --runs 1 --out runs/t23b-imdb --budget 0.4`
- 跑完后 `da-remote start t23b-krama -- dab ... --dataset krama --query 1 --config no-context --runs 1 --out runs/t23b-krama --budget 0.4`（依次跑，不并发）
- `da-remote pull t23b-imdb`、`da-remote pull t23b-krama`

**imdb q1**
- 输出：`imdb query1 run0: finished`；`da-remote: evals exited 0 at 2026-10-21T02:51:36Z`；`memory peak: 1586 MB used (total_mb=7940)`；`spend: $0.0208`
- events：`{"type": "dab_load", "seconds": 50.1}`；`mcp_servers: [{"name": "data", "status": "connected"}]`；result 为 success，43 轮，terminal_reason completed
- run.log 只有 psql 的 `NOTICE: table ... does not exist, skipping`（DROP IF EXISTS），没有报错
- 容器峰值：da-dab-postgres 816 MiB（开始时 171；装载中最高约 384，会话查询中升到 816），da-dab-mongo 64 MiB

**krama q1**
- 输出：mongorestore `1651 document(s) restored successfully. 0 document(s) failed`；`krama query1 run0: finished`；`evals exited 0 at 2026-10-21T03:04:09Z`；`memory peak: 1981 MB used (total_mb=7940)`；`spend: $0.1471`
- events：`{"type": "dab_load", "seconds": 8.4}`；mcp_servers connected；result 为 success，66 轮
- 上一会话推断 krama 的 dump_folder 会同样失败，f1be2b6 修复后 krama 能正常装载
- 容器峰值：da-dab-mongo 778 MiB（装载中），da-dab-postgres 398 MiB

**删库后内存**
- imdb 删库后：主机内存从会话末的 1497 MB 降到 956 MB（krama 开始时的读数；imdb 开始时是 916 MB）。postgres 容器 763 → 313 MiB，没有回到 171。
- krama 删库后：主机内存从约 1775 MB 降到 1330 MB（03:04:09 最后一次采样）。mongo 容器仍是 434 MiB（开始时 63）。
- 两次 run.log 都没有删库失败的报错。
- 推断（未确认）：容器里剩下的是数据库自己的缓存（Postgres 的 buffer、Mongo 的 WiredTiger cache），不是残留的库。

**结论：全量 DAB 54×5 在 7.9 GB 服务器上**
- 不分批，保留每次运行都装库、删库，不改成按数据集只装一次。
- 依据：
  - 运行是串行的（evals/dab.py:658），同一时间只装着一个数据集；
  - 两个最大的数据集峰值 1586 MB / 1981 MB，余量约 6 GB；
  - 重装代价：imdb 每次 50 s，每条题 5 次运行约多 4 分钟；krama 每次 8 s；与会话时长（4.5 分钟和 11 分钟）相比不大。
- 剩余风险（转给全量 DAB 工单）：mongo 容器删库后不释放缓存。WiredTiger 默认缓存上限可能接近 3.4 GB（推断，服务器配置未确认）。全量跑时看 memory.log 里 mongo 的走势；若逼近上限，给 benchmarks 容器加 `--wiredTigerCacheSizeGB`。
- 上一会话 Spec 第 4 条（`--runs 5` 时 imdb 要装 5 次）就此定为：保留现状。

本会话没有新提交，不需要 push。本机已 pull 到 `runs/t23b-imdb/`、`runs/t23b-krama/`。

### 2026-10-21 主会话：合并 PR #25

CI 绿（f1be2b6）。Codex 第一次 status failed 无评论，第二次 "Something went wrong"，第三次 "Didn't find any major issues"（Reviewed commit f1be2b65a7）。已 `gh pr merge 25 --squash`。后续：mongo 容器删库后缓存不释放，全量 DAB 时看 memory.log；verifier 侧逐键核对见工单 25 的已知缺口。
