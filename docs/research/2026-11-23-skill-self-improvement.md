# Skill 小范围自动改写：本仓库现状与上游、业界做法取证（2026-11-23）

目的：为"从开发集失败自动起草 Skill 修改 → 在开发集上筛 → 通过的开 PR 等人批"做取证。只列事实与分歧，不做设计决定。访问日期除注明外均为 2026-11-23。`未确认` 表示没读到或没核实。

## 1. 本仓库现状（已读文件）

### 1.1 失败归因 `evals/attribute.py`（691 行）

- 只处理开发集失败：`dev_failures()` 取 `manifest["dev"]` 内的失败 trial，基础设施错误单列不归因（attribute.py:493-500）；开发集外的 trial 只计数、不读事件文件（模块文档 1-6 行；测试 `test_holdout_trials_are_never_read`，tests/test_eval_attribute.py:125）。
- 每个失败一次 judge 调用：读事件轨迹（`render_trace`，超 60,000 字符保留头尾）、该次运行用的 Skill 全文、任务说明和验证器输出（可选），回一个 JSON：`failure_mode`(FM1–FM4)、`evidence`、`skill`、`skill_quote`、`suggestion`（JUDGE_PROMPT，attribute.py:63-105）。
- 回复校验 `check_reply`（:350-370）：标签必须在 FM1–FM4；`skill_quote` 在该 Skill 原文里逐字出现（空白归一后比较）；`suggestion` 里不能含取自基准的具体名字（`specific_names`/`named_in`，名字来自 manifest、事件里看到的表/列、任务说明和验证器输出）。不合格重试一次并列出问题（`MAX_ATTEMPTS = 2`）；引用仍不逐字 → 该失败不归因；建议仍含具体名字 → 建议被扣下（`suggestion=None`）。
- 判 Skill 版本：judge 读的和引用校验用的，是该运行 version 头里 `skill_version` 对应的那份 Skill（`skill_sets()`，:145-149）；版本找不到就不归因（`test_a_run_on_an_unavailable_skill_version_is_not_attributed`）。
- 预算：默认 `$2.0`（`DEFAULT_BUDGET_USD`），`Meter` 到上限就停调、其余列为未归因；每次调用的预算上限是剩余额度。
- 输出：`attribution.json` + `report.md`（"Nothing here has been applied. Review each suggestion before editing a Skill."，:587 附近）。**不修改任何文件，没有"应用"步骤**，也没有"改后重测"步骤。
- 测试 27 个（`tests/test_eval_attribute.py`），覆盖上述全部校验、预算、密钥掩码、SDK judge 的花费记账。
- 局限（由代码得出）：提议粒度是"每个失败一条自由文本建议"，不同失败的建议之间没有合并、去重、冲突检查；`suggestion` 没有指定改哪个文件的哪一节（只有 `skill` 名和被引用的原文）；"通用"只由名字黑名单把关（不会发现没点名但仍是题目专属的描述）。

### 1.2 流程规则

