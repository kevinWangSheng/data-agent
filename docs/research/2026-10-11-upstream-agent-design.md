# 上游 agent 设计取证：主循环、验证、澄清、Memory、事件流、失败改进

日期：2026-10-11。方法：读源码与官方文档。未核实处标 `未确认`。

仓库与 commit：SP = SignalPilot@f5eee22，SPP = signalpilot-plugin@f7b0c09，BI = BIRD-Interact@451fe2c，nao@5bde830，dash@6d86fca，ktx@49a4ae6。SP 仓库内有三份插件副本：基准镜像装 `benchmark/signalpilot-plugin/`（`benchmark/Dockerfile.dbt-agent:56-58`）；`signalpilot-plugin/` 是线上用的正本（`signalpilot-plugin/SOURCE_OF_TRUTH.md:3-9`）。

## 1. 主循环与 skills

- 基准：一次 `query()`（SP `benchmark/agent/sdk_runner.py:85`），`permission_mode=bypassPermissions`（在容器内）、MCP、thinking 20k；不传 `agents=` / `setting_sources`（`:52-60`）。dbt 系统提示末尾要求"先加载 dbt-workflow skill"（`prompts/dbt_local_system.md:23`）；用户提示只有 `DBT TASK: {instruction}`（`agent/prompts.py:20`）。SQL 赛道加载 `sql-workflow` + 方言 skill（`sql_runner.py:103-116`）。
- max_turns：dbt 200（`direct.py:666-668`）；SQL 50，大库 75（`sql_runner.py:96-100,343`）；产品聊天 50（`claude_agent.py:419`）。
- 产品聊天：`setting_sources=["user"]`、`plugins=[{type: local}]`、`skills="all"`、hooks（SP `notebook-server/.../_server/ai/claude_agent_options.py:195-236`）。
- 拓扑：一个主 agent；子 agent 是插件 `agents/*.md`，经内置 Task 工具调用；仓库内无 `AgentDefinition`。dbt run 事后失败时 runner 另起一次快速修复（`direct.py:257-261`）。
- dbt 相关 skills（行数）：dbt-workflow 367（8 步编排）、dbt-write 304、dbt-debugging 64、dbt-testing 110、dbt-snapshots 120、dbt-versioning 78、knowledge-base 117、dbt-knowledgebase 208、domain-* 26–70、write-report 110；agents：verifier 100、value-verifier 69。
- SQL skills：sql-workflow 149（探索→成形→自底向上 CTE→验证→保存）、duckdb-sql 47、sqlite-sql 178、snowflake-sql 118、bigquery-sql 136、signalpilot 28（工具目录）。
- dbt-workflow 8 步（`signalpilot-plugin/skills/dbt-workflow/SKILL.md:83-265`）：1 扫项目 2 加载辅助 skills 3 校验并修过期上游 4 发现宏 5 研究数据 6 写 `technical_spec.md` 7 写并构建模型 8 验证并修复。1–3、8 必跑；无需构建时跳过 4–7（`:64-68`）。

## 2. 验证协议（SP dbt-workflow Step 8）

- 第 8 步并行派出 `verifier` 与 `value-verifier`，给原题、项目目录、连接、模型名、`prebuild_state.md`、`technical_spec.md`；不得传期望值或建议修法（`SKILL.md:268`）。
- 结构检查（`agents/verifier.md:20-74`）：表存在、列契约、行数/粒度/NULL 来源、排序契约、源绑定。数值检查（`agents/value-verifier.md:20-44`）：直接值保留（双向 EXCEPT）、聚合交叉核对、显式状态过滤。
- 输出：逐模型逐项 `PASS / FAIL / NEEDS / N/A` + 建议 + 汇总（`verifier.md:78-95`）。验证者只读，"Fix nothing"（`verifier.md:6,99`）。
- 修复者是主 agent：先复现 FAIL，复现不了带反证重派；复现了回到第 6 步记入 spec，再在第 7 步做最小修改，改过的模型两个验证者重跑（`SKILL.md:270-274`）。
- 停止：全部 PASS 或 N/A；无轮数上限，只受 max_turns 约束（`:276`）。

## 3. 澄清

- SP 聊天：整条回复为 `CLARIFICATION_REQUESTED: <一个问题>`，worker 发 `clarification_requested` 事件并把运行置为 `waiting_for_user`（`gateway/.../standalone_chat/worker.py:77,451-466`）。规则："Ask rarely, disclose always… only when exploration leaves an ambiguity that changes the answer… Never guess in silence"（`prompts/standalone_chat_system.md:403-411`）。
- nao：`clarification` 工具，一个问题 + 2–5 个可点选项（`apps/shared/src/tools/clarification.ts:8,16-20`），调用即结束本轮（`apps/backend/src/agents/stop-conditions.ts:21-24`）；"genuinely ambiguous and proceeding would likely produce the wrong result"（`components/ai/system-prompt.tsx:148-155`）。
- BIRD-Interact-ADK：a-interact 预算 `6 + 2·歧义数 + 2·耐心`（`orchestrator/ainteract.py:42-54`）；ask_user 2、submit 3、execute_sql 1、查列含义 0.5（`system_agent/agent.py:61-70`）；"先探索，意图有歧义再问，一次一个"（`:72-75`）；模拟用户拒答 SQL/schema 问题（`user_simulator/prompts.py:110,129`）。
- ktx：只在指标、时间范围、实体或粒度真有歧义且推断不出时问（`packages/cli/src/skills/analytics/SKILL.md:41`）。

