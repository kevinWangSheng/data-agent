# DAB 同模型差距：DUO、Stela 怎么做的（2026-11-21）

问题：DAB 上我们 0.7588（第 31/56，全榜中位数 0.7820）。同样用 `deepseek-flash`（DeepSeek-V4.1-Flash）的 DUO 是 0.9230，Stela 是 0.8114。差在哪里？哪些做法在 ADR-0005（Skill 和提示不写基准专属内容）下能借？

来源：上游 README 榜单（ucbepic/DataAgentBench@f13e270）；PR #132（DUO，含 `leaderboard_submissions/duo_notes.md` 和 fork release `duo-traces-2026-10-31` 的 `opencode/run_dataset.sh`、`prompts/SOURCES.md`）；PR #103（Stela，含提交者补充的两个 skill 全文、重建的系统提示词、`traces.jsonl`）。两个 sonnet 子 agent 只读深读，主会话抽查核对。下载件只在会话 scratchpad，不进 git。

## 榜单位置（脚本按 README 表算）

| 范围 | 条数 | 平均 | 中位数 | 我们 0.7588 |
|---|---|---|---|---|
| 全部 | 56 | 0.7182 | 0.7820 | 第 31，高于 25 条 |
| 2026-09 起 | 22 | 0.8755 | 0.8872 | 高于 3 条 |
| Tuned = 是 | 25 | 0.8714 | 0.8768 | 全部低于 |
| Tuned = 否 | 31 | 0.5947 | 0.6103 | 第 8 |

## DUO（0.9230；自报 0.9611）

- 运行：opencode，每题一个沙箱会话；DAB 的 4 个只读工具加沙箱 shell/python；查询超时 60 s；步数上限 100，最后一步去掉工具强制总结；运行失败有重试（细节未确认）；effort 设置未找到（未确认）。
- 提示包 29 个文件：1 份系统契约 `benchmark.md`、12 份数据集说明、16 份按题说明；其中 10 份按题说明写明"trace-calibrated"或对着评分脚本校准（例：DEPS q1 "validator accepts any single tied member"）。这些违反 ADR-0005，不能借。
- 维护者从 "Verification notes" 处截断答案再判分（有几次 notes 里的值和答案不同），0.9611 → 0.9230。每题 5 次是"4 次正式 + 1 次补跑"拼的，有没有挑选未确认。
- 按数据集，差距集中在 DUO 有按题说明的数据集：PATENTS 1.00 vs 0.47、DEPS 0.90 vs 0.50、GITHUB 1.00 vs 0.60、agnews 0.70 vs 0.40、stockmarket 1.00 vs 0.72、PANCANCER 0.93 vs 0.67、crmarenapro 1.00 vs 0.83。
- 推断（子 agent，未做无说明的消融）：16 分差距里约 10–13 分来自数据集和按题说明，3–6 分来自可借的通用规则。

## Stela（0.8114）

- 设置：effort `max`；Tuned = 是；任务超时 1 小时；工具 `run_query`（3524 次）、持久 Python 工作区 `execute_python`（1719 次）、`plan`、按需 `load_skill`。
- 系统提示词短、通用，不提 DAB。两个 skill（`analysis-verification`、`semantic-analysis`）没有点名数据集或题目，但部分检查项（分母含未定标签、前缀带身份、政策条款生效条件）看起来来自 DAB 失败复盘（推断），按 ADR-0005 属于边界。
- 语义操作 `semantic.classify/extract/resolve`：每次运行 1000 条记录、200 次请求、20 万 token 预算；模糊行标 unresolved 不硬判；`resume=` 只重试失败行；`require_complete()`。
- 证据契约：记录总体、覆盖、未定数量，只提示不拦截；抽样里常是 `coverage: unknown`。
- 策略复盘：另一次模型调用，在"失败聚集"或"反复改查询"时插入一条建议消息；270 个 trial 里 65 个触发，共 130 次。复盘提示词没记录，未确认。
- 按数据集：stockmarket +0.24、agnews +0.15、PATENTS +0.13、DEPS +0.10，其余接近，bookreview、googlelocal 比我们低。

## 能借的（ADR-0005 下通用）和我们的现状

| 做法 | 来源 | 我们现在 | 差距 |
|---|---|---|---|
| 推理强度 / 思考预算 | Stela `max` | 不设（`evals/board.py:144-145`） | 没有；端点是否支持未确认 |
| 并列处理 | DUO 契约 | `skills/sql-workflow/SKILL.md:37` 用 `RANK() <= N`，但第 50 行要求"最多 N 行" | 两条互相矛盾，有并列时会砍掉 |
| 限定词语义（至少/多于、按哪个事件取时间窗） | DUO 契约 | 没有 | 缺 |
| 连接键规范化并检查匹配率；解析文本里的数字和列表 | DUO 契约 | `sql-workflow` §4 只有行数、NULL、扇出 | 缺 |
| 编码列和文本标签列的选择 | DUO 契约 | 没有 | 缺 |
| 第二条路径复算、逐条约束复核 | DUO 契约、Stela skill | `sql-workflow` §4 部分有 | 部分 |
| 答案契约细节（首次出现带值、小数不用百分号、不用计数代替列表） | DUO 契约 | `ANSWER_RULE`（`src/data_agent/agent.py:179`） | 接近，差几条 |
| 分类工具：unresolved、续跑、必填字段 | Stela | `classify_text`（`src/data_agent/classify.py`） | 缺这三项 |
| 中途策略复盘 | Stela | 只有轮数提醒 | 缺，改动最大 |

不能借：DUO 的 12 份数据集说明、16 份按题说明，`benchmark.md` 里对应单个数据集惯例的几条（EMA、定投、方括号哨兵值），以及"按本评测参考管线的惯例作答"这种框定语。

## 结论（推断）

- 合规能追的主要是 Stela 那一档（约 5 分），不是 DUO 那一档。DUO 多出来的大部分靠按题说明。
- 改动全做完，估计在 0.78–0.82，大约能过全榜中位数；没有实测，未确认。
- DAB 没有开发集和留出集之分，在 DAB 上调通用规则仍有过拟合风险。改动要能在产品和 data-eng-bench 上讲得通。
