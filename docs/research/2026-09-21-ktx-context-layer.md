# ktx(Kaelio)context layer 调研

- 日期:2026-09-21
- 来源:`git clone --depth 1 https://github.com/Kaelio/ktx`,HEAD `49a4ae6f` (2026-07-07, "fix(ci): repair slow sl test and sync uv.lock to 0.16.0 (#345)"),版本 0.16.0,Apache-2.0
- 克隆位置:`/private/tmp/claude-501/-Users-shenghuikevin-dev-AI-data-agent/9033292c-ed20-4b41-b53c-003a04b95c28/scratchpad/ktx`
- 下文所有 `file:line` 相对该克隆根目录;标 `未确认` 的项未读源码或无法本地验证

## Bottom line

- **ktx 的"context"= 三个落盘产物**:① `semantic-layer/<conn>/*.yaml`(表→grain/columns/measures/joins/segments,可编译成 SQL)② `wiki/global/*.md`(带 frontmatter 的业务知识页,`sl_refs`/`refs` 构成图)③ `raw-sources/<conn>/live-database/<sync>/`(scan 快照:每表 JSON、`relationships.json`、`descriptions.json`、`embeddings.json`、`relationship-profile.json`)。矛盾标记只出现在 ingest 报告的 `conflicts_resolved[]`(`definitional_contradiction`,`flaggedForHuman`),不是运行时可查的表。
- **对宽 schema 最值得抄的不是"检索",而是"分层暴露 + 按需展开"**:MCP 端 `discover_data` 只返回 refs(kind/id/score/≤200 字 snippet),真正的列元数据要再调 `entity_details`(≤20 个实体/次),YAML 要再调 `sl_read_source`,wiki 全文要再调 `wiki_read`;`sl_query` 默认不回 SQL/plan(`include:['sql']` 才给)。没有 token 预算器、没有按问题切子集的 schema 打包——控制上下文完全靠"refs-first + 逐个 hydrate"这条工作流约束。
- **检索本身很朴素**:wiki 是 FTS5 bm25 + embedding cosine + term-overlap 三车道 RRF(权重 1.5/2/0.75,k=60);semantic-layer 与 raw table/column 只是子串命中率 × 字段权重(name 1.2 > display 1.1 > description 1 > expr 0.9,column 的 sample_value 1.3),没有 embedding。可以直接抄 RRF 那 30 行,不必抄整套。
- **join graph 才是 ktx 的硬核部分**:scan 阶段用 7 个信号(名字相似、类型兼容、值重叠、embedding、目标列唯一性、null 率、结构先验)加权打分 + DB 侧验证 + 可选 LLM 提案,阈值 accept≥0.85 / review≥0.55;有每列候选上限 25、验证预算 `min(2×表数,1000)`、整阶段 600s 墙钟预算——这三个预算是它"宽 schema 不爆"的真正手段。查询期用 Python planner 走 join graph 并检测 fan-out/chasm trap(按 measure source 分组预聚合)。
- **建议:抄想法,不要 as-is 接 MCP**。理由:ktx 的产物依赖 LLM 驱动的 ingest(`ktx ingest` 需要配置模型+embedding,MongoDB 不能跑 SL/SQL),MCP 工具 12 个但只在有 ktx 项目(`ktx.yaml` + `.ktx/` 状态 + Python daemon)时可用;体量 ~115k 行 TS + ~10k 行 Python,自带 Claude/Codex 登录、telemetry、setup 向导,对我们只是一层"schema 上下文"的需求来说过重。值得直接搬的:(a) 12 个 MCP 工具的输入/输出 schema 作为接口设计参考;(b) `hybrid-search-core.ts`+`rrf.ts` 的融合;(c) relationship-scoring 的信号向量与阈值;(d) `ktx-analytics` SKILL 里"先 discover→再 hydrate→值用 dictionary_search→先 sl_query 后 sql_execution"的工作流文本。
- **成熟度**:YC 公司维护、1.6k stars/106 forks(gh api,2026-09-21)、474 个 TS 测试文件(~161k 行测试)+37 个 Python 测试、CI/release 工作流齐全;但 main 最后提交 2026-07-07(两个半月无提交,`pushed_at` 2026-09-11 应是其他分支,未确认)。**没有公开的端到端 text-to-SQL 准确率数字**;仓库里只有 join 发现的 benchmark(17 个 fixture,2–71 张表,测试断言部分 fixture 上 PK/FK recall=precision=1),README 的"自动解决 chasm/fan trap""标记矛盾"有代码支撑但无量化。

