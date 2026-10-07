# 产品全局图

给人看的系统地图：做什么、由哪些部分组成、一道题怎么跑、怎么证明它有效。细节以 `CONTEXT.md`（词汇）、`docs/adr/`（决策）、`.scratch/phase1/spec.md`（一期范围）为准；本文与它们冲突时以它们为准。

## 1. 一句话

一个开源、可自托管的 data agent：给它一个业务问题或 dbt 数据任务，它自己探索数据库、写 SQL 或改 dbt 模型、自检后交付。卖点不是"能写 SQL"，而是 **agent 的行为设计**——Skill、验证子 agent、上下文策略、失败归因——并且每一层都用公开榜单上的消融实验证明有贡献。

## 2. 用户与任务（一期）

| 任务类型 | 例子 | 评测 |
|---|---|---|
| dbt 分析工程 | 新建或修改 dbt 模型、修数据 bug（示意） | data-eng-bench（103 题，DuckDB，pytest 判定） |
| 跨库查数 | 跨 Postgres / Mongo / SQLite / DuckDB 回答问题，含脏键 join、非结构化字段抽取 | DAB（54 题，Pass@1） |

不做：CRUD 写数据、Web UI、语义层建模、微调（ADR-0006）。

## 3. 运行时结构

```
                 问题 / 任务说明
                       │
            ┌──────────▼───────────┐
            │  data-agent CLI      │  Claude Agent SDK 的 query() 循环
            │  模型可配置           │  默认 DeepSeek（ANTHROPIC_BASE_URL）
            └──┬───────┬───────┬───┘
   [开关]Skill │       │       │ [开关]验证子 agent
   SKILL.md    │       │       │ 审表/列/行数/取值，只改 FAIL
   做事方法     │       │       │
        ┌──────▼───────▼───────▼──────────────────┐
        │  本地 MCP server（stdio）                 │
        │  基础：list_tables / query_database        │
        │  验证：map_columns / audit_model_sources … │
        │  [开关]上下文：discover / hydrate /        │
        │        dictionary_search                  │
        │  ── 只读网关（SignalPilot engine/，AST 拦截 DDL/DML）
        └──────┬───────────────────────────────────┘
               │ 只读 SQL
        DuckDB / Postgres（DAB 另有 Mongo，经 DAB 自带工具）

   SDK 自带工具：读写文件、Bash → `dbt run`（唯一的"写"：dbt 写入）
   每次运行落盘：轨迹（trajectory.json）+ token/成本
```

四个开关：`skills`、`verifier`、`context_tools`、`ask_user`（一期恒关，无人回答）。全关 = **基线**。

## 4. 一道 dbt 题怎么跑完

1. Harbor 起任务容器（内置 `retail.duckdb` 和 dbt 项目），在容器里装好 data-agent。
2. agent 读题 → 按 Skill 的工作流探索：先 discover 找相关模型/表的引用，再按需 hydrate 细节，用 dictionary_search 对齐取值。
3. 改 dbt 模型文件，`dbt run` 物化。
4. 验证子 agent 只读地核：表存在、列齐全、行数与 fan-out、关键取值；有 FAIL 就交回主 agent 修，修完再审。
5. 结束；任务自带的 `tests/test.sh` 判定通过与否，Harbor 收集分数、轨迹、成本。

DAB 类似，只是数据跨多个库、交付的是答案 JSON，不经 Harbor（ADR-0005）。

## 5. 怎么证明有效（评测与开发循环）

```
开发集 fast-30 ──跑──► 失败题 ──FM1–FM4 归因──► 人改 Skill ──► 再跑   （失败归因循环，只在开发集上看失败）
                                                      │
留出集 30 题（从未看过失败）◄─────────── 新旧 Skill 对比 ┘
   └─ 6 配置 × k=3：基线 / 全开 / 关 Skill / 关验证 / 关上下文 / 全开旧版 Skill
       → 每层贡献 = 全开 − 关该层，按题配对 bootstrap 95% CI
全量 103×3 → data-eng-bench 榜；DAB 54×5（全开 + 基线）→ DAB 榜
```

验收：全开在留出集上超过基线（CI 不含 0）；消融表拆出每层贡献（负贡献照报）；`jaffle_shop` + Postgres 冒烟测试通过（ADR-0004、ADR-0006）。

## 6. 五块 agent 能力在一期的落点

| HANDOFF 里的五块 | 一期实现 | 消融里怎么体现 |
|---|---|---|
| Skill | 工单 03 | 关 Skill |
| 验证子 agent | 工单 04 | 关验证 |
| 问与做的策略 | `ask_user` 工具已在词汇里，一期恒关 | 二期用 BIRD-Interact 模拟器 |
| 宽 schema 上下文策略 | 工单 05 | 关上下文 |
| 失败归因驱动的改进 | 工单 07 + 人改 Skill | 新版 vs 旧版 Skill |

Memory（运行时按数据源沉淀笔记）：评测中每题从空开始、题后丢弃，对分数没有影响，一期不单独实现，二期随产品侧再做。

## 7. 抄谁

| 部分 | 上游 |
|---|---|
| agent 循环写法 | SignalPilot `benchmark/agent/sdk_runner.py` |
| Skill、验证协议 | signalpilot-plugin `skills/`、`agents/verifier.md` |
| 只读网关、验证工具 | SignalPilot `engine/` 与 MCP tools（ADR-0002） |
| 上下文工具契约 | ktx（ADR-0003） |
| Harbor 接入 | Harbor `agents/installed/claude_code.py` |
| DAB 接入、失败分类 | DAB `common_scaffold/`、`failure_analysis/`（ADR-0005） |
| 澄清策略（二期） | BIRD-Interact-ADK |

## 8. 二期（不在一期范围）

LiveSQLBench 180 道 SELECT、澄清层消融、Memory、MongoDB 产品连接器、Langfuse、DABstep 冒烟。
