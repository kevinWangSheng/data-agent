# 109 — 诊断：trial 为什么撞上 "Prompt is too long"，压缩为什么从没触发

Status: done
Blocked by: —
Spec: 无（人 2026-11-22 定范围，见"已和用户定下的"）
Claimed by: Claude Code (fable)，2026-11-22
Branch: worktree-109-diagnose-prompt-too-long

执行模型：fable（根因不明的诊断，`~/.claude/rules/model-routing.md`）。派工命令见 Comments。

## Goal

一份研究笔记，用证据回答下面 4 个问题，并给出一个具体的修复方案：用哪些 SDK/CLI 变量、设成多少、预期效果和代价。**只诊断，不修**，不改 `src/`、`evals/`、`skills/`。

## 已和用户定下的（2026-11-22）

- 第一步只诊断：只读、不花模型钱，读服务器上已有的运行产出和 CLI 源码。
- 上下文管理用第三方 SDK，不自己造（`HANDOFF.md:407`，已锁定）。所以修复方案要先在 Claude Code CLI 和 Agent SDK 已有的设置里找，不设计自己的压缩或截断。
- 修复本身、用哪批题验收、过线标准、花费，等这份笔记出来后再和人对齐，本工单不定。
- 如果查明窗口确实是 1M、拒绝是接口偶发错误，结论就写"不是上下文管理问题"，并写清楚接下来该查什么。

## Authority

