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
- Review rounds: 3（第 1 轮 Codex + eng:code-review，8703954；第 2 轮 Fable，7da7e94；退回第 1 轮：Codex at capacity，eng:code-review，6c5ab48）
- Model spend: $0（工单会话无模型调用）
- Outcome: in-review，退回第 1 轮已修，branch `worktree-46-harness-hooks`（385acb6、8703954、7da7e94、f63fdb9、6ae95cd、6c5ab48），PR #47；集成测试断言问题交人；服务器运行 t46-real 进行中
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
- 2026-11-05 主会话：PR #47 的 Codex app 替代审查（Fable 5.1，审 7da7e94）。核过没问题的：钩子字段与 SDK 0.2.168 契约一致（PreToolUse `hookSpecificOutput.updatedInput`/`permissionDecision`，SubagentStop `agent_type`/`agent_transcript_path`/`last_assistant_message`，Stop `decision: block` + `reason`）；`summarize_transcript` 对真实 transcript 的轮数去重正确；NEEDS_INFO 不拦、按模型保留状态、第三次放行并记 `ended_with_unresolved`；同一轮两个验证者只有 `verifier` 计数；现有 `verifier.md` 的 `- CHECK n: STATUS - detail` 和 `### Summary` 都能解析。**退回**（第 1 轮）：
  - 退回 1（P2）：`verifier_v2/report.py:16-18` 不再接受 `CHECK n - Title: STATUS`（旧 `CHECK_RE` 的注释记录过真实输出 `**CHECK 2 - Aggregate Cross-Validation: FAIL**`；verifier.md 的标题就是 `### CHECK 1 - Table Existence`，模型抄标题进行很常见）。实测 `- CHECK 1 - Table Existence: PASS - found` 解析成 `{}`，于是全 PASS 的模型按规则 1 拦两次、`overall` 成 NEEDS_INFO。spec 要求的是收紧成行首匹配，不是去掉标题容忍。修法：编号后允许 ` - 标题` 再接 `:`；验收测试里已有的格式不变。
  - 退回 2（P2）：`verifier_v2/stop.py:62-64` 模型在报告后被删或改名时 `hashes.get(model)` 为 None 触发规则 2，而 `record_report`（第 48 行）跳过不在 workspace 的名字，再派单也清不掉，只能等两次拦截用完；`tests/test_verifier_v2_hooks.py:106` 把这个行为锁住了。场景：agent 建了临时模型、验证者点名、agent 删掉。修法：workspace 里不存在的模型从 Stop 状态里去掉（`model_diff` 仍会报它 removed，供验证者看）；改那条测试。
  - 顺手（P3）：指纹在 SubagentStop 时取而不是派单时取（`hooks.py:88`/`stop.py:46`），主 agent `run_in_background` 派验证者后继续改模型，记录的哈希已含改后内容，规则 2 永远不触发；改为 PreToolUse 派单时取哈希。
  - 顺手（P3）：`report.py:24-29` 标题形如 `### Model: customer_ltv`、`### 1. customer_ltv`、别名标题都记不到模型；用 45 的 `project.model_name` 解析并去掉常见前缀。
  - 顺手（P3）：`hooks.py:98,112` `record_report`/`stop_decision` 里的异常没接住（如 `stop.py:35` 读文件时文件被删），SDK 会转成 error 控制响应，CLI 怎么处理未核实，Stop 钩子可能变成静默放行。接住后用现有 `error` 事件记录，Stop 钩子出错时不拦（不把运行卡死），SubagentStop 出错时不改状态。
  - 不退回（P3）：`hit_max_turns` 按 `turns >= 50` 数而 harness 没设上限，47 设 `maxTurns` 时一并处理，已在工单会话的"不修"里。
  - 宏和 schema.yml 改动不进指纹，符合 spec，记已知限制。

### 2026-11-05 工单会话（补充项，commit f63fdb9）

- `evals/process.py` 新增 `verifier_checks(event)`，把 `verifier_end.checks` 归一成 `{model, check, status}` 行：旧列表原样返回；v2 映射按模型顺序、编号升序展开。`trial_metrics` 对归一后的行统计。`evals/atif.py` 的 `extra["checks"]` 走同一函数，两种输入都输出列表形状。
- 测试：`tests/test_eval_process.py`（列表、映射各一例，归一函数的排序与空值）、`tests/test_eval_atif.py` 加映射一例；实现前红、实现后绿。`uv run pytest tests/test_eval_process.py tests/test_eval_atif.py -q`：15 passed。`make check`：1674 passed, 1 skipped, 1 xfailed。
- 之前"不修"清单里 `evals/process.py:52`、`evals/atif.py:150` 一条已解决，`ablate` 收尾在 v2 运行上不再抛错。`atif` 展开成行而不是透传映射，让旧运行和 v2 运行形状一致。未跑审查（补充项，不算退回轮）。

### 2026-11-05 工单会话（退回第 1 轮修复，commits 6ae95cd、6c5ab48）

