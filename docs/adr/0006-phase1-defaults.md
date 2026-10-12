# 一期默认口径：范围、顺序、预算、验收

按 `AGENTS.md`「推进原则」直接定的默认值，可随时改。

- **范围**：已移到 `docs/plan.md` 第 1 节（2026-10-11 讨论过）。
- **顺序**：先 dbt（data-eng-bench，消融全在它上面；SignalPilot 插件有 8 步 dbt 工作流和 verifier 可抄），后 DAB。
- **预算**：计费按模型走 API key（默认 DeepSeek，见 ADR-0001 Update），成本可精确报告。暂不设金额上限；第一个 tracer bullet 跑完开发集后按实测单次成本 × 约 1,119 次外推，再定上限与模型（ADR-0001 待议项）。超支时的降级顺序：留出集 k 3→2 → 去掉"全开旧版 Skill"配置 → 留出集 30→20 题；上榜所需的全量 103×3 与 DAB 54×5 不降。
- **超过基线**：已移到 `docs/plan.md` 第 4 节（2026-10-12 讨论过）。
- **接任意 Postgres + dbt**：已移到 `docs/plan.md` 第 4 节。

## Consequences

- 任务范围以 `docs/plan.md` 第 1 节为准。