- plan §3 "失败 → 改 Skill"（docs/plan.md:86）："照 SP 赛后自诊断，只看开发集失败题；agent 写报告……建议必须通用、不针对单题；人审后再提交。**不做全自动改写。**"
- ADR-0004（docs/adr/0004-ablation-holdout.md）：开发集 = data-eng-bench `fast-30`（30 题），"只用于失败归因循环：看失败原因、改 Skill、再跑"；留出集 30 题，开发期不看逐题失败细节。Update 2026-11-08：失败归因循环不给定量收益，只作"改进依据"；`fast-30` 偏难（29 题中 16 hard + 4 very_hard）。
- ADR-0005（docs/adr/0005-dab-integration.md:8,23,65）："Skill 不含任何基准专属内容"；DAB 无开发/留出之分，plan 定过"DAB 结果不能用来改 Skill"，2026-11-14 人批准一次例外，条件是每处修复通用、不含题目/数据集/评分脚本内容。
- ADR-0008（docs/adr/0008-change-acceptance.md）：
  - 起因（:3）：dev-3 里 full 57.8% 低于 baseline 68.9%，差值 95% 区间 [-20.0, -3.3]；"工单 25 按 LLM 归因一次改了 5 类 Skill 规则，只用 3 题 × 1 次验证，没测出提升，Skill 长了 8–9%"。结论：先人读轨迹，LLM 归因（FM1–FM4）"只作辅助，不单独决定改什么"。
  - 一个失败模式一个改动，写明目标题（:7-8）。
  - 改动过关三条（:9-13）：开发集每题 3 次，新版点估计不低于旧版；目标题成功次数合计上升（"总分不降但目标题没涨的改动不合入"）；回归集不退步。
  - 回归集（:14-16）：入集要 6/6；一轮低于 3/3 补跑 3 次，补跑仍 ≤1/3 才算退步；原文自己算："按单次成功率约 0.9 算，12 道里至少误判一道的概率约 9%；……小幅退步由第 1 条总分兜底"。
  - 旧版结果复用条件（:17）：version 事件字段一致、benchmark commit 和机器一致、两周内、无基础设施报错。
  - Update 2026-11-01：每张修复工单先在 PR 分支上"初筛"：只跑目标题、full、每题 3 次，目标题成功合计**严格高于**旧版同题合计；初筛不看总分和回归集；再合并验收一次。止损：第二次仍不过、或开发集迭代累计花费达线（¥300，后提到 ¥420）就停。
  - Update 2026-11-04：最后一轮修复后不再开下一轮；分数波动"如实写进报告"。
  - 留出集只在最终消融时跑，修复只能依据开发集失败。
- `evals/accept.py`（模块文档 1-30 行）已实现这三条加旧版复用判定，输出 pass / fail / rerun-regression / rerun-old；`CHANGED_BY_DESIGN = {git_commit, skill_version, skills}`（工单 83 加 `skills`，使旧版复用不因它失效）。开发集外的任务一律按数量拒绝（不点名）。
- docs/report.md "Failure attribution" 与 "Limitations"：
  - dev 集分数走势（report.md:151-168）：dev-3 full 52/90、baseline 62/90 → accept-1 full 60/90 → accept-2 full 56/90、baseline 62/90 → final full 60/90、baseline 59/90；"The code changed between these rows, so they are not repeated measurements."
  - 最终 dev 对比：full 66.7% ± 2.5，baseline 65.6% ± 2.2；full − baseline +1.1（−11.1 到 +13.3），无一个配对差值显著（report.md:115-125）。
  - 按题配对：对 baseline，full 赢 4 输 5 平 21；"Two tasks are 6.7% of the set, below the 10% at which a general fix round would have been considered"（report.md:130-145），即现有人工流程有"≥10% 的失败集中才修"的门槛。
  - Limitations（:394 起）："30 tasks × 3 trials did not separate the layers; every paired interval crosses zero."；数据集 DAB 的分数"followed changes made after seeing that benchmark's results"，已披露。
  - 本仓库此前几轮修复结果：工具与环境修复在过程指标上可见（超长输出 1.70 → 0 次/trial），**分数上看不出**；accept-1 到 final 之间 full 都在 60/90 附近。
- `skill_version`（src/data_agent/agent.py:1250-1257）：对 Skill 目录下所有文件做 sha256，取前 12 位，任何 Skill 改动都改变它；写进每个运行的 `version` 事件（agent.py:2161，Skill 层关闭时写 `"none"`）；工单 83 增加 `version.skills`（注入的 Skill 名列表）。`skills-frozen/` 存冻结的旧版 Skill，run config 通过 `skills_dir` 指向某个版本目录（src/data_agent/run_config.py:32-53），所以"新旧版本并存、可按版本回放"已有机制。

### 1.3 评测入口怎么跑子集、算分

