# 08 — DAB 接入

Status: ready-for-agent
Blocked by: 03, 04, 05
Spec: ../spec.md

## 做什么

`common_scaffold/tools` 包成 MCP 工具；官方 `db_description_withhint.txt`；补 `postgres-sql` Skill；输出提交 JSON + 轨迹；基线与全开各跑 54×5。

## 验收

- [ ] 本地 PG ≥17.5 + Mongo 8 起好，记录资源占用
- [ ] 单题跑通并通过官方 validate
- [ ] 全开与基线结果 + 提交 JSON

## 参考

ADR-0005；DAB `common_scaffold/`、README “Create Your Customized Agents”

## Comments
