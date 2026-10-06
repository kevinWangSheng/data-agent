# 04 — 验证子 agent + 6 个验证工具

Status: ready-for-agent
Blocked by: 02
Spec: ../spec.md

## 做什么

移植 `list_tables / map_columns / check_model_schema / audit_model_sources / verify_model_values / query_database`；`verifier.md` 作为子 agent；协议照抄：审哪几项、只改 FAIL、改完再审、停止判据。`verifier` 开关控制。

## 验收

- [ ] 6 个工具在 DuckDB 与 Postgres 上各有测试（修 `SHOW TABLES` / `COUNT(*) FILTER` 方言）
- [ ] 开关开时轨迹里出现子 agent 调用
- [ ] fast-30 k=1 分数记录在 Comments

## 参考

ADR-0002；signalpilot-plugin `agents/verifier.md`、`value-verifier.md`

## Comments