- `ablate`（evals/ablate.py 模块文档 1-65 行）：`--split dev --task <名> --trials N --config full` 即可跑开发集的指定子集；含预检（余额、readiness、每配置冒烟 1 题）、支出闸门（预算 80% 前停）、基础设施错误续跑不计分、版本/commit/机器一致性检查、结束后 scrub + report + compare。`--split dev` 时 `diverge/<config>/` 自动出配对分歧。
- 算分：`evals/metrics.py`（data-eng-bench 算法，准确率 ± 按题标准误、pass@k；出错记 0）；`evals/compare.py`：按题成功率差，**任务级配对 bootstrap**，95% 百分位区间，区间不含 0 才算显著（compare.py:14-30，每题等权）。
- `evals/diverge.py`：两个 job 在开发集上的逐题成功数差异 + 过程指标，列出成功数不同的题（ADR-0008 "先读这些轨迹"）；遇开发集外的 trial 只计数，遇基础设施错误拒绝。
- `evals/board.py`：只跑榜单（注册表 data-eng-bench，103 题，含留出集 30 题），不适用于筛 Skill 改动。
- 成本口径（ADR-0008 Consequences）：每个改动验收约 $7–15（只跑新版）；dev-3 full 90 条估算 $14.46；dev 上 full 90 条实测 $8.83、no-verifiers $4.42、baseline $3.57（report.md:117-121）。

## 2. 上游与业界做法（逐家）

### 2.1 SignalPilot 赛后自诊断

- 位置：`~/dev/AI/data-agent-refs/SignalPilot`，commit `f5eee229`（2026-09-28）；`benchmark/prompts/post_grade_review.md`（180 行）、`benchmark/ade/post_grade.py`、`benchmark/ade/runner.py:230-250`。许可证 Apache-2.0（仓库根 LICENSE）。`signalpilot-plugin` 仓库（commit `f7b0c091`，2026-06-19）只有 skills/agents，没有自诊断脚本。
- 触发：评分后仅对失败任务（runner.py:230 "Step 6: Post-grade review (failures only)"）；用 `continue_conversation=True` 恢复**同一个 agent 会话**，再发复盘提示。
- **数据：先把金标解注入工作目录**（post_grade.py `_inject_gold`：`_gold/changes.patch`、`_gold_applied/models/`；提示 :7-12 "The files below show the CORRECT answer"）。即 SP 的诊断**不是**盲的，在 ade-bench 上直接对着答案诊断。与本仓库 attribute.py 的做法不同：本仓库 judge 看轨迹、任务说明、验证器输出，不给金标，也不把留出集写进报告。
- 提议者与粒度：agent 自己写 `failure_report.html`，含 Decision Trace、Discovery Analysis（DISCOVERABLE / CONTRADICTORY / ABSENT）、Prompting Report（"Quote the EXACT rule you followed"，:97-107）、Prompt Suggestions（每条：Problem / Current rule / File / Proposed fix / Why it helps，:136-145）。粒度是**逐条规则建议**（指向文件，文本给定）。
- 通用性约束（:147-153）："Must be general-purpose (not task-specific)"、"Must not contradict existing rules in other files"、"Prefer narrowing existing rules over adding new ones"、"Prefer tool-level fixes over prompt rules"。
- 评审与应用：脚本里没有自动改 Skill 的代码，报告由人读（未确认 SP 内部是否另有人工流程文档；`grep` 仓库 docs 未发现）。没有重测筛选步骤。
- 防污染：把 Skill/agents/系统提示做词表扫描，含 `benchmark|Spider2|gold|grader|grading|…|post-grade|prompt` 等词即构建失败（`benchmark/Dockerfile.dbt-agent-opus5-state-audit:23`）；plan 里已引用。
- 本仓库已借鉴：FM 归因、逐字引用 Skill 原文、"必须通用"（docs/research/2026-10-11-upstream-agent-design.md:41）。

### 2.2 ACE（Agentic Context Engineering）

