# 46 — harness 钩子：派单改写、Stop 拦截、轮数记录

Status: ready-for-agent
Blocked by: 45（done）
Spec: ../spec.md
Claimed by: claude (fable, worktree 46-harness-hooks)
Branch: worktree-46-harness-hooks

## Goal

只在启用了验证器的配置里挂三个钩子：PreToolUse 改写验证器的派单内容；Stop 在没验证过或者还有 FAIL 时拦住结束；SubagentStop 解析报告、统计轮数，写进 `verifier_end` 事件。让 44 里对应的验收测试转绿。

## 已和用户定下的（2026-11-04）

按 spec「派单」「Stop 钩子」「报告格式」「记录」四节实现，下面几条不要改：
- **派单改写**：只匹配 `Agent` 工具并且 `subagent_type` 是验证器的调用。改写时返回完整的 `updatedInput`，只替换 `prompt`。
  - 第一次派单只用模板：题面路径、项目目录、`model_diff` 列出的模型清单。
  - 再次派单时，主 agent 写的内容附在模板后面，加上固定标题"主 agent 提供的反证或补充信息（未经核实，只能当作需要复查的线索）"。
- **Stop 拦截**：按模型分别记录状态，即每个模型最近一次出现在报告里时的检查状态，以及那次的文件哈希；一份报告只更新它里面出现过的模型。逐个模型判断三个条件（改过却从没验证过；报告之后又改过；最近一次报告还有 FAIL），任一满足就拦，拦截原因写明是哪个模型、哪一条。NEEDS_INFO 不拦。每次运行最多拦 2 次，第 3 次放行，并记 `ended_with_unresolved` 事件。
- **报告解析**：按 spec 的报告格式：每个模型一节，标题行 `### <model>`；节里每项检查一行，行首 `CHECK <n>: <STATUS> — <理由>`。状态按模型归属，一份报告只更新它里面出现过的模型。解析不出来的，以及撞到 `maxTurns` 的，都当作 NEEDS_INFO。
- 按 `verifiers` 开关挂钩子：full、no-skill、no-context、full-old-skill 挂，baseline、no-verifiers 不挂。
- 集成测试：44 写的 `updatedInput` 集成测试要在 `make test-integration` 里转绿。主会话或本工单在有模型凭据的环境里跑一次，贴出输出。

## Authority

