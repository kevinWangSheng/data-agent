# 验证器墙钟根因：轮数，不是工具（2026-11-05）

依据：`runs/t47-screen2`（5ab5f6a，full，5 题 × 3，开发集）15 个 trial 的事件文件，每个事件带 `parent`、`tool_timing.duration_ms`；加源码阅读。脚本和中间输出在本机 `runs/t47-screen2/diag/`（不进 git）：`breakdown.py`（每次验证调用的拆分）、`parallel.py`（同轮并发与 compare_by_key 耗时）、`redundancy.py`（派单链、冗余、分步 LLM 间隔、报告尾巴）、`components.py`（汇总占比）、`turns_by_kind.txt`。没有调模型，花费 $0。源码行号按 5ab5f6a（工单 47 分支，PR #48）：主 checkout 的 `agents/verifier.md`、`verifiers.py`、`verifier_v2/hooks.py`、`dispatch.py` 还是合入前的版本，行号对不上；`stop.py`、`contract.py`、`compare.py`、`verifier_tools.py` 两边相同。所有口径：一次"验证调用"= 主 agent 一次 `subagent_type="verifier"` 的 Agent 调用，起止取 `tool_start`/`tool_end`；一"轮"= 子 agent 一批工具调用（下一批的第一个 `tool_start` 在上一批全部 `tool_end` 之后）；子 agent 没有 thinking/text 事件，两批之间的空隙就是模型生成时间。

## 0. 结论

- **根因是验证器每次调用的轮数**：27 次验证调用共 740 轮，设计上只需要 5 轮（读题、contract_check、grain_facts、model_diff、compare_by_key）加写报告；实际中位数 21 轮、p90 38、最大 50（撞 `maxTurns`）。多出来的轮几乎全是 `query_database`/`list_tables` 探索（482 轮，65%）。每轮模型生成中位数 4.6 s、均值 8.5 s（deepseek-flash），所以墙钟 71% 是模型时间，工具只占 27%。
- **第二个乘数是重派范围**：派单模板把快照以来改过的全部模型都列进去（`dispatch.py:21`，`hooks.py:92` 用 `changed_models(workspace, snapshot)`），验证器提示词要求"verify every model the dispatch message lists"（`verifier.md:21`）。pos-operations 每次 FAIL 后重派都重验 21 个模型，哪怕主 agent 写明"只有 1 个模型变了"或"没有文件变"。上游 SignalPilot 只重派改过的模型（`benchmark/signalpilot-plugin/skills/dbt-workflow/SKILL.md:270`）。
- 工具侧已不是瓶颈：compare_by_key 单独跑 5 s，但它是 CPU 密集型，同一轮并发 6 个以上时每个 21 s，总量不变；pos 一次调用 21 个模型至少 105 s 的工具时间，这是 4 步设计里按模型数线性的部分。
- 155 s：按现在的模型延迟，1 个模型一次调用的下限约 60 s（实测最好 93 s）；4–5 个模型约 120–150 s；21 个模型约 400 s 以上。中位数 ≤155 要靠"探索轮数砍到每模型 1 轮 + 报告缩短 + 重派只验改过的模型"三条一起，cross-sell（4 模型）才能落到 155 内，pos 永远到不了但不影响中位数。单改任何一条都不够（推断，§5）。

## 1. 墙钟拆分（问题 1）

27 次验证调用（含 2 次被 Harbor 4000 s 掐断的），`components.py`：

| 成分 | 秒 | 占比 | 说明 |
|---|---|---|---|
| 模型生成：探索/其他轮之前的空隙 | 5508 | 48.5% | 532 轮，中位 6.3 s，p90 24.7 s |
| 模型生成：写参考 SQL 的轮之前 | 1544 | 13.6% | 103 轮，中位 13.5 s，p90 27 s；生成速度中位 481 字符/s |
| 工具执行（同轮并发去重叠） | 3071 | 27.1% | compare_by_key 376 次 5215 s、query_database 1191 次 1028 s、contract_check 31 次 666 s、grain_facts 306 次 601 s |
| 报告尾巴（最后一个 tool_end 到 Agent 结束） | 1067 | 9.4% | 1 模型 7–66 s，5 模型 23–47 s，21 模型 40–95 s；输出 240–740 字符/s |
| 启动（Agent 开始到第一次工具调用） | 158 | 1.4% | 中位 4.6 s：PreToolUse 改写派单时跑 `changed_models`+`model_hashes`（`hooks.py:92,98`）加子 agent 起动 |