- 来源：arXiv:2510.04618（提交 2025-10-06，v3 2026-03-29），https://arxiv.org/abs/2510.04618 ，HTML 版 https://arxiv.org/html/2510.04618 ；代码 https://github.com/ace-agent/ace ，Apache-2.0（页面标注，访问 2026-11-23）。
- 粒度：增量条目。上下文是"structured, itemized bullets"，每条有 id 与 helpful/harmful 计数；Curator 输出小的 delta，由"lightweight, non-LLM logic"确定性合并：新增条目、原位更新、语义嵌入去重；整段重写被点名为失败模式（§2.2：全文重写导致 "context collapse"，示例 18,282 → 122 token，准确率跌到无适应基线以下；"brevity bias" 指优化趋向短而泛的提示）。
- 谁提议谁评审：Generator 跑轨迹、Reflector 从成败里抽教训（可迭代多轮）、Curator 把教训写成 delta。三个角色通常同一个模型；**没有独立的评审者对每条 delta 做通过/拒绝**（HTML 版 §3，我读到的页面未描述接受前检查；README 同样不描述每条更新的闸门）。
- 数据与划分：离线适应在训练集上优化、测试集上评估；在线适应逐个测试样本"先预测再更新"（batch=1）；最多 5 轮 Reflector、5 个离线 epoch（§4.1/4.2）。仓库 README：离线模式有 train/val/test，保存 `best_playbook.txt`（暗示用验证集选整本 playbook，而非逐条验收；划分比例未给）。
- 监督：有/无金标两种结果都报；无标签时靠执行反馈（代码成败），论文记录"缺少可靠反馈时在金融任务上可能退化"（§4.3/4.4）。
- 防改坏：靠增量+确定性合并避免整体塌缩；有去重/裁剪（相似度阈值默认 0.9）；**没有逐条回归验证**（未确认，页面未覆盖最后约 31k 字符，已读范围内未见）。
- 人在哪：论文流程里不涉及（全自动）。

### 2.3 DSPy GEPA

- 来源：arXiv:2507.19457（Agrawal, Khattab 等），https://arxiv.org/abs/2507.19457 ，HTML https://arxiv.org/html/2507.19457 ；代码 https://github.com/gepa-ai/gepa ，MIT（我克隆 `ce5b0d6e`，2026-11-09，LICENSE 为 MIT，2026-11-23 核实）。
- 粒度：整段提示（每个模块一段指令）重写，不是增量条目；一次改一个模块，模块轮流选（§3 "module to update is chosen round-robin"）。另有"系统感知 merge"把两条谱系的模块拼起来（论文 §4 Obs.5：+Merge 在部分任务最多 +5%，Qwen3 8B 上反而退步）。
- 谁提议谁评审：reflection LM 读当前提示、轨迹、得分与文字反馈（可来自编译器报错、失败的评分项等）后提出新指令；**评审是数据**：先在小批量上比较，再上验证集。
- 数据：训练集拆成 feedback 集和 Pareto（验证）集（Algorithm 1，`n_pareto`）；小批量 `b` 是超参，页面未给值；"works with as few as 3 examples"（README）。
- 接受条件：子样本（minibatch）上新提示的**分数之和**严格大于父提示才进候选池，然后在 Pareto 验证集上全量评估（§3 与 Algorithm 1；代码 `src/gepa/strategies/acceptance.py:44-61`：`StrictImprovementAcceptance` 默认，另有 `ImprovementOrEqualAcceptance`）。
- 防过拟合/选择：每个验证实例各自维护"最佳得分"，保留"在至少一个任务上最优"的候选，剔除被严格支配的，下一个父代按各自领先的任务数加权采样（§3.1）；最后返回验证集平均分最高的候选；"The majority of GEPA's rollout budget is spent on validation"（§4）。论文 §5.1 说明：推理时搜索可以把全部任务同时当训练和 Pareto 集，是有意过拟合。
- 噪声处理：论文和 `api.py` 只给 `val_evaluation_policy`（默认 `FullEvaluationPolicy`）和上述两个接受准则；**没有重复测量、配对检验或方差估计**（代码里 grep `noise|stochastic|re-eval` 只在 engine 的接口注释里出现"Stateful or stochastic criteria"）。minibatch 和验证集都是单次评估。
- 人在哪：算法内无；产出是新提示，人决定是否采用。

### 2.4 Voyager 技能库

- 来源：arXiv:2305.16291，https://arxiv.org/abs/2305.16291 ；代码 https://github.com/MineDojo/Voyager ，MIT（commit `55e45a88`，2023-07-27）。
- 粒度：只增不改。技能是可执行代码，key 是技能描述的 embedding，value 是代码；"Voyager incrementally builds a skill library by storing the action programs"（§1）。论文未描述覆盖、编辑、版本化或冲突处理（我读到的页面范围内）。改进只发生在单个任务的 4 轮迭代里：环境反馈、执行报错、自我验证的批评一起回灌，"If the agent gets stuck after 4 rounds of code generation, then we query the curriculum for another task."
- 谁提议谁评审：agent 写代码；另一个 GPT-4 critic 做自我验证，**只有验证成功才入库**（§2.3，Algorithm A.1 `add_skill(code)` 仅在 success 为真时调用）；论文自己承认 "Occasionally, self-verification module may also fail"（§4）。
- 数据：没有开发/留出划分；评估是换新世界的零样本任务（§3.3）。入库的验证来自任务本身的环境成败，不是外部评分。
- 防改坏：入库前自验证；检索 top-5；没有淘汰/回滚（未确认）。
- 人在哪：无。
- 与本仓库的相关度：Voyager 的"技能"是代码；本仓库的 Skill 是给模型读的说明书，二者的校验信号不同，借鉴价值主要是"成功才入库"和"只增不改"这两点。

