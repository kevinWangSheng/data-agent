# 消融口径：开发集/留出集分离，留出集上跑 6 个配置

没有一个参考项目做过消融（SignalPilot 只报总分），只能按学术惯例。决定：

- **开发集** = data-eng-bench 官方 `configs/fast-30.txt`（30 题），只用于失败归因循环：看失败原因、改 Skill、再跑。
- **留出集** = 从其余 73 题中按 `task.toml` 的 `difficulty` 分层预抽 30 题，抽样脚本与种子进 git；开发期不查看留出集的失败细节。
- 留出集上跑 6 个配置，每题 3 次（榜单要求的 k），共 540 次：全关（基线）、全开新版 Skill、关 Skill、关验证子 agent、关上下文策略、全开旧版 Skill。四个运行时层从代码层面就是 config 开关；失败归因循环不是开关，用"新版 vs 旧版 Skill 在留出集上的分差"表示其收益。
- 103 题全量只跑最终新版配置一次，用于榜单分数，不外推为模块贡献。
- 澄清层的消融挪到二期（需要 BIRD-Interact 模拟器，一期不跑）。
- 主指标：判定通过率；必做辅助指标：token 成本。修复率、上下文命中率不进验收。

## Consequences

- ~~消融改一次 Skill 就要重跑留出集的相关配置；预算见 Q5（待讨论）。~~ 由 ADR-0008 取代（2026-10-31）：留出集只在最终 6 配置消融时跑，最多再跑一次。
- `fast-30` 偏难（29 题可比对的里 16 hard + 4 very_hard；全量是 46 medium / 45 hard），留出集按全量比例抽会偏易，开发集与留出集分数不可直接互比。
- 一期只有 data-eng-bench 走这套；DAB 的 5 次 × 54 题按其提交格式跑最终配置，不做消融。

## Update 2026-11-08：留出集改为 5 个配置，全量用拼接凑出

人定（2026-11-08），依据是对齐会话 data-agent-ea 的审计和 Codex 的只读决策审查，主会话核实过代码：

- **留出集跑 5 个配置**：full、baseline、no-skill、no-verifiers、no-context，每题 3 次，共 450 个 trial。删掉 full-old-skill，理由有三：
  - 旧 Skill 引用的 `verify_model_values` 和 `value-verifier` 已经不存在（`skills-frozen/fa6028eef62b/dbt-workflow/SKILL.md:20,59-66,215`；`src/data_agent/verifiers.py:27` 只剩 `verifier`）；
  - 旧 validate 默认超时是 60 s，现在是 300 s；
  - 冻结后 `skills/` 的 10 次提交里，只有 f095f2d（工单 25）来自失败归因，其余是工具和 harness 的适配。
  - 所以这一行测的是"旧说明书配新工具"，不是失败归因循环的收益。
- **参照配置仍是 full**（`evals/ablate.py:105` 写死，不改代码）。每层贡献 = full 减去关掉该层的配置。另外单独报一行产品对比：no-verifiers 对 baseline（产品默认配置是 no-verifiers，人定 2026-11-08）。
- 只出汇总。差值不显著时写"看不出方向"，不写"没有收益"。
- **失败归因循环不给定量收益。** 开发集的归因报告（`runs/dev-3-attr/report/report.md`）、f095f2d 的改动记录和 FM1–FM4 的证据保留，在报告里作为"改进依据"。
- **no-verifiers 关掉的是整个验证层**，主 agent 的自查还在（`skills/dbt-workflow/SKILL.md` Step 8 没有 verifier 的分支）。它还顺带拿走了：
  - `map_columns`（注册在验证工具里）；
  - 任务原文文件（`src/data_agent/agent.py` 的 TASK_FILE_NOTE）；
  - Skill 里"Verification before delivery"一节（只在 verifier 运行时注入，`verifiers.py:54`）。
  - 报告要按这个范围写。
- ~~**103 题全量用拼接凑出**~~（同日作废）：b7c6071 上 `ablate` 用本地 `--path` 跑，trial 的来源是 `tasks`、没有 `task.ref`，榜单 CI 会过滤掉（`leaderboard/core/hub.py:94`，`ci/static_analysis.py:163-170`）；agent 版本是 git commit，不同 commit 的 trial 也拼不到一行。
- **103 题全量改为全新跑**（人定 2026-11-08）：先由工单 `.scratch/leaderboard-run/issues/01` 加一种运行方式，用登记处数据集 `snowflake-labs/data-eng-bench@sha256:de31…`、只跑一个配置。再在那个新 commit 上跑 103 × 3 no-verifiers，约 309 个 trial、约 $15。`src/`、`skills/` 和 b7c6071 一致。
- **DAB 只跑榜单的 54 题**：12 个数据集，baseline 和 no-context 各 54 × 5 = 270，共 540 个 trial。
  - 服务器上的 DAB clone（`6ad9d68`）有 104 题，多出来的 5 个新数据集（civic_unstructured、cve、imdb、krama、usaspending，共 50 题）不在榜单计分范围（DAB `README.md:158`），不跑。
  - 正式评测前先在 b7c6071 上小试跑：17 个数据集各跑 query1，两个配置各 1 次，共 34 个 trial。