## 1. ktx 产出的"context"到底是什么

### 1.1 项目目录(落盘产物)

`README.md:~170` 给出的布局,源码路径可对上:

```
my-project/
├── ktx.yaml                         # 连接/存储/ingest 配置
├── semantic-layer/<connection-id>/  # 每表一个 YAML(semantic source)
│   └── _schema/<shard>.yaml         # scan 写的物理表 manifest(类型、PK、join 出处)
├── wiki/global/*.md                 # 共享业务知识页
├── wiki/user/<user-id>/*.md         # 用户私有页(同 key 覆盖 global)
├── raw-sources/<connection-id>/     # ingest 原始证据与报告
│   └── live-database/<syncId>/      # scan-report.json, connection.json, tables/*.json,
│                                    # relationships.json, relationship-profile.json,
│                                    # relationship-diagnostics.json, descriptions.json, embeddings.json
└── .ktx/                            # SQLite 索引、secrets、worktrees(git-ignored)
```

证据:
- `_schema` manifest:`packages/cli/src/context/scan/local-enrichment-artifacts.ts:20,79,452,504`;Python 侧 `python/ktx-sl/semantic_layer/manifest.py:1-8`("two-tier schema architecture":manifest 存 DB 原生类型/PK/join 出处,投影成 SourceDefinition)。
- scan 产物文件名:`local-enrichment-artifacts.ts:335,475-476,552-554`;`local-scan.ts:135`(`scan-report.json`);`local-structural-artifacts.ts:77,166`(`warnings.json`、`connection.json`)。
- `raw-sources/<conn>/<sourceKey>/<syncId>` 路径规则:`packages/cli/src/context/ingest/raw-sources-paths.ts:15-17`。

### 1.2 Semantic source YAML(metric/measure/join 定义)

Schema 双份维护,TS(`packages/cli/src/context/sl/schemas.ts`)与 Python Pydantic(`python/ktx-sl/semantic_layer/models.py`)锁步(`schemas.ts:4-7` 注释)。关键字段(`schemas.ts:145-177` `sourceDefinitionSchema`;`models.py:107-123` `SourceDefinition`):

| 字段 | 含义 | 出处 |
|---|---|---|
| `name`, `table` 或 `sql`(二选一) | 源身份与物理表/SQL 视图 | `schemas.ts:172-175` |
| `grain: [col...]` | 行粒度,必填非空,列名不能带点 | `schemas.ts:73-79,156` |
| `columns[]: {name,type(string/number/time/boolean),role(time/default),visibility(public/internal/hidden),descriptions{user/ai/dbt/db},expr,enum_values,constraints,tests}` | 列 | `schemas.ts:87-101`;描述优先级 user>ai>dbt>db `models.py:37-47` |
| `joins[]: {to,on,relationship(many_to_one/one_to_many/one_to_one),alias}` | join 图边 | `schemas.ts:80-85` |
| `measures[]: {name,expr,filter,segments,description}` | 指标 | `schemas.ts:14-20` |
| `segments[]: {name,expr}` | 命名布尔谓词 | `schemas.ts:22-26` |
| `inherits_columns_from` | 从 `_schema` manifest 继承列类型/描述 | `schemas.ts:148-153` |
| overlay 形态(无 table/sql,只加 measures/computed columns/column_overrides/joins) | 叠加在 manifest 表上 | `schemas.ts:187-203` |

示例(`examples/local-warehouse/semantic-layer/warehouse/orders.yaml`):

