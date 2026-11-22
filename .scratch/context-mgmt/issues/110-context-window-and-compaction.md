# 110 — 按 1M 窗口跑，并在仓库内打开自动压缩

Status: ready-for-agent
Blocked by: 109（研究笔记随 PR #117 合入 main 后才派：`-w` 从 `origin/main` 建 worktree）
Spec: 无（人 2026-11-22 定，见"已和用户定下的"）
Claimed by: —
Branch: —

## Goal

agent 的会话不再在约 177k token 处被 Claude Code CLI 本地拦下（`terminal_reason: blocking_limit`，答案栏是 "Prompt is too long"），自动压缩在每次运行里都打开，在 367k 处兜底。打不打开压缩只看仓库代码，不看运行所在机器的 Claude 用户设置。

## 已和用户定下的（2026-11-22）

- 修法走仓库内（对齐会话第 1 个主题，人答"可以"）：
  1. `sdk_env()` 加 `CLAUDE_CODE_MAX_CONTEXT_TOKENS=1000000`。
  2. `sdk_env()` 加 `CLAUDE_CODE_AUTO_COMPACT_WINDOW`。
  3. 自动压缩用 SDK 的 `settings` 参数（CLI 的 `--settings`）传 `{"autoCompactEnabled": true}` 打开，**不改服务器的** `/root/.claude/settings.json` 和 `/root/.claude.json`。
- grilling 4 条（人答 yes）：
  - Q1：`CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000`，写纯数字（CLI 2.1.301 不认 `400k`）。
  - Q2：所有运行统一生效：DAB、dbt、对话页面都走 `sdk_env()`/`build_options`，不按路径区分。
  - Q3：验收看行为不看分数，真实运行见 Acceptance。修复之后要不要重跑 DAB 全量或重新提交，不在本工单，另定。
  - Q4：真实运行花费上限 $5。

## Authority

