# 验证层 v2：硬性事实交给工具，模型只对照题面判断改动

日期：2026-11-04。决策见 `docs/adr/0009-verifier-v2.md`；验收规则见 ADR-0008 Update 2026-11-04；证据见 HANDOFF "accept-2 根因审查" 第 2 条和 `runs/accept-2/reading/design-audit/audit-verifiers.md`（只在本机）。

## Problem Statement

accept-2 里验证层花费大、信号弱，有时还会把 agent 带偏：

- **PASS 几乎不说明什么**：全部 PASS 的 trial 成功率 63%，full 整体是 62%。34 个失败 trial 里只有 10 个收到过 FAIL。
- **花费大**：验证器占 full 运行时间的约 1/3；按估算，full 比 baseline 多出的花费里约 60% 来自验证器（未确认）。
- **会让 agent 改错**：
  - CHECK 6 会审查 agent 没碰过的逻辑，在 division-by-zero 里让 agent 删掉了评分器需要的过滤条件；
  - 被删掉的过滤条件，没有任何检查能看到；
  - CHECK 5 用来对比的"原样"，来自主 agent 自己在派单消息里的说法。
- **硬性事实交给模型判断**：pos-operations 里粒度键有 256 行 NULL，验证器以"已在文档中说明"为由判了 PASS。
- **值验证器主要是复核实现本身**：它用模型自己的数据源重算，在 cross-sell 里放过了被换掉的源表。
- **派单消息带着主 agent 的假设**：212 次派单里有 120 次写了上游表名。
- **FAIL 只靠提示词约束**：4 个带着 FAIL 结束的 trial 全输；1 个 trial 从没派过验证器就结束了。
- **Skill 和系统提示词的说法互相矛盾**：Skill 说"照验证器的处方原样改"，系统提示词说"先复现再做最小改动"。

## Solution

能确定计算的事实交给工具和 harness 算，模型只做两件需要判断的事：对照题面判断"改了什么"，以及对照题面写一条独立的参考 SQL。

- **快照**：harness 在 agent 开跑前，保存 dbt 项目的逻辑文件，作为"原样"。
- **4 个确定性工具**：`model_diff`、`contract_check`、`grain_facts`、`compare_by_key`。
- **只保留一个验证子 agent**，拿掉值验证器。
- **harness 钩子**：
  - 派单改写：第一次派单只用模板；
  - Stop 拦截：没验证过或者还有 FAIL，就不能结束；
  - 记录每次验证调用跑了几轮。
- **统一 FAIL 处理规则**，只在系统提示词里写一处。

## Implementation Decisions

### 快照

- **时机**：agent 开跑前，由 harness（`data-agent run`）保存。只在启用了验证器的配置里做。
- **范围**：workspace 里每个 dbt 项目（有 `dbt_project.yml` 的目录）的下面这些文件：
  - `dbt_project.yml`；
  - `models/`、`macros/`、`snapshots/`、`tests/` 下的 `.sql` 和 `.yml`；
  - `seeds/` 下的文件，单个文件不超过 5 MB，超过的只记文件名和哈希。
- **不包括** `dbt_packages/`、`target/`、`logs/`。
- **位置**：本次运行的临时目录，在 workspace 外面。只有 MCP 工具能读；agent 用 Read 去读会被 workspace 守卫拒绝。
- 不对数据库里的数据做快照。

### 工具（MCP，只在启用了验证器的配置里注册）

**`model_diff()`**：列出所有新建和改动的模型，每个模型逐类给出改动前后的增、删、改。只报事实，不下判断。

- 数据源：`ref` 和 `source`。
- 过滤：WHERE 和 HAVING，每个谓词单列一条。
- 连接：类型和 ON 条件。
- 行数相关：DISTINCT、QUALIFY、`row_number() = 1` 一类去重；UNION 和 UNION ALL；LIMIT。
- 粒度：GROUP BY。
- 窗口函数：新增或改动的窗口函数。`contract_check` 用这一项。
- `dbt_project.yml` 里和这个模型相关的配置改动：物化方式、`where`、`vars`。
- 解析方法：把 `{{ ref(...) }}` 和 `{{ source(...) }}` 替换成表名，`config()`、`var()` 和宏调用换成占位符，再用 sqlglot 解析（方言跟随网关）。
  - 解析失败或者含 `{% if/for/set %}` 的模型，退回逐行文本 diff，并标明"未解析"。base image 的 2356 个模型里只有 6 个含这类语法。
