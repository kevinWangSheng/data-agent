# 07 — 失败归因 FM1–FM4

Status: ready-for-agent
Blocked by: 06
Spec: ../spec.md

## 做什么

读 Harbor `trajectory.json`（及 DAB 轨迹），用 DAB `failure_analysis/` 的 judge prompt 给开发集失败题打唯一标签，输出汇总表。

## 验收

- [ ] 对 fast-30 的失败题产出标签表
- [ ] 只对开发集运行（拒绝留出集）

## 参考

ADR-0005；DAB `failure_analysis/`

## Comments
