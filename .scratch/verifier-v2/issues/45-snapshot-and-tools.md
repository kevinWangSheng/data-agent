# 45 — 快照与 4 个确定性验证工具

Status: ready-for-agent
Blocked by: 44, 39（phase1）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

实现 spec 里的快照、`model_diff`、`contract_check`、`grain_facts`、`compare_by_key`，并在启用了验证器的配置里注册成 MCP 工具；`verify_model_values` 不再注册。让 44 里对应的验收测试转绿。

## 已和用户定下的（2026-11-04）

全部按 spec「快照」和「工具」两节实现，下面几条不要改：
- 快照范围：`dbt_project.yml`；`models/macros/snapshots/tests` 下的 `.sql` 和 `.yml`；`seeds/` 单个文件 ≤5 MB。不含 `dbt_packages/target/logs`。放在 workspace 外的运行临时目录。
- `model_diff` 的解析方法：替换 `ref`/`source`，把 `config`/`var`/宏调用换成占位符，再用 sqlglot 解析；解析不了就退回文本 diff 并标"未解析"。
- 列契约只认题面和快照里原有的 YML；窗口函数只查改动过的；粒度检查的豁免只认题面和原有 YML；`compare_by_key` 拒绝引用被测模型或其他改动过的模型，用 full outer join 覆盖全部键。
- 所有 SQL 走只读网关；表目录用工单 39 的缓存。

## Authority

- May edit: `src/data_agent/verifier_v2/`、`src/data_agent/verifier_tools.py`（只改注册：加新工具、去掉 `verify_model_values`）、`src/data_agent/agent.py`（只加开跑前的快照调用）、`NOTICE`、`tests/`（`tests/acceptance/` 除外；按 AGENTS.md 只许删掉转绿测试的 `xfail`）
- Must not edit: `tests/acceptance/` 的其余内容、`src/data_agent/agents/`、`skills/`、`src/data_agent/configs/`、钩子相关代码（工单 46）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0.3

## Context

- spec：`.scratch/verifier-v2/spec.md`；ADR-0009。
- 上游参考：`SignalPilot/benchmark/signalpilot-plugin/agents/verifier.md:34-74`（粒度、NULL 子检查，以及 MODIFY 时 population 检查记 N/A）。
- 现有工具和舍入规则：`src/data_agent/verifier_tools.py`（`verify_model_values` 的舍入要沿用到 `compare_by_key`）。

## Acceptance

- [ ] 44 里快照、`model_diff`、`contract_check`、`grain_facts`、`compare_by_key`、配置相关的验收测试转绿（删掉 `xfail`，其余一字不改）。
- [ ] 单元测试：sqlglot 在 duckdb 和 postgres 两种方言下都能解析夹具。
- [ ] `make check` 通过。
- [ ] （主会话）服务器真实运行：对 dbt-fix-division-by-zero 的 base image 项目做一次快照，手工改掉一个过滤条件后调 `model_diff`，贴出输出，并记下 2356 个模型下的快照耗时和大小。

## Evidence required

验收测试和单元测试的输出；`make check` 输出；真实运行的输出。

## Stop conditions

- 有验收测试只有改测试本身才能通过。
- 快照在 2356 个模型的项目上超过 10 秒或 50 MB：停下，贴出实测数据。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
