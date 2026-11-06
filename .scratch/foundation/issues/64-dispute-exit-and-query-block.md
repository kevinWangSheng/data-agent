# 64 — 验证器与主 agent 争议的收场，钩子硬拦验证器查派单模型

Status: ready-for-agent
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）；../../verifier-v2/spec.md
Claimed by: —
Branch: —

## Goal

验证器对同一个没改过的模型、在看过反证之后仍判同一项 FAIL 时，运行可以结束，争议记进事件和最终回复，不再一轮轮重派。验证器不能用 `query_database` 查本次派单的模型本身，由钩子强制，不只靠提示词。

## 已和用户定下的（2026-11-06，grilling 第 2 轮 Q3、Q5，第 3 轮 Q1、Q2）

- **争议的判定**：同一模型的同一 CHECK 第二次 FAIL，并且
  - 模型指纹和上一次 FAIL 时相同（中间没改过）；
  - 中间那次重派带了反证（派单里有 `dispatch.py` 的 `UNVERIFIED_HEADING` 一节，且内容非空）。
- **Stop**：争议项不再让 Stop 规则 3 拦；运行结束时发 `ended_disputed` 事件，列出争议项（模型、CHECK、两次 FAIL 的派单序号）。其余 FAIL 照旧拦，`MAX_BLOCKS` 不变。
- **主 agent 提示词**（`src/data_agent/verifiers.py` 的 VERIFIERS_PROMPT FAIL 段，约 :69-72，以及 :61 "still has a FAIL" 那句）：带反证重派一次后同一项仍 FAIL、你没有改模型时，可以结束，在最终回复里列出每个争议项和你的依据。Skill 第 8 步（`skills/dbt-workflow/SKILL.md` 约 :278-293）只引用系统提示词，不另写规则；若有冲突措辞就同步。
- **硬拦**：PreToolUse 钩子拦验证器（按 `agent_id` 认出是 `verifier` 子 agent）发出的 `query_database`，SQL 读到本次派单列表里的模型（按建成的表名和别名）就拒绝，理由写"不要查本次要验证的模型；用 grain_facts / compare_by_key"。SQL 解析不了就放行。主 agent 的查询不受影响。
- NULL key 规则（`agents/verifier.md` 约 :90）不动：accept-2 的 `pos-operations__JfH8WAC` 里 256 行源列自带的 NULL 主键被评分判错，放宽会放过它。

## Authority

- May edit: `src/data_agent/verifier_v2/hooks.py`、`src/data_agent/verifier_v2/stop.py`、`src/data_agent/verifier_v2/dispatch.py`（只读出派单是否带反证，不改模板文本）、`src/data_agent/verifiers.py`（VERIFIERS_PROMPT）、`skills/dbt-workflow/SKILL.md` 第 8 步、`evals/process.py`（只为兼容新事件）、`tests/test_verifier_v2_hooks.py`、`tests/test_verifier_v2_redispatch.py`、`tests/test_verifiers.py`、新的测试文件
- Must not edit: `tests/acceptance/`、`src/data_agent/agents/verifier.md`（归工单 63）、`src/data_agent/verifier_v2/project.py`（归工单 60）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0（初筛由主会话在 59–64 全部合入后合跑）

## Context

- 证据（2026-11-06，读 `runs/t47-screen3/jobs/full/pos-operations__{JPS92zS,HGcLuCF,qpLnwaW}`）：
  - CHECK 3 FAIL 原文："167 NULL-key rows, 0 duplicates; no exemption (task does not make NULL order_source part of the grain)"；三个 trial 评分都 69/69，是假阳性。同样 167 行，不同调用在 PASS 和 FAIL 之间摇摆。
  - HGcLuCF 第 2 次调用后主 agent 没改模型、只带反证重派，第 3 次验证器才改判；qpLnwaW 第 3 次全 PASS。每次调用 535–684 s。screen3 里没有"循环到截止"，0 次 `ended_*` 事件。
  - 维持重派的是 `stop.py:105-108`（最后一份报告有 FAIL 就拦）和 `verifiers.py:69-72,79-80`（FAIL 复现不了只能带反证再派，结束条件不允许有 FAIL）。现有出口：验证器改判、`MAX_BLOCKS=2` 后记 `ended_with_unresolved`（`hooks.py:45,200-207`）、剩余时间不足记 `ended_unverified`。
- 硬拦的事实：
  - 子 agent 里触发的 PreToolUse 输入带 `agent_id`；`agent.py:397-434` 已按 SubagentStart/SubagentStop 跟踪运行中的子 agent，可照此建 `agent_id → agent_type`。
  - 表名解析：`verifier_v2/sql.py:144` 的 `table_references`；模型建成的名字和别名：`compare.py:215` 的 `_forbidden`（`compare_by_key` 已用同样规则拒绝读改动模型的参考 SQL）。
  - 工单 47 的 (c)：t47-screen3 验证器仍有"看模型输出"约 73 次、"干跑参考 SQL"约 90 次（按 SQL 文本粗分）。
  - 拒绝的写法照 `dispatch.py` 的 `deny_dispatch`（`permissionDecision: deny` + reason）。
- 上游：SignalPilot plugin 只派一轮、没有 Stop 硬门（`/Users/shenghuikevin/dev/AI/data-agent-refs/signalpilot-plugin/skills/dbt-workflow/SKILL.md:255-276`）；未见"意见不一致怎么收场"的条款。

## Acceptance

- [ ] 新测试：同一模型同一 CHECK，模型未改、中间派单带反证，第二次 FAIL 后 Stop 不拦，发 `ended_disputed` 并列出该项（修复前会失败）。
- [ ] 新测试：模型改过、或中间派单没带反证，第二次 FAIL 仍照旧拦。
- [ ] 新测试：同一份报告里争议项和非争议 FAIL 并存时，仍因非争议项拦。
- [ ] 新测试：验证器的 `query_database` 读派单模型（裸名、带 schema、别名）被拒；读源表、读未派单的表放行；SQL 解析失败放行；主 agent 的同一查询放行。
- [ ] 新测试：VERIFIERS_PROMPT 含争议收场的规则；Skill 第 8 步与之不冲突。
- [ ] `evals/process.py` 读到 `ended_disputed` 不报错。
- [ ] 现有钩子、Stop、验证器测试和 `tests/acceptance` 全部通过。
- [ ] `make check` 通过。

## Evidence required

测试输出；用 t47-screen3 `pos-operations__HGcLuCF` 的验证器报告序列离线回放 Stop 判定，贴出第 2 次 FAIL 后的判定（前后对比）。初筛由主会话在 59–64 全部合入后合跑。

## Stop conditions

- PreToolUse 输入里拿不到可靠的 `agent_id`，分不出验证器和主 agent 的调用（只做争议收场，硬拦交回）。
- 争议判定必须改报告格式或派单模板文本。
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
