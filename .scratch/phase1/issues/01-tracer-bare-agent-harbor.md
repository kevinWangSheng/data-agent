# 01 — 裸 agent 跑通 Harbor 并测成本

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md

## 做什么

`agent/` CLI（Claude Agent SDK `query()`，只用 SDK 自带的文件/Bash 工具，四个开关全关）+ Harbor `BaseInstalledAgent` 子类。先跑 1 题 data-eng-bench，再跑 `configs/fast-30.txt` k=1。

## 验收

- [ ] `harbor run -d snowflake-labs/data-eng-bench -a <module>:<Class> -m anthropic/claude-sonnet-5-5` 单题跑通，`reward.txt` 产出
- [ ] 每个 trial 有 ATIF `trajectory.json`，`AgentContext` 填了 token 与 `cost_usd`
- [ ] fast-30 k=1 跑完，记录通过率、单题平均 token/成本，按 ×1,119 外推总成本，写进本 issue 的 Comments
- [ ] 据此在 ADR-0001 / ADR-0006 记下模型与金额上限

## 参考

SignalPilot `benchmark/agent/sdk_runner.py:57-64`；Harbor `agents/installed/claude_code.py`；`docs/research/2026-09-21-harbor-signalpilot-dab.md` §1

## Comments
