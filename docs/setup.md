# 资源与环境准备

开工前一次性准备。依据：ADR-0001 Update、ADR-0006、ADR-0007。2026-10-07 本机检查结果见末尾。

## 现在就要（开工前）

| # | 资源 | 说明 | 状态 |
|---|---|---|---|
| 1 | DeepSeek API key，充值约 $20 | platform.deepseek.com。一期上限暂定 $300、推算约 $250（未实测，见 `docs/plan.md` 第 7 节） | ☑ 人已填（2026-10-15）；工单 03 的真实运行用它跑通（2026-10-16） |
| 2 | Anthropic API key，充值约 $20（可选） | 只用于工单 09 的 5 题 Sonnet 5.5 对照（约 $7）；不跑 Claude 全量 | ☐ |
| 3 | x86_64 Linux 服务器 | Docker、uv、git、git-lfs；Mac 能 SSH 免密登录。台数和大小按工单 09 实测的单题峰值内存定（见 `docs/plan.md` 第 7 节"服务器与并发"）；20 GB 空闲磁盘（题目约 11 MB，DAB 数据约 13 GB，其余是镜像） | ◐ 别名 `da-server`：6 核 / 7 GB / 98 GB，SSH、Docker、uv、git、git-lfs、rsync、env 文件都通，按 plan §7 口径并发 3（`da-remote check`，2026-10-20）。这台不能升配。仓库经只读 deploy key（SSH）clone 到服务器，`da-remote setup → push → start → status → pull` 1 题冒烟跑通（2026-10-20） |
| 4 | Mac 上的 colima 运行中（Docker 运行时；数据在外接盘 sd710） | 开发期跑测试用数据库和单题 | ☑ `m1-kind` 运行中，`docker` context 已切到 `colima-m1-kind`（2026-10-16，`docker info` 通） |
| 5 | 私有 GitHub 仓库作为 remote | 代码从 Mac 同步到服务器靠它；一期跑通前保持私有，之后转公开 | ☑ `github.com/kevinWangSheng/data-agent`（2026-10-12） |

服务器只经 `da-remote` 访问（用法见脚本头注释）：`setup [<commit>]` 拉仓库到指定 commit 并 `uv sync`；`push <name>` 把本机 `runs/<name>/`（如临时 manifest、题目副本）传上去；`start <name> -- <evals 子命令与参数>` 后台跑评测，日志在服务器 `runs/<name>/run.log`，评测的 `--out` 用 `runs/<name>`；`status [<name>]` 看状态、日志末尾和花费；`pull <name>` 取回本机 `runs/<name>/`；`ablate` 的 run 默认只取回汇总（`reports/`、`run.log`、控制文件），逐题 trial 与 Harbor 日志留在服务器（ADR-0004）；只有按服务器仓库的 `evals/manifest.json` 核对、每个 job（含预检的冒烟 job）的题和每个 trial 的题都在 dev 列表里时才整份取回（ADR-0008，供人读轨迹），判断不了的照旧只取汇总。`exec -- <命令> [<参数>]` 在服务器仓库目录下跑一条命令（参数原样传，不经 shell；要管道就 `bash -c`），stdin 关闭，stderr 并进 stdout，输出同样遮掉地址（只遮地址），退出码原样返回（ssh 本身连不上也是 255）；查状态、看日志、一次性检查都用它，不直接 ssh。它不放宽别的规矩：不读 env 文件等密钥，不看留出集逐题细节（ADR-0004）。

开发集运行不再借临时 manifest：`ablate --split dev` 跑真 manifest 的开发集，`--task <题>` 可重复、只跑其中几题（例：`start dev-4 -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest evals/manifest.json --out runs/dev-4 --budget 5 --split dev --task <题> --trials 1 --config full`）。报告目录里每个配置另有 `diverge/<配置>/`：与 full 的逐题配对分歧和每题过程指标；两个现成 job 也可直接比：`python -m evals diverge --baseline <job> --candidate <job> --manifest evals/manifest.json --out <目录>`（只列开发集题，其他题只计数）。

两个榜单由 `da-remote benchmarks` 备好（可重复跑，已就绪的步骤跳过），全放在服务器仓库旁的 `da-benchmarks/`：

- `data-eng-bench/`（钉 `a3278ad`）：评测用 `--tasks-dir ../da-benchmarks/data-eng-bench/tasks`（路径相对服务器仓库，`start` 在那里跑）。agent 依赖的 wheel 缓存（`~/.cache/data-agent/wheels`，工单 20）不用手动预热：`ablate` 在第一个 Harbor job 前按题目镜像的平台自动填好，日志里是 `wheels:` 开头的行；填不上就在花钱前退出 2。
- `DataAgentBench/`（钉 `6ad9d68`，无许可证，只在服务器 clone）：数据由 DAB 自带的 `download.sh` 从 Hugging Face 取并按 `dataset_manifest.tsv` 校验 sha256（每次 `benchmarks` 都全量校验，13 GB 约 75 秒；日志 `da-benchmarks/download.log`）。DAB 这个 clone 里 git-lfs 的 filter 设成不做事（只改该 clone 的 `.git/config`）：否则 `evals/dab.py` 记 DAB commit 时的 `git diff` 会把 13 GB 数据读进内存过 LFS 过滤，7 GB 的机器扛不住；数据改由 `dab.py` 按清单核对；评测用 `--dab-root ../da-benchmarks/DataAgentBench`。
- 镜像 `python-data:3.12`（DAB 的 Dockerfile，`execute_python` 用）。
- 容器 `da-dab-postgres`（`postgres:17`，127.0.0.1:15432）与 `da-dab-mongo`（`mongo:8`，127.0.0.1:37017，WiredTiger 缓存 0.5 GB）；数据卷 `da-dab-pgdata`、`da-dab-mongodata`，删掉容器和卷即可重来。DAB 装库要调 `psql` 和 `mongorestore`，服务器不装系统包，所以 `da-benchmarks/bin/` 里的同名包装脚本经 `docker exec` 在容器里跑它们（两个容器只读挂载 DAB 目录，路径与宿主机相同）。装库由 `evals/dab.py` 在每次会话开始前做、结束后删（同一时间只装着一个数据集），用时记在 trace 的 `dab_load` 事件里；装失败记 `infra_error`，不起会话。DAB 的工具服务器只连已装好的库：imdb 的 1.6 GB dump 装得比 SDK 等 MCP 服务器启动的约 30 秒久（工单 23）。
- 连接设置（`PG_*`、`MONGO_URI`、`PG_CLIENT` 和 `bin/` 的 PATH）写在服务器 `~/.config/data-agent/dab.env`（600，密码随机生成、不打印）；`start <name> -- dab ...` 会额外读它，并用 `--with-requirements evals/dab-requirements.txt` 跑。删了 `dab.env` 而保留 Postgres 数据卷会对不上密码，要连卷一起删。
- 查询超时（工单 36）：DAB 路径上每条查询（`query_database`、`list_tables`）最多跑 150 秒，和网关同一个 `TIMEOUT_SECONDS`；超时后 agent 收到和网关一样的错误 `Query timed out after 150s`，同一个库的下一条查询照常执行。DAB 每次查询都新开连接，`evals/dab_server.py` 启动时把 DAB 各库模块里的驱动名换成我们的包装（不改 DAB clone）。各库的停止方式：Postgres 在连接参数 `options` 里加 `-c statement_timeout`（保留 DAB 自己的只读选项），由服务端取消；MongoDB 给客户端 `timeoutMS`，pymongo 把它作为每次操作的 `maxTimeMS` 发给服务端；SQLite 和 DuckDB 在本进程里跑，由计时器对本次查询开的连接调 `interrupt()`。`--query-timeout` 只给测试调小。
- 数据库事实（工单 37）：Postgres、DuckDB 库上，`list_tables` 每张表多给 `rows_estimate`（取不到写 `"unknown"`，视图不给），另有 `describe_table(db_name, table)`、`explain_query(db_name, sql)`，超时报错附计划和索引，内容同下面"数据库事实工具"。所有配置都带，不受层开关控制。这些 SQL 都先过 `governed()`，再交给 DAB 的 `QueryDBTool.exec`；SQLite、MongoDB 行为不变，对它们调两个新工具返回可读错误。

