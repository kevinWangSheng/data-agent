# SignalPilot MCP server 复用评估(2026-09-21)

研究对象:
- 主仓库 `SignalPilot`(Apache-2.0),本地克隆:`/private/tmp/claude-501/-Users-shenghuikevin-dev-AI-data-agent/9033292c-ed20-4b41-b53c-003a04b95c28/scratchpad/SignalPilot`
- 插件仓库 `signalpilot-plugin`,同目录下 `scratchpad/signalpilot-plugin`

下文路径缩写:`G/` = `SignalPilot/signalpilot/gateway/gateway/`,`P/` = `signalpilot-plugin/`。所有结论来自读源码;未核实处标 `未确认`。

## Bottom line

- **MCP server 不是独立进程,而是 FastAPI gateway 的一个挂载点**(`G/main.py:697-715` 把 FastMCP 的 streamable-http app 挂到 `/mcp`;`G/mcp/server.py:60-95` 另有 stdio 入口,但同样 import 整个 `gateway` 包)。凡是碰 connection 的工具都走 `_store_session()` → SQLAlchemy async session → **强制 Postgres 元数据库**(`G/db/engine.py:32-35` 无 `DATABASE_URL` 直接抛错,`init_db` 用 Postgres advisory lock + Alembic,`G/db/engine.py:108-150`)。没有 SQLite/内存元数据库模式。
- **本地开发是有「无 auth」模式的**:`SP_DEPLOYMENT_MODE` 不为 `cloud` 且 DB 里没有 API key 时,MCP 中间件把所有请求当 `org_id=user_id="local"` 并授予全部 scope(`G/auth/mcp_api_key.py:292-345`);stdio 也同样(`G/mcp/server.py:59-66`)。但 org/user 表、audit 表、Alembic 迁移仍然都要跑——「无 auth」≠「无 Postgres」。
- **整套 gateway 最小可跑集**:`db`(postgres:17)+ `gateway` 两个容器即可让 `/mcp` 可用(不需要 web/notebook/sandbox/minio/mailpit);但 gateway 镜像的依赖树是全量的(`claude-agent-sdk`、`boto3`、`vercel`、`alembic`、`pymssql`、`psycopg2` 等,`gateway/pyproject.toml`),启动时还会初始化 notebook proxy client、JWT secret 文件、git repos 目录、health monitor、BYOK provider(`G/main.py:55-130`)。裸机 `uvicorn gateway.main:app` 需要自己准备 `DATABASE_URL`,其余本地模式都有自动生成默认值(加密 key 落盘 `~/.signalpilot`,`G/store/crypto.py:110-121`)。
- **DB 支持**:12 种(postgres/duckdb/mysql/snowflake/bigquery/redshift/clickhouse/databricks/mssql/trino/sqlite/xata,`G/models/connections.py:18-30`,`G/connectors/registry.py:62-75`)。**MongoDB 不支持**——全 gateway 只在 ClickHouse 危险函数黑名单里出现过 `"mongo"` 字样(`G/engine/denylists.py:102`),没有 connector、没有 DBType。
- **两个 verifier 实际依赖的 MCP 工具只有 6 个**:`list_tables`、`map_columns`、`check_model_schema`、`audit_model_sources`、`verify_model_values`、`query_database`(`P/agents/verifier.md:24-45`,`P/agents/value-verifier.md:22-46`),外加 Claude Code 内建 Glob/Read/git。`analyze_project_db` 是 `dbt-workflow` skill Step 1 用的,不在 verifier 里(`P/skills/dbt-workflow/SKILL.md:30-33`)。这 6 个工具的核心逻辑合计约 **700-900 行纯 Python**(见 §3 表),依赖只有「一个能 `execute(sql) -> list[dict]` 和 `get_schema() -> dict` 的 connector」+ sqlglot;`_store_session`/`pool_manager`/audit 这些外围可以整体替换成一条本地连接。
- **只读 SQL guard 是可整体搬走的独立模块**:`G/engine/{validation,denylists,transforms,dialects,_sqlglot}.py` 合计 **651 行**,唯一第三方依赖 `sqlglot>=25`,包内只互相 import,不碰 store/db;有独立测试 `tests/test_sql_engine.py`、`tests/test_sql_governance_dialects.py`(只 import `gateway.engine`)。复制这 5 个文件 + 2 个测试文件即可在 data-agent 里直接用。
- **推荐**:solo 开发者、目标只是 Postgres/DuckDB 上的验证工具 → **自实现 6 个工具 + 拷贝 `engine/` guard**(估 1-2 天,含把 DuckDB 专用 SQL 如 `SHOW TABLES`/`PRAGMA table_info` 泛化成 information_schema)。跑整套 gateway 的代价:2 容器 + 一个全量 Python 依赖树 + Postgres 元数据库 + 30 张 Alembic 表,换来的是 30+ 工具和审计/governance 管道;若后续要 dbt_execute、schema diff、governed query 路由这些,再考虑整套复用。