## 4. Memory

| 系统 | 存什么 | 何时写 | 审批 | 范围 |
|---|---|---|---|---|
| SP 知识库 | context / decisions / rules / troubleshooting（`gateway/.../models/knowledge.py:42-52`） | agent 调 `propose_knowledge`（`mcp/tools/knowledge.py:316-335`） | `pending` 待人批准，批准前可"Evaluate change"跑评测（`docs/docs/product/knowledge-base.mdx:117-127,146`） | org / project / connection（`models/knowledge.py:36-39`） |
| dash | 错误模式、坑、用户纠正（`dash/settings.py:26-36`） | 出错或被纠正时 `save_learning`（`instructions.py:135-142`） | 无 | 全局 |
| nao | 用户指令与画像（`memory-system-prompt.tsx:22-47`） | 每轮后台 LLM 抽取（`services/agent.ts:589,801`） | 无，用户可开关编辑 | 用户 × 项目 |
| ktx | wiki 与语义层修改（`context/mcp/context-tools.ts:80-81`） | `memory_ingest`（`skills/analytics/SKILL.md:25`） | 无，git worktree 自动合并（`memory-agent.service.ts:58-63,257`） | 连接或全局 |

防污染只有 SP 有，且在评测层：agent 容器拿不到评测仓库与 `eval.json`（`docs/docs/evals/eval-repo.mdx:240-250`）；e2e 校验"无标准答案泄漏"（`benchmark/tests/validation/validate_bench_e2e.py:6`）；赛后改进建议"必须通用、不针对单题"（`prompts/post_grade_review.md:148`）。

## 5. 运行事件流

- SP 聊天：34 种持久化事件（`gateway/gateway/models/standalone_chat.py:23-58`，TS 镜像 `web/lib/api/standalone-chat.ts:140-174`）。与 agent 过程相关的：`status`、`progress`、`text_delta`、`thinking_delta`、`tool_started`、`tool_completed`、`sql`、`source`、`intermediate_result`、`clarification_requested`、`artifact_created`、`error`、`plan_created`、`files_changed` 等。文本增量每 0.15 秒或 2000 字合并一次，其他事件前先冲刷以保序（`worker_deltas.py:20-22`）。SDK 消息到事件的映射在 `worker.py:291-411`；前端走 SSE `/runs/{id}/events?after=<seq>`，可断点续传（`standalone-chat.ts:404-417`）。
- SP 基准：扁平记录 `agent_output.json`，条目为 thinking / text / tool_use / tool_result，带轮次与时间戳（`benchmark/agent/sdk_runner.py:77-138`）。
- nao：AI SDK `UIMessage` parts，工具调用状态 `input-streaming / input-available / output-available / output-error`（`apps/backend/src/types/chat.ts:116-124`）。
- Harbor → ATIF 转换：见 `2026-09-21-harbor-signalpilot-dab.md`；本次未重新核实。

## 6. 失败 → 改进

- SP ADE runner：仅失败时把标准答案放进工作目录，续接同一会话（max_turns 60，`benchmark/ade/runner.py:232-247`），agent 写 `failure_report.html`（`ade/post_grade.py:166-174`）：决策轨迹、DISCOVERABLE / CONTRADICTORY / ABSENT 分类（`post_grade_review.md:80-90`）、逐层引用 prompt/skill 原文（`:92-134`）、带文件与修改内容的建议（`:136-153`）。agent 提议、人来应用，没有自动应用的代码。
- SP 产品评测：知识条目变更时自动跑，回归告警（`docs/docs/evals/running.mdx:11-13,120-133`），人批准。
- dash：`python -m evals improve`，LLM 生成替换计划，自动改白名单文件，回归则回滚，最多 N 轮（`evals/improve.py:1-11,366-428`）；全自动，未见留出集。
- nao：定时读 90 天对话找信号，自动开 PR，每次最多 3 个（`types/context-recommendation.ts:20-22,35-70`），人决定合并。

## Claude Agent SDK（官方文档，2026-10-11）

- Skills：`setting_sources=["project"]` 加载 `.claude/skills/*/SKILL.md`，`allowed_tools` 含 `"Skill"`（https://code.claude.com/docs/en/agent-sdk/python.md）。单个 skill 按次开关文档未写（未确认）；每次运行各自构造 options 即可实现配置级开关（推断）。
- 子 agent：`ClaudeAgentOptions(agents={...})`，可单独设 `tools`、`model`、`max_turns`（https://code.claude.com/docs/en/sub-agents.md）。
- 自定义工具：`@tool` + `create_sdk_mcp_server`，可实现阻塞等待人回答的 `ask_user`。
- Hooks：`PreToolUse`（可拦截）、`PostToolUse`、`SubagentStart/Stop`、`Stop` 等；Python 是否全部支持未确认（https://code.claude.com/docs/en/agent-sdk/hooks.md）。
- 消息流：`AssistantMessage`、`ResultMessage`（含 `total_cost_usd`、`usage`、`num_turns`）；`include_partial_messages=True` 发增量事件。
- 预算：`max_turns`、`max_budget_usd`，可能超出一次响应的花费（https://code.claude.com/docs/en/agent-sdk/agent-loop.md）。