```yaml
name: orders
table: public.orders
descriptions: { user: Orders placed through the storefront. }
grain: [id]
columns: [{name: id, type: number}, {name: status, type: string}, {name: amount, type: number}]
measures: [{name: order_count, expr: count(*)}, {name: total_revenue, expr: sum(amount)}]
joins: []
```

### 1.3 Wiki 页(列/业务描述、矛盾说明)

frontmatter 类型 `packages/cli/src/context/wiki/types.ts:16-38`:`summary`(必填,搜索结果只展示这个)、`tags`、`refs[]`(→其他 wiki key)、`sl_refs[]`(→`<conn>.<source>`)、`connections[]`(限定连接)、`usage_mode: always|auto|never`、`source`、`tables[]`、`representative_sql`、`usage{executions,distinct_users,p50/p95_runtime_ms,error_rate}`(历史 SQL 模式页)、`stale_since`/`archived_since`。key 必须是扁平 token(`keys.ts:1`)。

注意:`usage_mode: always` 在代码里只做校验(`knowledge-wiki.service.ts:205-207`)并写入索引(`:289`),**没找到任何运行时消费者把 always 页强制注入上下文**(grep `usage_mode|usageMode` 于 `context/mcp`、`context/wiki`、`setup-agents.ts` 仅命中校验/存储)。docs 说的"agent must surface"(`docs-site/.../wiki-retrieval.mdx` 表格)是约定,不是机制。

### 1.4 矛盾标记(doc-contradiction flags)

不是独立产物,而是 ingest 报告(`IngestReportSnapshot`)里的 `conflicts_resolved[]`:

```ts
// packages/cli/src/context/ingest/report-snapshot.ts:25-35
kind: 'structural_duplicate' | 'near_duplicate' | 'definitional_contradiction' | 're_ingest_change'
contestedKey?, artifactKey, detail, flaggedForHuman: boolean
```

由 LLM ingest agent 通过 `emit_conflict_resolution` 工具写入(`context/ingest/tools/emit-conflict-resolution.tool.ts:15`)。处理规则在 `packages/cli/src/skills/ingest_triage/SKILL.md:30-36`:同名不同公式 → 全部改名加域后缀(`churn_risk_engagement_based`)、争议名不进 SL、写一页 `<concept>-definitions.md` 列出所有变体 + 出处、**总是 flag**。所以"矛盾"最终以 wiki 页 + 报告条目两种形式存在,agent 运行时只能通过 wiki 搜到那页。

### 1.5 Scan 快照(raw table/column 元数据)

`packages/cli/src/context/scan/types.ts:50-80`:`KtxSchemaTable{catalog,db,name,kind(table/view/external/event_stream),comment,estimatedRows,columns[],foreignKeys[]}`,`KtxSchemaColumn{name,nativeType,normalizedType,dimensionType,nullable,primaryKey,comment}`。enrichment 阶段再叠加 `descriptions{ai,...}`、`sampleValues`(`search/discover.ts:44-47` 的 `RawTable` 类型)。

关系(inferred join)结构 `scan/relationship-discovery.ts:66-75`:`{id, source:'inferred', from{tableId,columnIds}, to{...}, relationshipType, confidence, isPrimaryKeyReference}`。

Data dictionary(供 `dictionary_search`):只对 categorical 类型列采样,排除 `_id$/_at$/email$/...` 等模式,cardinality>200 跳过,每列最多存 100 值,采样 10000 行(`scan/data-dictionary.ts:9-34`)。

## 2. 如何暴露给 agent

### 2.1 MCP server(主路径)

`ktx mcp start` 起本地 daemon(stdio/HTTP,`mcp-stdio-server.ts`/`mcp-http-server.ts`),`ktx setup --agents --target <claude-code|codex|cursor|opencode|universal>` 写 `.mcp.json`/`~/.claude.json`/`.cursor/mcp.json` 的 `mcpServers.ktx`(`setup-agents.ts:281-303`),并安装 `ktx-analytics` SKILL(`setup-agents.ts:425-509`)。

12 个工具(`packages/cli/src/context/mcp/context-tools.ts:60-79` 描述,输入 schema `:81-230`,输出 schema `:232-430`):