## 1. MCP server 暴露的工具与 connector 支持

### 1.1 工具注册机制

所有工具通过 `@audited_tool(mcp)` 注册(`G/mcp/audit.py:311-328` 把 `mcp.tool()` 包了一层审计),`G/mcp/tools/__init__.py` 导入各模块触发注册。工具函数 grep(`grep -A1 "^@audited_tool(mcp)"`)得到 **62 个工具**:

| 模块 | 工具 |
|---|---|
| `tools/query.py` | plan_query, query_database, check_budget, explain_query, validate_sql, query_history, estimate_query_cost, debug_cte_query |
| `tools/schema/catalog.py` | describe_table, list_tables |
| `tools/schema/exploration_*.py`, `explore_value.py`, `stats.py`, `summary.py`, `dates.py`, `relationships.py` | explore_columns, explore_table, explore_column, schema_statistics, schema_overview, get_date_boundaries, find_join_path, get_relationships |
| `tools/schema/ddl.py` | schema_diff, schema_diff_branches, xata_branch_diff, xata_list_branches, create_xata_branch, delete_xata_branch, get_dbt_profile, schema_ddl, schema_link |
| `tools/model_map.py` | map_columns, analyze_project_db, find_column_producers |
| `tools/model_verify.py` | check_model_schema, analyze_grain, validate_model_output, audit_model_sources, compare_join_types, verify_model_values |
| `tools/connections.py` | list_database_connections, connection_health, connector_capabilities |
| `tools/dbt_project.py` / `dbt_execute.py` / `refresh_mart.py` | dbt_error_parser, generate_sql_skeleton, dbt_execute, refresh_mart |
| `tools/knowledge.py` | get_knowledge, search_knowledge, read_knowledge, propose_knowledge, archive_knowledge |
| `tools/notebook.py` / `reports.py` / `notion.py` / `semantic_layer.py` / `sandbox_vm.py` / `workspace_projects.py` | run_notebook, read_notebook, manage_report, manage_dashboard, list_notion_integrations, notion_search, notion_fetch_page, notion_create_page, list_semantic_metrics, verify_metric_conformance, sandbox_exec, sandbox_write_file, sandbox_read_file, list_workspace_projects |

FastMCP 实例定义在 `G/mcp/server.py:32-48`(`stateless_http=True`)。

### 1.2 支持的数据库

`DBType` 枚举(`G/models/connections.py:18-30`):postgres, duckdb, mysql, snowflake, bigquery, redshift, clickhouse, databricks, mssql, trino, sqlite, xata。注册表 `G/connectors/registry.py:62-75` 一一对应到 `G/connectors/drivers/*.py`(每个 driver 200-700 行,基类 `G/connectors/base.py` 463 行)。