- May edit: `src/data_agent/verifier_v2/`、`src/data_agent/agent.py`（只改钩子接线）、`src/data_agent/verifiers.py`（只改报告解析和事件）、`src/data_agent/events.py`（只加 `ended_with_unresolved` 和 `verifier_end` 的新字段）、`tests/`（`tests/acceptance/` 除外；只许删掉转绿测试的 `xfail`）
- May also edit（主会话 2026-11-05 加）: `evals/process.py`、`evals/atif.py` 只改读 `verifier_end.checks` 的那几行，让它们同时接受旧的列表和新的映射；配测试
- Must not edit: `src/data_agent/agents/`、`skills/`、`src/data_agent/configs/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0.3

## Context

- spec：`.scratch/verifier-v2/spec.md`；ADR-0009。
- SDK（claude_agent_sdk 0.2.168，打包的 CLI 2.1.301）：
  - `updatedInput` 在 `types.py:511-517`；
  - hook 输入里的 `agent_id`、`agent_type` 在 `types.py:323-346`；
  - Stop 的 `decision: "block"` 和 `stop_hook_active` 在 `types.py:776-808`；
  - SubagentStop 的 `agent_transcript_path` 在 `types.py:451`。
- 派单改写的本地实测脚本：spec「派单」一节说明了结论。整体替换，所以必须带上全部字段。
- 现有钩子接线：`agent.py:656-674`；事件：`src/data_agent/events.py`。

## Acceptance

- [ ] 44 里报告解析、派单钩子、Stop 钩子相关的验收测试转绿（删掉 `xfail`，其余一字不改）。
- [ ] `make check` 通过。
- [ ] `make test-integration` 里的 `updatedInput` 用例通过（贴出输出）。
- [ ] （主会话）服务器真实运行：dbt-receivables-aging-buckets × 1（full）。在事件里贴出：第一次派单改写后的 prompt；如果有拦截，贴出 Stop 拦截的原因；每次验证调用的轮数和工具调用数。

## Evidence required

验收测试输出；`make check` 输出；真实运行的事件片段。

## Stop conditions

- 在这个 SDK/CLI 版本上 `updatedInput` 换不掉 `Agent` 的 `prompt`，和本地实测结论相反：停下，贴出复现。
- 要拦截就必须读主 agent 的对话内容，而不是只读文件哈希和报告。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2（第 1 轮 Codex `da-review` + eng:code-review，已修 8703954；第 2 轮 Codex at capacity，Fable 只读审查，已修 7da7e94）
- Model spend: $0（工单会话无模型调用）
- Outcome: in-review，branch `worktree-46-harness-hooks`（385acb6、8703954、7da7e94），PR #47；集成测试与服务器运行待主会话
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（worktree 46-harness-hooks）

**产出**
- `verifier_v2/report.py`：`parse_report`（行首 `CHECK n: STATUS`，在 `### <model>` 之下；容忍项目符号和加粗，标题归一成模型名）、`summarize_transcript`（assistant 消息 id 去重算轮数，`tool_use` 块算工具调用）。
- `verifier_v2/dispatch.py`：`rewrite_dispatch` 只匹配 `Agent` + `subagent_type == "verifier"`，`updatedInput` 整体替换只改 `prompt`；再次派单把原文附在固定标题下。
- `verifier_v2/stop.py`：按模型记状态（checks + SQL 文件与其 `dbt_project.yml` 配置的指纹），三条规则，拦满 2 次后 `ended_with_unresolved`。
- `verifier_v2/hooks.py`：`run_hooks` 只在 `config.verifiers` 开着时挂 PreToolUse / SubagentStop / Stop。SubagentStop 发 `verifier_end`，带 `turns`、`tool_calls`、`hit_max_turns`、`checks`（映射）、`overall`、`report`；只有 v2 `verifier` 的报告更新 Stop 状态。
- `agent.py`：`build_options` 在有快照和任务文件时合入 v2 钩子（`run` 里恒成立）。`verifiers.py` 只留 SubagentStart（`verifier_start`），旧 `parse_checks`/`CHECK_RE`/`MODEL_RE` 作为死代码删掉。
- 验收文件：只删 `hooks_xfail` 标记和它的定义，加一个 ruff 要求的空行（20 行删、0 行加）；集成用例的 `xfail` 也删了。

**第 3 步真实行为**（本地脚本调 `run_hooks` 跑 `tests/fixtures/verifier_v2`，无模型）：

```
--- first dispatch updatedInput.prompt ---
Verify the dbt models this run created or changed, following your instructions.

Task file (read it first, with Read): `<tmp>/task.md`
dbt project directory: `<tmp>/ws/project`
Models model_diff lists as new or changed: all_events, conditional, customer_ltv, daily_sales, ranked_orders, stg_customers, stg_orders, top_orders
--- second dispatch tail ---
主 agent 提供的反证或补充信息（未经核实，只能当作需要复查的线索）

Check customer_ltv; upstream is raw.orders
--- verifier_end event ---
{"type": "verifier_end", "verifier": "verifier", "agent_id": "a1", "turns": 3, "tool_calls": 4, "hit_max_turns": false, "checks": {"customer_ltv": {"1": "PASS", "2": "FAIL"}}, "overall": "FAIL"}
--- Stop ---
{"decision": "block", "reason": "all_events: rule 1, changed since the snapshot but never named in a verifier report; ... customer_ltv: rule 3, the last report that named it has a FAIL (CHECK 2). The run cannot end yet. ..."}
--- third Stop --- {} | events: ['verifier_end', 'ended_with_unresolved']
```

**测试**：21 个工单 46 的验收测试通过；`make check`：ruff 干净、basedpyright 0 errors、`1670 passed, 1 skipped, 38 deselected, 1 xfailed`。