| 工具 | 输入 | 输出(要点) |
|---|---|---|
| `connection_list` | – | `connections[]{id,name,connectionType,members?,queryPolicy?}`;有 `_ktx_federated` 跨库项 |
| `discover_data` | `query, connectionId?, kinds?[wiki/sl_source/sl_measure/sl_dimension/table/column], limit≤50(默认10)` | `refs[]{kind,id,score(归一到最大=1,3位小数),summary,snippet(≤200字),matchedOn,tableRef?,columnName?}`——**只有 refs,不含列列表** |
| `wiki_search` | `query, limit≤50, connectionId?` | `results[]{key,path,scope,summary,score,matchReasons?,lanes?}`,`totalFound` |
| `wiki_read` | `key` | `{key,summary,content(全文),scope,tags,refs,slRefs}`,无截断 |
| `sl_read_source` | `connectionId, sourceName` | `{sourceName, yaml(全文字符串)}`,无截断 |
| `sl_query` | `measures[](key 或 {expr,name}), dimensions[]{field,granularity}, filters[], segments[], order_by[], limit(默认1000), include?:['sql','plan']` | `{headers,rows,totalRows,notes?[](compile_only/fan-out 警告),sql?,plan?}`——SQL/plan 默认省略(`:452-470` 注释) |
| `entity_details` | `connectionId, entities[≤20]{table(串或{catalog,db,name}), columns?}` | 每表 `{kind,comment,estimatedRows,columns[]{name,nativeType,normalizedType,dimensionType,nullable,primaryKey,comment},foreignKeys[],snapshot{syncId,extractedAt}}`;错误码 `scan_missing/table_not_found/ambiguous_table/column_not_found` 带候选 |
| `dictionary_search` | `values[≤20], connectionId?` | 每值 `matches[]{sourceName,columnName,matchedValue,cardinality}` + `misses[]{reason}` + 覆盖率统计 |
| `sql_execution` | `connectionId, sql, maxRows≤10000` | `{headers,headerTypes?,rows,rowCount}`;parser 级只读校验(`connections/read-only-sql.ts`) |
| `sql_dialect_notes` | `connectionId` | `{dialect, notes(文本)}`:FQTN、引号/大小写、日期函数、top-N、JSON 访问 |
| `memory_ingest` | `content(markdown), connectionId?` | `{runId}`,异步跑 LLM memory agent 写 wiki/SL |
| `memory_ingest_status` | `runId` | `{status,stage,captured{wiki[],sl[],xrefs[]},commitHash,...}` |

工具结果用紧凑 JSON(不缩进)放进 `content.text`(`context-tools.ts:432-441` 注释解释:避免 rows 每个标量一行)。

### 2.2 agent 在任务开始 vs 任务中调什么

装到 agent 侧的 `ktx-analytics` SKILL(`packages/cli/src/skills/analytics/SKILL.md:11-27`)规定的顺序:

1. 任务开始:`discover_data` 先(只拿 refs);规则 "Always run `discover_data` before writing SQL. Do not guess table names."(`:30`)
2. 并行 hydrate 命中项:wiki→`wiki_read`;sl_*→`sl_read_source`;table/column→`entity_details` + 小 `sql_execution` 采样确认日期编码/null/枚举值
3. 用户提到业务值 → `dictionary_search`
4. 写 SQL 前 → `sql_dialect_notes`
5. 优先 `sl_query`,SL 不覆盖才 `sql_execution`
6. 结束 → `memory_ingest` 沉淀学到的东西

多连接时的 `connectionId` 传参规则也写死在 SKILL(`:35-40`)。没有"任务开始加载全量 schema"的步骤——**agent 首轮看不到任何表列表**,全靠搜索。

### 2.3 CLI(次路径)

`ktx sl --json`、`ktx sl "revenue" --json`、`ktx sl query --measure ... --format sql|--execute`、`ktx wiki "..." --json`、`ktx status --json`(`docs-site/content/docs/guides/serving-agents.mdx`)。Cursor/OpenCode 等目标装的是 rules 文件走 CLI。