- DuckDB/SQLite 在 `SP_SANDBOX_ENABLED=true` 且非 cloud 时换成 sandboxed 版本(`registry.py:41-52`),否则直接本进程打开文件。
- Snowflake/BigQuery/ClickHouse/Databricks/Trino 的驱动是 optional extras(`pyproject.toml [project.optional-dependencies]`),Postgres/MySQL/MSSQL/DuckDB 是硬依赖。
- **MongoDB:不支持。** `grep -rni mongo G/ --include='*.py'` 只命中 `G/engine/denylists.py:102`(ClickHouse 的 `mongo()` 表函数黑名单)。

## 2. 单人开发者能否独立跑 MCP server

### 2.1 进程形态

- HTTP:`G/main.py:697-715` 在 FastAPI app 上 `app.mount("/", MCPAuthMiddleware(mcp.streamable_http_app()))`,`/mcp` 路径由 FastMCP 内部路由。Dockerfile 的 CMD 就是 `uvicorn gateway.main:app --port 3300`(`Dockerfile.gateway:54`)。插件 README 的接入方式是 `claude mcp add --transport http signalpilot http://localhost:3300/mcp`(`P/README.md:15`)。
- stdio:`G/mcp/server.py:60-95`,`SP_MCP_TRANSPORT` 默认即 stdio,`python -m gateway.mcp.server`;cloud 模式拒绝启动。但 `gateway.mcp` 包 `__init__` 顶部就 `from gateway.db.engine import get_session_factory`,且每个工具调用 `_store_session()`(`G/mcp/context.py:71-99`)→ 仍要 `DATABASE_URL`。**stdio 模式不会跑 `init_db()`**(那在 FastAPI lifespan 里,`G/main.py:98`),所以表得先由 HTTP 模式启动一次建好。`未确认`:stdio 模式在表已存在时能否完整工作(未实际运行)。

### 2.2 最小依赖

| 需要 | 证据 | 本地模式默认 |
|---|---|---|
| Postgres 元数据库 `DATABASE_URL` | `G/db/engine.py:32-35` 缺失即 `ValueError`;`init_db` 用 `pg_advisory_lock`、`to_regclass`(`:125-138`)→ 必须是 Postgres | 无默认,compose 给 `postgresql+asyncpg://signalpilot:changeme_dev_only@db:5432/signalpilot`(`docker-compose.yml:61`) |
| `SP_DEPLOYMENT_MODE` | `G/runtime/mode.py:12` 默认 `local` | 不设即可 |
| `SP_ENCRYPTION_KEY`(连接串加密) | `G/store/crypto.py:110-121` 本地模式没设就生成到 `SP_DATA_DIR/`(默认 `~/.signalpilot`) | 自动生成 |
| notebook session JWT secret | `G/auth/jwt_secret.py:68-95` 本地模式读/生成 `/gateway-secrets/notebook_jwt_secret`;lifespan 里失败即 `SystemExit(1)`(`G/main.py:86-92`) | 自动生成(`_gateway_secret_path()` 具体路径 `未确认`,裸机需可写) |
| auth / org / user 表 | Alembic 迁移一次性建全(`G/db/migrations/`);MCP 中间件在「DB 中零 API key」时放行并把 org/user 设为 `"local"`(`G/auth/mcp_api_key.py:292-345`) | 无需配置,但表必须存在 |
| API key | 不需要;若想加,`G/dev_local_api_key.py` 会生成 `sp_local_*` 写到 `SP_DATA_DIR/local_api_key` | 可选 |
| 其他 env(`.env.example`) | 全部可选:端口覆盖、`SP_HOST_DATA_DIR`(sandbox 里读宿主 DuckDB/SQLite 文件)、Anthropic key(notebook chat)、Slack/Notion OAuth | 不设 |

lifespan 还会做:`ensure_repos_dir()`(git 仓库目录)、`health_monitor.load_from_db()`、加密健康检查、BYOK provider 配置、可能的 MinIO/S3 客户端(`G/main.py:94-130+`;S3 相关是否在缺 env 时报错 `未确认`)。

