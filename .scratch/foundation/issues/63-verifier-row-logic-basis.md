# 63 — 验证器：删除原有逻辑要题面点名，参考 SQL 沿用快照行集，报告再短一点

Status: ready-for-agent
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）；../../verifier-v2/spec.md
Claimed by: —
Branch: —

## Goal

主 agent 删掉或放宽原有过滤、连接、去重而题面没有点名时，验证器判 FAIL；CHECK 7 的参考 SQL 不再跟着主 agent 的改动走。验证器少做提示词不需要的源表探查，报告每行更短。

## 已和用户定下的（2026-11-06，grilling 第 2 轮 Q4、Q5，第 3 轮 Q2）

- **CHECK 6（`src/data_agent/agents/verifier.md` Step 3，约 :113-118）**：删除或放宽原有的过滤、连接、DISTINCT/QUALIFY/去重，依据只能是题面点名这个条件，或点名它影响的那些行（引用原文）。从成功标准、行数、"题面没说要保留"推出来的不算依据，判 FAIL，并引用被删的那一行。
- **Step 4 参考 SQL（约 :125-140）**：题面没提到的行集规则（过滤、连接、去重），照快照模型原样写进参考，从 `model_diff` 的 removed 行取；题面提到的照题面写。这样被删掉的过滤会表现为 only-in-model。仍不读模型现在的 SQL。
- **源表探查**：Step 4 现有那句"`query_database` 只用于题面规则需要的源表事实"再压一次：写明每个模型通常不需要探查，需要时一次查完；不加次数上限。
- **报告**：CHECK 行除 FAIL 外只写状态和关键计数，每行细节不超过约 100 个字符；CHECK 5、6 只写有问题的改动（全部有依据就写 "all changes have a basis"）。Summary 规则不变。2026-11-06 量过 t47-screen3：每个模型的报告已经是 900–1900 字符，这项预期每次调用只省几秒到几十秒。
- 措辞是通用规则，不能出现具体题目、表名或列名（ADR-0005）。
- NULL key 规则（约 :90）不动。

## Authority

- May edit: `src/data_agent/agents/verifier.md`、`tests/test_verifiers.py`、新的测试文件
- Must not edit: `tests/acceptance/`、`src/data_agent/verifiers.py`（VERIFIERS_PROMPT 归工单 64）、`src/data_agent/verifier_v2/`、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0（初筛由主会话在 59–64 全部合入后合跑）

## Context

- 证据（2026-11-06，读 `runs/t47-screen3/jobs/full/dbt-fix-division-by-zero__{6uVCUp4,7f7yp8X,KkKSw8q}`）：
  - 三个 trial 都只挂 `component_3_row_count: 390 vs 382`（`verifier/test-stdout.txt`）：主 agent 有意删掉了原模型的 `where status != 'CANCELLED'`，理由是成功标准的字面意思；评分器、原模型和参考解答都保留这个过滤。
  - 验证器的 `model_diff` 清楚列出 `-    where status != 'CANCELLED'`（6uVCUp4 事件 seq 241），CHECK 6 却写"Removed … has a basis: success criterion 3 … the task names no status exclusion"判 PASS；三次参考 SQL 的 `src` CTE 都只有 `WHERE customer_id IS NOT NULL`，所以 CHECK 7 0 mismatch。两个历史通过的 trial（`runs/t47-screen/.../xMzmqsL`、`runs/t47-screen2/.../qLyePQj`）参考带着 status 过滤：参考的行集跟着 diff 走，不独立。
- 上游思路：SignalPilot plugin `agents/verifier.md:40,48,74`（"verbatim authorization" 限定改动范围；"FAIL an unrequested … change"）。本机路径 `/Users/shenghuikevin/dev/AI/data-agent-refs/SignalPilot/signalpilot-plugin/agents/verifier.md`。
- 墙钟根因：`docs/research/2026-11-05-verifier-wall-root-cause.md` §5 杠杆 1、3。

## Acceptance

- [ ] 新测试：`load_definition("verifier")` 的提示词含"删除原有逻辑须题面点名、推论不算"的规则（修复前会失败）。
- [ ] 新测试：Step 4 含"题面没提的行集规则沿用快照模型（来自 model_diff 的 removed 行）"。
- [ ] 新测试：报告模板与 `report.py` 的解析仍兼容（用模板渲染一份报告，`parse_report` 得到每个模型 7 个状态）。
- [ ] 提示词里没有具体题目、表名或列名。
- [ ] 现有 `tests/test_verifiers.py`、`tests/acceptance` 里的验证器测试全部通过。
- [ ] `make check` 通过。

## Evidence required

测试输出；改后提示词的相关段落原文贴进 Comments。初筛由主会话在 59–64 全部合入后合跑（5 题 × 3，过线见 ADR-0008 Update 2026-11-06）。

## Stop conditions

- 规则必须写到具体题目才能生效（违反 ADR-0005）。
- 需要改 `report.py` 的解析规则或 VERIFIERS_PROMPT。
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
