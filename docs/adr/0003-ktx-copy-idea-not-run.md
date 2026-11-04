# 宽 schema 上下文策略：抄 ktx 的工具契约，不运行 ktx

ktx（TS ~115k 行 + Python ~10k 行）需要 LLM 驱动的 ingest、embedding 后端与 Python 守护进程，MongoDB 只做上下文不做 SQL，且无端到端准确率数据（`docs/research/2026-09-21-ktx-context-layer.md`）。决定：不集成 ktx，只复制三个思路——(1) discover→hydrate 分层：搜索工具只回引用 + ≤200 字摘要，agent 再按需读全文；(2) `dictionary_search`：按关键词查列的真实取值；(3) join 发现先用 SignalPilot `analyze_project_db` 的 `_id` 列匹配，不够再抄 ktx 的 7 信号打分与预算（25 候选/列，≥0.85 接受）。

## Consequences

- 上下文层是三四个 MCP 工具，无 ingest、无向量库；agent 拿不到"整库预扫"的语义层，靠运行时探索 + Memory 补。

## Update 2026-11-02：数据库报出的事实进基础工具层

工单 37 triage 时核对：我们执行查询前不给索引、约束、行数估计和执行计划；列类型和行数只有上下文层的 `hydrate` 给，`baseline`、`no-context`（DAB 的最终版）都拿不到。三家现成做法（SignalPilot、Postgres MCP Pro、Google MCP Toolbox）都在基础层提供单表详情（含索引）和不真跑的执行计划。

决定：
- **基础工具层**（所有配置都有，DAB 包装层也有）放数据库自己能报出来的事实：`list_tables` 带行数估计；`describe_table` 给列类型、可空、主键、外键、索引、行数估计（Postgres 加 `pg_stats` 的 `n_distinct`、`null_frac`）；`explain_query` 只跑 `EXPLAIN`；查询超时的报错附执行计划和相关表的索引。
- **上下文层**只留需要推断或检索的策略：`discover`（按关键词找表，只回引用和短摘要）、`dictionary_search`（查真实取值）、按 `_id` 列名推断的连接候选。`hydrate` 保留，补上约束和索引。
- 上面的 discover→hydrate 契约不变：仍然不整库塞进上下文，单表详情按需读取。

## Consequences（2026-11-02）

- 上下文层消融衡量的是"策略"的贡献，不再包含"给不给事实"；`baseline` 变强，消融数字和 dev-3 不可直接比。
- 工单 37 合入后要在新 main 上重跑合并验收（plan §10 第 13b 项）。

## Update 2026-11-04：`dictionary_search` 改成预建字典

accept-2 里，`dictionary_search` 在调用时把所有表的所有文本列现扫一遍，没有总时限。在约 2400 个模型的 dbt 库上，6 次调用跑满了 Claude Code 的 1800 秒上限，被放弃的扫描还占着库文件，后面的 `dbt run` 拿不到写锁。原因是只抄了 ktx 的工具契约，没抄它的前提：ktx 在 scan 阶段预建采样字典，搜索时只读字典（`packages/cli/src/context/scan/data-dictionary.ts:9-34`、`sl/dictionary-search.ts`，ktx@49a4ae6）。

决定（工单 42）：
- agent 开跑前由 harness 建字典，只在开了上下文层的配置里建。只扫基础表，不扫视图。
- 收列规则和上限用 ktx 默认值：只收类别型文本或布尔列；按 ktx 的列名规则排除 `_id`、`_at`、`email` 一类列；不同值超过 200 的列跳过；每列最多 100 个值；每张表采样 10000 行。
- 总时限 60 秒，单条语句 10 秒。时限用完就保留已建好的部分，搜索结果说明覆盖范围。
- 建字典用只读连接，建完立即关闭；建的过程可以被取消。搜索只读内存里的字典，不连库。
- 返回覆盖范围（扫了几张表、几列，采样多少行，跳过的列和原因）和未命中原因（值不在样本里、表没扫到），照 ktx 的 `searched` / `misses` 字段。ID 类值请用 `query_database` 查。

## Consequences（2026-11-04）

- `dictionary_search` 只能搜到样本里的值，罕见值可能搜不到；未命中原因会告诉 agent 去哪一列用 SQL 确认。
- 上下文层的配置开跑会多花建字典的时间，最多 60 秒。