- May edit：`docs/research/2026-11-xx-prompt-too-long.md`（新建，日期用实际日期）；scratchpad 里的统计脚本（不提交）
- Must not edit：`src/`、`evals/`、`skills/`、`tests/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：`scripts/da-remote exec -- <只读命令>`、`scripts/da-remote pull <name>`。要用仓库里的 `scripts/da-remote`：`PATH` 里的 `~/.local/bin/da-remote` 是 2026-10-20 的旧版本，没有 `exec`（对齐会话 2026-11-22 核实）。（只拉需要的运行；拉到本 worktree 的 `runs/`，不提交）。不 `start`，不在服务器上写文件。
- Spend cap：$0（不调模型；问题 3 靠文档和源码查不清就停下交回，不实测）

## Context

- 已知现象（对齐会话 2026-11-22 查的）：
  - 例子：`runs/f19c-dab/pkg/traces/agnews/query3/run1/events.jsonl`。第 42 轮：`error` 事件 `source: model, error: invalid_request`，接着 `text` 是 "Prompt is too long"，`result` 是 `terminal_reason: blocking_limit`、`is_error: true`。整个 trial 累计 `cached_input_tokens` 882,560、`uncached_input_tokens` 68,270、`output_tokens` 115,986。
  - 本机有事件文件的运行（都是 DAB）：`blocking_limit` 在 `f19-dab` 有 1 次，`f19b-dab` 3 次，`f19c-dab` 5 次；`dabtrial-no-context`、`dabtrial2-no-context`、`t61-krama`、`v18u-no-context-agnews` 各 1 次。所有运行里没有一次压缩事件。统计脚本思路：遍历 `events.jsonl`，按顶层目录数 `result.terminal_reason`，以及 `text` 里的 "Prompt is too long"。
  - ADR-0008 里的相关记录：`docs/adr/0008-change-acceptance.md:245`（第 18 项：5 个撞上限，baseline 4、no-context 1）、`:266`（有一次运行器把撞上限记成了 finished，答案栏是报错文字）、`:312`；ADR-0005 `:37`（100 轮实验里 agnews 第 4 题 2 次撞上限）。
- 我们的配置：`src/data_agent/agent.py:1222-1237`（`sdk_env`）里没有任何压缩或上下文窗口的变量；评测不设 `CLAUDE_CONFIG_DIR`（`agent.py:1539`、`:1619`），所以 CLI transcript（如果有）在服务器账户默认的 Claude 配置目录里。版本：`sdk_version` 0.2.168，`cli_version` 2.1.301（事件里的 `version`）。
- 官方文档（对齐会话用 web-fetch 读的，部分是摘要，**要重新核对原文**）：
  - https://code.claude.com/docs/en/model-config：不认识的模型名按 1M 上下文算（摘要）；`CLAUDE_CODE_MAX_CONTEXT_TOKENS` 可以改 CLI 假定的窗口。
  - https://code.claude.com/docs/en/env-vars：`CLAUDE_CODE_AUTO_COMPACT_WINDOW`、`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`、`CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT`、`DISABLE_AUTO_COMPACT`、`DISABLE_COMPACT`。
  - https://api-docs.deepseek.com/quick_start/pricing：上下文长度只有一行 "1M"，看不出是否适用于 `deepseek-flash`。https://api-docs.deepseek.com/guides/anthropic_api 没有写上下文上限和 prompt-too-long 错误。
- 对不上的地方（推断，未确认）：42 轮累计输入约 95 万 token，按线性增长估算，最后一次请求只有几万 token，离 1M 很远，却被拒成"太长"。
- 打包的 CLI 是 venv 里 `claude_agent_sdk` 包中压缩过的 JS，可以用 `grep -o` 查（先例：工单 43，`docs/research/2026-11-04-serial-mcp-calls.md`）。

## 要回答的 4 个问题

1. **dbt 那边有没有**：服务器上开发集、留出集、data-eng-bench（`f17`）的全部事件文件里，`blocking_limit` 或 "Prompt is too long" 各多少次，按运行和配置统计。**留出集只报汇总次数，不看、不写逐题细节**（ADR-0004）。
2. **失败时的请求多大、谁拒的**：还原几次失败里最后一次请求的 token 数。优先用 CLI transcript 里逐次调用的 usage；没有 transcript 就从事件估算，并写明是估算。判断 "Prompt is too long" 是 DeepSeek 接口返回的，还是 CLI 按自己的窗口先拦下的，证据用 CLI 源码 `file`/偏移量加上事件里的字段。
3. **`deepseek-flash` 的真实窗口**：官方文档原文。文档和源码都查不清就停下交回，不花钱实测。
4. **为什么从没压缩**：对照 CLI 2.1.301 对不认识的模型名怎么算窗口和压缩阈值（源码和文档），说明我们的 trial 为什么没走到压缩。

## Acceptance

- [ ] 研究笔记逐个回答 4 个问题：每条结论带证据（命令和输出、源码位置、文档原文引用和 URL），区分"已核实"和"推断"。
- [ ] 修复方案：具体变量和取值，只改设置不改代码时改哪里（`sdk_env` 的行号），预期会怎样，有什么风险（例如压缩后丢掉任务细节；对 dbt 任务的影响），什么证据会推翻它。只是草稿，不提交到 `src/`。
- [ ] 留出集只有汇总数字。
- [ ] 笔记里没有密钥、服务器 IP、连接串。

## Evidence required

统计脚本的命令和输出（贴进笔记或最终回复），用到的源码位置和文档 URL。

## Stop conditions

- 问题 3 只能靠真实调用模型才能定：停下交回，写清楚要怎么测、估计花多少。
- `scripts/da-remote` 在 worktree 会话里被拒绝：停下交回，写清楚需要主会话代跑哪些只读命令。
- 发现运行产出里没有足够的数据（例如没有 transcript、事件也估不出请求大小）：写明缺什么，按已有证据给出结论，不为补数据去跑评测。

## Failure handling

Status 改 `ready-for-human`，Comment 写明查到哪一步、卡在哪里、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted。诊断完成，根因确认（CLI 按 200k 窗口本地拦截 + 服务器用户设置关了自动压缩），修复方案草稿待对齐
- Human review minutes: —

## Comments

- 2026-11-22（对齐会话）：范围由人确认。派工：
  `claude -p -w 109-diagnose-prompt-too-long --model fable --permission-mode auto "/implement /Users/shenghuikevin/dev/AI/data-agent/.scratch/context-mgmt/issues/109-diagnose-prompt-too-long.md"`
  这是只交研究笔记的诊断工单：照 AGENTS.md 工作流程第 4–6 步提交笔记、跑两道审查（只审笔记的证据和结论是否对得上），第 3 步的"真实运行"换成问题 1 的统计命令和输出。
- 2026-11-22（工单 agent，主会话抄录）：笔记 `docs/research/2026-11-22-prompt-too-long.md`，提交 `6a78a31`、`b555cd0`（未 push）。认领时写主 checkout 被会话隔离拦下，由主会话补写认领字段。
  - 第 3 步（问题 1 统计，只读）：`scripts/da-remote exec -- python3 -c "$(cat count_events.py)" runs`：
    ```text
    dab   files=2830  results=2830  blocking_limit=46  prompt_too_long_reason=0  ptl_text=46
    dbt   files=2042  results=2036  blocking_limit=0   prompt_too_long_reason=0  ptl_text=0
    dab {'completed': 2742, 'blocking_limit': 46, 'max_turns': 27, 'None': 9, 'budget_exhausted': 6}
    dbt {'completed': 1794, 'api_error': 240, 'max_turns': 1, 'deadline': 1}
    ```
  - transcript 统计：`sessions=2004 with_compaction=0 with_prompt_too_long=38`，单次请求最大 180,212 输入 token。脚本在 agent scratchpad，未提交。
  - `da-review main`：无可操作问题。`eng:code-review main`：Standards 无违规；Spec 提的 9 条表述问题在 `b555cd0` 全修（46/37 口径、估算方向、dbt 按运行拆分、DeepSeek 报错识别标未确认）。修完未再审一轮。只改 Markdown，未跑 `make check`，pre-commit 全过。
  - 未查清：服务器 `autoCompactEnabled: false` 是谁在 2026-10-20 写的；dbt 容器内的 Claude 设置；180k–1M 之间 DeepSeek 是否都接受。
  - 范围外发现：dbt 事件里 240 次 `api_error`，未查。
- 2026-11-22（主会话）：PR #117 CI 绿；Codex GitHub app 审查 `b555cd0`，无意见（`has_any_findings: false`）。已 squash 合并。修复另开工单 110。
- 2026-11-23（主会话）：范围外发现"dbt 事件里 240 次 `api_error`"已查（服务器只读，按 `runs/**/agent/events/*.jsonl` 的 result 事件统计）：240 次全部出自 10-22 的 `runs/dev-3`（该运行共 450 个 trial），`api_error_status` 全是 402，报错文字 "API Error: 402 Insufficient Balance"，即那次运行中途模型余额用完。之后的运行里 0 次。不是 bug，不开工单。