- 每轮空隙：n=863，中位 4.6 s，p90 21.9 s，均值 8.5 s。主 agent 同口径中位 2.4 s、p90 10.3 s；验证器更慢是上下文更大（推断，没有子 agent 的 token 数）。
- **并行**：同一轮多个调用确实并发（446 个多调用轮，span/sum 中位 0.57；工单 48 的 `readOnlyHint` 生效）。但 compare_by_key 耗时随并发线性上升：并发 1 时中位 5.0 s，2 → 5.6，3 → 6.4，4 → 8.8，5 → 12.9，≥6 → 21.4 s（p90 38 s）。`tool_end−tool_start` 和 `duration_ms` 差 <0.1 s，不是批次假象。所以它是 CPU 密集（容器 DuckDB 2 线程，见 foundation-latency-audit §7；Python 侧 sqlglot 解析与每次调用重跑一遍 `model_diff`（`verifier_tools.py:1574`）也占份额，两者分摊未确认），并发不省墙钟：一次 pos 调用 21+ 个 compare_by_key 的工具并集是 165–379 s。
- 串行等待：主 agent 在验证器窗口内没有任何工具调用（15 个 trial 都是 0），验证器是纯阻塞。

## 2. 轮数与冗余（问题 2）

每次调用按轮的类型（`turns_by_kind.txt`，中位数）：读题 1，contract_check 1，grain_facts 1，model_diff 1（9 次调用漏调），**探索（query_database/list_tables）15**，compare_by_key 2。

- 探索的内容（`redundancy.py` 按 SQL 粗分）：1191 次 query_database 里 746 次是探针（源表状态分布、去重计数、SUM 对账），121 次 `SELECT * … LIMIT n` 看模型输出，226 次把参考 SQL 先干跑一遍，33 次查目录；64 次报错。例：`pos-operations__usBkExJ` 第 2 次调用（最终 PASS）144 次 query_database，其中 96 次在第一个 compare_by_key（seq 2008）之前。读 SQL：`dbt-customer-cross-sell-insights__Jwnx5GC` 有 14 轮 Read/Glob/Grep，提示词明说"the model's SQL stays unread"（`verifier.md:83,145`）。
- 参考 SQL 重写：compare_by_key 每模型 >1 次的比例，小题 0–1 个模型，pos 每次调用 7–17 个模型调 2–4 次；报错重试只有 10 次，其余是列名/别名没对上再调（提示词 `verifier.md:155-157` 要求"fix the aliases and call again"）。
- 重派：15 个 trial 共 27 次验证调用。1 次：receivables ×3、division ×2、cross-sell ×3；2 次：division g5SF2vv（FAIL→PASS）、web ×2（FAIL→PASS）、pos usBkExJ（NEEDS_INFO→PASS）；3 次：web JdBYs8u（FAIL→FAIL→NEEDS_INFO）；4 次：pos HkugDi2（FAIL×3→掐断）、pos phUCrZZ（FAIL×3→掐断，另有 3 次 general-purpose 子 agent 抄文件 303 s，也算进了报告的 Agent 并集）。全部重派由 FAIL 或 NEEDS_INFO 触发；Stop 拦截没有事件记录，次数未确认。
- 重派时的重验：每次重派 grain_facts 都是 21 次、compare_by_key 21+ 次。`HkugDi2` 第 4 次派单原文"No model files have changed since your last report. Only the CHECK 3 NULL-key FAILs need re-judging"（seq 2255），验证器仍跑了 21 grain + 21 compare + 79 query，756 s 后被掐断；`phUCrZZ` 第 7 次"Only fct_payment_analysis changed"（seq 3147），同样 21+21+21。`verifier_end.turns` 分布：p50 21、p90 38、p95 41、最大 50（1 次撞上限，usBkExJ 第 1 次）；`tool_calls` p50 62、p90 206、最大 237。

## 3. pos-operations（问题 3）

