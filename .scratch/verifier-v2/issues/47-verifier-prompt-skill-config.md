# 47 — 单一验证器的提示词、Skill 第 8 步和配置

Status: in-review
Blocked by: 46（done）
Spec: ../spec.md
Claimed by: claude (worktree 47, 2026-11-05)
Branch: worktree-47-verifier-prompt-skill-config

## Goal

只留一个验证子 agent，按 spec 的 4 个步骤和报告格式工作；拿掉值验证器；FAIL 处理规则只写在系统提示词这一处；Skill 第 8 步改成用新工具和 `compare_by_key`；`maxTurns` 设为 50。这张工单合入后，验证层就是最终上线的形态，钩子已经生效。

## 已和用户定下的（2026-11-04）

- **验证器的 4 个步骤**（spec「验证器」）：
  1. `contract_check`，照抄结果；
  2. 从题面挑粒度键，调 `grain_facts`；
  3. 对照题面判断 `model_diff` 的结果；
  4. 用第 2 步定下的键，按题面独立写参考 SQL，调 `compare_by_key`。
- 只报 CHECK 1–7，格式严格照 spec。
- **系统提示词**里的 FAIL 处理：先复现，再做最小改动；复现不出来，就带上反证再派一次。NEEDS_INFO：补上信息再派一次，或者自己把这项检查做掉。
- **Skill**（`skills/dbt-workflow/SKILL.md`）只改第 8 步，就是第 220–279 行附近。工单 41 合入后行号可能变，以 "### Step 8" 这一节为准。
  - 删掉"照处方原样改"；
  - 主 agent 自己的键级核对改用 `compare_by_key`；
  - FAIL 处理只引用系统提示词，不写第二遍。
- 删除 `agents/value-verifier.md`，更新 `NOTICE`。
- baseline 和 no-verifiers 的行为不变。
- full-old-skill 的冻结版 Skill（`skills-frozen/`）不改，派单以系统提示词为准。它里面过时的 value-verifier 说法记为已知的干扰因素，留给最终报告。

## Authority

- May edit: `src/data_agent/agents/`、`src/data_agent/verifiers.py`（提示词、`AgentDefinition`、工具列表）、`skills/dbt-workflow/SKILL.md`（只改 Step 8 一节）、`NOTICE`、`tests/`（`tests/acceptance/` 除外；只许删掉转绿测试的 `xfail`）
- Must not edit: `src/data_agent/verifier_v2/`（工具和钩子，有问题就停下交回）、`src/data_agent/configs/`、Skill 的其他节、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（初筛由主会话跑）
- Spend cap: $0.5（本地或单题真实运行）；初筛由主会话跑

## Context

- spec：`.scratch/verifier-v2/spec.md`；ADR-0009；审查报告第 8 条（Skill 和系统提示词互相矛盾）。
- 上游：SignalPilot benchmark 版的验证器和 SKILL（`benchmark/signalpilot-plugin/agents/verifier.md`、`skills/dbt-workflow/SKILL.md:264-270`）。

## Acceptance

- [ ] 44 里剩下的验收测试转绿（配置：只有一个验证器、`maxTurns` 为 50）。
- [ ] `make check` 通过。
- [ ] **初筛**（主会话，full，每题 3 次，DeepSeek 闲时，对照 accept-2 full）：
  - 目标题：dbt-fix-division-by-zero、pos-operations、dbt-customer-cross-sell-insights、web-session-quality-scoring、dbt-receivables-aging-buckets。accept-2 合计 7/15。
  - 过关两条都要满足：
    - 成功次数 ≥8/15；
    - 每个 trial 验证器墙钟时间（各次调用的起止时间段取并集）的中位数 ≤155 秒，这 5 道题在 accept-2 的基线是 311 秒。
  - ≥8/15 在噪声范围内，初筛只用来筛一下，最终判断交给统一评估。
  - 同时报告：每次验证调用的工具调用数和轮数分布（p50、p90、p95、最大值），撞到 `maxTurns` 的次数，Stop 拦截的次数和原因。

## Evidence required

验收测试输出；`make check` 输出；初筛的汇总表（由主会话贴）。

## Stop conditions

- 提示词写不出 spec 的 4 个步骤，因为工具缺东西：停下，交回给 45 或 46。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3
- Review rounds: 3（第 1 轮 Fable + eng:code-review，8bde360；退回第 1 轮 eng:code-review，cc65d28；退回第 2 轮 eng:code-review，0126941；Codex 4 次 at capacity）
- Model spend: $0（工单会话无模型调用）
- Outcome: in-review，两轮退回已修完，branch `worktree-47-verifier-prompt-skill-config`（ad09e0a … cc65d28、626d226、0126941），PR #48；初筛过线判定需在 0126941 上重跑
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（worktree 47-verifier-prompt-skill-config）