数据库事实工具（工单 37，ADR-0003 Update 2026-11-02）：数据库自己报出的事实属于基础工具层，所有配置（含 `baseline`、`no-context`）都有，只做 Postgres 和 DuckDB。取数代码在 `src/data_agent/db_facts.py`，所用 SQL 都是普通 SELECT，经只读网关执行。

- `list_tables`：每张表带行数估计 `~N rows`。Postgres 取 `pg_stat_user_tables.n_live_tup`，为 0 时取 `pg_class.reltuples`，都没有写 `rows unknown`；DuckDB 取 `duckdb_tables().estimated_size`。视图不带。
- `describe_table(table)`：列（类型、可空）、声明的主键和外键（数据库自己的定义文本）、索引定义、行数估计。Postgres 每列再带 `pg_stats` 的 `n_distinct`、`null_frac`；表在 `pg_stats` 里没有统计时写 `Column statistics: none in pg_stats for this table`，不现场扫表。DuckDB 的 `duckdb_indexes()` 只列 `CREATE INDEX` 建的索引，主键背后的索引以主键的形式出现。
- `explain_query(sql)`：SQL 先过网关同一套检查（含行数上限），再在前面加 `EXPLAIN` 执行，不带 `ANALYZE`；在网关的只读连接和超时下跑，照原样返回数据库的计划文本。传入 `EXPLAIN ...` 本身会被网关拦下。
- `query_database` 超时：报错是 `Query timed out after 150s`，空一行后是 `Plan (EXPLAIN, without running the query):` 和计划文本，再是 `Indexes on the tables this query reads (from pg_indexes):`（DuckDB 为 `duckdb_indexes()`），每张涉及的表一行或多行 `表: 索引定义`，没有索引写 `none`（表名用 sqlglot 从 SQL 里取，跳过 CTE 和表函数）。`EXPLAIN` 本身失败时只附 `EXPLAIN failed: 原因`。MCP 框架会在报错前加 `Error executing tool query_database: `。
- `hydrate`（上下文层）在原有输出上，每列加 `, nullable` 或 `, not null`，末尾加 `Keys:`、`Indexes:` 两段，和 `describe_table` 共用取数代码；行数仍是精确的 `count(*)`。

DAB 算分（工单 35）：`uv run --with-requirements evals/dab-requirements.txt python -m evals dab-score OUT --dab-root ../da-benchmarks/DataAgentBench [--baseline OUT_BASE] [--runs N] [--all-queries] [--strict] [--report PATH] [--seed 0] [--n-boot 10000]`。

- 判分用 DAB clone 里的 `common_scaffold/validate/validate.py`（按路径 import，不复制）：空答案判错，否则跑该题目录下的 `validate.py`。某题的验证器报错或返回的不是 `(bool, reason)`，就退出 2 并点名那道题，不当作答错。
- 口径是 DAB 的 Pass@1：每题通过率 = 通过的运行数 ÷ 应有运行数；每个数据集取各题平均；再对数据集取平均。默认只算输出里出现的题，并在 stderr 和报告的 `coverage` 里写明覆盖了 clone 里多少题；这样算出的分数不能和榜单比。`--all-queries` 按榜单规矩把 clone 里每道题都算进来，没跑的题按 0 算（每题应有 `--runs` 次，不给时取输出里跑得最多的那道题的次数）。
- 应有运行：输出里的 `run<k>/` 目录和 submission 条目；给了 `--runs N` 时，再加上每道出现过的题的 run 0..N-1。没有答案的运行（基础设施报错不进 submission）列在 stderr，默认按 0 算；`--strict` 下退出 2、不写报告，重跑后再算。
- 给 `--baseline` 时做配对 bootstrap（候选减 baseline，百分点，95% 百分位区间，区间不含 0 才算显著）：在每个数据集内对题有放回重抽，每次重抽都含所有数据集。两份输出覆盖的 (数据集, 题) 不一致就退出 2，只报不一致的个数。DAB 上的比较是 `no-context` 对 `baseline`（DAB 的工具服务器没有上下文工具）。
- 输出：终端汇总（Pass@1、各数据集、各题通过数），加一份 JSON 报告（默认 `OUT/dab_score.json`）。
- 规定：DAB 只有一套题，没有开发集和留出集之分。逐题结果只用来排查基础设施问题，不能用来改 Skill（ADR-0005）。

`da-remote check` 也报这些就绪项：两个 clone 的 commit、题目数、DAB 数据大小与清单一致（只比大小，快；sha256 由 `benchmarks` 校验）、镜像、`dab.env`、两个数据库从宿主机可连；以及模型账户余额（`python -m evals.balance`，DeepSeek 的 `GET /user/balance`：报数额和 DeepSeek 说的能否付费，key 只进请求头、不打印；CNY 按 7.3 折成美元，偏保守）。

两种花费口径（工单 34）：

