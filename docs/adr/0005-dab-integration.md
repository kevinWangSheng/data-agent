# DAB 接入：用它自带的工具与官方提示，不用针对 DAB 调的 prompt

DAB README 的 "Create Your Customized Agents" 一节允许自带 agent、复用 `common_scaffold/tools`（已处理 PostgreSQL / MongoDB / SQLite / DuckDB 连接）。榜单有两列：Hints（是否用 `db_description_withhint.txt`）与 Tuned prompt（是否针对 DAB 的任务惯例专门写 prompt）。2026-09-28 核对：前 25 名全部用了 Hints；Tuned prompt 前 25 名中 16 个用、9 个不用（含第 15、18–25 名）；脚注 7 批评的是提交者自写的提示，不是官方文件。

决定：
- DAB 运行时把 `common_scaffold/tools` 包成本项目的 MCP 工具；一期因此不另做 MongoDB 连接器，产品侧 MongoDB 挪出一期。
- 使用官方提示文件（与榜单惯例一致，否则分数不可比）。
- 不写针对 DAB 的 prompt：本项目是通用 agent，Skill 不含任何基准专属内容；榜单提交标记 Tuned prompt = 否。
- Postgres 方言没有上游 Skill，按插件 `duckdb-sql` / `sqlite-sql` 的格式补一个 `postgres-sql`。

## Consequences

- 一期"接任意数据源"的产品能力只覆盖 Postgres + dbt；MongoDB 仅在 DAB 评测里可用。
- 失败归因采用 DAB 自带的 FM1–FM4 与其 judge prompt（`failure_analysis/`），两个榜共用；SignalPilot 无失败分类可参考。
- Trace 只用基准自带格式（Harbor 的 `trajectory.json`、DAB 提交要求的轨迹）；SignalPilot 未用 Langfuse，Langfuse 挪二期。

## Update 2026-10-16：judge 提示自己写

DAB 没有许可证，不能复制它 `failure_analysis/` 的 judge 提示原文（AGENTS.md 硬规矩）。失败归因沿用 FM1–FM4 的分类思路（定义见 `CONTEXT.md`），judge 提示由本项目自己写；上面 Consequences 里"采用其 judge prompt"作废。spec（`.scratch/phase1/spec.md` Further Notes）已按此写。

## Update 2026-11-14：例外一轮，用 DAB 的失败诊断做三处通用修复

plan §10 第 4 项定过"DAB 结果不能用来改 Skill"，因为 DAB 没有开发集和留出集之分。人在 2026-11-14 批准一次例外：第 18 项跑完后，按失败诊断做三处修复，条件是每一处都是通用修法，不含任何 DAB 题目、数据集或评分脚本的内容；"Skill 与提示不写基准专属内容"的决定不变。

诊断（no-context，270 次里失败 60 次，分布在 15 道题；逐题通过情况用服务器上的官方评分脚本核过）：

| 类别 | 次数 | 说明 |
|---|---|---|
| 题目或评分脚本的问题 | 23 | 不修 |
| 答案写法 | 15 | 答对了，但名称和数值之间夹了别的列、名称被截断，或在答案旁列出别的候选，评分脚本判错 |
| 没有语义分类的手段 | 9，另有 4 次耗尽轮数或上下文 | 题目要按文章内容分类，agent 只能逐篇读或用关键词近似 |
| 有答案但没在轮数用完前交出 | 2 | Skill 第 6 节写"最后 3 轮给答案"，但 agent 看不到上限和当前轮数 |
| agent 推理错误 | 7 | 不单独修 |

另有 5 次通过是评分脚本宽松造成的，修复后可能不再通过。

100 轮诊断实验（2026-11-14，ea740b5，临时配置把查询上限从 50 调到 100、大库从 75 调到 150，3 道题各 3 次，实付约 $0.51）：没有一次用到 100 轮；前两道题的空答案消失，但通过次数没有变多（music_brainz_20k 第 3 题 2/3，crmarenapro 第 7 题 1/3）；agnews 第 4 题 1/3，另 2 次撞上下文长度上限。结论：不调上限，改为让 agent 看见预算；agnews 靠分类工具。

决定（工单在 `.scratch/dab-fixes/issues/`）：
- 68 答案写法：改查询任务结尾那句提示，产品（`src/data_agent/agent.py`）和 DAB（`evals/dab.py`）用同一句。只答所问；每个名称后紧跟它的值；名称和文字照数据原样；假设只写短句，不放表格，不列备选。
- 69 轮数预算可见：开头写明本次上限，剩 10 轮和剩 3 轮各提醒一次；只对查询类任务；在基础层，所有配置都有；查询上限仍是 50。Skill `sql-workflow` 第 6 节改成引用这个提醒。先查清 SDK 怎么数轮数，拿不到准确的剩余轮数就停下，不用近似值。
- 70 语义分类工具：基础层工具，所有配置（含 baseline）都有，产品和 DAB 的工具服务器都注册。用只读查询取文本，按给定类别分批调本次运行的模型，逐行标签存下来供后续查询使用；花费计入本次运行的花费和预算上限；有单次行数上限。

验证与重跑：
- 三张工单合入后做小范围验证（约 21 次，no-context）。过线标准：
  - 68：PATENTS 第 3 题、googlelocal 第 2 题、DEPS_DEV_V1 第 1 题各 3 次，至少 6/9 通过。
  - 69：music_brainz_20k 第 3 题、crmarenapro 第 7 题各 3 次，没有空答案。
  - 70：agnews 第 2、4 题各 3 次，工具被调用，第 4 题没有空答案。
  - 任何一条不过就停下交给人；69 不过时由人定要不要调上限。
- 过线后，经人确认，在同一个提交上全量重跑 540 次（两个配置 × 54 题 × 5 次，约 $10）。第 18 项的旧结果保留，报告里新旧并排。
- DAB 榜单提交用重跑的结果，提交说明写明：这三处修复是看过本榜失败情况后做的，以及修复前的分数。榜单的 Tuned prompt 一栏怎么填，提交前由人对照榜单定义再定。
