# LiveSQLBench 现状与接入成本取证

日期:2026-11-23
方法:读一手源码与官方页面,只取证,不做设计决定。引用格式为 `仓库@commit: 路径:行号` 或 `URL(访问 2026-11-23)`。没查到的标 `未确认`。

被读取的来源:
- livesqlbench:`https://github.com/bird-bench/livesqlbench` @ `e15cd22`(2026-05-27,`git clone --depth 1` 到会话临时目录;本机 `data-agent-refs/` 里没有这个仓库)。下文 `LSB:` 指它。
- harbor:本地参照 clone `~/dev/AI/data-agent-refs/harbor` @ `dc933be`(2026-10-10),`pyproject.toml:3` 版本 0.24.0;上游 `main` 当日(2026-11-23)`pyproject.toml` 版本 0.27.0。
- BIRD-Interact:本地 `~/dev/AI/data-agent-refs/BIRD-Interact` @ `451fe2c`(2026-03-29)。
- 官方页面:`https://livesqlbench.ai`、HuggingFace 数据集卡(均 2026-11-23 访问,经 WebFetch 摘要,非逐字)。

---

## 结论摘要(只列事实)

- LiveSQLBench 现有四个数据集:Base-Lite(270 题)、Base-Lite-SQLite、Base-Full v1(600 题)、Large-v1(480 题)。官网写 1.5 版 "coming soon"、2.0 开发中。只有 Base-Lite 公开了 SELECT/管理题划分(180/90);Full、Large 没给划分,只给 `category` 字段取值 `Query` / `Management`。
- 官方 Harbor 路径仍是 `LiveSQLBench-CLI/`:vendor 的 Harbor 版本仍是 `0.1.45`(`LSB: LiveSQLBench-CLI/pyproject.toml:3`)。Harbor 上游 registry(80 个数据集)里没有 livesqlbench;上游 `adapters/` 里有 `bird_bench`(BIRD SQL dev,SQLite)和 `spider2-dbt`,没有 LiveSQLBench。
- 评分是在 PostgreSQL 里执行 agent 的 SQL,和 `sol_sql` 比对(SELECT 题用 soft EX,管理题用测试用例)。`sol_sql`、`test_cases`、`external_knowledge` 不公开,需发邮件申请。
- 榜单提交仍是 Google Doc 说明;官网另有"提交代码库、由官方流水线评测"的 verified 标记。该文档本次抓不到,具体材料和审核周期未确认。

ADR-0001 四条旧理由逐条核对:

| 旧理由 | 现在是否成立 | 依据 |
|---|---|---|
| vendor 旧版 Harbor 0.1.45 | 成立。仍是 0.1.45;本仓库钉 0.24.0,上游 main 已 0.27.0 | LSB `LiveSQLBench-CLI/pyproject.toml:3`;本仓库 `pyproject.toml:30`;上游 pyproject |
| API 不同 | 成立(推断,见 2.3):vendor 版 agent 目录里是 `base.py` + `install-*.sh.j2` 模板,与 0.24 的 `install()/run()` 是同一类差异;适配器生成的 `task.toml` 用 `version = "1.0"` | LSB `LiveSQLBench-CLI/src/harbor/agents/installed/`;`adapter.py:297-320` |
| 需打镜像 | 成立,且更重。要自建 agent 镜像,并且每个数据库一个 PostgreSQL 镜像(`build_db_images.sh`);数据库 dump 来自 Google Drive | LSB `LiveSQLBench-CLI/README.md` "Build Docker images" |
| 提交走 Google Doc | 成立。README 仍指向 Google Doc 提交说明 | LSB `README.md:311-313` |

另有一条旧结论需要更新:`HANDOFF.md:143` 写 "邮件 bird.bench25@gmail.com 标题 `[livesqlbench-base-lite GT&Test Cases]` 自动回" 是申请 GT 的方式,不是提交方式;这点仍成立(HF 数据集卡,2026-11-23)。

---

## 1. LiveSQLBench 当前状态

### 1.1 版本与题数
- Base-Lite:18 个库、270 题,"180 SELECT-only, 90 Management",2025-05-28 发布。(HF `birdsql/livesqlbench-base-lite` 卡:270 行,dev 分割;LSB `README.md` 新闻)
- Base-Lite-SQLite:2025-07-28 发布,SQLite 方言。(LSB `README.md:46` 附近新闻)
- Base-Full v1:22 个库、600 题,2025-09-04 发布。(LSB `README.md:41`)
- Large-v1:18 个库、480 题,每库约 54 表、约 986 列,2026-03-02 发布。(LSB `README.md:37`;HF `birdsql/livesqlbench-large-v1` 卡)
- 官网(`https://livesqlbench.ai`)新闻:1.5 版 "coming soon"、2.0 开发中、2026-11-09 另发 Data Intelligence Index v1.0 "Muscle"(500 题,来自 9 个基准,200 公开 300 留出;这是另一个项目)。
- 内部矛盾:官网/README 的特性表里 Large 一行出现过 600 题,其他位置都写 480。以 HF Large-v1 卡的 480 为准,表里 600 为 `未确认`。