验收文件未动（`git diff f63fdb9..HEAD -- tests/acceptance` 为空）。`make check`：1690 passed, 1 skipped, 1 xfailed。

- 退回 1：`report.py` 的 `CHECK_RE` 编号后允许 ` - 标题`（`-`、`–`、`—`）再接 `:`，加粗照旧容忍。
- 退回 2：`stop.py` 只对 workspace 里仍存在的模型判规则 2、3；`record_report` 把已不存在的模型从状态里去掉；原锁住旧行为的测试改成 `test_stop_forgets_a_reported_model_deleted_since`。
- 顺手 1：PreToolUse 改写派单时取 `model_hashes` 入队，SubagentStop 取队首。SDK 的两个钩子输入无法互相对应（`tool_use_id` 对 `agent_id`），按派单顺序配对；两个验证者同时在飞时报告可能配到更早的指纹，只会多报规则 2 不会漏报。
- 顺手 2：`report.model_name` 去掉列表编号和 `Model:`/`Table:`/`Relation:` 标签（含加粗），再取首词、去路径/schema/`.sql`；有 `workspace` 时再走 45 的 `project.model_name`。`parse_report` 加可选 `workspace=`。
- 顺手 3：hooks 里取哈希、解析报告、Stop 判定接住 `OSError`/`ValueError`，用现有 `error` 事件记录（`source="verifier_v2"`）。Stop 出错放行；SubagentStop 出错状态不变。
- 测试：`tests/test_verifier_v2_hooks.py` 每项都配（标题 8 例、标签同名模型 3 例、alias/大小写、删除后不拦、派单时指纹、两验证者并行、异常各一）。
- 真实行为（本地脚本）：`### Model: <m>` 节和 `- CHECK 1 - Table Existence: PASS - found` 都解析；派单后报告前改 `customer_ltv.sql` 触发规则 2；删掉模型后 Stop 放行；模型文件不可读时 Stop 放行并记 `error` 事件。
- 审查：`da-review` at capacity；eng:code-review 已修（6c5ab48）：`### **Model:** orders` 解析成空名；模型叫 `table`/`model` 被剥空；并行验证者单一指纹变量漏报（改成排队）；SubagentStop 报告解析两次。
- 不修：检查标题里含冒号或括号形式不解析（verifier.md 没有这种形式）；有 workspace 时恰有模型叫 `summary` 会把 `### Summary` 当成它；派单钩子里 `changed_models` 仍在 try 外（失败时宁可报错也不让原 prompt 直达验证者）；`verifier_end.checks` 带 `Summary` 节，退回前就如此。

### 2026-11-05 主会话

- 退回轮已 push 到 PR #47，等 CI。服务器真实运行 `t46-real`（dbt-receivables-aging-buckets × 1，full）在 6c5ab48 上开跑。Fable 复审退回轮 diff。
- 2026-11-05 主会话：Fable 复审退回轮 diff（7da7e94..6c5ab48）。核过没问题的：全部 CHECK 行格式（含 ` - 标题`、加粗、en/em-dash）与标题归一（`main.orders`、`orders_v2` 不串、`Model:` 标签、列表编号、别名、大小写）；删除/改名/重建的 Stop 行为；`error` 事件形状；`verifier_checks` 的 int/str 键排序。**退回**（第 2 轮，最后一轮）：
  - 退回 A（P2）：`hooks.py:63-68,118` "报告配最早一次派单的指纹"在报告乱序到达时会漏拦：派 D1（alpha=v1）→ 改 alpha 为 v2 → 派 D2 → D2 先回报（不点名 alpha，弹出 h1）→ D1 回报 `### alpha CHECK 1: PASS`（弹出 h2，alpha 记成 v2 的哈希但验的是 v1）→ Stop 放行，v2 从没验过。与注释"只多报不漏报"矛盾；`test_hooks_pair_each_report_with_its_own_dispatch` 只测了顺序到达。修法：在飞的每份报告都取**最早**未结派单的指纹，只有在飞数归零时才清队列（只多报不漏报）；补乱序到达的测试。
  - 顺手（P3）：队列泄漏：改写过的派单若永远没有 SubagentStop（子 agent 出错或撞上限；SDK 是否仍触发 SubagentStop 未核实），它的哈希一直留在队首，之后所有报告都配到这份旧快照，改过的模型会被规则 2 一直拦到 MAX_BLOCKS 用完。给队列加上界或按在飞数清理（与退回 A 的规则一起实现）。
  - 顺手（P3）：`hooks.py:138-141` Stop 钩子把 `ValueError` 也接住并放行；`stop_decision` 路径设计上不抛 ValueError，抛了就是逻辑 bug，现在会静默放行。收窄到 `OSError`。
  - 顺手（P3）：`report.py:20-23` `1. CHECK 1: PASS`（编号列表在前）仍解析不到，与标题已接受列表编号不一致。允许行首列表编号。