- 没动过的模型和没动过的逻辑，不出现在输出里。

**`contract_check(models)`**：对每个模型给出三项检查的状态和证据。

1. **存在**：模型在库里存在、能查。
2. **列契约**：只认题面要求的输出列，加上**快照里原有的 YML**。
   - 模型和 YML 都是 agent 新建的，就只按题面检查。
   - agent 改过或新建的 YML 不能作为契约来源。
3. **排序不唯一的窗口函数**：只查 `model_diff` 报出的新增或改动的窗口函数，其他的记 N/A。
   - 判断方法：在每个分区里，对 ORDER BY 用的那组列计数，有重复就算排序不唯一。

**`grain_facts(model, key_columns)`**：返回粒度键的 NULL 行数和重复行数。

- 键是验证器给的列，加上快照里原有 YML 中同时有 `unique` 和 `not_null` 测试的列。
- 规则：NULL 或重复大于 0 就判 FAIL。
- 只有题面或快照里原有的 YML 能豁免：它们把那些不同的列定义成粒度的一部分，或者写明了更细的粒度。agent 写的 `technical_spec.md`、YML 描述和注释都不能豁免。
- 工具返回事实，以及是否命中可以豁免的来源（附出处）。最终状态由验证器按这条规则写。

**`compare_by_key(model, key_columns, reference_sql)`**：按键对比模型和参考 SQL 的结果。

- 参考 SQL 引用了被测模型本身，或者本次新建、改动过的任何模型，直接拒绝，并说明原因。这些模型由 `model_diff` 给出，用 sqlglot 取表名来判断。
- 全部键做 full outer join 比对，不抽样。
- 什么算"值相等"，按模型列的类型定。这套规则原来写在 `agents/value-verifier.md:124-129` 的提示词里，现有工具代码里没有实现；这次改成由工具在代码里判断：
  - DECIMAL：参考值四舍五入到这一列的小数位数后，必须和模型值相等；
  - 浮点（DOUBLE、REAL、FLOAT）：相对误差不超过 1e-6，即 `|model - ref| <= 1e-6 * |ref|`；参考值为 0 时要求两边都是 0；
  - 整数、字符串、日期、布尔：必须完全相等；
  - 两边都是 NULL 算相等；只有一边是 NULL 算不一致。
- 返回：只在一边出现的键数、值不一致的键数，各给最多 20 个样例。
- 参考 SQL 照旧要过只读网关。

保留的旧工具：`list_tables`、`query_database`、`map_columns`、`check_model_schema`、`audit_model_sources`。`verify_model_values` 跟着值验证器一起不再提供。

### 验证器（只有一个，`agents/verifier.md`）

按顺序：

1. 调 `contract_check`，照抄结果（CHECK 1、2、4）。
2. 从题面挑粒度键，调 `grain_facts`，按上面的规则写 CHECK 3。挑键是判断。
3. 调 `model_diff`，逐条对照题面判断（CHECK 5、6）：
   - 新增或删除的数据源、过滤、连接、行数相关逻辑，在题面里要有依据；
   - 没动过的逻辑不审。
4. 用第 2 步定下的键，根据题面独立写参考 SQL，调 `compare_by_key`（CHECK 7）。

`maxTurns` 为 50。撞到上限、没有写出报告的，按 NEEDS_INFO 处理，不算 FAIL。

### 报告格式（钩子和事件都按这个解析）

- 每个模型一节，标题行是 `### <model>`。
- 节里每项检查一行：`CHECK <n>: <PASS|FAIL|NEEDS_INFO|N/A> — <理由>`。n 取 1 到 7。
- 解析沿用 `verifiers.py` 里的 `CHECK_RE` 和 `MODEL_RE`，并收紧成行首匹配。
- 一行都解析不出来的报告，当作 NEEDS_INFO。