### 2.5 Anthropic skill-creator

- 来源：本机副本 `~/.claude/plugins/marketplaces/anthropic-agent-skills/skills/skill-creator`（仓库 anthropics/skills，副本 HEAD `e4bb5d96`，2026-11-16；`LICENSE.txt` 为 Apache License 2.0；注意此仓库部分 Skill 另有专有许可，我只核对了 skill-creator 目录下的 LICENSE.txt）。
- **两个不同的循环**：
  1. 改 Skill 正文：人在环的迭代（SKILL.md:167-320）。每个测试用例同时起两个子 agent：with-skill 与基线，基线在改进旧 Skill 时是改动前的快照（"Before editing, snapshot the skill … point the baseline subagent at the snapshot"，:186）；grader 对断言打分，`aggregate_benchmark` 出每个配置的通过率、耗时、token 的 mean ± stddev 和差值；analyst 读基准数据找"始终通过的断言、高方差评测"（agents/analyzer.md:218-256，例 "Eval 3 shows high variance (50% ± 40%) - run 2 had an unusual failure that may be flaky"）；人在浏览器 viewer 里逐用例留反馈；agent 据反馈重写，在新 iteration 目录里全量重跑（:308-321）。停止条件：人满意、反馈全空、或无实质进展。改动指引（:298-306）："Generalize from the feedback … Rather than put in fiddly overfitty changes, or oppressively constrictive MUSTs"；"Keep the prompt lean"；"Explain the why"。
  2. 改 `description`（触发准确度）：全自动脚本 `scripts/run_loop.py`（SKILL.md:390-394）：评测集按 should_trigger 分层 **60% train / 40% held-out test**（`split_eval_set`，run_loop.py:24-44，默认 seed 42），每条查询跑 **3 次**取触发率，最多 5 轮；`improve_description.py` 用失败案例让模型改写，并指示"we don't want to overfit … Instead, try to generalize from the failures to broader categories"（improve_description.py:127）；"`best_description` — selected by test score rather than train score to avoid overfitting"。
- 另有盲比较（comparator/analyzer，SKILL.md:325 起）供"新版是否真的更好"用，人按需调用。
- 粒度：正文整段按反馈重写（由模型决定）；description 整句重写。谁提议：模型；谁评审：正文由人看输出（外加量化基准），description 由 held-out 分数。
- 噪声：每条 3 次、报 stddev、分析器标注"高方差/可能 flaky"；**没有显著性检验**（未发现）。

### 2.6 hermes-agent 的技能自学

- 来源：`/private/tmp/claude-501/-Users-shenghuikevin-dev-AI-data-agent/ab0b6835-4ba1-4bbc-b5cf-8e797d9d04d3/scratchpad/im-refs/hermes-agent`，commit `b4bacb95`（2026-11-23），MIT（Nous Research，LICENSE 首行）。
- 机制：`agent/background_review.py`（1499 行）每轮对话后可起一个守护线程，fork 出同模型的 agent 复盘该会话，并**直接**写技能库（模块文档 1-5 行："Writes go straight to the memory + skill stores"）；`_SKILL_REVIEW_PROMPT`（:532）要求 "Be ACTIVE — most sessions produce at least one skill update"；信号是用户纠正、非平凡技巧、被加载的技能出错；偏好顺序：改已加载技能 > 改现有伞形技能 > 加 `references/` 支持文件 > 新建类级技能；规则 "Read-before-write (ENFORCED — skill_manage refuses otherwise)"；"Do NOT capture" 清单；不删技能。
- `agent/curator.py`（1220 行）：闲置触发的维护（默认 7 天间隔；LLM 合并默认关，`DEFAULT_CONSOLIDATE = False`），状态机 active/stale/archived 按使用时间戳自动流转，"never delete, only archive (recoverable); pinned skills bypass all auto-transitions"；用户可 `lock` 让某个技能不可被 agent 改。
- 回滚与审计：`tools/skill_ledger.py` 每次技能变更追加一条 JSONL（前后文件内容按 sha256 内容寻址存备份），支持单次编辑回滚；注释写明 "TELEMETRY, NOT A GATE"；`agent/curator_backup.py` 做整库快照并保留 N 份。
- 数据与筛选：**没有任何评测数据参与**；没有训练/验证划分、没有重测。信号来自用户的对话反馈。（grep `tools/skill_manager_tool.py` 对 "eval|benchmark" 只命中权限闸门 `wa.evaluate_gate`，与效果评估无关。）
- 人在哪：事后——可 lock、pin、回滚；默认不需要事前批准（`skill_manage` 的写受 `_is_background_review` 等守卫限制，细节未逐行核实）。