**改动**
- `src/data_agent/verifiers.py`：一个验证者，工具 `list_tables`、`query_database` 加 4 个 v2 工具，`maxTurns=50`，FAIL 和 NEEDS_INFO 规则只在系统提示词里有一份。
- `src/data_agent/agents/verifier.md`：按 spec 的 4 步和钩子能解析的 CHECK 1–7 报告重写。`value-verifier.md` 删除，`NOTICE` 更新。
- `skills/dbt-workflow/SKILL.md` 第 8 步：一个验证者，有 `compare_by_key` 时做键级核对，FAIL 处理引用系统提示词。无验证者的回退文本从 main 原样恢复，no-verifiers 配置不变。文件头改动说明只改第 8 步相关句子。
- 测试：验收测试去 xfail；单元测试跟随新提示词和第 8 步。

**第 3 步真实行为**（无模型；`uv run python -I /tmp/t47-real.py` 对六个配置 build options）：

```
full            agents=['verifier'] maxTurns=[50] verifiers_prompt=True
no-skill        agents=['verifier'] maxTurns=[50] verifiers_prompt=True
no-context      agents=['verifier'] maxTurns=[50] verifiers_prompt=True
full-old-skill  agents=['verifier'] maxTurns=[50] verifiers_prompt=True
baseline        agents=[] maxTurns=[] verifiers_prompt=False
no-verifiers    agents=[] maxTurns=[] verifiers_prompt=False
verifier.tools: ['Read', 'Glob', 'Grep', 'mcp__data__list_tables', 'mcp__data__query_database', 'mcp__data__model_diff', 'mcp__data__contract_check', 'mcp__data__grain_facts', 'mcp__data__compare_by_key']
hooks: ['PreToolUse', 'Stop', 'SubagentStart', 'SubagentStop']
```

验收测试 `test_verifier_configs_define_one_verifier` 去 xfail 后先红（`assert set(options.agents) == {"verifier"}`）后绿。`make check`：ruff、format、basedpyright 干净；`1693 passed, 1 skipped, 38 deselected in 301.48s`。

**审查第 1 轮**：`da-review main` 两次 at capacity，Fable 只读审查代替，加 `eng:code-review main`（Standards、Spec）。
- 已修（8bde360）：第 8 步在括号里复述 FAIL 规则、无验证者回退文本被改写（Standards 1、Spec 1/2）；系统提示词的停止句没给"主 agent 自己解决了 NEEDS_INFO"留出口（Fable 1）；报告模板原样复制会解析成 PASS，改成 `<PASS|FAIL|NEEDS_INFO|N/A>`（Fable 4）；第 8 步行数句点名 CHECK 3 而它现在是粒度检查，改成 CHECK 3 和 7（Fable 3）；改动说明换行（Standards 4）。
- 不修，Authority 外：Skill `## Tools` 一节仍把 `verify_model_values` 写成"value-verifier 在第 8 步用"（Standards 3、Fable 2），运行时无害（有"仅当在你的工具列表里"的限定，且工具未注册），需后续工单；`verifier_v2/dispatch.py`、`hooks.py` 里"until ticket 47 removes it"的过时注释同理。
- 不修，设计如此：`VERIFIER_MAX_TURNS` 在 `verifiers.py` 和 `verifier_v2/hooks.py` 各定义一份（hooks 导入 verifiers，反向会循环；hooks.py 在 Authority 外），有测试钉住两者相等。`VERIFIER_NAMES` 仍是单元素元组，`agent.py` 和 `hooks.py` 按成员判断。验证者工具表不含 `map_columns`、`check_model_schema`、`audit_model_sources`：spec 的"保留的旧工具"指 server 保留（已保留），4 步不需要它们（Spec 4）。
- 2026-11-05 主会话：PR #48 已开。Codex app 替代审查（Fable 5.1）。核过没问题的：verifier.md 的 `### <model>` + `CHECK n: STATUS — detail` 与 `report.py` 的 `MODEL_RE`/`CHECK_RE` 一致，`N/A` 能匹配，尖括号模板不会解析成 PASS，`### Summary` 不记录；4 步只用定义里有的工具，`task_columns` 格式与 `parse_task_columns` 一致；verifier.md 只依赖派单模板的三个字段，"主 agent 提供的反证"标题与 `UNVERIFIED_HEADING` 一致；系统提示词的 FAIL/NEEDS_INFO/Stop 措辞与 spec 和三条规则一致；第 8 步无针对题目的内容；配置文件未变。**退回**（第 1 轮）：
  - 退回 1（P2）：`agents/verifier.md:131-146`（第 4 步）与 `verifier_v2/compare.py:144-154`：提示词没要求参考 SQL 的非键列用模型的列名，而工具只比较两边同名的列。场景：题面"每客户总收入"，验证者写 `SUM(amount) AS revenue`，模型列叫 `total_revenue`，工具回 "compared columns: (none)"，三个计数全 0，按"全 0 即 PASS"得出空洞的 CHECK 7 PASS。修法：第 4 步要求参考 SQL 的输出列别名成模型的列名；compared columns 为空时记 NEEDS_INFO 而不是 PASS。
  - 退回 2（P2）：`agents/verifier.md:85-88`（第 2 步）允许粒度键"取自运行前就存在的 YML"，但验证者只能 Read 当前项目的 YML（快照目录被守卫拦，`model_diff` 不报 YML 改动），分不清是原有的还是主 agent 新写的。场景：题面没说粒度，主 agent 在新建的 schema.yml 里给粗键加 `unique` 测试，验证者拿它调 `grain_facts`，CHECK 3 按 agent 自己的定义通过，正是 ADR-0009 禁止的。修法：粒度键只从题面取，题面没有就 NEEDS_INFO；需要原有 YML 的豁免时依赖 `grain_facts` 自己从快照 YML 读出的 "exemption candidate" 行，不让验证者自己去读 YML 当键源。
  - 不退回（P3）：Skill `## Tools` 仍写 `verify_model_values`（Authority 外，已记）；无验证者回退文本里的 "value CHECK 2" 编号（刻意原样恢复）；`hooks.py`、`dispatch.py` 里 "until ticket 47" 的过时注释（Authority 外）。这三条交对齐会话定是否开清理工单。
  - 初筛 `t47-screen` 已在 8bde360 上开跑（主会话决定不等闲时，多花约 ¥15）；过线判定要在修后的提交上重跑，这次的数据只看墙钟和轮数分布。

