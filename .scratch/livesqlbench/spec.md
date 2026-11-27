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
- 按库分层随机抽 60 题做开发集、120 题做留出集，固定随机种子，写进 `evals/manifest.json`（和 data-eng-bench 同一个文件、同一套 dev/holdout 判断，`da-remote pull`、可观测性的选档都直接适用）。（Update 2026-11-24，主会话对照代码更正）题号写成 `lsb-<instance_id>`，进清单顶层的 `dev`、`holdout`，见文末 U2。
- 开发集：读轨迹、看报错，照 ADR-0008 改通用的 Skill、提示词、工具和运行方式，也可以用 `evals improve`（`.scratch/skill-improve/spec.md`）。始终不看标准答案。
- 每个改动两道关：LiveSQLBench 开发集初筛，加 data-eng-bench 回归集（`evals/regression.json`）不退步。不写针对 LiveSQLBench 题目或库的内容（ADR-0005）。
- 接入和环境问题（适配器 bug、连库、超时、输出格式、方言报错）在任何集合上出现都直接修；修的时候只看报错的类型和数量，不看留出题的题面和轨迹（ADR-0004）。
- 已报的 data-eng-bench、DAB 分数不重跑（2026-11-23 决定），报告写明各自对应的版本。

### 适配器（Q3）

- 照 Harbor 0.24 自带的适配器结构（`~/dev/AI/data-agent-refs/harbor` 是稀疏 checkout，`adapters/` 不在磁盘上，用 `git -C ~/dev/AI/data-agent-refs/harbor show dc933be:adapters/bird_bench/...` 读），如 `bird_bench`、`spider2-dbt`）写，参考 LiveSQLBench 自带 CLI 的 `adapter.py`（（Update 2026-11-24，主会话对照代码更正）它在 `LiveSQLBench-CLI/` 下，那里的 `LICENSE` 是 Apache-2.0，见文末 U9），不用它 vendor 的 Harbor 0.1.45。
- 每题一个 Harbor 任务：题面、库名、PostgreSQL 连接（agent 只读，经我们的只读网关）；任务类型 `query`。（Update 2026-11-24，主会话对照代码更正）agent 和沙箱都拿不到写库凭据，见文末 U6；给 agent 官方任务里的公开 `db_assets`，见 U8。
- 每个库一个 PostgreSQL 镜像，用官方 dump（Google Drive）构建，版本照它的任务模板（14.12）；由 `da-remote benchmarks` 在服务器上下载、校验、构建（和 DAB 数据同样的方式，可重复跑、已就绪的步骤跳过）。
- 评分：照官方 soft EX（去掉 `DISTINCT`、`ROUND` 后比较执行结果），reward 二值；用官方的样例或评测脚本对拍，结果一致才算接入完成。
- agent 的答案格式：查询题要交 SQL 还是结果，照官方任务说明；实现时核实，必要时在任务说明里写清（只写格式要求，不写解题内容）。
- 镜像、dump 只放服务器，不推送到任何镜像仓库，不重新发布数据（许可证写法不一致：仓库 MIT，README 与 HF 卡写 CC BY 或 CC BY-SA，见取证笔记）。
- 评测入口照例不开 `--network`，不挂外部 MCP，不开图表。

### 运行（Q5、Q6）

- 接入工作现在开始，和其他 feature 并行。
- 开发集调优用 `ablate --split dev`（（Update 2026-11-24，主会话对照代码更正）`evals board` 只跑 registry 里的 data-eng-bench，`evals/board.py:176`，不用于本榜单，见 U3），属于小额运行（每次不超过 30 题时默认放行）。
- 留出集全量在里程碑：所有会改 agent 行为的工单都合入后（含 SDK 升级、Skill 修改），跑一次。开跑前人批准范围和花费，花费按冒烟测出的单题成本估。
- 留出集跑 4 个配置，每题 3 次：

  |  | 上下文工具 关 | 上下文工具 开 |
  |---|---|---|
  | Skill 关 | `baseline` | `no-skill` |
  | Skill 开 | `no-context` | `no-verifiers`（推荐配置） |

  （Update 2026-11-24，主会话对照代码更正）`ablate` 现在写死以 `full` 为参照（`evals/ablate.py:105`、`:829-831`），128 加 `--reference`，见 U1。报告 3 个配对 bootstrap 差值：`no-verifiers − baseline`（两层合起来）、`no-verifiers − no-context`（上下文工具）、`no-verifiers − no-skill`（Skill）。`full` 在查询题上和 `no-verifiers` 行为相同（验证器只用于 dbt，`src/data_agent/agent.py:1385-1387`，（Update 2026-11-24，主会话对照代码更正）原写 `:1340-1342` 是旧行号），`full-old-skill` 与本榜单无关，都不跑。若人认为 4 个配置的花费太高，退回只跑 `no-verifiers` 和 `baseline`。