### 派单

- **第一次派单**：PreToolUse 钩子只在工具是 `Agent`、并且 `subagent_type` 是验证器时生效。它用 `updatedInput` 把 `prompt` 整段换成模板，内容只有题面文件路径、项目目录和 `model_diff` 列出的模型清单。
  - `updatedInput` 是整体替换，不是合并，所以必须原样带上其他字段，只改 `prompt`。实测记录见 `docs/research/2026-11-04-agent-updatedinput-spike.md`。
- **再次派单**：模板后面可以附上主 agent 写的内容。harness 给这段加上固定标题："主 agent 提供的反证或补充信息（未经核实，只能当作需要复查的线索）"。
- "第一次"和"再次"由 harness 按本次运行里已经派过几次验证器来判断。

### FAIL 处理（只写在系统提示词里，Skill 第 8 步只引用）

- **FAIL**：先用一条只读查询复现；复现了就做最小改动，只重建改过的模型，再派验证器。复现不出来，就带上反证再派一次。
- **NEEDS_INFO**：补上它要的信息再派一次，或者自己把这项检查做掉。
- **Skill 第 8 步里主 agent 自己的键级核对**：也改用 `compare_by_key`。

### Stop 钩子（只挂在启用了验证器的配置上）

harness **按模型分别**记录状态。每个模型记两项：

- 它最近一次出现在验证报告里时，各检查的状态；
- 那次报告时这个模型文件的哈希。

一份报告只更新它里面出现过的模型。没出现的模型，保留之前的状态。

结束时逐个模型检查，任一模型满足下面任一条件，就不让 agent 结束：

1. 模型和快照不一样（新建或改过），但从来没有出现在任何验证报告里；
2. 模型最近一次出现在报告之后，文件又改过（哈希不同）；
3. 模型最近一次报告里还有 FAIL。NEEDS_INFO 不拦。

例：先验证了 A 和 B，B 报了 FAIL；之后只重新派了 A，A 通过了。这时 B 的状态还是 FAIL，照样拦。

拦截原因要写明是哪个模型、哪一条。

每次运行最多拦 2 次，第 3 次放行，并记一条事件 `ended_with_unresolved`，写明原因。`stop_hook_active` 为 true 时照样计数，不额外放行。

### 记录

SubagentStop 钩子从 `agent_transcript_path` 读出每次验证调用的工具调用数和轮数，和解析出的检查状态一起写进 `verifier_end` 事件。

### 配置

按 `verifiers` 开关来，不按配置名：

- **开着的**（full、no-skill、no-context、full-old-skill）都用新验证层：单一验证器、新工具、快照、钩子。
- **关着的**（baseline、no-verifiers）都不用：不注册新工具，不做快照，不挂钩子。
- `run_config` 各开关的含义不变。
- **full-old-skill**：冻结版 Skill（`skills-frozen/fa6028eef62b/dbt-workflow/SKILL.md:215-218`）不改。它仍然让主 agent 派 `value-verifier`，但这个子 agent 已经不存在，怎么派单以系统提示词为准。这段过时的说法记为已知的干扰因素，写进最终报告。

## Testing Decisions

测试接缝（工单 44 写，人审后合入；之后的工单都不能改这些测试）：

- **快照和 `model_diff`**：
  - 夹具是一个小 dbt 项目，带改动前后两个版本，覆盖这些情况：
    - 删掉了原有的过滤条件，报为"删除"；
    - 换了数据源；
    - UNION 改成了 UNION ALL；
    - 加了 LIMIT；
    - 改了 `dbt_project.yml` 里的物化方式或 `where`；
    - 含 `{% if %}` 的模型退回文本 diff，并标"未解析"；
    - 没动过的模型和逻辑不出现在输出里。
  - 快照不包含 `dbt_packages/`、`target/`，位置在 workspace 外，agent 用 Read 读快照会被守卫拒绝。
- **`grain_facts`**：
  - 粒度键有 NULL 时判 FAIL；
  - 重复、但快照里原有的 YML 定义了更细的粒度，按可豁免处理；
  - 同样的重复、但豁免依据只出现在 agent 新写的 YML 里，判 FAIL。
