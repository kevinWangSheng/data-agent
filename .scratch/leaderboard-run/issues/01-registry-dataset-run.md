# 01 — 用登记处数据集跑上榜用的全量

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

新增一个 evals 子命令（暂名 `evals board`，名字可以改，但要写明理由）。它只跑一个配置，题目从登记处的 `snowflake-labs/data-eng-bench@sha256:de31878dba7e5a9aad85dc8276820b0a20ab593aed46ff93dfbbbdb6007ec995` 取，可以用 `-i` 拆批，跑出来的 trial 能通过榜单 CI 的检查。

## Authority

- May edit: `evals/`、`tests/`（新增测试；已有测试只能加用例，不能改断言）、`scripts/da-remote` 的 `pull`（见验收第 6 条）、`docs/setup.md` 里讲怎么运行的部分
- Must not edit: `src/`、`skills/`、`docs/adr/`、`docs/plan.md`、`.claude/`、`CODING_STANDARDS.md`、其他工单文件
- Server access: none（第 6 条的真实运行要求分支已推送，由主会话在 push 后做）
- Spend cap: $0.20 of model calls（1 道题 × 1 次，约 $0.05）

## Context

- Spec: `../spec.md`（为什么 `ablate` 跑出来的 trial 上不了榜）
- 榜单 CI：`../data-agent-refs/data-eng-bench/leaderboard/src/leaderboard/ci/static_analysis.py:64-71,122-176`；`core/hub.py:26-27,94,101-111`；`leaderboard/SUBMIT.md:34-46,60-61`
- 官方 duckdb 配置：`../data-agent-refs/data-eng-bench/configs/data-eng-bench-duckdb.claude-code.yaml`（`DB_TYPE=duckdb` 用 `environment.env` 传，不算覆盖）
- 可以复用的：`evals/ablate.py` 的 job 运行、`--budget` 花费上限（`evals/spend.py`）、续跑和 infra 处理（`keep_spend`、`--fail-fast`）、wheel 预取（`prefetch_wheels`，现在按本地 tasks 目录找镜像，要能用于登记处的题）、readiness（`evals/readiness.py`）
- Harbor 0.24.0：`harbor run -d <name>@<ref>`、`-i/--include-task-name`
- agent 版本 = git commit（`evals/harbor_agent.py:233`）

## Acceptance

- [ ] 子命令接受：一个 `--config`、`--out`、`--budget`、`--trials`（默认 3）、`--n-concurrent`、可重复的 `--task`（转成 `-i`）。数据集名和 ref 写成常量，和 `core/hub.py` 的 `DATASET`、`DATASET_REF` 一致。
- [ ] Harbor 命令用 `-d <DATASET>@<DATASET_REF>`，不用 `--path`。job 配置里没有 `timeout_multiplier`（或为 1.0），没有 `ci/static_analysis.py:64-71` 列出的任何覆盖键；`DB_TYPE=duckdb` 用 `environment.env` 传。用 FakeHarbor 测试断言命令行和写出的配置。
- [ ] 花费上限、续跑（保留中断和 infra 报错 trial 的花费，记一次）、`--fail-fast`、readiness 的行为和 `ablate` 一致，有测试覆盖。同一个 OUT 里不同 `--task` 的几批，按 commit、Skill 版本、模型、配置一致的规则拒绝混用。
- [ ] 同一个 OUT 的几批合并后，用 `evals score`（或新子命令自带的报告）给出 103 题 × 3 的准确率 ± 标准误和 pass@k，和榜单算法一致（`leaderboard/core/metrics.py`）。还没凑满 103 × 3 时，要明确写出缺了哪些。
- [x] 取证（第 1 次派工已做，结果见 Comments）：登记处 v1.0 和 a3278ad 的 103 题都有差异。人定（2026-11-09）接受这些差异照样上榜：榜单只认 v1.0 的摘要，所有提交者跑的是同一份题（PR #10 的 67.96% 也是）。差异要写进报告（见下一条）。
- [ ] 基础镜像：登记处 Dockerfile 是 `FROM dbt-bench-base`。新子命令开跑前检查本机有没有这个镜像，没有就 `docker pull ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0` 再 `docker tag` 成 `dbt-bench-base`（GHCR 公开可拉，2026-11-09 查过 HTTP 200），不改题目文件。拉取或 tag 失败时在起 job 之前退出。用 FakeHarbor 或替身测试断言这个顺序。
- [ ] 数据保存和报告（结果要写进简历，所以要能事后分析）：
  - 每个 trial 的 `result.json`、`trajectory.json`、`config.json`、花费和 token 都留在 OUT 里，和 `ablate` 一样能续跑。
  - 报告（md + json）给出：103 × 3 的准确率 ± 标准误和 pass@k（榜单算法）；按难度分的准确率；总花费、每 trial 和每次成功的花费、token、墙钟；infra 排除数。
  - 报告里固定写一节"和 a3278ad 的差异"：`dbt-cart-abandonment-recovery` 在 v1.0 上必得 0 分（`final_score` 未定义），所以 103 题的上限是 102/103；97 题题面保留 DuckDB/Snowflake 兼容提示；同时给出去掉这道题后的 102 题准确率，便于和本地分数对照。
  - 留出集隐私（ADR-0004）：登记处 103 题里包含留出集 30 题。报告的逐题表只列不在 holdout 里的题（split 从 `evals/manifest.json` 读）；留出集 30 题只给汇总（通过率、花费）。`da-remote pull` 对这种 OUT 只带回报告和非留出题的 trial，留出题的 trial 留在服务器上，供人上传时用。用测试断言报告和 pull 都不出现留出题的题名。