- May edit：`src/data_agent/agent.py`（只限 `sdk_env()` 和 `build_options()` 传给 `ClaudeAgentOptions` 的 `settings`）、新测试文件（如 `tests/test_context_window.py`），以及因为新增 env 而要同步的现有测试里的期望值（不改断言的意图）
- Must not edit：`evals/`、`skills/`、提示词、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`；服务器上的任何文件
- Server access：none。真实运行由主会话做（工单 agent 拿不到模型环境变量）：工单 agent 在最终回复里给出要跑的命令
- Spend cap：$5（主会话的真实运行）

## Context

- 根因和证据：`docs/research/2026-11-22-prompt-too-long.md`（工单 109）。要点：
  - CLI 2.1.301 不认识 `deepseek-flash`，按 200,000 窗口算，估算值到 177,000 就不发请求（笔记 §2.1）。DeepSeek 文档写 1M（§3）。
  - 服务器用户设置有 `autoCompactEnabled: false`，我们用 `setting_sources=["user"]` 读它（`src/data_agent/agent.py:1643`），压缩和被动压缩都不跑（§4）。
  - `CLAUDE_CODE_MAX_CONTEXT_TOKENS` 对不以 `claude-` 开头的未知模型直接生效，不看压缩开关（§5.1）；`CLAUDE_CODE_AUTO_COMPACT_WINDOW` 范围 100,000–1,000,000，压缩在"窗口 − 33,000"处触发（§5.1）。
  - 不用的做法和原因：§5.2（`[1m]` 后缀、`CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT`、`CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE`）。
- 代码位置：
  - `sdk_env()`：`src/data_agent/agent.py:1219-1237`。它的值盖过子进程继承的同名变量（docstring）。
  - `build_options()`：`src/data_agent/agent.py:1521`，`ClaudeAgentOptions(...)` 在 `:1621-1655`。对话页面的会话另设 `CLAUDE_CONFIG_DIR`（`:1619`），`--settings` 对它同样生效。DAB 走同一个 `build_options`（`evals/dab.py:439-440`）。
  - SDK：`settings: str | None`，不带 `sandbox` 时原样交给 `--settings`（`claude_agent_sdk/types.py:2460-2464`，`_internal/transport/subprocess_cli.py:660`）。`build_options` 不传 SDK 的 `sandbox`。
  - 测试接缝先例：`tests/test_effort_option.py:24-35`（`options()` 建选项，`cli_args()` 取 SDK 生成的 CLI argv）。
- **未确认，要本工单证实**：`--settings` 这一层的优先级高于用户设置，CLI 读 `autoCompactEnabled` 的 `ks()`（笔记 §4(c)，偏移 199160294）会查到它。先查 CLI 源码确认；源码看不清的，靠下面的压缩探针定。
- 已知风险（写进 Comments，不因此停下）：dbt 的 trial 从没撞线，容器里的 Claude 设置和以前是否压缩过不可查；改完后 dbt 的压缩点是 367k。压缩用 `deepseek-flash` 写摘要，可能丢任务细节（笔记 §5.3）。

## Acceptance

- [ ] 单元测试（在 `build_options` 接缝上）：选项的 `env` 里 `CLAUDE_CODE_MAX_CONTEXT_TOKENS == "1000000"`、`CLAUDE_CODE_AUTO_COMPACT_WINDOW == "400000"`；`settings` 解析出来是 `{"autoCompactEnabled": true}`，SDK 生成的 argv 里有 `--settings` 和这个值；`turn` 不为空（对话页面）时同样成立。
- [ ] 单元测试：shell 里导出的 `CLAUDE_CODE_MAX_CONTEXT_TOKENS`、`CLAUDE_CODE_AUTO_COMPACT_WINDOW`、`DISABLE_AUTO_COMPACT`、`DISABLE_COMPACT` 不能改变上面的结果（前两个由 `sdk_env` 覆盖；后两个会让压缩关掉，`sdk_env` 要把它们设成空值或别的不生效的值，以 CLI 源码对这两个变量的判断为准，写明依据）。
- [ ] `make check` 通过。
- [ ] 真实运行（主会话在服务器上跑，no-context，同一个提交）：
  - 目标题：DAB agnews query3、stockmarket query2 各 5 次。0 次 `blocking_limit`；CLI transcript 里至少一次单次请求输入超过 177,000 token。没有一次超过 177,000 时，不算过，记"未证实"交回给人。
  - 压缩探针：1 次，把压缩点临时降到 177,000 以下（不改提交的代码。工单 agent 从 CLI 源码找一个 `sdk_env` 不设、子进程能继承的变量来做，例如 `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`，写明它在 2.1.301 里的含义和取值；找不到这种变量就停下交回）。transcript 里出现压缩。没出现：`--settings` 没盖过用户设置，停下交回，不改服务器。
  - dbt 冒烟：开发集 1 题，正常结束（不看对错）。

## Evidence required

`make check` 输出；工单 agent 给出的真实运行命令；主会话跑完后的统计命令和输出（`terminal_reason` 计数、transcript 里单次请求的最大输入 token、压缩次数）、实付花费。

## Stop conditions

- CLI 源码显示 `--settings` 盖不过用户设置的 `autoCompactEnabled`：停下交回，写明源码位置。
- 找不到能做压缩探针的继承变量：停下交回。
- 修复要改 `evals/` 或提示词：停下交回。
- 修复最多 2 轮，还不过就停下。

## Failure handling

Status 改 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-22（对齐会话）：范围和 4 条 grilling 由人确认（见"已和用户定下的"）。压缩探针的做法从"临时把窗口压到 100000"细化成"用继承变量把压缩点降到 177k 以下"：`sdk_env` 的值盖过继承变量，不改代码就压不低窗口；目的不变（证明 `--settings` 打开了压缩）。派工（109 合入后）：
  `claude -p -w 110-context-window-and-compaction --model opus --permission-mode auto "/implement /Users/shenghuikevin/dev/AI/data-agent/.scratch/context-mgmt/issues/110-context-window-and-compaction.md"`
