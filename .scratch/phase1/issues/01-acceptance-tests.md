# 01 — 三个接缝的验收测试与夹具

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

spec 约定的三个接缝（A：`data-agent run` CLI；B：MCP server；C：评测 CLI）各有一组验收测试和所需夹具，并在测试里定下各接缝的外部接口。测试全部标 `xfail(strict=True)`，`make check` 保持绿。

## Authority

- May edit: `tests/acceptance/`、`tests/fixtures/`、`pyproject.toml`（只加 pytest 标记配置）
- Must not edit: 其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none（本工单不跑 integration）

## Context

spec「Testing Decisions」三个接缝的核验项；spec「Implementation Decisions」里的 CLI 接口、MCP 工具分组、评测 CLI 功能；CONTEXT 的 Read-only gateway、Discover→hydrate、Dictionary search、Evaluation holdout 词条。夹具：小 DuckDB 文件（几张有脏键和枚举值的表）、迷你 dbt 项目（2–3 个模型）、伪造的 Harbor 结果目录与事件文件（含开发集与留出集各几题、含出错运行）。

## Acceptance

- [ ] 接缝 A（`integration` 标记）约 4 个：查数答案在容差内；dbt 模型建成且数值正确；事件文件版本头在第一条、序号连续、结果事件带 token 与花费；基线配置下无验证者事件和工具；要求写库的任务被拦且运行不崩溃
- [ ] 接缝 B 约 12–15 个：工具列表随配置变化；SELECT 可执行；DDL、DML、危险函数各若干条被拦并返回可读错误；discover 只回引用与 ≤200 字摘要；hydrate 回全文；dictionary search 回真实取值；6 个验证工具在 DuckDB 夹具上各一个通过、一个失败用例；Postgres 版用例标 `integration`
- [ ] 接缝 C 约 9 个：同一种子抽样相同且按难度分层；算分与 `metrics.py` 一致；出错记 0；bootstrap 区间在已知数据上符合预期；留出集报告不含任何逐题结果或题号；开发集报告含逐题结果；scrub 移除 key、私有端点、IP、主机名；累计花费到 80% 停止
- [ ] 所有测试标 `xfail(strict=True)`；`make check` 通过
- [ ] 测试只测外部行为（CLI 输入输出、MCP 客户端看到的结果、评测 CLI 输出文件），不引用任何内部函数

## Evidence required

`make check` 输出；`uv run pytest tests/acceptance -q -rx` 输出（显示每个测试都是 xfail）。人审并合并后，后续工单才能开工。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