### 1.2 SELECT 与管理题划分
- 只有 Base-Lite 给出数字:180 SELECT(`category = Query`)+ 90 管理(`category = Management`,即 CRUD)。(HF Base-Lite 卡)
- Full、Large:`category` 字段同样有这两个取值,但卡上没给各自题数。划分数 `未确认`。
- 本仓库 `docs/plan.md:98` 写的"只跑 180 道 SELECT 题"仍与 Base-Lite 的数字一致;对 Full/Large 不成立(无划分数)。
- 官方 CLI 适配器没有按类别过滤的参数:`run_adapter.py` 只有 `--data-root --data-jsonl --gt-jsonl --agent-image --eval-src-dir --db-dump-root --output-dir --ids --ids-file --limit --force --list`。只跑 SELECT 题要靠 `--ids-file` 自己列。(LSB `LiveSQLBench-CLI/adapters/livesqlbench/run_adapter.py` 参数表)

### 1.3 方言与环境
- 主版本 PostgreSQL。任务说明模板里写 `dialect: PostgreSQL`、`version: 14.12`(LSB `.../template/documents/user_query.md`);CLI 的数据库镜像基于 `postgres:14.12`(`.../template/environment/Dockerfile.postgresql:1`)。
- Large-v1 的 dump 官方建议用更新的服务端:`build_db_images.sh ... --postgres-base-image postgres:18`(LSB `LiveSQLBench-CLI/README.md`,"Large-v1 (newer dumps often need a newer server image)")。Large 实际要求的最低版本 `未确认`。
- 官方评测环境:Docker Compose 三个容器 `livesqlbench_postgresql`、`livesqlbench_postgresql_base_full`、`livesqlbench_so_eval_env`;预构建镜像在 Docker Hub(`docker.io/shawnxxh/bird-interact-postgresql:latest`、`...-full:latest`、`shawnxxh/livesqlbench-so-eval:latest`)。(LSB `README.md:41-48` 新闻与 "Evaluation Environment Setup";`evaluation/docker-compose.yml:5,17,29`)镜像体积 `未确认`。

### 1.4 许可证
- 仓库 `LICENSE` 是 MIT(LSB `LICENSE`,版权 "bird_sql");`README.md:6-7` 徽章写 CC BY-SA 4.0。
- HF 数据集卡自相矛盾:元数据 `cc-by-4.0`,卡尾 `cc-by-sa-4.0`(Base-Lite 与 Large-v1 卡都是)。哪个有效 `未确认`。
- 官网未写许可证。

---

## 2. 官方现在推荐怎么跑

### 2.1 官方路径
LSB `README.md:33-35`(2026-04-04 新闻)给了两条:
- **LiveSQLBench-CLI**:"Harbor-based evaluation framework" 面向 CLI agent(OpenHands、Claude Code、Aider 等),写明支持 base-lite 与 base-full-v1;`CLI README` 的流程还包含 Large-v1。
- **LiveSQLBench-Agent**:Google ADK 的文本到 SQL agent 框架,LiteLlm 多供应商,每题数据库隔离(模板库克隆),默认每题 30 步预算。
- 另有原始基线脚本(`config.py` + `run_baseline.sh`)和 Docker Compose 评测。

### 2.2 是否被 Harbor 原生收录
- 上游 `registry.json`(`https://raw.githubusercontent.com/laude-institute/harbor/main/registry.json`,2026-11-23 下载,80 条):名字含 `bird`/`sql`/`spider`/`live` 的只有 `bird-bench`、`spider2-dbt`、`livecodebench`;`grep -ic livesql` 为 0。
- 上游 `adapters/` 目录(GitHub API 2026-11-23):`bird_bench`、`livecodebench`、`spider2-dbt`;没有 livesqlbench。`bird_bench` 是 BIRD SQL dev 的 1,534 题 SQLite 适配器(其 README),不是 LiveSQLBench。
- 本地 clone `harbor@dc933be`:`grep -ril livesql` 无命中。
- 结论:没有被 Harbor 原生收录,只能走 LSB 自带的 `adapters/livesqlbench/run_adapter.py` 生成任务目录。