## 3. 对照表

| 维度 | 本仓库现状 | SignalPilot | ACE | GEPA | Voyager | skill-creator | hermes-agent |
|---|---|---|---|---|---|---|---|
| 改动粒度 | 每失败一条自由文本建议，人手改 Skill | 逐条规则建议（文件+原文+新文本） | 增量条目（id+计数），非 LLM 合并 | 整段指令重写，一次一个模块 | 只增：新技能=一段代码 | 正文整段重写；description 整句重写 | 对现有 SKILL.md 打补丁、加 references/ 文件，不删 |
| 谁提议 | judge 模型（attribute.py） | agent 自己，复盘同一会话，看得到金标 | Reflector+Curator | reflection LM（看轨迹+反馈） | 同一 agent + critic 反馈 | 模型（据人反馈或失败案例） | fork 出的同模型 agent |
| 谁评审 | 人；引用和名字由代码校验 | 人读报告（未见脚本化评审） | 无逐条评审；整本 playbook 用验证集选 | 数据：小批量+验证集 | critic 自验证 | 正文：人+量化基准；description：held-out 分 | 无；事后可回滚 |
| 筛选数据与划分 | 开发集 30 题 × 3；留出集不碰 | 评分后失败题，金标可见 | 训练/验证/测试；在线版边测边学 | feedback 集 + Pareto 验证集 | 无划分；新任务零样本 | description：60/40 train/test；正文：用户给的用例 | 无 |
| 接受条件 | ADR-0008 三条（点估计不低于旧版；目标题合计上升；回归集不退步） | 无 | 无逐条条件；验证集最优整本 | 小批量得分和严格上升，再入候选；最终取验证平均最高 | 自验证成功 | description：test 分最高；正文：人满意 | 无 |
| 防改坏 | 回归集；旧版保留可重跑；止损线；"一次一个失败模式" | "不矛盾已有规则"、"优先收窄"、词表扫描 | 增量+去重，避免整段重写塌缩 | Pareto 保留多样候选 | 只增不改 | 对比基线（旧版快照）、泛化要求、保持精简 | 读后写、归档不删、ledger 回滚、可 lock |
| 人的位置 | 读报告、改 Skill、批 PR | 读报告 | 无 | 无（采用结果） | 无 | 逐用例看输出并写反馈 | 事后（lock/回滚） |
| 防过拟合手段 | 开发/留出分离；点名检查；"通用"提示 | 要求通用；扫词 | 验证集选整本 | 验证集；Pareto | 零样本新任务 | 60/40 划分；提示语要求泛化 | 类级技能命名约束（"不能是 PR 号/错误串"） |
| 噪声处理 | k=3 点估计；回归集补跑；bootstrap 只在留出集终评 | 无 | 无（页面未见） | 无（单次评估，和之和比较） | 无 | 每条 3 次；报 stddev；analyzer 标 flaky | 无 |
| 许可证 | 本仓库 | Apache-2.0 | Apache-2.0 | MIT | MIT | Apache-2.0（skill-creator 目录） | MIT |

## 4. 多数做法

