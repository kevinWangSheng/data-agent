# 26 — scrub 把题目数据里的 `/home/<x>` 当成主机路径

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`evals scrub` 不再因为轨迹里出现题目数据本身的 `/home/<x>`（例如网页 URL 的路径）而判"检查不完"；真正的主机 home 路径仍然被拦。修好后 `ablate` 能在 dev-3 这样的 job 上出报告。

## Authority

- May edit: `evals/scrub.py`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试、`evals/ablate.py`、`scripts/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- 服务器上 dev-3 两次 `ablate` 都以 exit 2 结束："scrub could not check every file of the baseline job, so no report"。主会话 2026-10-21 单跑 `evals scrub runs/dev-3/jobs/baseline` 的输出：
  - `.../dbt-cart-abandonment-recovery__ixzCvZ3/agent/events/run-20261021T194022Z-dce21b13.jsonl:53: /home/193`
  - `.../agent/trajectory.json:448: /home/193`
  - `scrub: changed 0 file(s) of 1006`
- 那一行的原文没看过（trial 在服务器上，`ablate` 运行只能取回汇总）。按形状推测是题目数据里的 URL 路径（如页面路径 `/home/193`），**未确认**；不能确认时按"URL 或 SQL 字符串里的路径"处理，不要靠猜具体内容。
- 规则在 `evals/scrub.py` 的 home 路径检查（模块 docstring 第 10 行起；`/home|Users/<x>` 正则约在第 47–50 行）。scrub 的目的见 plan §7：上榜轨迹公开，不能漏主机路径、用户名、IP、密钥。
- 放宽规则只能针对"不是本机或跑 job 的机器的 home"的情况；宁可多拦，不能漏拦（例如 `/home/<真实用户名>/...` 仍须被删或报出）。

## Acceptance

- [ ] 一个包含 URL 路径 `/home/193`（以及 JSON 里转义过的同样内容）的假轨迹，scrub 通过且不改动它；配修复前会失败的测试
- [ ] 本机 home、`--home` 给出的 home、以及形如 `/home/<字母用户名>/...` 的未知 home 路径，行为与现在一致（已有测试保持通过，必要时补一条）
- [ ] 真实运行：对本地已有的 Harbor job 副本（`runs/dev-2/out/jobs/full` 复制到新目录）跑 `uv run python -m evals scrub <副本>`，贴输出
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令与输出。

## Stop conditions

发现必须放宽到可能漏掉真实主机路径才能过；需要人决定的事；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