### 2.3 版本差距
- LSB CLI 依赖:`LiveSQLBench-CLI/pyproject.toml:3` `version = "0.1.45"`(这是 vendored 的 `src/harbor` 自身版本,README 要求 `cd LiveSQLBench-CLI && uv sync` 后 `uv run harbor ...`,即用 vendor 版,不是 `uv tool install harbor`)。
- 本仓库:`pyproject.toml:30` `harbor==0.24.0`,`uv.lock:694` 同。
- 上游 main:0.27.0。
- 差距:本仓库钉的 0.24.0 比 vendor 版新 23 个小版本;vendor 版里内置 agent 仍是 `install-*.sh.j2` 模板风格(`src/harbor/agents/installed/` 目录)。本仓库 `evals/harbor_agent.py:215,246` 是 0.24 的 `BaseInstalledAgent.install()` 风格(`:49` import)。两种 API 能否互通,没有实测,`未确认`;上面"API 不同"按目录结构推断。
- 适配器生成的任务格式:`task.toml` 含 `version = "1.0"`、`[verifier] timeout_sec = 1200`、`[agent] timeout_sec = 1800`、`cpus = 2`、`memory_mb = 4096`、`storage_mb = 20480`、`docker_image = <agent 镜像>`(`adapter.py:297-320`)。生成的任务能否被 0.24 直接读取 `未确认`。
- docker-compose:`main` 服务 + `postgresql` 边车(`healthcheck: pg_isready`),环境变量 `PGHOST=postgresql PGUSER=root PGPASSWORD=123123`(`.../template/environment/docker-compose.yaml`)。边车服务必须是 `pull_policy: never` 的本地镜像 `livesqlbench-postgresql:latest`,所以每个库的镜像要先本地构建。
- 与本仓库离线约定的关系:LSB 模板里没有看到 `allow_internet` 之类的网络开关(`grep -i network` 只命中表名),任务默认是否联网 `未确认`;本仓库 `evals/harbor_agent.py` 注释写明评测不开 `--network`(`:319`),这个约定在 LSB 路径下怎么落地要另查。

---

## 3. 评分方式与答案是否公开

- agent 产出:写 `/app/pred.json`,格式 `{"instance_id": ..., "predicted_sql": [SQL...]}`;说明里写"最多 50 步"(`.../template/documents/README_TASK.md`)。
- 验证:`tests/test.sh` 等 PostgreSQL 就绪后跑 `tests/evaluator.py`,写 `/logs/verifier/reward.txt`,二值(1/0)(`.../tests/test.sh`;`evaluator.py` 文件头 "emitting a binary reward")。
- 评测内容:`evaluator.py:130-175` 读 `/tests/task_payload.json`(含 `sol_sql`、`preprocess_sql`、`test_cases`、`conditions`),对 `category == "Query"` 强制用 `TEST_CASE_DEFAULT`(`LSB evaluation/src/test_utils.py`:先去注释、去 DISTINCT、去 ROUND,再 `ex_base` 比对执行结果);管理题用题目自带测试用例(`test_cases`)。官方 README:"Soft EX metric is used to evaluate SELECT-ONLY tasks; customized test cases are designed for DBA tasks."
- 隐藏答案:是。HF 卡:公开字段 `instance_id, selected_database, query, preprocess_sql, clean_up_sql, category, high_level, conditions, difficulty_tier`(Large 另有 `normal_query`);🔒 隐藏字段 `sol_sql, external_knowledge, test_cases`。拿 GT 要发邮件到 `bird.bench25@gmail.com`,Base-Lite 的标题为 `[livesqlbench-base-lite GT&Test Cases]`,卡上写自动回复约 30 分钟;Large-v1 标题 `[livesqlbench-large-v1 GT&Test Cases]`。
- 防作弊相关事实:
  - 适配器把 `sol_sql` 和 `test_cases` 写进每个任务的 `tests/task_payload.json`(`adapter.py:359-366`),同时 `solution/task_payload.json` 供 oracle 用。GT 一旦申请到,就在本地任务目录里。
  - `external_knowledge` 不公开,但任务说明里把 KB 当成 `db_assets/<db>_kb.jsonl` 给 agent(`README_TASK.md`、`adapter.py:254,270`)——也就是说,走 CLI 路径时 agent 能看到知识库文件。其来源(GT 邮件里的字段还是公开 KB)`未确认`。
  - 评测期间 `tests/` 是否只在验证阶段才进容器,取决于 Harbor 版本行为;本次没有在 0.24 源码里核对,`未确认`。

---

## 4. 提交与榜单流程

