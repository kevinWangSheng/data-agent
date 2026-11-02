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