## 3. 需要的输入与支持的数据库

### 3.1 输入
- **必需**:一个 primary source 连接(`ktx.yaml` `connections.<id>.driver`)+ LLM backend(Anthropic API / Vertex / AI Gateway / 本地 Claude Code 会话 / 本地 Codex 登录,README FAQ)+ embedding backend(默认本地 `sentence-transformers`,由 Python daemon 托管;`skills/ktx/SKILL.md:52-55`)。`ktx ingest` 没有模型+embedding 会报 enrichment-readiness 错(`skills/ktx/SKILL.md:117-120`)。
- **可选 context sources**(每次 setup 只能加一个):dbt、MetricFlow、LookML、Looker、Metabase、Sigma、Notion、Google Drive(`packages/cli/src/skills/` 下的 `*_ingest`/`*_synthesize` 目录;`docs/terminology.md` 正式列表为 dbt/MetricFlow/LookML/Looker/Metabase/Notion)。
- 历史 SQL(query history)ingest:`skills/historic_sql_patterns`、`historic_sql_table_digest`,产出带 `usage` 统计的 wiki 页。

### 3.2 数据库

`scan/types.ts:3-13` 的 driver 枚举:`sqlite | duckdb | postgres | sqlserver | bigquery | snowflake | mysql | clickhouse | athena | mongodb`;`connections/drivers.ts:30-228` 十个 driver 都有实现。

| DB | 支持 | 备注 |
|---|---|---|
| Postgres | 完整 | FK 完整检测(`primary-sources.mdx:92`) |
| DuckDB | 完整(只扫 `main` schema,只读打开,文件必须先存在) | `primary-sources.mdx` DuckDB 节;postgres/mysql/sqlite/duckdb 任意组合可派生 `_ktx_federated` 跨库连接(ATTACH) |
| SQLite | 完整 | `better-sqlite3`;FK 需 `PRAGMA foreign_keys=ON`(`:606`) |
| MongoDB | **仅 context**:collection→table,采样最近 1000 文档推断字段;**不能** `ktx sql`、不能 SL 编译(`examples/mongodb/README.md:8-11`;`primary-sources.mdx:731,737`) |
| Snowflake/BigQuery/ClickHouse/Athena | 无 FK 元数据 → 全靠 relationship discovery(`:166,278,364,484`) |

## 4. 宽 schema 下如何控制上下文大小

**结论:没有 token 预算器、没有 per-question schema 子集打包;"小"靠三件事:refs-first 的工具分层、搜索 limit、以及 scan 期的 join 发现预算。**

### 4.1 运行时(agent 侧)
- `discover_data` 只回 refs,snippet 硬截 200 字(`search/discover.ts:73-80` `cap200`),limit 1–50 默认 10(`:441`)。列信息要 `entity_details` 再取,一次 ≤20 实体(`context-tools.ts:171`),但**单表返回全部列**,无按列裁剪(除非传 `columns` 过滤)。
- `sl_query` 默认省略 SQL 与 plan,只保留 `notes`(fan-out/compile-only)(`context-tools.ts:452-470`)。
- `wiki_read`/`sl_read_source` 返回全文,无上限(`local-project-ports.ts:182` 直读);ingest 内部工具才有 `MAX_YAML` 截断(`sl/tools/base-semantic-layer.tool.ts:109`)。
- grep `tokenBudget|maxTokens|charBudget|truncat` 于 `context/mcp|wiki|sl|search|scan` 仅命中上述两处。