### 2.3 `docker-compose.dev.yml` 带起什么

`docker-compose.dev.yml` 是 **overlay**,不是独立栈——文件头(`:3-4`)给的用法是:

```
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build db gateway notebook sandbox
cd signalpilot/web && pnpm exec next dev ...
```

它只覆盖 4 个服务:`gateway`(源码 bind-mount + 文件监视自动重启,`SP_ALLOW_PRIVATE_CONNECTIONS=true`,启动前先跑 `dev_local_api_key` 写共享 key 文件)、`notebook`(Dockerfile.notebook,自动生成 JWT secret)、`web`(仅 `docker-web` profile)、`gateway-chat-worker`。基础 `docker-compose.yml` 共定义 `db`(postgres:17)、`gateway`、`eval-object-proxy`(nginx)、`gateway-chat-worker`、`notebook`、`web`、`minio`、`minio-init`、`mailpit`、`sandbox`(`docker-compose.yml:20-403`)。

**最小服务集估计:`db` + `gateway` 两个容器**。gateway 的 `depends_on`(`:174`)具体列表 `未确认`是否硬依赖 minio/sandbox;compose 里 `SP_SANDBOX_ENABLED: "true"`(`:96`),不带 `sandbox` 容器时 DuckDB/SQLite 连接会走 `SandboxedDuckDBConnector` 去找 `http://sandbox:8080` 而失败——裸机或自定义 compose 应设 `SP_SANDBOX_ENABLED=false`(`registry.py:41-52`)。

结论:**「本地无 auth」是有的,「无 Postgres」没有。** 单人跑起来大约需要:一个 Postgres 容器 + 一个 gateway(容器或 `uv run uvicorn gateway.main:app`,前提是装完全量依赖)+ 用 REST/CLI 注册一条 connection(`G/cli.py:41-58` 的 `sp connect`,或 `POST /connections`,具体路径 `未确认`)。

## 3. 两个 verifier 依赖的工具与实现成本

### 3.1 verifier 实际调用什么

| Agent | MCP 工具(按调用顺序) | 非 MCP |
|---|---|---|
| `P/agents/verifier.md` | `list_tables`(CHECK 1,`:27`)→ `map_columns(connection_name, model_name, project_dir)`(CHECK 2,`:31`)→ `check_model_schema`(`:35`)→ `audit_model_sources(..., sample_nulls=true)`(CHECK 3,`:38`)→ 手写 `SELECT ... GROUP BY ... HAVING COUNT(*)>1`(fan-out 排查,`:47-49`,隐含 `query_database`) | Glob `models/**/*.sql`、Read `models/*.yml`、`git show HEAD:<path>`(CHECK 5) |
| `P/agents/value-verifier.md` | 手写 `SELECT * FROM <model> LIMIT 5`(CHECK 1,`:23`)→ **`mcp__signalpilot__verify_model_values`**(CHECK 2 强制,`:37-38`)→ 手写 `SELECT DISTINCT <status_col>` / `COUNT(DISTINCT <key>) WHERE ...`(CHECK 3,`:122-127`) | Read SQL 文件、Skill(领域规则) |

插件整体引用频次(`grep -rhoE` 于 `P/`):query_database 14、describe_table 8、analyze_project_db 7、list_tables 6、explore_column 6、schema_overview 5、map_columns 5、get_date_boundaries 5、compare_join_types 4、audit_model_sources 4、verify_model_values 3、find_join_path 3、其余 ≤2。`analyze_project_db` 只在 `dbt-workflow` skill 里(`P/skills/dbt-workflow/SKILL.md:30-33,86-87`),与 `scan_project.py`(564 行,纯文件系统扫描,无第三方依赖,`P/skills/dbt-workflow/scan_project.py:1-11`)配对使用;`validate_project.py` 235 行。

