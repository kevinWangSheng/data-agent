# LiveSQLBench：第三个榜单，Base-Lite 的 180 道 SELECT 题

日期：2026-11-23。决策来自当天的 grilling（Q1–Q6，人逐条确认）。取证笔记：`docs/research/2026-11-23-livesqlbench.md`。降级理由见 ADR-0001（四条仍成立，本 spec 用自己写适配器的办法绕开前三条）。

## Problem Statement

agent 只在 data-eng-bench（dbt 任务）和 DAB（跨库查数）上出过分，一期的"消融拆出各层贡献"没过（data-eng-bench 留出集 30 题，所有差值区间都过 0）。需要第三个榜单说明 agent 不只适配了已有的两个，并用更大的留出集再做一次消融。

## Solution

- 在现有的 Harbor 0.24 上写一个 LiveSQLBench 适配器：Base-Lite 的 180 道 SELECT 题转成 Harbor 任务，每个库一个 PostgreSQL 镜像，评分照官方的 soft EX。
- 180 题分开发集 60、留出集 120；开发集上可以调，留出集在版本冻结后盲跑一次。
- 留出集跑 4 个配置（Skill × 上下文工具 的 2×2），每题 3 次，报告推荐配置对裸模型、以及两层各自的贡献。

## Implementation Decisions

### 标准答案（Q1）

- `sol_sql`、`test_cases`、`external_knowledge` 不公开。由人用自己的邮箱发邮件申请（HF 数据集卡：发到 `bird.bench25@gmail.com`，标题 `[livesqlbench-base-lite GT&Test Cases]`，自动回复）。agent 只起草，不代发。
- 标准答案只放服务器上的 `da-benchmarks/`，不进 git、不进 `runs/` 的取回内容、不进事件文件；agent 运行时拿不到（Harbor 在 agent 跑完后才拷入测试，和 data-eng-bench 一样；0.24 的这一行为实现时核实）。
- 答案到手之前，适配器、镜像、冒烟（只看能不能跑完、不算分）可以先做。

### 数据集与划分（Q2、Q4）

- Base-Lite 的 180 道 SELECT 题（18 个库）；不做 90 道管理题（要写库，和只读设计相反，ADR-0010），不跑 Full、Large。
- 按库分层随机抽 60 题做开发集、120 题做留出集，固定随机种子，写进 `evals/manifest.json`（和 data-eng-bench 同一个文件、同一套 dev/holdout 判断，`da-remote pull`、可观测性的选档都直接适用）。
- 开发集：读轨迹、看报错，照 ADR-0008 改通用的 Skill、提示词、工具和运行方式，也可以用 `evals improve`（`.scratch/skill-improve/spec.md`）。始终不看标准答案。
- 每个改动两道关：LiveSQLBench 开发集初筛，加 data-eng-bench 回归集（`evals/regression.json`）不退步。不写针对 LiveSQLBench 题目或库的内容（ADR-0005）。
- 接入和环境问题（适配器 bug、连库、超时、输出格式、方言报错）在任何集合上出现都直接修；修的时候只看报错的类型和数量，不看留出题的题面和轨迹（ADR-0004）。
- 已报的 data-eng-bench、DAB 分数不重跑（2026-11-23 决定），报告写明各自对应的版本。

### 适配器（Q3）

- 照 Harbor 0.24 自带的适配器结构（`~/dev/AI/data-agent-refs/harbor/adapters/`，如 `bird_bench`、`spider2-dbt`）写，参考 LiveSQLBench 自带 CLI 的 `adapter.py`（MIT 部分照许可证处理），不用它 vendor 的 Harbor 0.1.45。
- 每题一个 Harbor 任务：题面、库名、PostgreSQL 连接（agent 只读，经我们的只读网关）；任务类型 `query`。
- 每个库一个 PostgreSQL 镜像，用官方 dump（Google Drive）构建，版本照它的任务模板（14.12）；由 `da-remote benchmarks` 在服务器上下载、校验、构建（和 DAB 数据同样的方式，可重复跑、已就绪的步骤跳过）。
- 评分：照官方 soft EX（去掉 `DISTINCT`、`ROUND` 后比较执行结果），reward 二值；用官方的样例或评测脚本对拍，结果一致才算接入完成。
- agent 的答案格式：查询题要交 SQL 还是结果，照官方任务说明；实现时核实，必要时在任务说明里写清（只写格式要求，不写解题内容）。
- 镜像、dump 只放服务器，不推送到任何镜像仓库，不重新发布数据（许可证写法不一致：仓库 MIT，README 与 HF 卡写 CC BY 或 CC BY-SA，见取证笔记）。
- 评测入口照例不开 `--network`，不挂外部 MCP，不开图表。

### 运行（Q5、Q6）