### 4.2 检索/排序
- **wiki**(`context/wiki/local-knowledge.ts:374-425`):三车道 lexical(SQLite FTS5 bm25)/ token(term overlap)/ semantic(embedding cosine,未配 embedding 则 `skipped`),`HybridSearchCore` 做 RRF:`score += weight/(60+rank)`(`search/rrf.ts:3-14`,权重 semantic 2 / dictionary 2 / lexical 1.5 / token 0.75),排序 score → 命中车道数 → id(`rrf.ts:16-18`)。索引文本 = key+summary+body+tags 顺序拼接(`knowledge-wiki.service.ts:273`)。
- **discover_data 的 SL 与 raw 车道**(`search/discover.ts:200-380`):对每个 source/measure/column 计算 `匹配词数/查询词数 × 字段权重`,取最佳字段;**无 embedding、无 FTS**,是全量线性扫描 `_schema`+scan JSON(`latestScan` 每次读全部 `tables/*.json`,`:127-150`)。三条车道(wiki/semantic_layer/raw_schema)再 RRF 融合(`:401-445`)。宽 schema 下这是 O(表×列) 的内存扫描,靠 limit 截断输出,不靠索引。
- 无 per-question 子集/图扩展:命中 table 后不会自动带出它的 join 邻居;agent 得自己看 `entity_details.foreignKeys` 或 SL 的 `joins`。

### 4.3 Scan 期(join 发现的预算,才是"宽 schema"真正的护栏)
`context/project/config.ts:171-217` 默认:
- `maxCandidatesPerColumn: 25`、`maxLlmTablesPerBatch: 40`、`profileSampleRows: 10000`、`profileConcurrency/validationConcurrency: 4`
- `validationBudget` 默认 `min(2×表数, 1000)` 条验证查询(`scan/relationship-budget.ts:22-25`),按分数排序后截断,剩余 `deferred`
- `detectionBudgetMs: 600_000`:整阶段 10 分钟墙钟,在 profile/LLM 提案/验证/复合键探测边界检查,超时返回 partial(`relationship-detection-budget.ts`)
- 候选生成:embedding 相似阈值 0.92、`minConfidence 0.72`、每列 parent 表上限 20(`relationship-candidates.ts:355,550-551,605,625-626`)
- 打分:7 信号加权(name .24 / valueOverlap .22 / profileUniqueness .22 / typeCompat .10 / embedding .10 / nullRate .08 / structuralPrior .04),`score = 0.56 + Σ×0.65`,类型不兼容直接 0(`relationship-scoring.ts:42-50,109-113`);accept≥0.85、review≥0.55、目标列 PK 分≥0.78 才能 accept(`relationship-graph-resolver.ts:66-68,113-119`)
- LLM 描述生成:按表批量 prompt(`description-generation.ts:319,785`),并发 5(`:533`)

### 4.4 查询期 fan-out/chasm
Python planner `python/ktx-sl/semantic_layer/planner.py:118-120,976-1150`:按 measure 所属 source 分组,若 join 路径含 `one_to_many` 边或多个独立 measure source → `has_fan_out=True`,每组各自预聚合 CTE("aggregate locality"),filter 不可达时直接抛错而非静默丢弃(`:1117-1140`)。`sl_query` 把 `has_fan_out`/`fan_out_description` 提到 `notes` 里(`context-tools.ts:452-467`)。

## 5. 规模与成熟度

| 项 | 数值 | 出处 |
|---|---|---|
| 语言 | TypeScript(CLI/MCP/ingest/scan)+ Python(SL planner `ktx-sl`,compute daemon `ktx-daemon`)+ Next.js docs-site | `README.md` Development 表 |
| 源码行数 | `packages/cli/src` 114,552 行 TS(不含测试);Python 非测试 9,717 行(planner 1,475、generator 1,434、lookml 1,056) | `wc -l` |
| 测试 | 474 个 `*.test.ts`,`packages/cli/test` 161,134 行;Python 测试 37 文件 17,129 行;`pnpm run check` 含边界检查、dead-code、type-check;CI/release/triage 三个 workflow | `find`/`wc`,`.github/workflows` |
| 依赖 | pnpm + uv 工作区;`@modelcontextprotocol/sdk`、zod、better-sqlite3、pglite(搜索原型)、DuckDB Node API | `package.json`、`scripts/pglite-*` |
| 版本/发布 | 0.16.0(2026-07-03),0.15.0(06-30),0.14.0(06-30);semantic-release | `gh api releases` |
| 最后提交 | main `49a4ae6f` 2026-07-07;仓库 `pushed_at` 2026-09-11(非 main,未确认) | `gh api` |
| 社区 | 1,601 stars,106 forks,52 open issues,创建 2026-05-10;YC P25 | `gh api repos/Kaelio/ktx` 2026-09-21 |
| Telemetry | 默认开启 PostHog(README Telemetry 节),可 opt-out | `README.md` |