### 3.2 每个工具的实现位置、体量、依赖

所有 DB 工具共享同一套「外壳」:`_validate_connection_name` → `_store_session()` 取 connection 记录、解密连接串、credential extras → `pool_manager.connection(db_type, conn_str, ...)` 得到 connector → `connector.execute(sql)` / `connector.get_schema()`。这层外壳每个工具约 20 行,自实现时替换成「拿一个已打开的 connector」即可。

| 工具 | 实现 | 核心行数(去掉外壳) | 依赖 | DB 方言假设 / 自实现注意 |
|---|---|---|---|---|
| `list_tables` | `G/mcp/tools/schema/catalog.py:106-233` | ~90 | `connector.get_schema()` 返回 `{key: {name, schema, columns[{name,type,primary_key}], foreign_keys[], row_count, database?}}`;`schema_cache` | 全靠 driver 的 `get_schema()`。Postgres driver 528 行、DuckDB 356 行,自实现要写自己的 information_schema 内省(含 PK/FK/row_count) |
| `check_model_schema` | `G/mcp/tools/model_verify.py:74-141` + `G/verification/model_schema.py`(36 行 `compare_columns`) | ~50 | `_get_column_names`(`G/mcp/helpers.py:12-30`:sqlite/duckdb 用 `PRAGMA table_info`,其他用 information_schema,默认 schema `public`) | 最简单,纯集合比较 |
| `audit_model_sources` | `G/mcp/tools/model_verify.py:345-600` | ~230 | `_get_column_names`、`_quote_table`(`G/mcp/validation.py:13-16`) | 每列一条 `COUNT(*) FILTER (WHERE col IS NULL), COUNT(DISTINCT col)`(Postgres/DuckDB 语法,MySQL 不支持 FILTER);每个 source 一条 `COUNT(*)`;取 5 行样本;低基数列 `SELECT DISTINCT ... LIMIT 15`;grain 判定靠**解析自己生成的文本行**(`:497-540`),自实现时应改成结构化 dict。N+1 次查询,每次重新从 pool 拿连接 |
| `verify_model_values` | `G/mcp/tools/model_verify.py:821-1067` | ~230 | `_quote_table`、`_qid` | **硬编码 `SHOW TABLES`**(`:900`,DuckDB/MySQL 语法,Postgres 会报错)和不带 `table_schema` 过滤的 `information_schema.columns`(`:868-871`);数值类型判定靠字符串包含 `INTEGER/BIGINT/DOUBLE/...`。>50 张表时按列重叠度 shortlist 15 张再 COUNT。自实现建议用 information_schema/duckdb_tables() 替换 |
| `map_columns` | `G/mcp/tools/model_map.py:709-766` + helpers `:26-707` | ~480(`_Schema` 259-300、profile 325-420、`_detect_lookups` 421-448、`_map_model` 468-611、`_map_explicit_upstreams` 611-685、SQL/YML 正则解析 72-257) | 文件系统读 dbt project(`_validated_project_dir` 限制在 workspace root,`:26-70`)、`connector.get_schema()`、pg_stats(postgres/redshift)或精确扫描(duckdb/sqlite/clickhouse)、`_SAMPLE=200_000` 采样 | 最重的一个;含用正则解析 `{{ ref() }}`/`{{ source() }}`/SELECT 列表、按前缀推断 upstream、检测列名冲突。**方言分支已内建**(`_PG_STATS`/`_COLUMNAR`,`:325-328`),Postgres/DuckDB 都覆盖 |
| `analyze_project_db`(非 verifier,dbt-workflow 用) | `G/mcp/tools/model_map.py:800-875` + `model_map_hints.py`(247 行,仅 stdlib) | ~320 | `schema_cache`、`_detect_lookups`、`_staging_gaps`、`_driving_table_gaps(ScanBudget)` | 有 1 小时进程内缓存;`ScanBudget` 从 env 读预算 |
| `query_database` | `G/mcp/tools/query.py:105-233` | 工具本身 ~130,但真正执行在 `G/governance/query_executor.py`(governance 包合计 5016 行) | `governed_query_executor.execute()` 依赖 health_monitor、pool_manager、PII redactor、cost estimator、plan limits、standalone_chat 对象存储、`GatewayGovernedQueryExecution`/`GatewayStructuredQueryResult` 表(`query_executor.py:20-38`) | **这是最不值得整搬的一个**:verifier 只需要「validate_sql → inject_limit → execute → 表格化」,即 `G/engine` + 30 行 |

