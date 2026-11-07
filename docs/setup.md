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

改动验收（ADR-0008，工单 29）：`uv run python -m evals accept --old OUT_OLD/jobs/full --new OUT_NEW/jobs/full --manifest evals/manifest.json --target <题> [--target <题> ...] --regression evals/regression.json [--rerun <补跑 job> ...] [--json]`。

- 两份 job 都要覆盖整个开发集、每题 `--trials`（默认 3）次，新版不能有基础设施报错；job、目标题、回归集、补跑里有任何非开发集题就拒绝（只报个数，ADR-0004），退出 2。
- 回归集文件是开发集题名的 JSON 列表，如 `["dbt-daily-order-summary", "pos-operations"]`；空列表 `[]` 表示还没有回归集。
- 三条过关条件：新版开发集成功次数不少于旧版（点估计不低，相等算过）；目标题成功次数合计严格上升；回归题新版一轮 k/k。低于 k/k 的题列在 `rerun these tasks` 里，在新版上补跑 k 次，补跑 job 用 `--rerun` 加进来再判；补跑成功率 ≤1/3 算退步。补跑 job 的 version 头要和新版一致。
- 旧版复用：两份 job 的每个 trial（基础设施报错的除外）都有 version 头，且除 `git_commit`、`skill_version` 外字段都一致（每个字段每个 trial 都要有，缺了显示为 `(missing)`；`config`、`layers`、`model`、`sdk_version`、`cli_version` 是每次运行都写的，两边都没有也算缺）；各自 `--out` 下最近一份运行记录（`records/`）的 benchmark commit、机器标签一致且都是非空字符串；旧版最早开始的 trial 到新版最晚结束的 trial 不超过 14 天（基础设施报错以外的旧版 trial 都要有 `started_at`、新版 trial 都要有 `finished_at`，缺了就算年龄未知）；旧版那一轮没有基础设施报错（job 里没有，`dropped/<config>/` 里也没有被续跑删掉的；`ablate` 续跑前把每个要删的 trial 的 `result.json` 和事件文件存到那里）。任何一条不满足，判 `rerun-old` 并列出原因；旧版有基础设施报错时前两条不计分。
- 命令不查 `--old` 是不是上一次合入的版本，由调用者保证。ADR-0008 的验收就是 `--trials 3`；别的值只用来试跑（如 k=1 的 `runs/dev-2`）。工单 29 之前的 `--out`（dev-2、dev-3）里，续跑删掉的、没写事件文件的 trial 没在 `dropped/` 留下 `result.json`，这种基础设施报错查不出来。
- 结论：`pass`（退出 0）；`fail`、`rerun-regression`、`rerun-old`（退出 1）。回归题已判退步时，不管旧版能否复用都是 `fail`。

密钥只放在仓库外的 `~/.config/data-agent/env`（照 `.env.example` 填，`chmod 600`），`make test-integration` 读取；不放仓库里的 `.env`，不贴进对话或文档。

## 以后才要

| # | 资源 | 用于 |
|---|---|---|
| 6 | Harbor Hub 账号 | 上榜提交（人做），data-eng-bench 提交要 Hub 上的 job 链接 |
| 7 | 服务器上的 PostgreSQL ≥17.5、MongoDB 8（用 Docker 起） | 工单 13 DAB；jaffle-shop（工单 08）在本地用 colima 起。服务器上由 `da-remote benchmarks` 起（工单 21） |
| 8 | DAB 数据（`dataset_manifest.tsv` 36 个文件约 13 GB） | 工单 13；服务器上由 `da-remote benchmarks` 下载并校验（工单 21） |

不需要：GPU、云数仓账号、LiveSQLBench 标准答案邮件（二期）。

## 上游参考仓库

放在仓库外 `~/dev/AI/data-agent-refs/`，按 `docs/research/` 里记录的 commit 检出：harbor、data-eng-bench、SignalPilot、signalpilot-plugin、ktx、DataAgentBench、BIRD-Interact。DAB 无 LICENSE，不复制进本仓库（ADR-0007）。已于 2026-10-14 clone。

## 本机检查（2026-10-07）

- arm64 Mac，10 核，16 GB 内存；内置盘只剩约 5.7 GB（2026-10-15，98%），外接盘 sd710 剩约 650 GB → 只做开发，不跑批量评测。
- 已装：Docker CLI 29.3 + colima（profile `m1-kind`：4 核 / 8 GB / 30 GB，数据在 sd710；没有 Docker Desktop）、uv 0.10.8、Node 24、gh（已登录）、Claude Code 2.1.292、Codex CLI 0.160.1。
- Harbor 已装（`uv tool install harbor`，2026-10-15）；git-lfs、psql 到工单要用时再装。
