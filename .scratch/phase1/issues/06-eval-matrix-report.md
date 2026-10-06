# 06 — 评测矩阵与报告

Status: ready-for-agent
Blocked by: 02
Spec: ../spec.md

## 做什么

留出集抽样脚本（按 `task.toml` difficulty 分层，种子进 git）；6 配置 × 30 题 × k 的批量运行脚本；按题配对 bootstrap 95% CI 的报告（通过率 + token 成本）。

## 验收

- [ ] 抽样可复现（同种子同结果的测试）
- [ ] 用 01/02 的结果生成一份示例报告
- [ ] 留出集失败细节默认不打印（防偷看）

## 参考

ADR-0004、ADR-0006

## Comments