- 高峰价花费：事件文件结果事件里的 `cost_usd`，按 `PRICES`（deepseek-flash 的高峰价）算。用途是单次运行的上限 `max_budget_usd`，不随时段变，agent 行为白天夜里一样。报告里的 `cost_usd` 列仍是它。
- 实际价花费：接近账户实际扣费。DeepSeek 闲时（高峰是周一至周五 UTC 01:00–04:00、06:00–10:00，其余都是闲时）每项单价减半。一次运行按事件文件第一条事件的 `ts` 定时段；`cost_basis` 是 `price_table`、模型是 DeepSeek 的闲时运行算 `cost_usd × 0.5`，其他情况（SDK 口径、别的模型、缺 `ts`）算 `cost_usd`。中国法定节假日不单独处理，按高峰算，宁可多算。用途：`evals spend` 的 80% 护栏、`ablate` 开跑前余额检查里的"已花的"、`evals dab` 的 `--budget`，以及报告里的 `billed_cost_usd` 列（开发集和留出集都有；开发集的 `trials.json` 每行也有）。
- `evals spend` 两个合计都打印，并给出闲时、高峰、时段未知的事件文件数。

`ablate` 的预检与快速失败（ADR-0008，工单 27）：

- 每次调用先在 `--out` 下写运行记录 `records/<UTC 时间>.json`：开始时间、benchmark commit（`--tasks-dir` 所在 clone 的 HEAD，有改动带 `-dirty`）、机器标签（环境变量 `DATA_AGENT_MACHINE`；`da-remote start` 设成 SSH 主机别名，env 文件可覆盖；本机跑要自己设，如 `mac-colima`）、代码 commit、模型。`--out` 下已有 job 时，benchmark commit 或机器标签和最近一份记录不同（或 benchmark 有改动、不在 git 里、没设机器标签、没有记录）就停（exit 2），不写新记录、不续跑也不复用；换了 benchmark 或机器就换一个 `--out`。
- 要起第一个 job 前做预检，任何一项不过就退出 2、不起正式 job：余额要够 `--budget` 减去已花的（按实际价花费）；每个配置在 manifest 开发集第 1 题上冒烟 1 次（`smoke/<UTC 时间>/<config>/`，日志 `logs/smoke-<config>.log`），要有 reward、无报错、事件文件有 version 头；再对冒烟结果跑 scrub。续跑已冒烟过的批次加 `--skip-smoke`（余额照查）。没有 job 要跑时不预检。
- 预检里的轻量 readiness（工单 02，`evals/readiness.py`；ADR-0008 Update 2026-11-07）。花费有界：不调模型、不多起 Harbor，只有 1 次 `docker info`（最多 30 秒）、最多 1 次查磁盘，加上读每配置那 1 个已有的冒烟 trial。
  - 阻断（退出 2，一个 job 都不起）：`--agent-kwarg agent_timeout_sec=` 不大于 agent 的 150 秒自停余量；`docker info` 失败或无响应；Docker 内存 < 并发 trial 数 × 1762 MiB；Docker data root 在本机时剩余磁盘 < 并发 trial 数 × 4 GiB（Colima 的 data root 在虚拟机里，不测磁盘）。并发数取 `--n-concurrent`、job config、续跑 job 保存的 `n_concurrent_trials` 里最大的，不超过 job 的 trial 数。
  - 告警（不阻断）：冒烟 trial 截止自停、工具语句超时、全量重派、prompt cache 命中率 < 50%。写进 `OUT/readiness/<UTC 时间>.json`，`ablation.md` 的 "Readiness" 一行列出。冒烟只跑开发集第 1 题，任务特有的长尾看不到。加 `--skip-smoke` 续跑时不冒烟，这四项告警这次不查、也不重读之前的冒烟 trial；`ablation.md` 里只有之前那次预检记下的告警。
- 跑的过程中每 10 秒看一次新完成的 trial：出现 `ApiUsageLimitError`（账户额度或余额用完）或配置错误立即停；连续 `--fail-fast N`（默认 5）个基础设施报错也停。停 Harbor 用它自己的方式（SIGTERM，它会取消 trial、清容器），300 秒不退才 SIGKILL，只对本次启动的那个进程。都是退出 2。`ApiUsageLimitError` 算基础设施报错（`evals report` 不记 0 分）：充值后用同一 `--out` 重跑，这些 trial 会被重跑。
- 续跑边界（工单 03）：同一 `--out` 再跑时，已完成的 job 直接复用；有中断（没有 `result.json`）或基础设施报错 trial 的 job 用 `harbor job resume` 只补缺的。被删的 trial 先把事件文件**移到** `OUT/dropped/<config>/`（有 `result.json` 的另复制一份过去，供 `evals accept` 和 infra 闸门认出它）：不计分，花费仍由 `evals spend`、余额检查和 80% 护栏记一次，替补 trial 跑完也不会重复计。逻辑失败（答错、agent 用完时间）是结果，job 视为完成，不自动重跑。checkout 有改动、代码 commit 或 Skill 版本与 job 里已有结果的 trial 的 version 头不同、benchmark commit 或机器与最近的运行记录不同、`--model`/split/题目/`--agent-kwarg` 与 `OUT/request.json` 不同，都拒绝复用和续跑，换一个 `--out`。已知限制：job 里一个带 `result.json` 的 trial 都没有时（第一批 trial 还没跑完就中断），没有 version 头可比，代码 commit 和 Skill 版本**不查**，会在当前 checkout 上续跑；所以改了代码以后一律换新的 `--out`，不要指望续跑替你拦。

合并分段结果与选配置（工单 04，ADR-0008 Update 2026-11-07）：

- 同一配置分几个 OUT 跑的 job 合起来出报告：`uv run python -m evals report OUT1/jobs/full OUT2/jobs/full --manifest evals/manifest.json --split dev --out <目录>`。合并要求 version 头里的 git commit、Skill 版本、config 一致，各 OUT 最近一份运行记录的 benchmark commit、机器、model 一致，`request.json` 除 `tasks` 外一致（dev 的 request 必须有非空题目列表），合并后每题正好 `--trials`（默认 3）次；任何一条不满足退出 2、不写报告。代码有新提交后跑的 job 和旧 job 的 git commit 不同，合不了：第 1 段要在新 commit 上重跑。
- 开发集报告里的 `gates` 分三部分：`correctness`（成功、失败、trial 数，不设通过线）；`infrastructure`（`passed` 只看 `unresolved`，即没被重跑补齐的 infra 错误数；已补齐的只在 `infra_excluded` 报数量和 `cost_usd`，`interrupted` 只报不判）；`efficiency`（每个全量重派的 trial 一条 warning，有 warning 就 `passed: false`；没有 warning 但有 trial 没有派单记录时为 `null`，即未知）。效率没过要照实写"效率告警"，不写闸门全过。留出集报告只有汇总，没有题名。
- 三配置并排：`uv run python -m evals select <full 报告目录> <baseline 报告目录> <no-verifiers 报告目录> --out <目录>`，写 `selection.json`、`selection.md`。拒绝留出集报告、同一配置给两次，以及 commit、model、benchmark、机器、split、`agent_kwargs`、（带 Skill 的配置之间）Skill 版本、题目或每题 trial 数不一致（退出 2）。显著性另用 `evals compare`。