- 接入工作现在开始，和其他 feature 并行。
- 开发集调优用 `evals board` / `ablate --split dev`，属于小额运行（每次不超过 30 题时默认放行）。
- 留出集全量在里程碑：所有会改 agent 行为的工单都合入后（含 SDK 升级、Skill 修改），跑一次。开跑前人批准范围和花费，花费按冒烟测出的单题成本估。
- 留出集跑 4 个配置，每题 3 次：

  |  | 上下文工具 关 | 上下文工具 开 |
  |---|---|---|
  | Skill 关 | `baseline` | `no-skill` |
  | Skill 开 | `no-context` | `no-verifiers`（推荐配置） |

  报告 3 个配对 bootstrap 差值：`no-verifiers − baseline`（两层合起来）、`no-verifiers − no-context`（上下文工具）、`no-verifiers − no-skill`（Skill）。`full` 在查询题上和 `no-verifiers` 行为相同（验证器只用于 dbt，`agent.py:1340-1342`），`full-old-skill` 与本榜单无关，都不跑。若人认为 4 个配置的花费太高，退回只跑 `no-verifiers` 和 `baseline`。
- 留出集只看汇总分（ADR-0004）；`da-remote pull` 的现有规则不取回留出题的 trial。
- 提交到 LiveSQLBench 榜单由人决定、由人做（AGENTS.md）。

## 工单

| 工单 | 内容 | 依赖 |
|---|---|---|
| 127 | 验收测试：在 Testing Decisions 的接缝上写测试，标 `xfail`；人审后合入 main | 无 |
| 128 | 适配器：任务生成、PostgreSQL 镜像（`da-remote benchmarks`）、soft EX 评分与对拍、`evals/manifest.json` 的 60/120 划分、`board`/`ablate` 能跑 LiveSQLBench、冒烟 | 127；评分对拍要等标准答案 |
| 129 | 报告：LiveSQLBench 的结果表、2×2 消融的 3 个差值、写进 `docs/report.md` 和 README | 留出集全量跑完 |

开发集上的调优按 ADR-0008 另开工单，或用 `evals improve`。

## Testing Decisions

单元测试不连库、不调模型、不下载数据：用两三道题的小样例（只含公开字段，不含标准答案）和替身。

- **任务生成**：样例题生成的 Harbor 任务结构正确（题面、库名、任务类型 `query`）；不含 `sol_sql`、`test_cases`、`external_knowledge` 的任何内容；管理题被跳过。
- **划分**：固定种子下结果稳定；每个库都有题在两边（库的题数允许时）；60/120；和 data-eng-bench 的条目不冲突；`da-remote pull` 和 `evals board` 把留出题当留出题处理。
- **评分**：soft EX 的规则（去 `DISTINCT`、`ROUND`；行顺序、列名的处理照官方）各一个用例；用官方样例对拍（拿到标准答案后补成集成测试）。
- **评测入口**：LiveSQLBench 的 `data-agent run` 命令行不带 `--network`、`--mcp-servers`、`--charts`。
- **答案不外泄**：标准答案文件的路径不出现在 agent 的 workspace、事件文件和取回的 `runs/` 里。

## 验收

- `make check` 通过。
- 冒烟：开发集 3 题 × 4 个配置各 1 次在服务器上跑完，无基础设施报错；记下单题成本，用它估留出集全量的花费。
- 评分对拍：拿到标准答案后，官方评测脚本和我们的 soft EX 在同一批答案上结果一致。
- 开发集调优（可选、按需）：照 ADR-0008。
- 里程碑：留出集 4 配置 × 120 题 × 3 次，人批准后跑；报告写进 `docs/report.md`。

## Out of Scope

- 管理类题（CRUD）、Base-Full、Large、Base-Lite-SQLite。
- 用 LiveSQLBench 自带的 CLI 和 Harbor 0.1.45。
- `full`、`full-old-skill` 配置。
- 代人提交榜单、代人发邮件。
- BIRD-Interact（带用户模拟器的多轮版本，澄清层消融；仍不做）。

## Further Notes

- 上游：LiveSQLBench 仓库 `bird-bench/livesqlbench` @ `e15cd22`（取证时临时 clone，没放进 `data-agent-refs/`；实现时 clone 到 `~/dev/AI/data-agent-refs/livesqlbench` 并记 commit）。复制其代码按 AGENTS.md 保留版权头、登记 `NOTICE`。
- 当前榜首 57.00（AntQL 1.1，Claude Opus 5.5），对应的数据集口径未确认；`HANDOFF.md` 里"榜首 48.0"已过时，报告引用前重查。
- 样本量：留出集 120 题，是 data-eng-bench 留出集 30 题的 4 倍，差值区间宽度粗估缩到约一半（约 ±5 分），未实测；消融仍可能读不出方向，照实报告。
