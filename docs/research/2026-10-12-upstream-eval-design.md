# 上游评测口径取证：分数汇总、"更好"的判定、答案隔离、冒烟数据集、成本

日期：2026-10-12。方法：读源码；DAB 论文只经 WebFetch 摘要，相关条目标 `未确认`。

commit：data-eng-bench@a3278ad，harbor@dc933be（只稀疏克隆 `src/`），DataAgentBench@6ad9d68，SignalPilot@f5eee22，nao@5bde830，dash@6d86fca，ktx@49a4ae6，BIRD-Interact@451fe2c。

## 1. 分数怎么汇总

- data-eng-bench 榜：准确率 = 成功 trial 数 / 全部 trial 数（reward > 0 算成功）；标准误按题计算 s² = (1/n²)·Σ pᵢ(1−pᵢ)/(kᵢ−1)，显示为 `X% ± Y%`（`leaderboard/src/leaderboard/core/metrics.py:28-48,227-238`）；pass@2/3 用无偏估计（`:20,51-72`）。只按准确率排名，不做显著性检验（`leaderboard/leaderboard.yaml:261-263`）。每题至少 3 次（`ci/static_analysis.py:46`）；出错或被取消资格的 trial 记 0（`SUBMIT.md:39-42`，`metrics.py:75-89`）。
- Harbor：默认对 trial 的 reward 取平均，缺 reward 记 0（`src/harbor/metrics/mean.py`，`job.py:680`）；内置 pass@k（`utils/pass_at_k.py`）；`src/` 里没有标准误、置信区间或 bootstrap。
- DAB：Pass@1 = 每个数据集各题平均通过率的平均（`README.md:81`，`stats_scripts/avg_accuracy.py:32-35`）；提交需每题 5 次共 270 次加轨迹（`SUBMISSION_RUBRIC.md:95-96`）；榜上无置信区间。论文也未报置信区间（未确认）。
- nao test：pass@1、pass@k、pass^k，无置信区间（`cli/nao_core/commands/test/summary.py:65-80`）。

共识：没有上游在比较 agent 时做显著性检验；只有 data-eng-bench 给了 ± 标准误。

## 2. 基线、消融与"更好"

- SP 回归告警：与同评测集、同构建指纹的前 5 次运行的中位数比，下降 ≥ 10 个百分点就告警（`gateway/gateway/evals/notifications.py:31-61`，`config/evals.py:79-81`）。
- SP 基准：单次运行的点估计，如 Spider2-DBT 65.6（`README.md:5-7,52-55`）。
- dash `improve.py`：改前改后对比，原来通过的题现在失败就回滚（`evals/improve.py:320-333,389-420`）。
- DAB 论文：做了"去掉提示""去掉描述和提示"两个消融，pass@1 变化不超过 5 个百分点（未确认原文）。
- DAB 自带 ReAct 脚手架（`common_scaffold/DataAgent.py`）的榜上分数，可作为榜单对照（Leaderboard reference）：Claude Opus 4.6 0.5551（5 次，`README.md:69`）、Gemini-3-Pro 0.4663（50 次，`:72`）、GPT-5-mini 0.3663（50 次，`:75`）；分数据集明细在 `docs/data/leaderboards.json`。

## 3. data-eng-bench 的答案隔离

- 任务镜像只含 dbt 项目、`retail.duckdb` 与辅助脚本，不含 `tests/`、`solution/`（`base-image/Dockerfile:42-85`）；`.dockerignore` 去掉 `target/`。
- Harbor 先跑 agent，再上传日志、收集产物，最后才把 `tests/` 拷进容器运行验证（`src/harbor/trial/single_step.py:36-53`，`verifier/verifier.py:165-186`）；`solution/` 只给 oracle agent（`agents/oracle.py:57-70`）。
- 期望值在 `test_outputs.py` 里从只读打开的 `retail.duckdb` 现算，没有答案文件（`tasks/dbt-abc-classification/tests/test_outputs.py:127-130,408-416`）；全部 pytest 通过才算成功。
- 剩余风险：任务和答案在 GitHub 上公开，官方配置关掉 WebSearch/WebFetch（`README.md:166-197`，`configs/*.claude-code.yaml`）；同一容器里 agent 可能在验证前篡改环境，榜单用 LLM `/judge` 审轨迹（`SUBMIT.md:148-170`）。

## 4. 演示与端到端数据集

- SP `e2e-eval/`：用桩 agent 测评测管线本身（`run_e2e.py:1-8`）。期望写在 `eval-set/eval.json`：读任务用 `checks:[{name, value, tolerance}]`，写任务用 `grade.kind:"model_rebuilt"` 加期望 `row_count`、`grain`、`columns`；还有故意出错的负例（`r2_wrong_answer`、`w2_rebuild_skipped` 等），检查评分能抓住错误。
- jaffle_shop 在上游有用：SP 的 live 测试克隆 `dbt-labs/jaffle_shop`（`gateway/tests/test_vercel_agent_workflow_live.py:30`）；nao 的 `example/` 用 `jaffle_shop.duckdb`（`example/nao_config.yaml:3-21`），用例是 YAML 的问题 + 参考 SQL（`example/tests/total_revenue.yml`）。
- dash：带种子的合成 SaaS 数据（`scripts/generate_data.py:1-12`）。ktx：Chinook、Northwind、Sakila（SQLite，钉 sha256，`scripts/public-benchmark-manifest.json`）。

## 5. 成本报告

- Harbor `AgentContext`：`n_input_tokens`（含缓存）、`n_cache_tokens`、`n_output_tokens`、`cost_usd`（`src/harbor/models/agent/context.py:21-32`），claude-code agent 在 `populate_context_post_run` 填写（`agents/installed/claude_code.py:1683-1686`）；自定义 agent 不填，榜上记 0。
- data-eng-bench 榜：整份提交的未缓存输入、缓存输入、输出 token 与 `total_cost_usd`，外加平均 trial 时长；只报总量（`metrics.py:116-153`）。
- SP 基准：逐题 `TaskResult` 含 `cost_usd`、`usage`、轮数、工具调用次数，取自 SDK `ResultMessage.total_cost_usd`（`benchmark/core/audit.py:40-55`）。
- DAB 榜无成本字段；论文画了成本-准确率图（`plotting_scripts/cost_acc.py:11-12`）。BIRD-Interact 报每题平均成本（`README.md:122`）。