- README:"For instructions on submitting ... please refer to the LiveSQLBench submission document"(Google Doc,`LSB README.md:311-313`)。
- 官网(2026-11-23):有 "Submission Guidelines" 文档链接;"Verified" 状态需要提交代码库、由官方流水线评测,带标记;联系邮箱 `bird.bench25@gmail.com`。榜单页写 "Last Updated: 2026-03-02",但部分条目日期更晚。
- Google Doc 正文:WebFetch 抓取返回 HTTP 400/重定向失败,没读到。所需材料、是否邮件、有无人工审核周期、审核时长:均 `未确认`。
- 榜单没有走 Harbor Hub 上传或 PR 流程的迹象(未见相关说明),但此条是从缺失推断,`未确认`。

---

## 5. 资源与花费的事实输入

- 数据库体量(官方 `check_db_metadata.py` 期望值,LSB `README.md` "Expected results"):
  - Base-Lite:18 库、175 表、2286 列、平均每表 1,038.48 行、合计约 207.15 MB。
  - Base-Full:22 库、244 表、2011 列、平均每表 1,121.19 行、合计约 272.00 MB。
  - Large-v1:18 库、971 表、17,749 列、平均每表 2,056.48 行、合计 746.33 MB(HF 卡:约 2M 行)。
- 镜像体积:`未确认`。每个库一个 PostgreSQL 镜像(基于 `postgres:14.12`,Large 建议 `postgres:18`);agent 镜像基于 `python:3.10-slim`(`Dockerfile.claudecode:1`)再装 agent。dump 压缩包经 Google Drive 下载,大小未读。
- 每任务容器配额(适配器默认):2 CPU、4096 MB 内存、20480 MB 存储;agent 超时 1800 s;验证超时 1200 s;SQL 单次最长 600 s(`adapter.py:297-320`;`README_TASK.md`)。
- 轮数:任务说明写"最多 50 步"(CLI);LiveSQLBench-Agent 默认每题 30 步(LSB `README.md` "LiveSQLBench-Agent");OpenHands 示例 `MAX_ITERATIONS=50`、`--agent-timeout-multiplier 0.333`(CLI README)。官方的单题实际平均轮数/token 数:`未确认`。
- Large-v1 平均提示约 84K token(LSB `README.md:37` 新闻,"~84K avg prompt tokens")。
- 参考分数(官方页面,2026-11-23):
  - 官网榜首(榜单为 "Base" 集,指标为通过全部测试用例的比例):AntQL 1.1(Claude Opus 5.5)57.00;AskRUI 50.33;DIA(C3 AI)48.00;ShanHe 2.0 42.83(verified);Jitto Build(Claude Opus 5.5, high)41.33。榜单对应的具体数据集(Lite 还是 Full)与题数口径 `未确认`。
  - 官方 README 讨论里的旧 Base-Lite 数字(2025-05-28):o3-mini 47.78%(每题 $0.0233)、GPT-4.1 44.10%($0.0336)、Claude Sonnet 4 42.59%($0.0623)。官方页面未说明这些数字是否含管理题,`未确认`。
  - 本仓库 `HANDOFF.md:143` 记的"榜首 48.0(2026-05)"已被官网当前榜单(57.00)取代。
- 单题花费、总花费:无官方 CLI 路径的数据,不在此估算。

---

## 6. 与 BIRD-Interact 的关系

BIRD-Interact 基于 LiveSQLBench:官方新闻写 "our BIRD-Interact, based on LiveSQLBench",2026-02 获 ICLR 2026 Oral(LSB `README.md:39`;本地 `BIRD-Interact/README.md:69`)。它是带用户模拟器的多轮交互版本(覆盖完整 CRUD),典型评测 1,968-5,496 轮交互,最强推理模型约 24%/18%(`BIRD-Interact/README.md:94-96`);另有 Google ADK 实现 BIRD-Interact-ADK(`:67`,agent、用户模拟器、数据库环境三服务)。LiveSQLBench 本身是单轮题。

---

## 未确认项汇总

- 提交 Google Doc 的正文(材料、渠道、审核周期)。
- 榜单("Base" 集)对应的具体数据集与口径;各条目的步数/花费。
- Full、Large 的 SELECT/管理题数;Large 在特性表里的 600 vs 480。
- 数据集许可证到底是 CC BY 4.0 还是 CC BY-SA 4.0;仓库 MIT 是否覆盖数据。
- Docker 镜像与 dump 的实际体积。
- vendor 的 Harbor 0.1.45 与 0.24 API 的实际兼容性;LSB 生成的任务目录在 0.24 下能否直接跑。
- 任务容器默认是否联网;`tests/` 与 GT 在评测中是否对 agent 容器可见(取决于 Harbor 版本行为,本次未核对)。
- KB(`<db>_kb.jsonl`)的来源与是否公开。
- 官方单题平均轮数/token。
