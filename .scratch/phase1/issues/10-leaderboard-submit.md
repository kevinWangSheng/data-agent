# 10 — 榜单提交与最终报告

Status: ready-for-agent
Blocked by: 06, 07, 08
Spec: ../spec.md

## 做什么

留出集 6 配置消融 → 全量 103×3 → `uv run lb submit`；DAB 开 PR；写消融与失败归因报告。

## 验收

- [ ] data-eng-bench 提交满足 CI：103 题 × ≥3、无 override、每个 trial 有 ATIF 轨迹
- [ ] DAB PR 附轨迹，标 Tuned prompt = 否
- [ ] 报告：基线 vs 全开（CI）、每层差值、失败类型分布、成本

## 参考

`docs/research/2026-09-21-harbor-signalpilot-dab.md` Bottom line

## Comments
