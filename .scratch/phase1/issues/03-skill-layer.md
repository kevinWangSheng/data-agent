# 03 — Skill 层（dbt）

Status: ready-for-agent
Blocked by: 01
Spec: ../spec.md

## 做什么

照抄 signalpilot-plugin 的 dbt 相关 SKILL.md（8 步工作流等），删除 SignalPilot 专属工具引用，改用本项目工具名；`skills` 开关控制是否加载。保留旧版快照，用于“全开旧版 Skill”配置。

## 验收

- [ ] 开关开/关时，轨迹里可见/不可见 Skill 加载
- [ ] Skill 不含任何基准专属内容（人工过一遍）
- [ ] fast-30 k=1 分数记录在 Comments

## 参考

signalpilot-plugin `skills/`；ADR-0005（不写针对基准的 prompt）

## Comments
