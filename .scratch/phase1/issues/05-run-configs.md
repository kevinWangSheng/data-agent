# 05 — 运行配置与层开关

Status: in-review
Blocked by: 03
Spec: ../spec.md
Claimed by: claude (worktree 05-run-configs)
Branch: worktree-05-run-configs

## Goal

6 个命名配置（基线全关、全开、关 Skill、关验证子 agent、关上下文策略、全开旧版 Skill）可选；配置决定加载哪些 Skill、暴露哪些工具；关掉的层不留事件。后续工单把各自的层接到这里的开关上。

## Authority

- May edit: `src/data_agent/`、配置文件目录、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $1

## Context

ADR-0004（6 个配置）；CONTEXT 的 Baseline、Ablation；spec「运行配置」。

## Acceptance

- [ ] 接缝 B 中「工具列表随配置变化」的验收测试转绿（以目前已有的工具为限）
- [ ] 接缝 A 中 `test_baseline_leaves_no_verifier_or_context_trace` 转绿（`make test-integration`；基线不开验证层，所以不依赖 07）
- [ ] Skill 版本取 Skill 目录内容哈希并写进版本头
- [ ] `make check` 通过

## Evidence required

用基线与全开两个配置各跑一次同一题，贴两份事件文件的工具列表差异。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: ~$0.007（两次真实运行）
- Outcome: 开关、配置、Skill 哈希版本头完成；接缝 A 基线测试等 04
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `01bd07e`、`551a919`）

**实现**：`src/data_agent/configs/` 6 个配置（baseline、full、no-skill、no-verifiers、no-context、full-old-skill），各含三层开关、Skill 目录、最大轮数（dbt 200 / query 50）、$1 预算；单测保证各配置轮数与预算一致。Skill 层关时 system prompt 不含 Skill，版本头 `skill_version: "none"`；开时为 Skill 目录内容哈希。版本头加 `layers` 字段。`build_server` 接收配置，06/07 的工具按开关注册。配置指向不存在的 Skill 目录时加载即失败，CLI 退出码 2。

**检查**：`make check` 532 passed, 15 deselected, 15 xfailed。

**真实运行**（同一 query 题，fixture DuckDB）：
```
data-agent run --task "What is the total amount of all completed orders? ..." --type query \
  --workspace runs/05/ws/dbt_shop --duckdb runs/05/ws/shop.duckdb --config {baseline|full} --events-dir runs/05/{config}
baseline exit=0  header: skill_version=none, layers all false; 10 turns, $0.0033; answer 1459.75
full     exit=0  header: skill_version=3c62ed4f0db4, layers all true; 7 turns, $0.00341; answer 1459.75
init MCP tools (both):  mcp__data__list_tables, mcp__data__query_database
built-in tools (both):  Glob, Grep, Read
verifier events (both): none
```
工具列表相同，因为层工具要到 06/07 才有。

**审查**：`da-review main` 两轮无意见。`eng:code-review main` 修了 3 条（缺失 Skill 目录加载即失败并配 CLI 测试；`build_server` docstring；测试辅助函数去重）。

**不修的理由**：
- 接缝 A `test_baseline_leaves_no_verifier_or_context_trace` 仍 xfail：该测试跑 dbt 任务，依赖 04（本工单 Blocked by 漏写 04）。04 合并后由主会话跑 `make test-integration` 并去掉 xfail。
- `full-old-skill` 目前等于 `full`：还没有旧版 Skill。已写进工单 12 验收标准。
- 大库 75 轮上限未做：没有"大库"定义与任务类型。已写进工单 13 验收标准。
- 保留 `layers` 字段、`SKILLS_DIR` 仅测试使用、配置文件重复轮数与预算（单测防漂移）。

**预期冲突**：04 也改 `agent.py`；合并后 dbt Skill 须从 `config.skills_dir` 加载、Skill 层关时跳过。