### 2026-11-05 工单会话（退回第 1 轮修复，commits e45d781、cc65d28）

只动 `src/data_agent/agents/verifier.md` 和 `tests/test_verifiers.py`；`verifiers.py` 的系统提示词没有粒度或参考列的措辞，不需同步。`make check`：1695 passed, 1 skipped。`uv run pytest tests/acceptance -q -k verifier`：48 passed。

- 退回 1，第 4 步：参考 SQL 的非键输出列别名成模型列名（例 `SUM(amount) AS total_revenue`）；列名来源是 `contract_check` 输出或题面列表，都没有时用 `query_database("SELECT * FROM <model> LIMIT 0")` 取列名，仍不读模型 SQL。PASS 条件改成"全 0 且题面定义的每个指标都在 `compared columns` 里"；`compared columns: (none)` 或指标落在 "reference columns the model lacks" 下，先修别名重调一次，仍没比到就 CHECK 7 = NEEDS_INFO。引用的字符串与 `render.py` 的 `render_compare` 输出一致（测试真实渲染对照）。模板 CHECK 7 行加 `<compared columns; …>`，仍以 `<` 开头不会被解析成 PASS。
- 退回 2，第 2 步：粒度键只从题面取，明说不能从项目 YML 取；题面没说粒度就 CHECK 3 = NEEDS_INFO。豁免只有题面或 `grain_facts` 从快照 YML 报出的 "exemption candidate"（措辞改成与工具输出一致的 "declares other columns unique"）；"Do not read YML files to find a key or an exemption"。全文不再出现 "original YML"。
- 测试：两个新测试先红后绿；旧 grain 测试去掉对 "original YML" 的断言。
- 真实行为（无模型）：六个配置的 agents/maxTurns/系统提示词输出与上一轮相同；`load_definition("verifier")` 的 prompt 含新句子、不含 "original YML"；工具表不变。
- 审查：`da-review` at capacity；eng:code-review（Standards、Spec）已修（cc65d28）：参考列部分匹配仍会"全 0 即 PASS" → PASS 要求题面每个指标都在 compared columns；contract_check 为 N/A 时没给列名来源 → 加 `LIMIT 0` 查询；豁免措辞与工具输出不符 → 改；测试引用的工具输出串与 `render.py` 无绑定 → 用 `render_compare` 渲染；docstring 引用退回轮 → 删。不修：第 1 步与第 2 步对快照 YML 的解释角度不同但不矛盾；测试按整句断言与既有风格一致；"confirm a candidate against the task" 比 spec 严一点是已审内容；YML 禁读是提示词规则，验证者仍有 Read/Glob/Grep。
- 2026-11-05 主会话：Fable 复审退回轮 diff（8bde360..cc65d28）。核过没问题的：提示词引用的工具输出字符串都在 `render.py`/`contract.py` 里原样存在；题面无指标已被"题面无计算 → NEEDS_INFO"覆盖；两边都 NULL 的列算比较过；模板 CHECK 7 行不会被解析成状态；没有措辞再让键来自项目 YML 或模型的 GROUP BY；系统提示词无冲突。**退回**（第 2 轮，最后一轮）：
  - 退回 A（P2）：`verifier.md:133-135` 的 `query_database("SELECT * FROM <model> LIMIT 0")` 取不到列名：`inject_limit` 保留 `LIMIT 0`，`mcp_server.format_rows`（`mcp_server.py:93-95`）零行时只返回 `Query returned 0 rows`，不打表头。场景：无 contract、题面无列表时验证者按建议查询得到空，只能猜别名或违规读模型 SQL。修法：改成 `list_tables(name_contains="<model>")`（列出列名且在验证者工具表里），或 `LIMIT 1`（有行才打表头）；`describe_table` 不在验证者工具表里，不要指向它。
  - 顺手（P3）：`verifier.md:144-150` 题面只定义行集（去重、过滤、"每 X 一行"而无指标）时参考 SQL 只有键列，`compared columns: (none)` 按新规则变 NEEDS_INFO，而键的两侧比对本来就能定胜负。改成："(none) 记 NEEDS_INFO，除非题面没有定义任何非键列，这时只看键的计数。"
  - 顺手（P3）：`verifier.md:73-78` 第 2 步说键只从题面取，但 `grain_facts` 按建出来的模型列名解析、名字不存在就 ToolError；题面说"每客户每月一行"没给列名，验证者得把它对到 `customer_id`/`month`。把第 4 步"列名不是逻辑，可以从 contract_check/题面/查询取列名，不读 SQL"那句同样写进第 2 步。
  - 顺手（P3）：`verifier.md:88-89` 和 `verifiers.py:66-67` 的 "confirm a candidate against the task" 在题面对粒度沉默时不可判定，spec 第 73 行允许快照 YML 自行豁免。写明确认的含义是"题面不与之矛盾"。另：粒度 NEEDS_INFO 时系统提示词的第一个选项（补信息再派）是死路，因为验证者必须忽略派单消息里给的粒度；在 VERIFIERS_PROMPT 加一句让主 agent 对粒度类 NEEDS_INFO 直接走第二个选项（自己跑检查），避免循环。

