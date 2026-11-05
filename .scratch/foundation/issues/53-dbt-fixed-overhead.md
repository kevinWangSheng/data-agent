# 53 — dbt 固定开销：scan 每个 yml 只解析一次，评测适配层设 DBT_TARGET，Skill 不来回切 --vars

Status: ready-for-agent
Blocked by: —
Spec: ../../../docs/research/2026-11-05-foundation-latency-audit.md（§5）
Claimed by: —
Branch: —

## Goal

scan 一次从约 74 s 降到 15 s 以内；data-eng-bench 的每个 trial 第一次跑 dbt 不再完整重新解析；Skill 提醒不要来回切换 `--vars`。scan 的输出内容不变。

## 已和用户定下的（2026-11-05，grilling Q9、Q10、Q18）

- `scan_project.py` 改成每个 yml 只解析一次，建好"模型 → 所在 yml 文件和同级模型"的映射；文件头和 `NOTICE` 照现有格式注明修改。
- `DBT_TARGET=dev` 只放在评测适配层 `evals/harbor_agent.py`，只对 data-eng-bench 生效；agent 本体（`src/data_agent/`）和 Skill 里都不写（ADR-0005）。
- "不要来回切换 `--vars`（每切一次多一次完整解析）"写进 `skills/dbt-workflow/SKILL.md`。工单 47 也改这个文件；后合入的那张解决冲突（先例：49 与 47）。

## Authority

- May edit: `skills/dbt-workflow/scan_project.py`、`skills/dbt-workflow/SKILL.md`（只加 `--vars` 那一句，放在讲 dbt 命令的地方，不动 Step 8）、`evals/harbor_agent.py`（只加环境变量）、`NOTICE`、`tests/test_dbt_skills.py`、`tests/test_harbor_agent.py`、新的测试文件
- Must not edit: `src/data_agent/`、`skills/` 下其他文件、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（服务器真实运行由主会话跑）
- Spend cap: $0

## Context

- scan：`scan_project.py:576-590` 对每个缺 SQL 的模型都重新 `rglob("*.yml")` 并 `yaml.safe_load`；本地 base-image 项目 34 × 31 = 1055 次，40 s 里占 35.7 s。服务器 16 次 scan 有 15 次是 70–79 s。scan 本身不调 dbt。
- 完整重新解析：镜像用 `--target dev` 构建（data-eng-bench `base-image/Dockerfile:64`），target 算进 vars_hash；agent 跑 dbt 不带 `--target`，所以每个 trial 第一次是完整解析（43–83 s，之后 25–38 s）。工单 40（`.scratch/phase1/issues/40-guards-workdir-dbt-timeouts.md:29`）当时认为避免不了；本机 dbt 1.10.23 实测：设环境变量 `DBT_TARGET=dev` 后再跑 `--target dev`，走部分解析（dbt `cli/params.py:678` 的 `envvar="DBT_TARGET"`）。服务器容器里是否同样生效、镜像自带的 `partial_parse.msgpack` 能否复用，未验证。
- 环境变量注入点：要核实 `evals/harbor_agent.py` 里传给 `exec_as_agent` 的 env（`:312-325` 附近），以及 validate 子进程会不会继承。
- `--vars`：receivables 两个 trial 里来回切 `--vars`，多出 5 次 60–68 s 的完整解析。
- 数据：本机 `runs/t47-screen/foundation-audit/a5/dbtcalls.txt`（每 trial 的 dbt 调用序列）。

## Acceptance

- [ ] 新测试：在一个有多个 yml 的小项目上，scan 输出和修改前逐字相同；yml 解析次数等于 yml 文件数（修复前会失败）。
- [ ] 新测试：Harbor agent 给 data-eng-bench 运行设了 `DBT_TARGET=dev`。
- [ ] `test_dbt_skills.py` 断言 SKILL.md 有 `--vars` 那一句，且不含针对某道题的内容。
- [ ] 本地复测：base-image 项目的 scan 单次 ≤15 s（记下前后对比）。
- [ ] `make check` 通过。

## Evidence required

- 测试输出和本地复测数。
- 主会话的真实运行：服务器 `dbt-receivables-aging-buckets` × 1（full）。scan 单次 ≤15 s；trial 第一次 dbt 调用的输出里没有 "Unable to do partial parsing"。命令和输出贴到 Comments。

## Stop conditions

- scan 输出必须变化才能做下去。
- 服务器上 `DBT_TARGET=dev` 仍然触发完整解析：记录下来，交回由人定是否保留。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
