# 05 — 上下文工具：discover / hydrate / dictionary_search

Status: ready-for-agent
Blocked by: 02
Spec: ../spec.md

## 做什么

discover 只回引用 + ≤200 字摘要；hydrate 读单个对象全文；dictionary_search 查列的真实取值；join 发现先用 `_id` 列匹配。`context_tools` 开关控制。

## 验收

- [ ] 三个工具的单元测试
- [ ] 开关开时 agent 不做整库 schema dump（轨迹抽查）
- [ ] fast-30 k=1 分数记录在 Comments

## 参考

ADR-0003；`docs/research/2026-09-21-ktx-context-layer.md`

## Comments