### 5.1 README 声称 vs 证据

| 声称 | 证据 |
|---|---|
| "detects joinable columns + resolves fan/chasm traps" | 代码在(§4.3/§4.4);benchmark 仅覆盖 join 发现:17 个 fixture(`packages/cli/test/fixtures/relationship-benchmarks/`,2–71 表,含 Chinook/Northwind/Sakila/AdventureWorks 公共库),测试断言若干 fixture 上 PK/FK recall=precision=1(`test/context/scan/relationship-benchmarks.test.ts:170-173,373-374,894-902`);`relationships:verify-orbit` 期望 6 表 fixture 上 9 accepted/0 review/0 rejected(`examples/orbit-relationship-verification/README.md`)。**无汇总报告文件入库**,`scripts/relationship-benchmark-report.mjs` 需本地跑 |
| "flags contradictions for human review" | ingest agent 工具 + SKILL 规则(§1.4),无量化 |
| 端到端 text-to-SQL/指标准确率 | **未找到任何数字**(grep `precision|recall|accuracy|%` 于 README/AGENTS/docs 只命中 CSS 与一处 ingest 耗时示例) |
| "self-improving" | `memory_ingest` → LLM memory agent 写 wiki/SL 并 git commit(`memory_ingest_status.commitHash`);闭环存在但无评估 |

## 6. 对 data-agent"宽 schema 上下文策略"的可复用清单

直接抄(小、独立、无依赖):
1. `packages/cli/src/context/search/rrf.ts` + `hybrid-search-core.ts`(~150 行):多车道 RRF、车道失败降级为 skipped 而非零分、lane breakdown 随结果返回便于调试。
2. `discover_data` 的输出契约:`{kind,id,score∈[0,1],summary,snippet≤200,matchedOn}`,以及"refs-first,再 hydrate"的工具分层(`discover_data` → `entity_details`/`sl_read_source`/`wiki_read`)。
3. `sl_query` 的 `include:['sql','plan']` 按需附带 + `notes[]` 提升关键警告的做法。
4. `dictionary_search` 的思路:categorical 列采样值倒排,让"Acme Corp 在哪列"不用猜(`data-dictionary.ts` 的候选列规则)。
5. `ktx-analytics` SKILL 的工作流与 `connectionId` 传参矩阵作为我们 agent 提示词的模板。

按需借鉴(较重):
6. relationship discovery 的信号向量 + 阈值 + 三重预算(每列候选 25 / 验证 `min(2n,1000)` / 600s),若我们要在 SQLite/DuckDB/Postgres 上自动推 join。
7. wiki frontmatter 的 `sl_refs`/`refs` 图 + 写入时校验引用(`wiki/wiki-ref-validation.ts`)。

不建议 as-is 接入的原因:
- 运行前置:`ktx setup` 需 LLM + embedding + Python daemon(uv 托管),ingest 是 LLM 驱动的多阶段 memory agent(`ingest/ingest-bundle.runner.ts` 136k 字节),对我们只想要"schema 上下文"的目标偏重。
- MongoDB 不走 SL/SQL;DuckDB 只扫 `main` schema。
- `discover_data` 的 SL/raw 车道是全量线性扫描 JSON,宽 schema 下每次调用都重读 `tables/*.json`。
- 无 token 预算、无子图扩展——这正是我们要补的部分,ktx 没提供。

## 未确认 / 未读
- `pushed_at` 2026-09-11 对应哪个分支未查。
- `relationship-candidates.ts` 的 blocking 细节(名字归一化/后缀匹配)只看了函数签名,未逐行读。
- `ingest-bundle.runner.ts`(memory agent 主循环)、`setup-*.ts` 向导、telemetry 未读。
- Google Drive / Sigma 适配器仅从目录名确认存在。
- 没有实际运行 `ktx` 或其测试套件。
