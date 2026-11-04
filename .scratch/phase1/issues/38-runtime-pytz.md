# 38 — 运行时依赖补上 `pytz`：带时区的时间戳列能查

Status: needs-triage
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

容器里装的 agent 能用 `query_database` 读出 TIMESTAMPTZ 列，不再报 "Required module 'pytz' failed to import"。修法是补上缺失的运行时依赖，并加一个测试，防止以后又出现"本地开发环境有、容器里没有"的依赖。

## 已和用户定下的（2026-11-04）

- 类别：**确定性修复**（ADR-0008 Update 2026-11-04）。验证方式是单元测试（其中一个修复前会失败），加一次单题真实运行；不做初筛，对分数的影响到统一评估里看。
- 修根因：把 `pytz` 加进 `pyproject.toml` 的运行时 `dependencies`。**不要**在网关里把 TIMESTAMPTZ 转成字符串来绕开报错。

## Authority

- May edit：`pyproject.toml`（只改 `[project] dependencies`）、`uv.lock`（只能是 `uv lock` 带出的变化）、`tests/`（`tests/acceptance/` 除外）
- Must not edit：`src/`、`skills/`、`evals/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none（服务器上的真实运行由主会话做）
- Spend cap：$0.2（单题真实运行）

## Context

- 现象：accept-2 里 `query_database` 报了 38 次 "Invalid Input Error: Required module 'pytz' failed to import"，其中 pos-operations 占 26 次。
- 根因（已核实）：
  - DuckDB 的 Python 客户端把 TIMESTAMPTZ 转成 Python 值时需要 `pytz`。
  - 我们只通过 dev 组的 dbt（`dbt-adapters` 依赖 `pytz`，`uv.lock:675-685`）间接装上了 `pytz`，所以本地测试一直能过。
  - Harbor 安装 agent 时用的是 `uv export --frozen --no-dev`（`evals/harbor_agent.py:150-156`），容器里就没有 `pytz`。
- 版本：`pytz` 不需要钉死版本，写下限即可，和 `uv.lock` 里已有的版本兼容。

## Acceptance

- [ ] 测试 A（修复前会失败）：`uv export --frozen --no-dev --no-emit-project` 的输出里包含 `pytz`。放在 `tests/test_packaging.py` 或新文件里。
- [ ] 测试 B：`DuckDBGateway` 对一张含 TIMESTAMPTZ 列的表执行 `query_database` 能返回行（DuckDB 夹具）。
- [ ] 顺带检查一遍：`src/data_agent/` 运行时 import 到的第三方包，是否都在 no-dev 导出里。如果还有别的包只靠 dev 组带进来，在 Comments 里列出来，**不要**顺手加进依赖，交给主会话决定。
- [ ] `tests/acceptance/` 原样全部通过。
- [ ] `make check` 通过。
- [ ] （主会话）服务器真实运行：pos-operations × 1（full），贴出轨迹里不再出现 `pytz` 报错的证据。

## Evidence required

测试 A 修复前失败、修复后通过的输出；`make check` 输出；`git diff uv.lock --stat`。

## Stop conditions

- `uv lock` 带出 `pytz` 以外的版本变化。
- 修复需要改 `src/` 或 `evals/harbor_agent.py`。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
