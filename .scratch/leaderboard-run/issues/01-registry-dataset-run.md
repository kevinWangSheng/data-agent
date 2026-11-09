# 01 — 用登记处数据集跑上榜用的全量

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

新增一个 evals 子命令（暂名 `evals board`，名字可以改，但要写明理由）。它只跑一个配置，题目从登记处的 `snowflake-labs/data-eng-bench@sha256:de31878dba7e5a9aad85dc8276820b0a20ab593aed46ff93dfbbbdb6007ec995` 取，可以用 `-i` 拆批，跑出来的 trial 能通过榜单 CI 的检查。

## Authority

- May edit: `evals/`、`tests/`（新增测试；已有测试只能加用例，不能改断言）、`docs/setup.md` 里讲怎么运行的部分
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
- [ ] 取证（本地做，`harbor download` 或登记处客户端，不花模型钱）：把登记处 v1.0 的题下载下来，和 `../data-agent-refs/data-eng-bench`（a3278ad）的 `tasks/` 逐个文件比较，在 Comments 里写明是否完全一致，不一致就列出哪些题、哪些文件不同。
- [ ] 真实运行（主会话在 push 之后做；工单 agent 在本地用 FakeHarbor 演示一次完整的 CLI 调用即可）：在服务器上 `da-remote setup <本分支 commit>`，用新子命令跑 1 道开发集的题 × 1 次（no-verifiers）。在 Comments 里贴出：trial 的 `config.json` 中 `task` 和 `source` 两项、对照登记处摘要的 `task.ref`、`trajectory.json` 是否存在、花费。
- [ ] `make check` 通过。

## Evidence required

测试命令和输出；第 5 条的比较结果；第 6 条的命令和真实输出（不要贴服务器地址或密钥）。

## Stop conditions

- 登记处数据集要求我们覆盖超时或资源，或者 agent 在登记处的题上起不来。
- 第 5 条发现题目文件和 a3278ad 不同，而且差异涉及测试或评分。
- 需要改 `src/` 或 `skills/`。
- 第二轮审查后还有问题。

## Failure handling

Status 设为 `ready-for-human`，写一条 Comment：试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
