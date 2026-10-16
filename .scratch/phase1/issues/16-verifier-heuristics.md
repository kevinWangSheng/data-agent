# 16 — 验证工具与验证提示的口径修正

Status: ready-for-agent
Blocked by: 07
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`verify_model_values`、`audit_model_sources`、`check_model_schema` 与两个验证子 agent 提示不再把正确的 SQL 判成 MISMATCH/FAIL，也不放过真实的 fan-out 与计数错误。指标与粒度的语义从模型 SQL 读出（sqlglot），不再靠结果类型和列名猜。

## Authority

- May edit: `src/data_agent/verifier_tools.py`、`src/data_agent/agents/`（verifier.md、value-verifier.md）、`skills/dbt-workflow/SKILL.md`（只限 Step 8 与验证工具的调用说明）、`tests/`（除 `tests/acceptance/`）、`NOTICE`（若改动上游复制文件的修改说明）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $1

## Context

来源：工单 07 Comments 里 PR #12 各轮 Codex 与 da-review 记为已知限制的意见（人决定 2026-10-16：在工单 12 消融前修）。ADR-0005（Skill 与提示不含榜单专属内容）。上游 signalpilot-plugin 的 `agents/verifier.md`、`value-verifier.md`（改动要在文件头修改说明里写明）。

## Acceptance

每条配一个修复前会失败的测试（构造的小 DuckDB 模型）：

- [ ] 用 sqlglot 解析模型 SQL（最外层 SELECT，跟进 CTE）：切片维度取 GROUP BY 表达式对应的输出列，不取聚合输出（`MAX(ordered_at) AS ordered_at` 不是维度）；数值分组列（如 `year INT`）若在 GROUP BY 里则是维度。
- [ ] 计数判定：只有最外层聚合是 `COUNT`（允许 CAST、COALESCE 这类透明包装）才是计数；`CAST(SUM(a)/COUNT(*) AS BIGINT)` 不是。
- [ ] `COUNT(DISTINCT x)` 与上游 `COUNT(DISTINCT x)` 比，不与 `COUNT(*)` 比。
- [ ] 条件计数 `COUNT(CASE WHEN p THEN 1 END)` / `COUNT(*) FILTER (WHERE p)`：基线带上谓词 p；带不上就标 inconclusive，不报 MISMATCH。
- [ ] 计数类指标精确比较；±10% 容差只用于非计数的加和/均值。
- [ ] 解析不了 SQL 时退回现有的列名规则，并在输出里说明"未解析 SQL"。
- [ ] `value-verifier.md` 的 `total_X` 规则只适用于行数名词（如 `total_orders`），与同文件的加和指标说明一致；`total_amount`、`total_revenue` 按加和。
- [ ] `verifier.md` CHECK 3：lookup 表同键多行一律报可能的 fan-out，除非确认模型预期粒度就是这样；非键字段不同只用来解释原因，不能据此判 PASS。
- [ ] `verifier.md` CHECK 1：读 `dbt_project.yml` 的 `model-paths`（缺省 `models`），在其下递归找 SQL 与 YAML（含 ephemeral/disabled 配置）。
- [ ] `check_model_schema` 接受"列名:类型"对并与 catalog 的类型比较（类型名做 DuckDB/PG 归一化）；`verifier.md` CHECK 2 的说明与工具能力一致。
- [ ] `make check` 通过

## Evidence required

`make check` 输出；full 配置真实 dbt 运行一次（接缝 A 的 DBT_TASK），贴两个 `verifier_end` 事件；另用一个含 `COUNT(DISTINCT)`、条件计数、`MAX(date)` 输出列的小模型不经模型直接调 `verify_model_values`，贴输出。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
