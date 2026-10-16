# 09 — Harbor 适配器

Status: ready-for-agent
Blocked by: 04
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

data-eng-bench 的题能经 Harbor 用我们的 agent 跑，结果与 token、花费回填进 Harbor 结果；顺带用 5 题真实运行测出单次成本。

## Authority

- May edit: `evals/`（Harbor 适配器）、`src/data_agent/`（只限适配需要的入口）、`tests/`（除 `tests/acceptance/`）、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（本地 colima；批量留到服务器）
- Spend cap: $5

## Context

plan §5 Harbor 与 dbt 版本、§7 运行时注意事项（报错记 0、只对临时错误重试、关网页搜索、版本头）；ADR-0001（`BaseInstalledAgent` 的 `install()`/`run()`）；上游 `~/dev/AI/data-agent-refs/harbor`、`data-eng-bench/configs/`。

## Acceptance

- [ ] 单题经 Harbor 跑通并出分
- [ ] 网页搜索与抓取关闭
- [ ] 只对模型接口临时错误重试
- [ ] token 与花费回填
- [ ] 上传的轨迹与事件文件不含本机绝对路径和用户名（工单 03 的 workspace guard 拒绝消息会写入绝对路径）
- [ ] workspace guard 拒绝读 workspace 内的 `.env*` 等凭据文件（工单 03 已知限制）
- [ ] 没装 `git` 的环境里也能构建 wheel：`hatch_build.py` 捕获 `OSError` 回落 `unknown`（工单 03 已知限制）
- [ ] 5 题真实运行的单次平均成本写进 Comments，并更新 plan §7 的推算

## Evidence required

Harbor 命令与结果摘要；5 题的成本统计。

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
