# 46 — harness 钩子：派单改写、Stop 拦截、轮数记录

Status: ready-for-agent
Blocked by: 45
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