| trial | 验证调用 | 每次墙钟 s | 模型数 | 轮数 | 工具并集 s | 其中 compare_by_key 次数/总秒 |
|---|---|---|---|---|---|---|
| HkugDi2 | FAIL, FAIL, FAIL, 掐断 | 606 / 789 / 952 / 756 | 20 / 21 / 21 / 21 | 37 / 39 / 40 / 51 | 180 / 230 / 276 / 237 | 29/272, 35/517, 34/926, 29/777 |
| phUCrZZ | FAIL, FAIL, FAIL, 掐断 | 652 / 1126 / 493 / 494 | 21 ×4 | 38 / 55 / 24 / 31 | 219 / 312 / 165 / 172 | 34/421, 40/578, 24/332, 28/183 |
| usBkExJ | NEEDS_INFO（撞 50 轮）, PASS | 1028 / 729 | 21 / 21 | 65 / 44 | 379 / 214 | 32/318, 30/508 |

- 成本按模型数线性：一次调用的墙钟 ≈ 90 s + 25–30 s/模型（1 模型 93–216，4 模型 284–365，5 模型 194–357，21 模型 493–1126）。每模型的 25–30 s 里：compare_by_key CPU 5 s（并发不省）、grain 1–2 s、写参考 SQL 2–3 s、报告 1.1 k 字符约 4 s、探索 1–3 轮各 6 s。所以是 4 步设计里"每模型一份独立参考 SQL + 全键比对 + 7 行报告"的线性成本，再乘以重派次数（2–4）。
- 每次重验全部 21 个模型（§2），而派单文字里改动的只有 0–8 个；三个 trial 合计 10 次验证调用里至少 6 次是整体重验。
- contract_check 一次 37–92 s（21 模型；内部先跑整份 `model_diff`，`contract.py:42`，再对每个改过的窗口函数发查询），grain_facts 21 个一批 16–45 s。
- 两次掐断：HkugDi2 第 4 次和 phUCrZZ 第 7 次派单时 trial 已用去约 3200–3500 s，是第 4/7 次子 agent；工单 54 的截止期 deny 在 5ab5f6a 还没合入，之后这两次会被拒掉，但验证器本身的用时不变。

## 4. 对照提示词和设计（问题 4）

| 观察 | 来源 |
|---|---|
| 探索轮多 | `verifier.md:133-134` "Use `list_tables` and `query_database` as needed to find the source tables and check your query runs; keep exploration short"——上限没有数字；Step 4 还要求参考 SQL "computed from the tables that existed before the run with the task's own rules"，模型为此逐表探源、逐条干跑 |
| 参考 SQL 反复重调 | `verifier.md:152-157`：别名没对上要"fix the aliases and call again"；`compare.py` 只比同名列 |
| 报告长 | `verifier.md:168-191`：每模型 7 行、每行要 quote 计数和"first examples"；pos 报告 22–30 k 字符，按 300 字符/s 要 60–95 s |
| 重派重验全部模型 | `dispatch.py:16-22` 模板列出 `changed_models(workspace, snapshot)`（`hooks.py:92`，快照以来全部）；`verifier.md:21` "Verify every model the dispatch message lists"；`hooks.py:44-48` BLOCK_INSTRUCTION 让主 agent "dispatch the verifier again for every model named here"，但改写后的派单仍是全量。Stop 状态是按模型记录并合并的（`stop.py:58-69`），部分报告在机制上可行 |
| 重派次数 | `verifiers.py` VERIFIERS_PROMPT：FAIL 复现不出来就"dispatch the verifier again and put the counter-evidence"；Stop 规则 3 任一 FAIL 就拦（`stop.py:86-89`）；pos 的 CHECK 3 NULL 键和 CHECK 7 在反证下仍 FAIL，于是第 3、4 次派单 |
| 工具并发不省时 | compare_by_key 每次调用重跑 `model_diff`（`verifier_tools.py:1574`）+ sqlglot + 全外连接 SQL，CPU 密集；容器 2 线程 |
| 上游对照 | SignalPilot benchmark 版验证器（`data-agent-refs/SignalPilot/benchmark/signalpilot-plugin/agents/verifier.md`）同样要求"one check for all supplied models in a single turn"；它没有"独立参考 SQL"一步（值验证由 value-verifier 的 `verify_model_values` 做），报告只有 CHECK 1–5；SKILL 第 8 步只对改过的模型重派（`SKILL.md:270`）。上游没有轮数或探索上限，也没有验证器耗时数据（未确认） |