- 留出集只看汇总分（ADR-0004）；`da-remote pull` 的现有规则不取回留出题的 trial。
- 提交到 LiveSQLBench 榜单由人决定、由人做（AGENTS.md）。

## 工单

| 工单 | 内容 | 依赖 |
|---|---|---|
| 127 | 验收测试：在 Testing Decisions 的接缝上写测试，标 `xfail`；人审后合入 main | 无 |
| 128 | 适配器：任务生成、PostgreSQL 镜像（`da-remote benchmarks`）、soft EX 评分与对拍、`evals/manifest.json` 的 60/120 划分、`ablate` 能跑 LiveSQLBench（（Update 2026-11-24，主会话对照代码更正）不含 `board`，见 U1–U5）、冒烟 | 127；评分对拍要等标准答案 |
| 129 | 报告：LiveSQLBench 的结果表、2×2 消融的 3 个差值、写进 `docs/report.md` 和 README | 留出集全量跑完 |

开发集上的调优按 ADR-0008 另开工单，或用 `evals improve`。

## Testing Decisions

单元测试不连库、不调模型、不下载数据：用两三道题的小样例（只含公开字段，不含标准答案）和替身。

- **任务生成**：样例题生成的 Harbor 任务结构正确（题面、库名、任务类型 `query`）；不含 `sol_sql`、`test_cases`、`external_knowledge` 的任何内容；管理题被跳过。
- **划分**：固定种子下结果稳定；每个库都有题在两边（库的题数允许时）；60/120；和 data-eng-bench 的条目不冲突；`da-remote pull` 把留出题当留出题处理（（Update 2026-11-24，主会话对照代码更正）去掉 `evals board`，见 U3）。
- **评分**：soft EX 的规则（去 `DISTINCT`、`ROUND`；行顺序、列名的处理照官方）各一个用例；用官方样例对拍（拿到标准答案后补成集成测试）。
- **评测入口**：LiveSQLBench 的 `data-agent run` 命令行不带 `--network`、`--mcp-servers`、`--charts`。
- **答案不外泄**：标准答案文件的路径不出现在 agent 的 workspace、事件文件和取回的 `runs/` 里。（Update 2026-11-24，主会话对照代码更正）"路径"指路径和内容两样，取回的 `verifier/` 输出也算，见 U7。
- **只读**（Update 2026-11-24，主会话对照代码更正；见 U6）：agent 的环境和命令行里没有写库凭据，也没有上游默认的 `root/123123`。

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

- 上游：LiveSQLBench 仓库 `bird-bench/livesqlbench` @ `e15cd22`（取证时临时 clone，没放进 `data-agent-refs/`；实现时 clone 到 `~/dev/AI/data-agent-refs/livesqlbench` 并记 commit）。复制其代码按 AGENTS.md 保留版权头、登记 `NOTICE`（（Update 2026-11-24，主会话对照代码更正）许可证分目录，见 U9）。
- 当前榜首 57.00（AntQL 1.1，Claude Opus 5.5），对应的数据集口径未确认；`HANDOFF.md` 里"榜首 48.0"已过时，报告引用前重查。
- 样本量：留出集 120 题，是 data-eng-bench 留出集 30 题的 4 倍，差值区间宽度粗估缩到约一半（约 ±5 分），未实测；消融仍可能读不出方向，照实报告。

## Update 2026-11-24：主会话对照代码更正

以下各条都是（Update 2026-11-24，主会话对照代码更正）：对照代码更正 spec 的说法，或执行已有规矩，或照官方、上游的做法；不改人已定的事项。

- **U1 参照配置**：仍以 `no-verifiers` 为参照。`ablate` 现在写死 `REFERENCE = "full"`（`evals/ablate.py:105`），`--config` 里必须有 `full`（`:829-831`），差值是 full 减各配置（`:744-746`）。128 给 `ablate` 加 `--reference <config>`，默认 `full`，现有行为不变。
- **U2 清单**：LiveSQLBench 的题号写成 `lsb-<instance_id>`，加进 `evals/manifest.json` 顶层的 `dev`、`holdout`，这样所有按清单判断留出题的地方照旧有效：
  - `da-remote pull`（`scripts/da-remote:623-735`）；
  - `evals/harbor_agent.py:138-146` `split_tier`；
  - `evals/report.py:365-382` `check_dev_list`。

  题号的前 32 个字符互不相同：`da-remote pull` 对没有 `result.json` 的 trial 按 `task[:32]` 判断（`scripts/da-remote:687-693`）。题号到库名的对照表提交在清单旁边。现在有几处把一个 split 当成单个榜单的全部题，128 让它们按榜单过滤，并更新 `tests/test_eval_manifest.py`（`:9-15` 钉死了 30/30）：
  - `ablate` 把 `manifest[split]` 整个当作要跑的题（`evals/ablate.py:834`），冒烟跑 `dev[:1]`（`:893`）；
  - `split_trials` 要求 split 里每题都有 trial（`evals/report.py:348-362`）。