上榜用的全量（leaderboard-run 工单 01）：`evals board` 只跑一个配置，题目从登记处 `snowflake-labs/data-eng-bench@sha256:de31…c995`（榜单 CI 认的 v1.0）取（Harbor `-d`，不用 `--tasks-dir`），trial 的 `config.task` 带登记处的题名、摘要和 source，job 配置取 `ablate` 的（不覆盖超时和资源，`DB_TYPE=duckdb` 走 `environment.env`），写到 `OUT/job-config.json`，agent 加上 `name: data-agent`、`model_name` 取 `--model`（默认 job 配置里的），这样榜单 CI 的 `matching_agent` 能在 job 配置里找到 trial 的 agent 和模型；不写 `reasoning_effort`（data-agent 不给模型设 effort，提交时 metadata 是 "none"）。

- 例：`da-remote start board-1 -- board --config no-verifiers --out runs/board-1 --budget 20`；`--trials` 默认 3，`--n-concurrent`、`--fail-fast`、`--skip-smoke` 同 `ablate`；`--model` 写进 job 配置，不传 Harbor 的 `-m`（Harbor 0.24.0 在只给 `--config`、不给 `--agent` 时忽略 `-m`）。
- 拆批：`--task <题>` 可重复，每批一个 job `jobs/<batch>/`（全量叫 `all`，其余按题集取哈希，job 名不含题名）。同一个 `--out` 再给同一组题就续跑那一批；和已有批次有重题、或配置/模型/每题次数/代码 commit/Skill 版本不同就拒绝（退出 2）。
- 预检同 `ablate`，另在最前面检查本机有没有 `dbt-bench-base`（登记处 103 题的 Dockerfile 都是 `FROM dbt-bench-base`）；没有就 `docker pull ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0` 再 `docker tag` 成 `dbt-bench-base`，失败就在起 job 前退出 2。题目文件不改。冒烟用 manifest 开发集里第一道在登记处的题。花费护栏、续跑、infra 处理同 `ablate`。
- 每批跑完写报告 `reports/<UTC 时间>/board.json`、`board.md`，合并 `--out` 下所有批次：榜单算法的准确率 ± 标准误和 pass@k；没凑满 103 × k 时列出缺的题（留出集只给个数）；按难度的准确率；花费（每 trial、每次成功）、token、墙钟；infra 排除数；"和 a3278ad 的差异"一节（`dbt-cart-abandonment-recovery` 在 v1.0 上必得 0 分，上限 102/103；97 题保留 DuckDB/Snowflake 兼容提示）和去掉这道题的 102 题准确率。逐题表不列留出集的题。
- `da-remote pull` 对 `board` 的 run 只取回报告、`run.log`（run 还没结束、里面出现留出题名（例如 `--task` 点了留出题）或读不了 manifest 时不取）、控制文件和非留出题的 trial 目录（按服务器仓库 `evals/manifest.json` 的 holdout 判断；没有 `result.json` 的、读不了的都不取）；留出题的 trial、Harbor 日志、job 文件、dropped 和冒烟留在服务器，供人上传时用。

改动验收（ADR-0008，工单 29）：`uv run python -m evals accept --old OUT_OLD/jobs/full --new OUT_NEW/jobs/full --manifest evals/manifest.json --target <题> [--target <题> ...] --regression evals/regression.json [--rerun <补跑 job> ...] [--json]`。

- 两份 job 都要覆盖整个开发集、每题 `--trials`（默认 3）次，新版不能有基础设施报错；job、目标题、回归集、补跑里有任何非开发集题就拒绝（只报个数，ADR-0004），退出 2。
- 回归集文件是开发集题名的 JSON 列表，如 `["dbt-daily-order-summary", "pos-operations"]`；空列表 `[]` 表示还没有回归集。
- 三条过关条件：新版开发集成功次数不少于旧版（点估计不低，相等算过）；目标题成功次数合计严格上升；回归题新版一轮 k/k。低于 k/k 的题列在 `rerun these tasks` 里，在新版上补跑 k 次，补跑 job 用 `--rerun` 加进来再判；补跑成功率 ≤1/3 算退步。补跑 job 的 version 头要和新版一致。
- 旧版复用：两份 job 的每个 trial（基础设施报错的除外）都有 version 头，且除 `git_commit`、`skill_version`、`skills` 外字段都一致（`skills` 是观测用的元数据，不参与复用匹配：它由已比较的 `config`、`layers` 和任务决定，工单 83 之前的版本也没有记录；每个字段每个 trial 都要有，缺了显示为 `(missing)`；`config`、`layers`、`model`、`sdk_version`、`cli_version` 是每次运行都写的，两边都没有也算缺）；各自 `--out` 下最近一份运行记录（`records/`）的 benchmark commit、机器标签一致且都是非空字符串；旧版最早开始的 trial 到新版最晚结束的 trial 不超过 14 天（基础设施报错以外的旧版 trial 都要有 `started_at`、新版 trial 都要有 `finished_at`，缺了就算年龄未知）；旧版那一轮没有基础设施报错（job 里没有，`dropped/<config>/` 里也没有被续跑删掉的；`ablate` 续跑前把每个要删的 trial 的 `result.json` 和事件文件存到那里）。任何一条不满足，判 `rerun-old` 并列出原因；旧版有基础设施报错时前两条不计分。
- 命令不查 `--old` 是不是上一次合入的版本，由调用者保证。ADR-0008 的验收就是 `--trials 3`；别的值只用来试跑（如 k=1 的 `runs/dev-2`）。工单 29 之前的 `--out`（dev-2、dev-3）里，续跑删掉的、没写事件文件的 trial 没在 `dropped/` 留下 `result.json`，这种基础设施报错查不出来。
- 结论：`pass`（退出 0）；`fail`、`rerun-regression`、`rerun-old`（退出 1）。回归题已判退步时，不管旧版能否复用都是 `fail`。

本地网页的后端（工单 77、78）：`uv run data-agent web --runs-dir runs/web [--port 8765] [--data-sources sources.toml]`，只听 `127.0.0.1`。

- 数据源文件只在给了 `--data-sources` 时才读，没有默认路径。不给时表单没有数据源，列出运行、读事件照常，提交运行返回 400。写法（相对路径从文件所在目录算）：

  ```toml
  [sources.shop]
  kind = "duckdb"
  path = "shop.duckdb"

  [sources.warehouse]
  kind = "postgres"
  env = "WAREHOUSE_URL"          # 存连接串的环境变量名；连接串本身不写进文件
  dbt_project = "warehouse_dbt"  # 可选；dbt 类型的任务要求有
  ```

