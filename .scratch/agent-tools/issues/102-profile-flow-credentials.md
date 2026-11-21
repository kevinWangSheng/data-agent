# 102 — 修复 Docker 工作区对紧凑 dbt profile 的凭据漏检

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: Codex 子代理 `profile_guard`
Branch: worktree-102-profile-flow-credentials

## Goal

Docker 工作区预检能识别嵌在块式 YAML 中的紧凑行内映射里的明文凭据，或在无法可靠解析该配置时拒绝挂载。无凭据的常规 dbt profile 继续正常使用。

## Authority

- May edit: `src/data_agent/workspace_guard.py`、`tests/test_workspace_guard.py`、`tests/test_sandbox_cli.py`。若修复落在共用 YAML 解析器，可改 `src/data_agent/verifier_v2/yaml_lite.py` 和其现有测试，并验证其余调用方兼容。
- Must not edit: 已有验收测试、工具快照、依赖及锁文件、其他工单、评测配置、`.claude/`。不 push、不合并。
- Server access: none
- Spend cap: $0

## Context

- [PR #109 的新 P1](https://github.com/kevinWangSheng/data-agent/pull/109#discussion_r4647906246)，工单 100 已确认漏检。独立分支以该 PR 当前头 `d51f863` 为基线，因为触发问题的凭据检查尚未合入 `main`。本工单完成是 PR #109 合入的前置条件。
- 沿用工单 100 的测试接缝，Docker CLI 的工作区预检。使用临时工作区和测试占位值，检查退出码和启动前拒绝，禁止触发模型。

## Acceptance

- [x] 用临时 dbt profile 复现紧凑行内映射下的漏检，测试在基线失败，在修复后通过。
- [x] Docker CLI 在启动 SDK 会话和创建事件目录前以退出码 2 拒绝该工作区。报错只包含文件名。
- [x] 现有安全 profile、环境变量引用、JSON 和普通 YAML 的覆盖继续通过。已有保守拒绝行为照旧。
- [x] `make check` 通过，提交只包含本工单的代码和测试。

## Evidence required

修复前失败、修复后通过的命令与输出，CLI 预检证据，`make check` 输出，修复提交。

## Stop conditions

需要新增依赖、修改已有验收标准，或触及本工单外的行为时停下交回主会话。

## Failure handling

报告分支和阻塞，主会话记入工单。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0 (data-agent model calls only)
- Outcome: 已随 PR #109 squash 合入 `main`（`4c67b5e`）；CI 绿、Codex GitHub app 审查零问题。
- Human review minutes: —

## Comments

- 2026-11-20（`profile_guard` 子代理）：先运行 `uv run pytest -q tests/test_sandbox_cli.py -k compact_profile_mapping`。在 `d51f863` 上 SDK 哨兵被触发，测试失败。修复后 1 passed。修复只调整了现有 YAML 解析器对带引号行内键的分隔符判断，并用测试保留普通键中冒号的行为。
- 实际运行：用临时 DuckDB 和测试 profile 调用 `data-agent run … --sandbox docker`，退出码 2，诊断指向 `profiles.yml`，没有创建事件目录，诊断中没有测试值。日志位于工单 worktree 的 `runs/102/cli-preflight.log`。
- `make check` 退出 0。Ruff 和类型检查通过，Python 2532 passed、2 skipped，前端 295 passed。日志位于工单 worktree 的 `runs/102/make-check.log`。
- `da-review d51f863` 无可操作问题。独立 Standards 和 Spec 审查各报告 0 项问题，Spec 复跑两项新增测试均通过。