- 提议者都是 LLM，读的是执行轨迹/失败样本，而不是只读最终答案（SP、ACE、GEPA、hermes、skill-creator 的 description 循环）。
- 提议都被要求"通用"并显式防过拟合措辞（SP、skill-creator、hermes）；单靠提示语约束，真正有机械检查的只有本仓库（逐字引用 + 名字黑名单）和 SP（构建时词表扫描）。
- 倾向增量/局部修改，避免整段重写丢信息：ACE（明说整段重写造成塌缩）、hermes（打补丁、不删、归档）、Voyager（只增）、SP（"优先收窄现有规则"）、skill-creator（"Keep the prompt lean"）。GEPA 是例外，整段重写，但靠验证集筛。
- 有训练/验证分离的几家（ACE 离线、GEPA、skill-creator description 循环）都用验证/测试集分数选最终版本，而不是训练集分数。
- 上述每一家的评测都是单轮、有限重复；GEPA/ACE/Voyager 没有显著性检验（未发现）。
- 没有一家是"自动改完直接部署且无事后手段"：要么有数据筛（GEPA/skill-creator），要么有回滚与锁（hermes），要么有人读（SP、skill-creator 正文）。

## 5. 分歧与必须由人定的点

1. 评审信号：GEPA/skill-creator(description) 用数据筛；SP 与 skill-creator(正文) 靠人读；hermes 不筛。本仓库 ADR-0008 已选数据筛+人批，但开发集 30 题×3 的功效（见 §6）决定了数据筛能识别多大的改动，这个取舍要人定。
2. 诊断时能不能看到答案：SP 注入金标；本仓库 judge 不给（verifier 输出可选）。是否允许把验证器输出/评分脚本信息给提议者，涉及 ADR-0004/0005 的边界，需要人定。
3. 提议粒度：整段重写（GEPA、skill-creator）vs 增量条目（ACE、hermes、SP）。本仓库 Skill 单文件 4–25 KB（dbt-workflow 25,203 字符，dbt-write 21,000），ADR-0008 起因里记录"Skill 长了 8–9%"且没测出提升；"只增不改"会让 Skill 持续变长，ACE 靠去重、hermes 靠 curator 控制，本仓库没有对应机制（未确认是否要）。
4. 一次提议多少个失败：ADR-0008 要求"一个失败模式一个改动"并有目标题；GEPA 一次一个模块、小批量；ACE 每个 batch 的 delta 可含多条。自动流程批量合并多个失败的建议会与 ADR-0008 的"一个失败模式一张工单"冲突，需要人定。
5. 接受条件的严格度：ADR-0008 初筛要求目标题成功合计严格高于旧版（Update 2026-11-01），合并验收又有"点估计不低于旧版"；GEPA 默认也是严格大于（`strict_improvement`）但只比较小批量之和；skill-creator 靠 held-out 分最大。"严格大于"在 3 次重复下的假阳性率见 §6，是否再加重复/更严格的条件需人定。
6. 目标题当训练集又当验证集：ADR-0008 的"目标题必须涨"用的正是触发提议的那几道题，即训练集本身。GEPA、skill-creator、ACE 都把训练与验证分开；本仓库开发集只有 30 题，且开发集同时是诊断与回归的来源。是否需要从开发集中拆出一个仅验证的子集（会进一步减小每份样本），属于需要人定的设计。
7. 人批的位置：提议后（读建议）还是筛后（读 PR）。hermes 选事后撤销，本仓库 plan §3 选事前批准。
8. 花费与止损：现有止损线是累计花费（¥300 → ¥420）与"第二次仍不过就停"；自动流程每轮约 $7–15（ADR-0008），上限由人定。
9. 失败来源范围：ADR-0005 禁止用 DAB 结果改 Skill（2026-11-14 的一次例外已用）；自动流程只吃 data-eng-bench 开发集还是也吃别的数据，需要人定。

## 6. 噪声问题：本仓库事实与各方做法

本仓库已确认的事实：