- **U3 调优命令**：开发集调优用 `ablate --split dev`。`evals board` 只跑 registry 里的 data-eng-bench（`evals/board.py:176`，没有 `--split`），所以去掉"`evals board` 把 LiveSQLBench 留出题当留出题处理"这一条。
- **U4 抽样**：新写一个按库分层的抽样函数；`evals sample` 只按 `difficulty` 分层，开发集是给定的清单（`evals/sample.py:37-52`）。
- **U5 Harbor 适配器**：现在写死 `run --type dbt` 和 DuckDB（`evals/harbor_agent.py:364-389`）。LiveSQLBench 任务经 job 配置的 kwarg `benchmark=livesqlbench` 改走 `--type query` 加 Postgres。
- **U6 只读**（AGENTS.md MUST，ADR-0010）：
  - LiveSQLBench 任务里，agent 和它的沙箱都不能拿写库凭据连 Postgres 边车。
  - agent 只经我们的只读网关、用只读角色连库。
  - 上游的默认凭据 `root/123123`（取证笔记 §2.3，上游 `template/environment/docker-compose.yaml`）不进 agent 的环境。
  - 上游模板把这组凭据同时给了 agent 所在的 `main` 服务（`PGUSER`/`PGPASSWORD`，`LiveSQLBench-CLI/adapters/livesqlbench/template/environment/docker-compose.yaml:13-14`）和数据库边车（`:24-25`），而且这个角色是 SUPERUSER（`bootstrap-db-image.sh:36`）。上游评测脚本要用这个角色建、删 `<库>_process_1`（`template/tests/evaluator.py:46`、`:91`、`:109`、`:119-127`），和 agent 在同一个容器里、在 agent 之后跑。所以 128 要把写库凭据只给验证阶段（如 Harbor 的 `[verifier] env`，0.24 在验证阶段合并它：`harbor/verifier/verifier.py:190`），不用公开的默认密码，并在 Comments 写明怎么做到的。
- **U7 答案不外泄**：
  - 我们的评测脚本不把标准 SQL、标准结果打进日志；不照抄上游 `evaluation/src/db_utils.py:302` 那样逐条打印查询的日志。
  - 开发集取回时带回的 trial `verifier/` 输出里，只有 reward 和我们自己的、不含答案的诊断。
  - "答案不外泄"一条里的"路径"指路径和内容两样。
- **U8 agent 的输入**：照官方任务，给 agent 公开的 `db_assets`（知识库、列含义、schema）。`external_knowledge` 属于标准答案，不给：上游适配器把它写进题面（`LiveSQLBench-CLI/adapters/livesqlbench/adapter.py:254,270`），这一处不照抄。
- **U9 许可证**（上游 @ `e15cd22`）：
  - 根目录 `LICENSE` 是 MIT（"Copyright (c) 2024 bird_sql"），`LiveSQLBench-Agent/LICENSE` 是 MIT（"Copyright (c) 2025 BIRD-Interact Authors"），`LiveSQLBench-CLI/LICENSE` 是 Apache-2.0（版权行是没填的模板）。原文 `:39` 的"仓库 MIT"只对根目录成立。
  - soft EX 的 SQL 处理和结果比较，照抄 `evaluation/src/test_utils.py`（MIT），不重写，登记 `NOTICE`。
  - 取自 `LiveSQLBench-CLI/`（Apache-2.0）的内容保留原头，也登记 `NOTICE`。
- **U10 预测 SQL 的格式**：已写明照官方任务说明（`:38`；官方是 `/app/pred.json` 的 `{"instance_id", "predicted_sql": [...]}`），由 128 核实；127 的测试不钉死。
- **U11 旧行号与路径**：
  - `agent.py:1340-1342` → `src/data_agent/agent.py:1385-1387`（`uses_verifiers`）；
  - 取证笔记里的 `pyproject.toml:30` → `pyproject.toml:35`（`harbor==0.24.0`）；
  - Harbor 参考适配器的路径（稀疏 checkout，用 `git -C … show dc933be:adapters/...` 读）。

## Update 2026-11-27：留出集分数偏低，在开发集上找原因（D1–D5）

人定（2026-11-27，对齐会话）。

### 背景（实测）

