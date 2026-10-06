# Spec — 一期：dbt 赛道打通 + 消融，DAB 跟进

依据：ADR-0001…0006、`CONTEXT.md`、`docs/research/2026-09-21-*.md`。所有口径都是可改的默认值（`AGENTS.md`「推进原则」）。

## 目标

一个 Claude Agent SDK 写的 CLI data agent：
1. 在 data-eng-bench 上用 Harbor 跑通，产出基线、消融表、全量 103×3 的榜单提交；
2. 同一份 agent 跑 DAB 54×5（含基线）并提交；
3. 能脱离榜单接 `jaffle_shop` + 本地 Postgres。

## 组件与抄谁

| 组件 | 做什么 | 上游（照抄/参考） |
|---|---|---|
| `agent/` CLI | `query()` 循环；四个开关：`skills` / `verifier` / `context_tools` / `ask_user`（一期恒关）；落盘轨迹与 token | SignalPilot `benchmark/agent/sdk_runner.py:57-64` 的 `ClaudeAgentOptions` 写法 |
| Harbor 接入 | 继承 `BaseInstalledAgent`：`install()` 把 CLI 装进任务容器，`run()` 执行，`populate_context_post_run` 写 ATIF `trajectory.json` 和 token/成本 | Harbor `src/harbor/agents/installed/claude_code.py`（轨迹转换 :1654-1683） |
| 只读 MCP server | `engine/` 只读 SQL 守卫原样复制；DuckDB / Postgres 连接器各几十行；基础工具 `list_tables` / `query_database` | SignalPilot `gateway/engine/`（ADR-0002） |
| 验证子 agent | `verifier.md` + 移植 6 个验证工具，修 `SHOW TABLES` / `FILTER` 方言 | signalpilot-plugin `agents/verifier.md`、`value-verifier.md`；SignalPilot MCP tools（ADR-0002） |
| Skill | dbt 工作流相关 SKILL.md 照抄，删 SignalPilot 专属工具名；补 `postgres-sql` | signalpilot-plugin skills（8 步 dbt 工作流、`duckdb-sql` / `sqlite-sql`） |
| 上下文工具 | `discover`（只回引用 + ≤200 字摘要）/ `hydrate` / `dictionary_search`；join 发现先抄 `_id` 匹配 | ktx MCP 契约、SignalPilot `analyze_project_db`（ADR-0003） |
| 评测脚本 | 留出集分层抽样（种子进 git）、6 配置矩阵、bootstrap 配对报告、成本汇总 | ADR-0004、ADR-0006 |
| 失败归因 | 读轨迹，按 FM1–FM4 打标签 | DAB `failure_analysis/` 的 judge prompt（ADR-0005） |
| DAB runner | `common_scaffold/tools` 包成 MCP 工具；用官方提示文件；输出 `[{dataset, query, run, answer}]` + 轨迹 | DAB `common_scaffold/`（ADR-0005） |
| 冒烟测试 | `jaffle_shop` + Postgres，约 5 个任务，脚本化 | ADR-0006 |

## 基线的定义

基线 = `agent/` 带只读 MCP 基础工具，四个开关全关。所以 01 + 02 合起来才是基线；之后每个 agent 层只加开关，不改基线代码路径。

## 不做（一期）

CRUD、MongoDB 产品连接器、Langfuse、LiveSQLBench、澄清层消融、语义层、微调。

## 切片（tracer bullet 优先，详见 `issues/`）

01 裸 agent 跑通 Harbor + 测成本 → 02 只读 MCP 基础工具（=基线）→ 03 Skill 层 / 04 验证子 agent / 05 上下文工具（可并行）→ 06 评测矩阵与报告 → 07 失败归因 → 08 DAB → 09 冒烟测试 → 10 榜单提交与报告。

## 待定（不阻塞开工）

- 模型与上限金额：01 实测后定（ADR-0001、ADR-0006）。默认先用 `claude-sonnet-5-5` 测量。
- 运行环境：需要 `ANTHROPIC_API_KEY`、Docker、`uv tool install harbor`；DAB 另需 PG ≥17.5 + Mongo 8（资源占用未确认）。
