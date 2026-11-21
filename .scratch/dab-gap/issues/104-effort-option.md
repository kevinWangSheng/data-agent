# 104 — 运行级 effort 选项

Status: done
Blocked by: —
Spec: ../spec.md（"effort（104）"）
Claimed by: claude (worktree 104-effort-option)
Branch: worktree-104-effort-option

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: done (needs a human decision on the no-flag default)

## Comments

**Evidence**
- `make check` (before and after the fixes): ruff passed, 348 files formatted, basedpyright 0 errors, pytest 2559 passed and 2 skipped, web 320 passed.
- New tests: `tests/test_effort_option.py` (8) and the end of `tests/test_dab.py` (12).
- Request-body check at no cost: I ran the real `data-agent run` (bundled CLI 2.1.301) against a local HTTP server that logs request bodies, on a DuckDB file built from `tests/fixtures/shop.sql`:
  ```
  DATA_AGENT_API_KEY=sk-local-fake DATA_AGENT_BASE_URL=http://127.0.0.1:18904 \
    uv run data-agent run --type query --workspace /tmp/104-capture/run/ws \
    --duckdb /tmp/104-capture/run/shop.duckdb --config no-context \
    --events-dir /tmp/104-capture/run/events --task "How many customers are there?" --effort max
  ```
  - With `--effort max`: the body had `output_config: {"effort": "max"}` and the `version` event had `"effort": "max"`.
  - With no flag: the only difference in the body was `output_config.effort`, which was `"xhigh"` (from user settings), or `"high"` with an empty config directory.
  - With `CLAUDE_CODE_EFFORT_LEVEL=low` set: `"low"` was sent before the fix and `"max"` after.
  - Every run sent `thinking: {"type":"adaptive"}`.
  - The server script is `/tmp/104-capture/server.py <port> <log.jsonl>`; it answers a single streamed "3".

**Reviews**
- The Fable reviewer (standing in for Codex) found nothing blocking.
- Fixed in `fbc4ad8`: the `CLAUDE_CODE_EFFORT_LEVEL` override, wrong comments and help text that said "endpoint default", `EFFORTS` now comes from `get_args(Effort)`, `effort=` is passed by keyword in `evals/dab.py`, and the DAB rerun test now checks that the error names the effort.
- `eng:code-review` Spec: no missing requirements and no scope creep. It also raised the default issue above, which is a spec decision, not something to fix here.
- Not fixed:
  - The repeated `**({} if effort is None else {...})` line in the two version headers: two lines, a helper isn't worth it.
  - Tests that use SDK private methods: it's the only way to see the CLI arguments, and `test_network_option` does the same.
  - The DeepSeek-specific test names: the allowed values really are DeepSeek's.
  - Leaving the key out when no effort is given: the spec says to change nothing then.

**Known limitations**
- Verifier subagents set no effort of their own, so whether they inherit the session's is 未确认; the main session's request check should include a dbt run with verifiers.
- `evals/board.py:144-145` still says data-agent sets no effort; that file is outside this ticket's scope.

**For the main session to check against the real endpoint:** run the same command with the real `DATA_AGENT_*` variables and `--effort max`, and check that the run finishes normally. The CLI has a fallback (the string "rejected output_config.effort; latching unsupported and retrying without it" is in the binary): if DeepSeek rejects the field, the CLI silently retries without it. I haven't confirmed where that message gets printed, so check the run's stderr or the CLI debug output for it. To see exactly what is sent, the local-server method above works.

**主会话真实运行（2026-11-21，fbc4ad8，真 DeepSeek 端点）**：`uv run data-agent run --type query --duckdb <shop.sql 建的 DuckDB> --config no-context --task "How many customers are there?" --effort max` → exit 0，答 "There are 12 customers."；`version` 事件 `"effort": "max"`；stderr 没有 "rejected output_config.effort" 回退信息（该信息打印位置未确认）。

**待人定（不挡合并）**：不给 `--effort` 时 CLI 会发自己的 effort（本机 `~/.claude/settings.json` 的 `xhigh`，空配置为 `high`），不是端点默认。spec 验证第 1 步的"默认"组实际是服务器上 CLI 的这个值，跑前在服务器核实。

**合入（2026-11-21，主会话）**：PR #113 → 2bc3f0e，CI 绿，Codex GitHub app 无意见。服务器上 `~/.claude/settings.json` 没有 `effortLevel`、`CLAUDE_CODE_EFFORT_LEVEL` 未设，所以服务器上不给 `--effort` 时 CLI 发 `high`（与端点默认一致），spec 验证第 1 步实际是 `high` 对 `max`。

**spec 验证第 1 步：effort 对比（2026-11-21，主会话，服务器 2bc3f0e，no-context）**
- 跑法：`runs/e104/par.py`（照 `runs/f18b/par.py`），运行名 `e104-<default|max>-<dataset>-<f|p>`，38 个运行全部 exit 0；合并在服务器 `runs/e104-merged-<effort>`，算分 `runs/e104-score`（`dab-score --strict`，本机有 `run.log`）。
- 11-15 重跑里不到 5/5 的 20 道题 × 3：default（服务器上 CLI 发 `high`）**31/60**，`max` **24/60**。
- 原来 5/5 的 10 道题 × 1：两组都 10/10。
- 花费：default $2.84，`max` $4.68（1.65 倍）；没做完的 trial：default 3 个，`max` 6 个。
- 30 题宏平均 Pass@1：default 0.7384，`max` 0.7009（−3.75 分，95% 区间 −8.33 到 +0.83，不显著）。`max` 掉得多的：agnews 6/12 → 3/12，crmarenapro 7/12 → 4/12，stockmarket 6/9 → 4/9。
- **结论**：留下 `max` 的第一条（20 道题上通过次数比默认多）不成立。照 spec，DAB 重跑用默认 effort，`--effort` 不给。