合计:自实现 verifier 需要的 6 个工具,核心逻辑约 **700-900 行**(list_tables 的内省部分另算,取决于自己 connector 的 `get_schema` 做多细);其中 `map_columns` 一个占一半。若先只做 value-verifier + verifier 的 CHECK 1/3(`list_tables`、`audit_model_sources`、`verify_model_values`、`query_database`),约 **400 行**。

### 3.3 connector 抽象要多薄

`BaseConnector`(`G/connectors/base.py:44-463`)对工具暴露的只有 `execute(sql, params, timeout) -> list[dict]` 和 `get_schema() -> dict`。`execute` 会 fire-and-forget 写 `gateway_audit_logs`(`:86-104`,`:156-205` 里 late-import `gateway.db.engine`),失败静默——所以 driver 本身不硬依赖元数据库,但拷贝 driver 时要删掉 `_audit_sql` 那一段。`pool_manager.py`(875 行)只 import `credential_identity`、`base`、`registry`、`ssh_tunnel`(`:21-24`),但功能是多租户连接池/SSH 隧道/凭证指纹,单人场景一条 `duckdb.connect()` / `asyncpg.connect()` 就够。

## 4. 只读 SQL guard

| 文件 | 行数 | 内容 |
|---|---|---|
| `G/engine/_sqlglot.py` | 15 | 硬依赖 import sqlglot(缺失 fail-closed) |
| `G/engine/dialects.py` | 24 | `SQLGLOT_DIALECTS` 映射 12 种 db_type → sqlglot 方言(mssql→tsql、xata→postgres) |
| `G/engine/validation.py` | 132 | `validate_sql(sql, blocked_tables, dialect) -> ValidationResult`:空/空字节/100KB 上限 → 去注释、去字符串字面量后正则检测 `;\s*\w` 语句堆叠 → `sqlglot.parse` → 多语句拒绝 → 语句类型 ∈ `_BLOCKED_STATEMENT_TYPES` 拒绝、不在 `Select/With/Union/Intersect/Except/Subquery` 拒绝 → 危险函数 → `SELECT INTO` → 提取 tables/columns 并按 `blocked_tables` 过滤 |
| `G/engine/denylists.py` | 394 | `_BLOCKED_STATEMENT_TYPES`(Create/Drop/Alter/Insert/Update/Delete/Truncate/Merge/Grant/Revoke/Comment/Rename/Replace/Command);`_DANGEROUS_FUNCTIONS` 按 10 个方言(postgres/clickhouse/bigquery/snowflake/mysql/duckdb/sqlite/tsql/databricks/trino)的 frozenset,未知方言取并集(fail-closed);`_UNIVERSAL_BLOCKED_FUNCTIONS`(load_extension/install_extension);`_check_dangerous_functions` 遍历 AST 检查 Func/Anonymous/Table(表函数如 `FROM read_csv(...)`、DuckDB `FROM 'x.parquet'` 路径表、Spark `parquet.\`path\``、T-SQL 四段名);`_check_into_clause` |
| `G/engine/transforms.py` | 86 | `inject_limit(sql, max_rows, dialect)`(缺失/`LIMIT ALL`/参数/PERCENT/超上限 → 覆写为 `min(current, max_rows)`,解析失败抛错不拼接)、`redact_sql_literals`(审计用字符串脱敏) |
| `G/engine/__init__.py` | 25 | 导出 |
| **合计** | **651** | |