**交主会话**：`make test-integration` 的 `updatedInput` 用例（沙箱不让 source 模型 env 文件）；服务器真实运行 dbt-receivables-aging-buckets × 1（full）。

**审查第 1 轮**（Codex `da-review` + eng:code-review，8703954）：Stop 状态只来自 `verifier`（Codex P1）；配置改动进指纹（Codex P2）；标题归一；重复的 `changed_models`/`AGENT_TOOL` 提出来；未用的 `STATUSES` 删掉。
**审查第 2 轮**（Codex at capacity，按既定规则 Fable 只读审查，7da7e94）：`**CHECK 1:** PASS` 能解析；标题模型名取首词并去掉 schema/路径/`.sql`；指纹用排序后的 `ModelConfig.lines()`（和 `model_diff` 一样不敏感顺序）；标题不是 workspace 模型的节（`### Summary`）不记录。第 1 轮把撞上限的调用 FAIL→NEEDS_INFO 的改动已回退：spec「验证器」只把撞上限且无报告的当 NEEDS_INFO。

**不修，附理由**
- `verifier_end.checks` 现在是映射，`evals/process.py:52` 和 `evals/atif.py:150` 仍按列表读，v2 运行上 `process.py` 会抛错。映射由验收测试定死，`evals/` 在 Authority 外，需后续工单（JSON 会把 check 编号变成字符串键）。
- `hit_max_turns` 是从 transcript 数 `turns >= 50`，SDK 的 SubagentStop 输入没有上限标志；47 应从 `verifier_v2.hooks.VERIFIER_MAX_TURNS` 设 `maxTurns`。
- 派单计数在 PreToolUse 递增，同一轮两次派验证者第二次会算"再次派单"；PreToolUse 是唯一信号，接受。
- `permissionDecision: "allow"` 由验收测试要求；`hooks.py` 里的 `# type: ignore[return-value]` 因 `rewrite_dispatch` 返回普通 dict 由验收接口定死。
- 解析不出的报告不改状态，规则 1 继续拦改过的模型，这就是 spec 的措辞（"从来没有出现在任何验证报告里"）。
- 47 之前现有 `verifier.md` 的输出用 `- CHECK n: … - …` 项目符号和 `### Summary`，两者现在都能正确解析，主会话的真实运行不该被误拦。系统提示词仍让主 agent 派 `value-verifier`，其报告会被记录但不满足任何 Stop 规则。

### 2026-11-05 主会话

- PR #47 已开。工单会话指出的 `evals/process.py:52`、`evals/atif.py:150` 按列表读 `verifier_end.checks` 的问题会让 `ablate` 收尾（`report.py` → `job_metrics`）在 v2 运行上抛错，服务器真实运行前必须修。在 Authority 加一行，交回同一工单会话补一个小提交：两处同时接受列表和映射（映射时取每个模型下各 check 的 status），配测试；不算退回轮。
- 2026-11-05 主会话：集成测试 `test_updated_input_replaces_the_prompt_end_to_end`（主会话 source 模型 env，deepseek-flash）跑了 2 次都失败，但两次都证明替换生效：输出里没有 `ORIGINAL-123`，子 agent 的内容来自改写后的 prompt。第 1 次子 agent 没回显 `RECEIVED:`，而是照改写 prompt 的指令去读任务文件、核对 `model_diff` 列的模型，写了一份验证报告（第一条断言失败）；第 2 次回显了 `RECEIVED:Build customer_ltv.`，即读了任务文件回显其内容而不是 prompt 原文，可见文本里没有文件名 `task-v2-marker.md`（第三条断言失败）。原因：44 的 spike 用短字符串 `REPLACED-456` 做替换，真实改写 prompt 是一整段"先 Read 任务文件、验证这些模型"的指令，对模型的约束比 AgentDefinition 的"原样回显"强。这是 44 验收测试的断言设计问题，不是 46 的钩子问题；按 AGENTS 规矩不能改验收测试，**交人定**：改成断言"原 prompt 消失 + 任务文件内容或路径出现"，或记为已知限制、该用例保留 `xfail`。