- 启动时校验：`kind` 只能是 `duckdb` 或 `postgres`；`duckdb` 要 `path`，`postgres` 要 `env`（必须是变量名，像连接串的拒绝）；`dbt_project` 给了就要存在；未知的表和键都拒绝；配了 `dbt_project` 的 DuckDB 数据源，库文件的路径必须写在 `dbt_project` 里面（按写的路径算，不跟符号链接：项目里的链接或链接目录下的文件都算在里面）；`--runs-dir` 和任何一个 `dbt_project` 互相包含（含相同）时拒绝启动，否则复制项目时会把工作目录复制进自己。提交时再查 DuckDB 文件存在、环境变量已设置。
- 每条运行 `<runs-dir>/<id>/` 放事件文件，`<runs-dir>/<id>/workspace/` 是工作目录：查数运行是空目录，DuckDB 原地只读；dbt 运行复制 `dbt_project`（不含 `target/`、`logs/`；符号链接复制成它指向的内容），DuckDB 文件一起复制，留在原相对位置（`profiles.yml` 用相对路径才会指向副本）。复制会跟进目录链接，所以项目里有指向 `--runs-dir`（或它的上级、下级）的目录链接时，这次提交返回 400，不复制。
- 同时只跑一条，再提交返回 409。取消只对本服务启动、还在跑的那条有效：按记录的进程号发 SIGTERM（运行在自己的进程组里，连同它起的工具进程），10 秒不退再 SIGKILL；返回运行结束时的概况。服务给自己启动的运行写 `<id>/web-run.json`（提交时的概况）；进程退出时组里剩下的进程一并停掉，没写 `result` 事件的记成"已取消"（本服务停的）或"失败"。服务正常退出时同样停掉在跑的那条。已知限制：服务被强杀时没机会记，那条运行一直显示运行中。
- 对话（工单 93，接口见 `tests/acceptance/test_chat_api.py` 开头的契约）：新建对话不选类型和数据源，每一轮把 `--data-sources` 里的全部数据源交给 agent（写成 `<runs-dir>/<run_id>/data-sources.toml`，只有名字、种类、DuckDB 路径或环境变量名）。对话目录是 `<runs-dir>/conversations/<id>/`：`conversation.json` 索引、各轮共用的 `workspace/`、会话存档 `claude-config/`（不进 `~/.claude`）。一条消息一轮、一个运行目录，运行列表、读事件、三栏运行页照常能看；第二轮起接着上一轮的 session。某轮在跑时再发的消息写进那个子进程的 stdin（插话），不开新一轮；"同时只跑一条"对表单和所有对话一起算，取消、服务退出时停进程的办法同上。命令行和表单跑的单次运行在对话列表里显示成只有一轮、不能追问的对话。已知限制：子进程写完 `result`、还没退出时发的消息仍算插话（返回 202），送不到。

密钥只放在仓库外的 `~/.config/data-agent/env`（照 `.env.example` 填，`chmod 600`），`make test-integration` 读取；不放仓库里的 `.env`，不贴进对话或文档。

`make test-integration` 只在本机手动跑（CI 和服务器都不跑）。其中的 Postgres 用例（`tests/test_postgres.py`、`tests/test_eval_jaffle.py`、`tests/test_db_facts.py`、`tests/test_verifier_v2_tools.py` 和接缝 B 的 Postgres 变体，2026-11 共 32 个）要环境变量 `DATA_AGENT_TEST_POSTGRES_URL` 指向一个本机临时库，没设就失败。要跑时用 colima 起一个临时容器：`postgres:17`，只绑 127.0.0.1，随机密码，跑完删容器；每个 worktree 用自己的库名或端口。

## 外部 MCP server（工单 116）

用法、校验规则和风险见 README 的 "External MCP servers"。配置文件放在仓库外，示例里只写 `${DATA_AGENT_MCP_*}` 引用，不写任何真实值。变量的值写进 `~/.config/data-agent/env`，运行前加载：`set -a && . ~/.config/data-agent/env && set +a`。

### MongoDB（stdio，本机 docker）

1. `colima start`，然后 `docker info` 能通。MongoDB MCP 的镜像钉在 `mongodb/mongodb-mcp-server:3.3.0`，用 `docker pull mongodb/mongodb-mcp-server:3.3.0` 预先拉好，这样第一次运行不用等下载。
2. 演示库：起一个只挂在自建 docker 网络上的 mongo 容器，再把 `tests/fixtures/mcp/support_tickets.json` 导进 `support.tickets`：

   ```bash
   docker network create da-mcp-demo
   docker run -d --rm --name da-mcp-mongo --network da-mcp-demo mongo:8.0
   docker exec da-mcp-mongo mongosh --quiet support --eval "db.tickets.insertMany($(cat tests/fixtures/mcp/support_tickets.json))"
   ```

3. 连接串写进 env 文件：`DATA_AGENT_MCP_MONGO_URL=mongodb://da-mcp-mongo:27017/support`。MCP server 也跑在 `da-mcp-demo` 网络上，所以按容器名就能连到 mongo。
4. 配置（例如 `~/.config/data-agent/mcp-mongo.json`）：

   ```json
   {
     "mcpServers": {
       "mongo": {
         "type": "stdio",
         "command": "docker",
         "args": ["run", "--rm", "-i", "--network", "da-mcp-demo",
                  "-e", "MDB_MCP_CONNECTION_STRING", "-e", "MDB_MCP_READ_ONLY", "-e", "MDB_MCP_TELEMETRY",
                  "mongodb/mongodb-mcp-server:3.3.0"],
         "env": {
           "MDB_MCP_CONNECTION_STRING": "${DATA_AGENT_MCP_MONGO_URL}",
           "MDB_MCP_READ_ONLY": "true",
           "MDB_MCP_TELEMETRY": "disabled"
         },
         "allowedTools": ["find", "aggregate", "count", "list-collections", "collection-schema", "list-connections"]
       }
     }
   }
   ```

   - `MDB_MCP_READ_ONLY=true` 时只注册 18 个只读工具；设成 `false` 会多出 `insert-many` 等写入工具。
   - 3.3.0 的查询工具要传 `connectionId`，所以清单里要有 `list-connections`。
   - 这个 server 默认发遥测，`MDB_MCP_TELEMETRY=disabled` 把它关掉。
   - 以上三条都在工单 115 用 MCP 客户端实测过。
   - 用 `DOCKER_HOST` 找 daemon 的机器，要在 `env` 里加 `"DOCKER_HOST": "..."`，因为包装命令不带这个变量。colima 靠 `~/.docker` 下的 context 找 daemon，不需要它。