### 2026-11-05 工单会话（退回第 2 轮修复，commits 626d226、0126941）

只动 `agents/verifier.md`、`verifiers.py`（VERIFIERS_PROMPT）、`tests/test_verifiers.py`。四项先写测试（5 个先红），改完全绿。`make check`：1698 passed, 1 skipped（两次）。`uv run pytest tests/acceptance -q -k verifier`：48 passed。

- 退回 A：列名回退改成 `list_tables(name_contains="<model>")`，注明子串会匹配到别的表、取模型自己那行；删掉 `LIMIT 0`。测试断言不含 `LIMIT 0`。
- 顺手 1：`compared columns: (none)` 记 NEEDS_INFO，除非题面没定义任何非键列，这时只看三个键计数。核过 `compare.py:141-178` 键列单独的参考能定行集题的胜负。
- 顺手 2：第 2 步加"工具按模型列名解析键，把题面的话对到模型列名；列名不是逻辑，来源是 contract_check 输出、题面列表或 `list_tables`，不读 SQL"；第 4 步改为引用第 2 步。
- 顺手 3："confirm a candidate against the task" 改成"题面不与之矛盾即可用"。VERIFIERS_PROMPT 加一句：粒度类 NEEDS_INFO 不要带自定粒度重派，主 agent 自己用 `grain_facts` 跑 CHECK 3、用 `compare_by_key` 加参考 SQL 跑 CHECK 7。核过主 agent 能调这两个工具（`mcp_server.py:269-270`、`agent.py:747`）。
- 真实行为（无模型）：prompt 不含 `LIMIT 0`、不含 "confirm a candidate"，含 `list_tables(name_contains="<model>")` 和 "does not contradict"；VERIFIERS_PROMPT 含 "run the grain check yourself"；maxTurns 50，工具表不变。
- 审查：`da-review` 第 4 次 at capacity。eng:code-review（Standards、Spec）同一条实质问题已修（0126941）：粒度句覆盖 CHECK 3 和 7 却只点名 `grain_facts`，CHECK 7 要用 `compare_by_key`。其余已修：豁免括注措辞；行集例外的双重否定；第 4 步重复列名来源；`list_tables` 子串多匹配注明；弱断言换具体句子；键列参考测试补真实渲染。不修：超宽行是报告模板和中文标题，本轮前就有；"defines no row set or computation" 是行集规则的直接推论。
