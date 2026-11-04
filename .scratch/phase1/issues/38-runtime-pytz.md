# 38 — 运行时依赖补上 `pytz`：带时区的时间戳列能查

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 38-runtime-pytz)
Branch: worktree-38-runtime-pytz

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (no model calls in this ticket; data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: done（PR #37）；服务器 pos-operations full 1/1，无 pytz 报错
- Human review minutes: —

## Comments

- 2026-11-04 工单会话（worktree 38-runtime-pytz）：
  - 改动：`pyproject.toml` 加 `pytz>=2025.2`；`uv.lock` 只多两行（`uv lock` "Resolved 150 packages"，没有别的版本变化，停止条件不成立）；`tests/test_packaging.py` 测试 A；`tests/test_gateway.py` 测试 B。
  - 测试 A 修复前（`uv run pytest tests/test_packaging.py::test_runtime_requirements_include_pytz tests/test_gateway.py -q`）：`AssertionError: assert 'pytz' in {...}`，1 failed, 2 passed；修复后 3 passed in 1.71s。
  - `make check`：ruff / format / basedpyright 全过；1447 passed, 1 skipped, 36 deselected in 405.96s。`tests/acceptance`：39 passed, 15 deselected。
  - 顺带检查：`src/data_agent/` 运行时 import 的第三方包只有 claude-agent-sdk、duckdb、mcp、psycopg、sqlglot，都在 no-dev 导出里；`pytz` 是唯一靠 dev 组带进来的，不需要主会话决定。
  - 审查第 1 轮：`da-review main`（Codex）无可操作缺陷。`eng:code-review main` Standards：无硬性违规，修了 `tests/test_gateway.py` 模块 docstring（a9289ae）；不修：`test_packaging.py` 两处 `uv` 子进程重复（两条不同子命令，抽 helper 过度）、`pytz` 只写下限（工单要求）、按 `==` 解析导出（标准 requirements 格式）。Spec：无阻塞；测试 B 调的是 `DuckDBGateway.query` 而非 MCP 工具，不改，因为工单点名 `DuckDBGateway` 且 pytz 报错发生在网关取数处；测试 B 在 main 上本地也能过（dbt 带来 pytz），守卫靠测试 A。
- 2026-11-04 主会话：服务器真实运行 `t38-real`（commit a9289ae；`ablate --split dev --config full --task pos-operations --trials 1 --budget 2`）：pos-operations reward 1（1/1），冒烟 cohort-retention-matrix reward 0；实际价 $0.2759（cost_usd $0.5519，全在闲时）；内存峰值 2168 MB。轨迹核对：`agent/setup/install.log:35` 有 `+ pytz==2026.5`；整个 trial 里 "pytz" 只出现这一处，"Invalid Input Error" 0 次；查询结果里有带时区的时间戳值（如 `2026-11-04T19:29:15.176383+00:00`）。PR #37 CI 绿、Codex app 无意见，squash 合入。
