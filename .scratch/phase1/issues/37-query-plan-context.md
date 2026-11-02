# 37 — 查询工具把执行计划、索引等确定性信息返回给模型

Status: needs-triage
Blocked by: 36
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

agent 写出慢查询时，工具直接把数据库给出的确定性信息返回给它，由模型自己改写 SQL：
- 执行计划（各节点的估算行数、连接方式）；
- 涉及表上已有的索引；
- 哪些连接键或过滤条件上套了函数。

改写后仍然超时，就报超时错误，到此为止。

## 已和用户定下的原则（2026-11-02）

- 改写由模型来做，工具不自动改写 SQL。理由：
  - 不改语义又能提速的改写，Postgres 优化器已经做了；
  - 剩下有效的改写，要么需要写权限（表达式索引、物化），要么需要知道题意；
  - 自动改写一旦和原句不等价，会静默返回错误答案，比超时更糟。
- 工具只返回确定性的事实，比如执行计划、索引、表的行数估计。不返回"可能很贵"这类启发式判断，也不用阈值替模型做决定。
  - 对照：SignalPilot `governance/cost_estimator.py` 的 `is_expensive`（> $1 或扫描 > 100 万行）就是这种启发式判断。只参考它取执行计划的方式（`EXPLAIN (FORMAT JSON)`，按叶子节点累加扫描行数），不照搬这个阈值。
- 兜底就是超时（工单 36），超时报错可以接受。
- 不在数据库侧加索引：
  - DAB 的库属于榜单，加索引会让分数和榜单不可比；
  - 真实用户的库，agent 按设计只读。

## 待定（triage 时和用户讨论）

1. **什么时候返回。** 有三种做法：
   - (a) 每条查询都附执行计划摘要；
   - (b) 只在超时的错误里附执行计划摘要和涉及表的索引；
   - (c) 单独提供一个 explain 工具，`list_tables` 里加上索引。

   推荐 (b) 加 (c) 里的"`list_tables` 带索引"：正常查询不多花一次往返；出问题时模型不用猜，直接拿到原因。用 `EXPLAIN`，不用 `EXPLAIN ANALYZE`，因为后者会把慢查询真的跑一遍。
2. **DAB 路径。** DAB 用的是它自带的 `QueryDBTool`（`evals/dab_server.py:156-165`）。在我们的包装层加执行计划信息，会改变 agent 在 DAB 上能看到的东西，要定这算不算"我们的 agent"的一部分（对照 ADR-0005）。
3. **Skill。** 是否加通用的查询写法，比如连接键上不套函数、先在 CTE 里规范化或先过滤。
   - 只能在 data-eng-bench 开发集上验证后再加，因为 DAB 结果不能用来改 Skill（plan §10 第 4 项）。
   - 内容必须通用，不能针对具体题目（ADR-0005）。
4. **适用的数据库。** Postgres、DuckDB 都有 `EXPLAIN`；Mongo 有 `explain()`；SQLite 有 `EXPLAIN QUERY PLAN`。一期先做哪些。

## Authority / Acceptance / Stop conditions

triage 定下上面几点后再写。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-02 对齐讨论：起因是 DAB 试跑 imdb 上的慢查询（见工单 36 Context）。
  - "两边套 `regexp_replace` 导致逐对比较"这一说法没有核实，Postgres 也可能对表达式等值条件走哈希连接。
  - 具体慢在哪，要看那几条查询的 `EXPLAIN`。