## 5. 155 s 能不能到（问题 5）

**事实**：15 个 trial 的验证器并集排序 93, 106, 113, 117, 155, 284, 294, 365, 377, 429, 462, 925, 1757, 2347, 2574，中位 365。要中位 ≤155 需要 8 个 trial ≤155：receivables ×3、division ×2 已在内，division g5SF2vv（377，两次调用）、cross-sell ×3（284–365，各一次调用、4 模型）是必须拿下的候选；web（5–6 模型、2–3 次调用）和 pos 不用指望。

**下限估计**（推断，按本次观测的 deepseek-flash 延迟）：一次调用 = 启动 5 + 读题轮 6 + contract 轮 (4+3…13) + grain 轮 (3+2) + model_diff 轮 (3+3) ≈ 35–45 s 固定，加每模型：写参考 SQL 2–3 s、compare_by_key 5 s（串行）、报告 4 s、至少 1 轮探索 6 s ≈ 17–18 s。1 模型 ≈ 55–65 s（实测最好 93 s，awcCRum，5 轮，其中写参考 SQL 一轮 46 s）；4 模型 ≈ 110–120 s；5 模型 ≈ 130 s；21 模型 ≈ 400 s。零探索、零重调才到这个数，所以 4–5 模型题 155 s 几乎没有余量，1 次派单是前提。

**杠杆**（按对中位数的预期节省排序；省时按 §1 的成分估算，推断）：

1. **把探索压到每模型约 1 轮**：提示词给硬上限（例如"query_database 总共不超过模型数 + 2 次，不看模型输出，不干跑参考 SQL；列名只从 contract_check/list_tables 取"），并删掉 Read/Glob/Grep 工具（`verifiers.py` READ_TOOLS）只留 Read 读题。省：cross-sell 每次 100–180 s（探索 15–20 轮 + Read 9–14 轮），web 60–150 s，pos 每次 150–300 s。风险：参考 SQL 更容易写错列名或表，CHECK 7 的 NEEDS_INFO/重调可能上升；需要看成功率。
2. **重派只验改过和 FAIL 的模型**：派单模板改成"上次报告之后改过的 + 上次 FAIL 的"（`dispatch.py`，用 `hooks.py` 已有的 `dispatched` 指纹和 `state`），提示词相应改成"verify the models listed"。Stop 状态按模型合并已支持。省：web 每 trial 100–250 s（第 2、3 次调用只剩 1–2 个模型），pos 每 trial 800–1500 s，division 无。对中位数的作用是把 377 的 g5SF2vv 拉到 ~250（第二次调用只有 1 模型，省的是重验以外的探索，取决于杠杆 1）。风险：模型 A 的修复改变了未改模型 B 的语义时漏验；上游接受这个风险。
3. **报告缩短**：CHECK 行只给状态 + 关键计数，样例只在 FAIL 时给，取消 Summary 节的重复。省：4 模型 25–30 s/次，5 模型 20–35 s，21 模型 40–70 s。零风险于判定（钩子只解析状态行，`report.py`）。
4. **compare_by_key 单次 5 s 的固定成本**：每次调用重跑 `model_diff` 和工作区别名解析；把"改动模型列表"按派单缓存、只解析一次。省：每模型 2–4 s（未确认，要本地 cProfile 分摊），pos 每次 40–80 s，小题 2–15 s。
5. **启动 4.6 s**：PreToolUse 里的 `changed_models` + `model_hashes` 走 2350 模型的项目；缓存后每次派单省 3–4 s。小。
6. **模型**：每轮 4.6 s、输出 300–480 字符/s 是 deepseek-flash 的数；换更快的模型直接按比例缩所有生成项（71% 的墙钟）。换模型的延迟数据未确认，且属于评测设定不是验证层改动。

**判断**：杠杆 1+2+3 合起来，cross-sell 预计 120–160 s、division 两次调用的 trial 约 200 s、web 约 250–400 s，中位数落在 ~155 边上，能不能过线要重跑初筛看；只做其中一条到不了。pos 在 4 步设计下每次调用 ≥400 s，是 21 模型 × （参考 SQL + 全键比对 + 7 行报告）的线性成本，不是 bug；要再降只能改设计（按模型分片并行派多个验证器，或 CHECK 7 只对题面定义了计算的模型做），这超出工单 47。