- 库:**sqlglot ≥ 25**(`pyproject.toml`),无其他依赖;包内只 import 彼此,不 import store/db/config。
- 可分离性:**高**。`tests/test_sql_engine.py` 只 `from gateway.engine import ValidationResult, inject_limit, validate_sql`;`tests/test_sql_governance_dialects.py` 只 import `gateway.engine` 与 `denylists`。把 `engine/` 5 个文件复制为一个包、改一下相对 import 即可。
- 同目录的 `dbt_validation.py`(666 行)是 dbt 模型 SQL 的另一套校验(处理 `{{ ref() }}` 等),不属于只读 guard,按需取。
- 已知局限(源码可见):堆叠检测是正则+sqlglot 双保险;`Command` 类型兜底 COPY/VACUUM;方言映射对 `postgresql`/`mssql` 做了别名归一(`denylists.py:334`)。`未确认`:sqlglot 对 DuckDB 扩展语法(如 `FROM read_parquet` 变体、`PIVOT`)的解析覆盖率,需跑 `test_sql_governance_dialects.py` 确认。

## 5. 两条路线的成本对比

| | 整套 gateway 复用 | 自实现 6 个工具 + 拷贝 `engine/` |
|---|---|---|
| 得到 | 62 个 MCP 工具、审计日志、governance(PII/limit/cost)、12 种 connector、schema cache、SSH 隧道 | verifier 所需 6 工具 + 只读 guard;Postgres/DuckDB 各一个 20-50 行 connector |
| 运行时 | Postgres 17 容器 + gateway 容器(或裸机全量依赖:fastapi/sqlalchemy/asyncpg/alembic/claude-agent-sdk/boto3/vercel/pymssql/psycopg2/duckdb/pyarrow…);Alembic 表;`~/.signalpilot` 落盘 key;连接需先经 REST/CLI 注册并 Fernet 加密存库 | 一个 Python 进程,`mcp` + `sqlglot` + `duckdb`/`asyncpg`,连接串走 env |
| 改动成本 | 0 代码,但要理解 `SP_SANDBOX_ENABLED`、本地 auth 放行、connection 注册流程;想改工具行为要在 fork 上改 | 1-2 天(map_columns 最重);同时修掉 `SHOW TABLES`/`FILTER` 等方言假设 |
| 风险 | 全量依赖树升级/安全面;MCP 走 HTTP 时 Host 校验列表(`server.py:14-30`);`init_db` 每次启动跑迁移 | 自写内省(PK/FK/row_count)质量;失去审计/PII |
| 适合 | 需要 dbt_execute、schema diff、governed query、多租户、或想直接跑上游插件全套 skill | 只要 Postgres/DuckDB 验证、solo、想嵌进 data-agent 自己的 MCP server |

**推荐**:自实现 + 拷贝 guard。若之后需要更多工具(`describe_table`、`explore_column`、`get_date_boundaries` 在插件里引用也多),`G/mcp/tools/schema/` 下每个都是 80-300 行、同样只依赖 `connector.execute/get_schema`,可逐个搬。

## 6. 未确认清单

- stdio 模式(`python -m gateway.mcp.server`)在表已建好时是否能不依赖 lifespan 完整工作(未运行)。
- 裸机启动 gateway 时 `_gateway_secret_path()` 与 S3/MinIO 相关配置缺失是否会阻塞启动(只读了 `main.py:55-130`)。
- `docker-compose.yml:174` gateway 的 `depends_on` 是否硬依赖 `sandbox`/`minio`(未展开该行)。
- REST 注册 connection 的确切 endpoint(`G/api/` 未读)。
- sqlglot 对 DuckDB 扩展语法的覆盖率(需跑测试)。