- 留出集里程碑 `lsb-holdout-1`（`2f90141`，120 题 × 3 次 × 4 配置，基础设施错误 0）：`no-verifiers` 35.3%，`baseline` 36.1%，`no-skill` 35.0%，`no-context` 37.8%；3 个消融差值的 95% 区间都跨 0（`runs/lsb-holdout-1/reports/20261127T032240.021856Z/ablation.md`）。实扣 $18.68。
- 开发集 `lsb-dev-1`（`no-verifiers`，60 × 1）：32/60 = 53.3%；18 个库都跑到，0 个异常。
- 官方旧数字（取证笔记 `docs/research/2026-11-23-livesqlbench.md:121`）：o3-mini 47.78%、GPT-4.1 44.10%、Claude Sonnet 4 42.59%；口径（是否含管理题）未确认。
- 和官方 baseline 的差别（`data-agent-refs/livesqlbench/baseline/src/prompt.py`、`prompt_generator.py`）：官方单次调用，把 `<db>_schema.txt`、`<db>_column_meaning_base.json`、整份 `<db>_kb.jsonl`（只留 `id`、`knowledge`、`description`、`definition`）直接放进提示词，temperature 0、max_tokens 6000（`call_api.py:112-116`）。我们只在任务说明里给文件路径，agent 自己读。三份资料最大的库合计约 75 KB（insider、archeology）。
- 开发集 60 个 trial 都打开过知识库；整份读完的判对 13/30，只读一部分 9/15，只 grep 9/14，"没读知识库"不是原因（主会话 2026-11-27 统计 `runs/lsb-dev-1` 的事件文件）。

### 目标

回答：分数低是模型本身的水平、agent 的做法、还是资料的给法；再按失败类别决定改哪里。只用开发集。

### 规矩（不变的部分照旧）

- 开发集：读轨迹、看报错；**始终不看标准答案**（`:27`）。标准答案只用于验证脚本判分。
- 留出集：不看逐题细节（ADR-0004）；本轮不跑留出集。"留出集最多再跑一次"要不要用，等 D5 的改动在开发集上有显著收益后再由人定。
- `external_knowledge` 不给 agent，也不给单次调用（U8）。
- Skill 里不写针对某道题的内容（ADR-0005）。

### 判定口径与花费

- "明显"＝按题配对 bootstrap，95% 区间不跨 0（和 `ablate` 的消融同一口径）；跨 0 写"看不出方向"。
- 本轮（D1–D4 的运行）标价合计封顶 $15；到了就停下交人。

### 步骤

- **D1 对照组补齐**（主会话，不改代码）：`ablate --benchmark livesqlbench --split dev --config no-verifiers --reference no-verifiers --trials 3 --out runs/lsb-dev-3`，60 × 3 = 180 个 trial（不能在 `lsb-dev-1` 上加 trial 数：`ablate` 的 `check_request` 拒绝改了参数的同一 `--out`）。
- **D2 单次调用对照**（工单 138）：照官方 baseline，用同一个模型 `deepseek-flash` 单次调用写 SQL，用我们已对拍过的验证脚本判分，开发集 60 × 3。
- **D3 agent 加完整资料**（工单 139）：生成任务时可选把三份资料（同官方 baseline 的字段）直接写进任务说明；在 `no-verifiers` 上跑开发集 60 × 3，和 D1 比。开关默认关，留出集结果、产品默认、data-eng-bench、DAB 都不受影响。
- **D4 轨迹归类**（主会话派 sonnet 子 agent，每题一个，只读轨迹和公开资料，不碰标准答案）：用 D1、D3 的开发集轨迹，把判错的 trial 归类。起始类别：知识库定义没找到或用错、题意歧义、过滤/聚合/排序/取列写错、没验证或验证不足、执行出错、原因不明；子 agent 可补新类别。同一题 3 次有对有错的单独列出。人只看汇总表。
- **D5 定改动**：照下表读 D1–D3，再按 D4 的类别排先后，另开工单改通用的 Skill、提示词、工具或运行方式；只在开发集上量。

| D2 对 D1 | D3 对 D1 | 原因在 | D5 方向 |
|---|---|---|---|
| 看不出方向 | 看不出方向 | 模型本身 | agent 层能补的有限；换模型或补通用方法 |
| 明显更高 | 明显更高 | 资料的给法 | 默认内联资料，再按 D4 补 |
| 明显更高 | 看不出方向 | agent 的做法 | 按 D4 改 Skill、提示词 |
| 明显更低 | — | agent 有帮助，35% 是模型水平 | 查开发集与留出集的差距 |

顺序：138、139 并行派工；D1 可以先跑。两张工单合入后主会话跑 D2、D3，再做 D4，结果写进本节下面，D5 回对齐会话定。