5. 运行：`uv run data-agent run --type query --config full --duckdb shop.duckdb --workspace work --events-dir events --mcp-servers ~/.config/data-agent/mcp-mongo.json --task "..."`。
6. 用完清理：`docker rm -f da-mcp-mongo && docker network rm da-mcp-demo`。

### GitHub（http，只读）

1. 在 GitHub 上建一个只读的 fine-grained personal access token，权限只给公开仓库的读取。把它写进 env 文件：`DATA_AGENT_MCP_GITHUB_TOKEN=...`。
2. 配置（例如 `~/.config/data-agent/mcp-github.json`）：

   ```json
   {
     "mcpServers": {
       "github": {
         "type": "http",
         "url": "https://api.githubcopilot.com/mcp/readonly",
         "headers": {"Authorization": "Bearer ${DATA_AGENT_MCP_GITHUB_TOKEN}"},
         "allowedTools": ["*"]
       }
     }
   }
   ```

   依据是 GitHub 官方文档（2026-11-23 访问）：
   - [`docs/remote-server.md`](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)：远程地址是 `https://api.githubcopilot.com/mcp/`，"Add `/readonly` to the end of any URL to restrict the tools in the toolset to only those that enable read access"。也可以改用请求头 `X-MCP-Readonly`、`X-MCP-Toolsets`（例如 `"issues"`）。
   - [README](https://github.com/github/github-mcp-server) 的 "Using a GitHub PAT"：PAT 用请求头 `"Authorization": "Bearer <PAT>"` 传。

   用 `/readonly` 端点时 `["*"]` 也只包括读工具。要更窄，就列出具体工具名（官方示例里有 `issue_read`、`get_file_contents`）。完整工具清单本工单没有核实（未确认）。
3. 运行示例：`uv run data-agent run --type query --config full --duckdb shop.duckdb --workspace work --events-dir events --mcp-servers ~/.config/data-agent/mcp-github.json --task "..."`。

### 已知限制

- 写入类工具没有调用前确认：列进清单就能直接调用。
- stdio server 跑在宿主机上，不在沙箱里，网络也不受 `--network` 控制。
- 外部工具的入参和输出会进事件文件；打开 trace 导出时还会进用户自己的 Langfuse。
- 凭据进 argv：SDK 0.2.168 把 `mcp_servers` 整个序列化成 CLI 的 `--mcp-config` 参数，所以 http server 展开后的请求头在 CLI 的命令行里，本机能列进程的用户看得到。stdio server 的值不在 argv 里：它们经 CLI 的环境传给包装命令，包装再从配置文件读出自己的 `env`。

## Slack 机器人（工单 131）

用法、权限和已知限制见 README 的 "Slack"。下面是建 Slack app 的步骤。没有注明出处的地方都是在 2026-11-24 读 Slack 官方文档（docs.slack.dev）核对过的，查不到的标了"未确认"。仓库里不写任何真实 token、工作区名或用户 id。

1. **建 app**：打开 <https://api.slack.com/apps>，选 Create New App，选工作区（[App settings quickstart](https://docs.slack.dev/app-management/quickstart-app-settings)）。2026-11-25 的创建页分 "From a template"（AI agent、Starter app）和 "From scratch"（Paste a manifest、Pick features）；选 **Paste a manifest**，贴下面的 YAML，第 2 步的 app-level token 和第 3–6 步的设置就一次配好（字段名照 [App manifest 参考](https://docs.slack.dev/reference/app-manifest)）。不选 AI agent 模板：它带 assistant 的 scope 和事件，本机器人用不到。manifest 生成不了 app-level token，第 2 步的 token 仍要手动生成；之后接着做第 7 步。2026-11-25 真实验收用的就是这份 manifest。

   ```yaml
   display_information:
     name: data-agent
   features:
     app_home:
       home_tab_enabled: false
       messages_tab_enabled: true
       messages_tab_read_only_enabled: false
     bot_user:
       display_name: data-agent
       always_online: true
   oauth_config:
     scopes:
       bot:
         - chat:write
         - im:history
         - channels:history
   settings:
     event_subscriptions:
       bot_events:
         - message.im
         - message.channels
     interactivity:
       is_enabled: true
     socket_mode_enabled: true
     org_deploy_enabled: false
     token_rotation_enabled: false
   ```

   要用私有频道或多人私聊，在 `scopes.bot` 加 `groups:history` / `mpim:history`，在 `bot_events` 加 `message.groups` / `message.mpim`。第 2–6 步是手动配置时的做法，也说明 manifest 每一项的来由。
2. **打开 Socket Mode**：在 app 设置的 Socket Mode 页打开 Enable Socket Mode。打开时设置页会引导你生成 app-level token，也可以在 Basic Information → App-level tokens 里生成，token 以 `xapp-` 开头。给 token 加 scope `connections:write`。这个 scope 的依据是工单 131 读的 [connections:write](https://docs.slack.dev/reference/scopes/connections.write) 页（"Grants permission to generate websocket URIs and connect to Socket Mode"）；Socket Mode 页和 [apps.connections.open](https://docs.slack.dev/reference/methods/apps.connections.open) 页都没提它，后者写的是 "No scopes required"，只要 app-level token。Socket Mode 下，事件和交互都不需要、也不能填 Request URL。Socket Mode 的 app 不能上公开的 Slack Marketplace。（[Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode)）
3. **Bot scopes**：在 OAuth & Permissions 的 Bot Token Scopes 里加这些：
   - `chat:write`：发消息、编辑消息（[chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage)）。
   - `im:history`：私聊。
   - `channels:history`：公开频道。
   - `groups:history`：私有频道（要用的话再加）。
   - `mpim:history`：多人私聊（要用的话再加）。

   每个 scope 对应的事件见第 4 步。
4. **订阅事件**：在 Event Subscriptions 打开 Enable Events，在 Subscribe to bot events 里加：
   - `message.im`（要 `im:history`）
   - `message.channels`（要 `channels:history`）
   - `message.groups`（要 `groups:history`）
   - `message.mpim`（要 `mpim:history`）

   出处见 [message 事件](https://docs.slack.dev/reference/events/message)和各子页。**不要**订阅 `app_mention`：机器人从 `message.*` 里判断有没有被 @，再订阅一遍，同一条 @ 就会收到两次。`message.*` 里带 `bot_id` 或 `subtype` 的事件（机器人发的、编辑、删除）都丢掉。官方文档写了 bot 发的消息带 `bot_id`（[bot_message](https://docs.slack.dev/reference/events/message/bot_message)），但没写机器人会不会收到自己发的消息（未确认，真实验收时看日志）。
5. **打开私聊**：App Home → Show Tabs，打开 Messages Tab（[App Home](https://docs.slack.dev/surfaces/app-home)）。不打开的话，用户没法私聊机器人。设置页里可能还有一个 "Allow users to send Slash commands and messages from the messages tab" 勾选框，官方文档里没查到（未确认），看到就勾上。
6. **打开交互（按钮）**：Interactivity & Shortcuts 里打开 Interactivity。Socket Mode 下不用填 Request URL（[Handling user interaction](https://docs.slack.dev/interactivity/handling-user-interaction)）。点按钮后要在 3 秒内确认，机器人收到就先确认。
7. **装进工作区，拿 bot token**：Install App → Install to Workspace。装好后在 OAuth & Permissions 页的 OAuth Tokens 下面有 Bot User OAuth Token，以 `xoxb-` 开头（[App Home](https://docs.slack.dev/surfaces/app-home)、[App settings quickstart](https://docs.slack.dev/app-management/quickstart-app-settings)、[Tokens](https://docs.slack.dev/authentication/tokens)）。以后改了 scope 要重新安装（未确认）。
8. **写进 env 文件**：`~/.config/data-agent/env` 里加两行（`chmod 600`）。

   ```bash
   DATA_AGENT_SLACK_BOT_TOKEN=...   # xoxb-...
   DATA_AGENT_SLACK_APP_TOKEN=...   # xapp-...
   ```

   启动前加载：`set -a && . ~/.config/data-agent/env && set +a`。
9. **填允许名单**：自己的 member id 的找法是在 Slack 里打开自己的 profile，点 ⋮，选 Copy member ID（[App Home](https://docs.slack.dev/surfaces/app-home) 里的说法）。把它填进仓库外的 `im.toml` 的 `allowed_users`（格式见 README）。
10. **把机器人拉进频道**：在频道里 `/invite @<机器人名>`。机器人只看得到它在的频道（这一步没查到官方出处，未确认）。

第 8 步只是本仓库自己的约定，没有外部出处。

几点说明：

- Slack bot 没有能用的 typing 提示。旧的 RTM API 有（[Legacy RTM API](https://docs.slack.dev/legacy/legacy-rtm-api)："Granular permission Slack apps cannot use the RTM API"），但新的 app 用不了；[`assistant.threads.setStatus`](https://docs.slack.dev/reference/methods/assistant.threads.setStatus) 只给 AI 类 app 用。所以机器人不显示 typing，进度看状态消息。
- 断线由 slack_sdk 自己重连。bot token 错了，启动时 `auth.test` 就失败，日志里写一行，机器人不起来，网页照常可用。连不上 Socket Mode（例如 app token 错了）时，slack_sdk 每 10 秒重试一次，每次写一行日志。
- Press 是否私聊，按频道 id 以 `D` 开头来判断（未确认，真实验收时核对）。

## 以后才要

| # | 资源 | 用于 |
|---|---|---|
| 6 | Harbor Hub 账号 | 上榜提交（人做），data-eng-bench 提交要 Hub 上的 job 链接 |
| 7 | 服务器上的 PostgreSQL ≥17.5、MongoDB 8（用 Docker 起） | 工单 13 DAB；jaffle-shop（工单 08）在本地用 colima 起。服务器上由 `da-remote benchmarks` 起（工单 21） |
| 8 | DAB 数据（`dataset_manifest.tsv` 36 个文件约 13 GB） | 工单 13；服务器上由 `da-remote benchmarks` 下载并校验（工单 21） |
| 9 | Langfuse 项目（Cloud US 区，或自托管）的一对 API key | 可选的 trace 导出（`.scratch/observability/spec.md`）。三个变量 `DATA_AGENT_LANGFUSE_PUBLIC_KEY`、`DATA_AGENT_LANGFUSE_SECRET_KEY`、`DATA_AGENT_LANGFUSE_BASE_URL` 都设了才导出，缺一个就什么都不发。本机写进 `~/.config/data-agent/env`；服务器上**由人**加进服务器的 env 文件（agent 不读也不写它），Harbor 的 trial 从那里经 `evals/harbor_agent.py` 拿到，只有 `--trace` 且这道题第一个 trial 才传进容器。Cloud 和自托管只差 `BASE_URL`。往 Cloud 只发公开演示数据（jaffle-shop、仓库夹具、公开榜单） |

不需要：GPU、云数仓账号。

## LiveSQLBench（工单 128）

服务器上由 `da-remote benchmarks` 准备，都在 `da-benchmarks/livesqlbench/` 下（`python -m evals livesqlbench prepare`）：

- **公开数据**：HF `birdsql/livesqlbench-base-lite`，钉在 revision `507d7d98f477b9d6a990628ea021bd2501cfd6e2`，放在 `data/`（`livesqlbench_data.jsonl` 和 18 个库各自的 `db_assets` 三个文件）。已在的文件不再下载。
- **dump**：Google Drive 文件 `1QIGQlRKbkqApAOrQXPqFJgUg8rQ7HRRZ`（上游 `LiveSQLBench-CLI/README.md:53`），用 `uv run --with gdown==5.2.0` 下载到 `dump/archive`。上游没有校验和：第一次下载时把 sha256 记进 `dump/archive.sha256`，之后每次比对，对不上就停。解开到 `dumps/`。
- **镜像**：每个库一个 `da-lsb-db-<db>:<版本>`（`postgres:14.12`），加 agent 所在的 `da-lsb-main:<版本>`；版本是 `evals/livesqlbench_images/` 里构建文件的摘要，构建文件一改就是新 tag。只在服务器本地，不推送到任何镜像仓库。建完核对 18 个库的表数和列数之和：175 张表、2286 列（上游根目录 `README.md:204-207`），对不上就停。
- **写库口令**：`~/.config/data-agent/livesqlbench.env`（mode 600），由 `benchmarks` 第一次运行时随机生成，里面只有 `LSB_PG_WRITE_PASSWORD`。它是验证阶段的写库角色 `lsb_writer` 的口令，构建镜像时经 `docker build --secret` 传入。只有 `da-remote start … -- ablate --benchmark livesqlbench …` 和 `da-remote start … -- livesqlbench-parity …` 的运行会 source 它，进 Harbor 宿主进程（对拍是它自己起的验证容器）的环境；任务里写的是 `${LSB_PG_WRITE_PASSWORD}` 模板，Harbor 验证时才解析。agent 用只读角色 `lsb_reader` 连库。这个文件丢了而镜像还在时，`benchmarks` 会停下：删掉 `da-lsb-db-*` 镜像再跑。
- **标准答案**：人申请到以后放在 `da-benchmarks/livesqlbench-answers.jsonl`（每行一道题的 `instance_id`、`sol_sql`、`test_cases`、`external_knowledge`），只放服务器，不进 git。没有这个文件时任务照样生成，`task.toml` 标 `gold_answer = false`，验证脚本写 0 并注明"没有标准答案"：只能冒烟，不算分；`ablate --benchmark livesqlbench --split holdout` 拒绝开跑。放好以后重跑 `da-remote benchmarks` 重新生成任务（工单 136）。
- **任务**：`tasks/`，180 道 `Query` 题，跑法 `ablate --benchmark livesqlbench --tasks-dir ../da-benchmarks/livesqlbench/tasks …`。`da-remote check` 列出数据、镜像、`main` 镜像能否 import 验证脚本要的 `psycopg` 和 `json_repair`、口令文件权限、任务数、有标准答案的题数，以及 `docker_image` 不是当前 `main` 镜像的旧任务数（构建文件改过以后要重跑 `benchmarks`；每次都整份重新生成任务）。
- **验证在独立环境里跑**：`task.toml` 写 `[verifier] environment_mode = "separate"`，并把 `/app/pred.json` 声明成 artifact。Harbor 在 agent 跑完后取回 `pred.json`、停掉 agent 的环境，再起一份新的同样的 compose 环境（新的 `main` 容器和从镜像新起的库），把 `pred.json` 传到原路径后跑验证。写库口令只在 `[verifier.env]`，那时 agent 起的进程都已停掉。`pred.json` 照上游先 `json.loads`，失败再用 `json-repair==0.58.5` 修复（诊断里记 `pred_json: repaired`）。验证脚本自己出错或连不上库、建不了 process 库时不写 `reward.txt`，只写诊断（`outcome: verifier_error` 和错误类名）：Harbor 记 `RewardFileNotFoundError`，trial 没有 reward，`ablate` 的冒烟会停下。正式运行里这样的 trial 按基础设施错误处理（工单 136，人 2026-11-25 定）：`evals/report.py` 的 `verifier_failed` 按诊断的 `outcome: verifier_error` 认出它（只认 `lsb-` 题），报告不计分、要求先重跑；`ablate` 再跑同一个 `--out` 时自己删掉这些 trial（结果和事件文件先留进 `OUT/dropped/`），再 `harbor job resume` 补跑。不用 `-f RewardFileNotFoundError`：Harbor 的 `-f` 只按异常类型删，会连 agent 自己造成的没有 reward（仍算 0）一起删掉重跑。
- **只读实测**：`da-remote exec -- bash -c 'set -a && . ~/.config/data-agent/livesqlbench.env && set +a && uv run --no-sync python -m evals livesqlbench readonly --task ../da-benchmarks/livesqlbench/tasks/<task>'`：起这道题的库镜像和一个带着任务自己 `[environment] env` 的 `main` 容器，从 `main` 里试只读角色的写操作、不带口令和用 `123123` 连库，再查 `main` 容器的环境里有没有写库口令；只打印 PASS/FAIL。
- **口令不进运行产出**：同样 source 口令文件后跑 `uv run --no-sync python -m evals livesqlbench leaks --dir runs/<run>`，只打印次数。口令只在服务器上，所以只在服务器上数：取回的 `runs/<run>/` 是服务器上那个目录的一部分，服务器上为 0 就盖住了取回的那部分。
- **oracle 与官方对拍**（工单 136）：`da-remote start lsb-parity -- livesqlbench-parity run --root ../da-benchmarks/livesqlbench --out runs/lsb-parity --trials runs/lsb-smoke`（`--trials` 可重复，指向已有运行目录；只读开发集 trial 的 `agent/pred.json`，留出集的只计数）。每个库起一个库容器，每道题先用标准 SQL 当预测（oracle），再用每个开发集 `pred.json`，各跑一次我们的验证（任务自己的 `tests/test.sh`，`main` 镜像）和上游的验证（上游 @ `e15cd22` 的 `template/tests/{test.sh,evaluator.py}` 加 `evaluation/src/`，镜像 `da-lsb-upstream-eval:*` 照上游 `python:3.10-slim` + `postgresql-client` + `requirements.txt` 在服务器上建，上游代码拉到 `da-benchmarks/livesqlbench/upstream/`），比较两边的 reward。上游会把每条查询（含标准 SQL）写进日志，所以两边的输出都写在 `runs/` 以外的 `da-benchmarks/livesqlbench/parity-scratch/`，读成 reward 和标记名后逐题删掉，跑完整个删掉。汇总写 `runs/lsb-parity/reports/parity.json` 并打印：oracle 的对错数和类别（执行出错、结果为空、结果不同）、两批的一致数；开发集逐题列题号和我们这边的诊断（不含 SQL 和结果），留出集只给数目。全对、全一致时退出码 0。任务里的验证脚本和本 checkout 的不一样时拒绝开跑：先重跑 `da-remote benchmarks`。`pull lsb-parity` 只取回 `reports/` 和 `run.log`。
- **标准 SQL 不进运行产出**：`da-remote exec -- uv run --no-sync python -m evals livesqlbench-parity leaks --answers ../da-benchmarks/livesqlbench-answers.jsonl --dir runs/lsb-parity`，数每条标准 SQL（原样和 soft EX 清洗后，空白归一）出现的次数，只打印次数；和口令一样只在服务器上数。
- **许可证**：仓库根目录 MIT，`LiveSQLBench-CLI/` 是 Apache-2.0，README 和 HF 数据卡写 CC BY / CC BY-SA，写法不一致（取证笔记 `docs/research/2026-11-23-livesqlbench.md`）。所以数据、dump 和镜像都只在服务器上，不重新发布；复制进本仓库的代码见 `NOTICE`。

## 上游参考仓库

放在仓库外 `~/dev/AI/data-agent-refs/`，按 `docs/research/` 里记录的 commit 检出：harbor、data-eng-bench、SignalPilot、signalpilot-plugin、ktx、DataAgentBench、BIRD-Interact。DAB 无 LICENSE，不复制进本仓库（ADR-0007）。已于 2026-10-14 clone。

## 本机检查（2026-10-07）

- arm64 Mac，10 核，16 GB 内存；内置盘只剩约 5.7 GB（2026-10-15，98%），外接盘 sd710 剩约 650 GB → 只做开发，不跑批量评测。
- 已装：Docker CLI 29.3 + colima（profile `m1-kind`：4 核 / 8 GB / 30 GB，数据在 sd710；没有 Docker Desktop）、uv 0.10.8、Node 24、gh（已登录）、Claude Code 2.1.292、Codex CLI 0.160.1。
- Harbor 已装（`uv tool install harbor`，2026-10-15）；git-lfs、psql 到工单要用时再装。