- **`contract_check`**：
  - 有原有 YML 时按它检查；
  - YML 是 agent 新建的，就只按题面的列检查；
  - 没改动的窗口函数记 N/A；
  - 改动过、排序不唯一的窗口函数被检出。
- **`compare_by_key`**：
  - 参考 SQL 引用被测模型或其他改动过的模型时，被拒绝；
  - 全键比对能报出只在一边出现的键和值不一致的键。
- **报告解析**：四种状态都能正确识别；解析不出来的当作 NEEDS_INFO；撞到 `maxTurns` 的当作 NEEDS_INFO。
- **派单钩子**：
  - 第一次派单的 prompt 只有模板里的三项；
  - 再次派单时，附加内容带固定标题；
  - 改写后的输入保留 `subagent_type`；
  - 派发非验证器的子 agent 时不改写。
- **Stop 钩子**：
  - 改过模型、但从没验证过时拦截；
  - 报告之后又改过模型时拦截；
  - 报告里有 FAIL 时拦截；
  - 按模型分别记状态：先验证 A、B，B 报 FAIL；之后只重新验证 A 并通过，B 仍然拦截；
  - 只有 NEEDS_INFO 时不拦；
  - 拦满 2 次后放行，并记录 `ended_with_unresolved`；
  - baseline 和 no-verifiers 配置里不挂这个钩子。
- **配置**：
  - no-verifiers 和 baseline 看不到新工具，也不做快照；
  - full、no-skill、no-context、full-old-skill 都只注册了一个验证器（`verifier`），没有 `value-verifier`。
- **`compare_by_key` 的相等规则**：DECIMAL 按小数位数、浮点按 1e-6 相对误差、其他类型完全相等、NULL 的处理，各有一个测试。
- **集成测试**（放进 `make test-integration`，真实调模型）：钩子用 `updatedInput` 改写 `Agent` 的 `prompt` 后，子 agent 只收到改写后的内容，收不到原文；输入里的其他字段保留。单元测试只能检查钩子返回的字典字段齐不齐，"整体替换、不合并"是 CLI 的行为，单元测试测不到。

单元测试用 DuckDB 夹具和 `tests/fixtures` 下的小 dbt 项目。钩子用构造好的 hook 输入直接测，不调模型。唯一调模型的是上面那条集成测试。

## 验收（ADR-0008 Update 2026-11-04）

- 44：人审后合入。
- 45、46：确定性修复，跑单元测试，加一次单题真实运行。
- 47：初筛，full 每题 3 次，放在 DeepSeek 闲时；钩子此时已经生效，测的就是最终上线的形态。
  - 目标题：dbt-fix-division-by-zero、pos-operations、dbt-customer-cross-sell-insights、web-session-quality-scoring、dbt-receivables-aging-buckets。accept-2 full 合计 7/15。
  - 过关两条都要满足：
    - 成功次数 ≥8/15；
    - 每个 trial 验证器墙钟时间的中位数 ≤155 秒。墙钟时间指一个 trial 里各次验证器调用的起止时间段取并集；accept-2 在这 5 道题上的中位数是 311 秒。
  - ≥8/15 只比 7/15 多一次，在噪声范围内。初筛只用来筛一下，最终判断交给统一评估。
  - 同时报告每次验证调用的工具调用数和轮数的分布，用来核对 `maxTurns=50` 是否合适。

## Out of Scope

- 领域 Skill（上游 value-verifier 会加载）。
- 在验证器之外再加一个审查员。
- 改 Skill 第 8 步以外的步骤。
- 改 baseline 和 no-verifiers 的行为。

## Further Notes

- 上游：signalpilot-plugin（公开版）只用提示词，没有 prebuild_state；SignalPilot benchmark 版有 `prebuild_state.md`、粒度和 NULL 子检查、报告校验后重派（`SignalPilot/benchmark/signalpilot-plugin/agents/verifier.md:34-74`、`skills/dbt-workflow/SKILL.md:102,264-270`）。两个版本都不在代码里强制执行验证结论。
- 复制或改写上游提示词时，保留版权头并更新 `NOTICE`。