- [ ] 真实运行（主会话在 push 之后做；工单 agent 在本地用 FakeHarbor 演示一次完整的 CLI 调用即可）：在服务器上 `da-remote setup <本分支 commit>`，用新子命令跑 1 道开发集的题 × 1 次（no-verifiers）。在 Comments 里贴出：trial 的 `config.json` 中 `task` 和 `source` 两项、对照登记处摘要的 `task.ref`、`trajectory.json` 是否存在、花费。
- [ ] `make check` 通过。

## Evidence required

测试命令和输出；第 5 条的比较结果；第 6 条的命令和真实输出（不要贴服务器地址或密钥）。

## Stop conditions

- 登记处数据集要求我们覆盖超时或资源，或者 agent 在登记处的题上起不来。
- 拉 GHCR 基础镜像、tag 成 `dbt-bench-base` 以后，登记处的题仍然起不来。
- 需要改 `src/` 或 `skills/`。
- 第二轮审查后还有问题。

## Failure handling

Status 设为 `ready-for-human`，写一条 Comment：试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1（第 2 次派工见 Comments 末尾）
- Review rounds: 0（没有提交）
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: 在取证（验收第 5 条）停下，命中停止条件 2，条件 1 大概率也会命中
- Human review minutes: —

## Comments

- 停下（2026-11-08，工单 agent）：在验收第 5 条取证时停下，没有写代码、没有提交，$0。
  - 登记处元数据：103 题，每题摘要都等于 a3278ad 的 `dataset.toml` 里记的摘要。`harbor download` 第一次在第 58 题报 ConnectError，重试后下全。
  - 和 a3278ad 的 `tasks/` 比：**103 题全都有差异**，有 310 个文件逐字节相同。
    - `environment/Dockerfile`（103 题）：登记处是 `FROM dbt-bench-base`，a3278ad 是 `FROM ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0`。
    - `task.toml`（103 题）：a3278ad 多了 `[environment.healthcheck]`，在 duckdb 下不起作用。
    - `tests/test_outputs.py`（103 题）：差的都是 Snowflake 连接参数（host、schema），在 duckdb 下不起作用。另有 1 处影响评分：登记处的 `dbt-cart-abandonment-recovery` 第 298 行写 `{"reward": final_score}`，但 `final_score` 没有定义，所以这道题每次都得 0 分；a3278ad 写的是 `final`。
    - `instruction.md`（97 题）：登记处还留着"SQL 要同时兼容 DuckDB 和 Snowflake"一类的提示，a3278ad 删掉了（上游 PR #2）。
    - `solution/solve.sh`（50 题）：只差 Snowflake host。
    - `job-config.yaml`：只有 a3278ad 有。
  - **很可能起不来**（推断，没有实际构建）：`dbt-bench-base` 在本机不是现成的镜像，Docker Hub 返回 404，Harbor 0.24.0 也不改写镜像名。
  - 推断（未核实）：a3278ad 改了 `tasks/` 以后没有重新发布，`dataset.toml` 还是 v1.0 的摘要。refs 是浅克隆，看不到 git 历史。
  - 以后如果接着做，可以用的事实：
    - `-i` 按 `snowflake-labs/<task>` 匹配；
    - Harbor 默认 `timeout_multiplier=1.0`，CI 检查的覆盖键都不设；
    - trial 的 `config.task` 会带 `name`、`ref`、`source="snowflake-labs/data-eng-bench"`；
    - 仓库的 manifest 只有 60 题，需要另写一份 103 题加摘要的清单。
  - 基线 `make check`（没改过的分支）：2033 passed，1 skipped，38 deselected。
  - 证据在本机 `/tmp/lr01-registry/`（classify.txt、tests_diff.txt、diff.txt、dl/），脚本在 `/tmp/lr01-scripts/`。
- 主会话核实（2026-11-08）：
  - 登记处的 `dbt-cart-abandonment-recovery/tests/test_outputs.py:291,298` 确认是 `final = …` 却写 `final_score`；同一题的 Dockerfile 是 `FROM dbt-bench-base`；instruction 多了 "## Guidelines - The SQL syntax should work on both DuckDB and Snowflake"。
  - **榜单现状**：Harbor Hub 的 leaderboard-read API 返回 0 行。唯一的提交是上游 PR #10（Jitto Build，Opus 5.5 high，在登记处 v1.0 上跑 103 × 3，0 errored，本地 67.96%），2026-09-30 开到现在还开着；提交者说这个 PR 上的榜单 CI 每次都是 startup_failure，没有任何 job 跑起来，维护者 2026-08-19 触发的 Publish base image 也一样。
- 人定（2026-11-09）：data-eng-bench 一定上榜，接受登记处 v1.0 和 a3278ad 的差异（包括那道必得 0 分的题）。工单改动：取证一条标为已做；新增基础镜像一条、数据保存和报告一条（含留出集隐私与 `da-remote pull` 过滤）；停止条件"差异涉及评分"删去，改为"tag 基础镜像后仍起不来"；Authority 加 `scripts/da-remote` 的 `pull`。上一轮的 `/tmp/lr01-registry/` 下载可以复用。状态回到 ready-for-agent，重新派工。
