# 104 — 运行级 effort 选项

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md（"effort（104）"）
Claimed by: —
Branch: —

## Goal

`data-agent run` 和 `evals/dab.py` 能用 `--effort low|high|max` 设模型的推理强度，值进运行记录；不给时行为和现在完全一样。

## Authority

- May edit: `src/data_agent/agent.py`（`build_options` 和运行入口的参数）、`src/data_agent/cli.py`、`evals/dab.py`（参数、运行条件、续跑检查）、`tests/`（新测试）
- Must not edit: `skills/`、提示词、`src/data_agent/classify.py`、`tests/acceptance/`、`src/data_agent/web/`、`web/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实请求核实和对比运行由主会话做）
- Spend cap: $0

## Context

- `build_options`（`src/data_agent/agent.py:1609` 起）组装 `ClaudeAgentOptions`；SDK 0.2.168 的 `ClaudeAgentOptions` 有 `effort`、`thinking`、`max_thinking_tokens` 三个字段。
- DeepSeek Anthropic 格式（https://api-docs.deepseek.com/guides/anthropic_api 、/guides/thinking_mode）：思考默认开、默认 effort `high`；effort 走 `output_config.effort`，取值 `low/high/max`；`budget_tokens` 被忽略。
- DAB 在进程内调 `build_options`（`evals/dab.py:437`）；运行条件写在 `evals/dab.py:654` 附近，续跑按条件判断（`:625` 的说明）。
- `--network`（工单 98）是现成的运行级选项先例：CLI 参数 → 运行入口 → `build_options` → `version` 事件。
- `evals/board.py:144-145` 写着 data-agent 不设 effort；这张不改 board。

## Acceptance

- [ ] 不给 `--effort`：`ClaudeAgentOptions` 和现在逐字段一致（测试）。
- [ ] 给 `--effort max`：`options.effort == "max"`；`version` 事件带 `effort`（测试）。
- [ ] DAB：`--effort` 进运行条件；同一 `--out` 下 effort 不同的续跑被拒绝（测试）。
- [ ] 非法取值在参数解析时报错（测试）。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出。最终回复里写好主会话核实用的命令：本地跑一道查数题（`tests/fixtures/shop.sql` 建的 DuckDB）带 `--effort max`，以及确认请求体里真的带上 `output_config.effort` 的办法（例如 CLI 调试日志或本地 HTTP 代理）。

## Stop conditions

- SDK 的 `effort` 字段在这个版本发不出 `output_config.effort`，又没有不改 SDK 的办法（例如只能用 `extra_args` 之类的未公开通道）：停下交回，写明查到的事实。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —

## Comments
