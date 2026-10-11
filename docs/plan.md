# 大致规划

写于 2026-10-11。这是当前的工作方向，不是规则：每一条都可以随讨论或实测结果修改，改了就直接更新本文件。已讨论过的部分标"已讨论"，其余是待讨论的默认想法。

## 1. 产品与业务（已讨论，2026-10-11）

依据：HANDOFF §3，上游取证 `docs/research/2026-10-11-upstream-product-shape.md`。

- **定位**：开源、可自托管的通用 data agent，对标 SignalPilot，项目主体是 agent 的行为。
- **用户与场景**：分析师或数据工程师给出业务问题或数据任务，agent 探索数据源、必要时澄清、执行、自检、交付。
- **任务类型**：目前两类——dbt 分析工程、跨库查数。原来的"SQL 与 CRUD 管理操作"先不做：上游五个项目都默认只读、写操作都被隔离，和我们的只读网关 + dbt 写入一致。
- **形态**：一期是命令行工具（ADR-0001）。要不要加聊天界面、Slack 或对外 MCP，放到第 2 节一起想。
- **数据源**：Postgres、DuckDB、dbt 项目；MongoDB 只在 DAB 评测里用（ADR-0005）。"文档"暂时指项目里已有的文件（dbt yml、README、榜单自带的库说明），用文件工具读；Notion/Confluence 之类的连接器以后再说。上游多数也是把这些先同步成文件再让 agent 搜。
- **一期大致算完成**：沿用 HANDOFF §3 的验收线——DAB 与 data-eng-bench 超过基线，消融能拆出各层贡献，能接任意 Postgres + dbt 跑。具体怎么测放到第 4 节。

## 2. 求职呈现（待讨论）

面试官最终看到什么：README、演示录屏、消融报告、博客，以及一句话怎么讲。可能会反过来影响第 1 节的形态（要不要界面）和第 4 节追哪些分数。trace（Langfuse）要不要提前也在这里一起看。

## 3. agent 设计（待讨论）

五块：Skill、验证子 agent、问与做、上下文策略、失败归因。已有方向：ADR-0002（移植 SignalPilot 的验证工具）、ADR-0003（抄 ktx 的工具契约）、CONTEXT.md 的词条。顺带要看的两点：

- 命令行里要不要支持交互式澄清（参考 SP 的暂停等待、nao 的 `clarification` 工具）。
- Memory 要不要人审（SP 用审批队列、ktx 用 git diff）。

## 4. 评测设计（待讨论）

已有方向：ADR-0004（开发集/留出集与消融配置）、ADR-0005（DAB 接入）。待定：怎么判定"超过基线"、"接任意 Postgres + dbt"怎么测——ADR-0006 草稿里的 bootstrap 置信区间、`jaffle_shop` 冒烟测试可以作为起点。不做 CRUD 的话，LiveSQLBench（二期）只跑 180 道 SELECT 题。

## 5. 技术栈（待确认）

草稿：ADR-0001 Update（模型可配置，默认 DeepSeek）、ADR-0007（Python 3.12 + uv + Claude Agent SDK + MCP Python SDK）。跟着上游走，主要是了解和确认。

## 6. 开发环境（待讨论）

编码规范、检查命令、测试策略、权限配置、人和 agent 的分工。

## 7. 运行与评测环境（大体已有）

见 `docs/setup.md`。还缺一个能接受的总花费上限。

## 8. 实施计划（最后做）

前面几节定了以后，再用 `/to-spec`、`/to-tickets` 重写或修改 `.scratch/phase1/` 的草稿。