- 规模：开发集 30 题 × 3 次 = 90 trial/配置；单配置准确率标准误约 ±2.2 到 ±2.5 个百分点（report.md:117-119），即约 2 个 trial；两个独立配置的差值标准误按独立估算约 3.5 个点（约 3 个 trial）。（后一数字为我据前者的推算，未单独核实。）
- 已观察到的波动：full 在 accept-1/final 都是 60/90，baseline 在 accept-2 62/90 与 final 59/90（代码有改动，report 说"moved within noise"，不是重复测量）；某题 3 次的 pass 数在新旧对比里常见 0/3 ↔ 2/3（如 report.md:126-135 的两道双配置丢分题，也有 3/3 对 0/3 的赢面）；dev 的 9 个长上下文 trial 对比里 5/9 对 6/9 被定性为"within trial-to-trial variation"（report.md:424 附近）。
- 现有判定规则对噪声的处理：回归集 12 题用"补跑 3 次"并自己估计 ~9% 误判；总分用点估计兜底；显著性检验（配对任务级 bootstrap）只用于留出集终评和 report 里的对比，**ADR-0008 明确不对开发集要求区间下限大于 0**："30 题 × 3 次功效不够，好改动也可能永远过不了"（Considered Options，:22-24）。
- "目标题成功合计严格高于旧版"：若目标题 n 道各 3 次、旧版和新版真实成功率相同，则新版合计"严格大于"的概率约 0.5 减去平局概率，不是小概率事件——这是据判定规则的推理，不是本仓库测过的数；ADR-0008 的 Update 2026-11-04 提到"推理变化与分数波动不阻止进入正式评测"，说明已认识到这一点（具体假阳性率未算，未确认）。
- 此前的人工修复："Six development tasks scored 0 in both configs"，其中五题读成任务说明与评分脚本不一致，不修（report.md 的 Failure attribution）；已合入的 Skill 规则改动（工单 25）没测出提升。

各方做法对噪声：

- GEPA：小批量上用单次得分之和做"严格大于"门，再用全验证集平均选择；预算大头花在验证上（§4）；Pareto 保留"在某任务上最好"的候选，起到不让单一噪声均值决定的作用，但不估计方差。
- skill-creator：每条 3 次取触发率；基准报告 mean ± stddev，analyzer 要求标注高方差或 flaky 的用例（agents/analyzer.md:218-256）；选版本靠 held-out 分，不做检验。
- ACE：论文用多 epoch 复访同样的训练查询来"强化"上下文，并报告多次实验的平均；已读范围内没有统计检验（未确认）。
- Voyager：用多个随机种子/多次运行报告里程碑，不是用于接受判据（我读到的页面没覆盖，未确认）。
- hermes / SP：无。
- 统计方法的一般来源：Evan Miller《Adding Error Bars to Evals》（arXiv:2411.00640，2024-11-01，https://arxiv.org/abs/2411.00640 ）："treats evaluation questions as samples from an unseen super-population"，给出比较两个模型与规划评测实验（功效）的公式；我只读到摘要，正文的具体建议（配对差、同题重复采样）本次未核实原文，ADR-0008 的开头已引用该文做"同题配对比较"依据。本仓库 `evals/compare.py` 已实现任务级配对 bootstrap。

## 7. 未确认

- ACE、GEPA、Voyager 论文各只读到 HTML 页面前约 100k 字符（页面总长 131k/181k/148k），之后部分（附录、消融）未读；GEPA 的 minibatch 大小、`n_pareto` 的实际取值未见；ACE 各数据集的 train/val/test 划分比例未见；ACE 是否有"整本 playbook 回滚/逐条验收"在已读范围内未见。
- Voyager 论文的"多次运行取平均"具体口径未读。
- 开发集和重复次数下，"严格大于"门槛的假阳性率与能识别的最小改动幅度没有算过；本文没有做模拟。
- SP 失败报告是否有后续脚本化的 Skill 修改流程：仓库里没找到，不等于没有。
- hermes 的写入守卫（`skill_manager_guards.py`）细节、技能写入是否有用户确认步骤，未逐行读。
- anthropics/skills 公开仓库与本机副本的一致性：我读的是本机 marketplace 副本（HEAD `e4bb5d96`，2026-11-16），未联网对比上游最新提交。
- 本仓库 `attribute.py` 的 judge 实际质量（真实报告里的引用通过率、建议被扣下的比例）：`runs/dev-3-attr/report/report.md` 存在（ADR-0004 Update 提到），本次未读（该目录是开发集失败细节，且本次不调模型、不重算）；因此"自动起草的建议里有多少可用"这个事实未确认。
