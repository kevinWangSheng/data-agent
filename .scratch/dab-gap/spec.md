# DAB 同模型差距：effort、通用分析规则、分类工具

Status: ready-for-agent（人 2026-11-21 放行，编排会话派工）
日期：2026-11-21。决策见 `docs/adr/0005-dab-integration.md` Update 2026-11-21。取证见 `docs/research/2026-11-21-dab-same-model-gap.md`。

## Problem Statement

DAB 上我们 0.7588，低于全榜中位数 0.7820。同模型（`deepseek-flash`）的 Stela 是 0.8114，DUO 是 0.9230。和 Stela 比，差距主要在 stockmarket（+0.24）、agnews（+0.15）、PATENTS（+0.13）、DEPS_DEV_V1（+0.10）。读过的做法里，不含基准专属内容、能借的有三类：

- **推理强度**：Stela 用 effort `max`；我们不设，跑的是端点默认（DeepSeek 文档："Thinking mode is enabled by default, with the default effort being `high`"）。
- **通用分析规则**：并列、限定词、时间窗、连接键规范化、换一条路径复算。`skills/sql-workflow/SKILL.md` 缺这些，而且第 37 行（`QUALIFY RANK() <= N`）和第 50 行（"at most N rows"）在并列时互相矛盾。
- **分类工具**：Stela 的语义分类有"未定"结果、只重跑失败行、指定必填字段；我们的 `classify_text`（`src/data_agent/classify.py`）没有。

## Solution

三张工单，可以并行：

| 工单 | 内容 | 改动面 |
|---|---|---|
| 104 | 运行级 `effort` 选项，接到 SDK，进运行记录和续跑条件 | `src/data_agent/`、`evals/dab.py`、CLI |
| 105 | `sql-workflow` 补通用分析规则；加一个测试，拦住 Skill 里出现 DAB 数据集名 | `skills/sql-workflow/`、`tests/` |
| 106 | `classify_text` 加"未定"结果、只重跑没标上的行、多列文本 | `src/data_agent/classify.py`、两个工具服务器、`tests/` |

## 决定

### effort（104）

- `data-agent run`、`evals/dab.py` 加 `--effort`，取值 `low`、`high`、`max`；不给就不传，和现在一样（端点默认）。
- 传给 SDK 的 `ClaudeAgentOptions.effort`（SDK 0.2.168 有这个字段）。SDK 是否把它发成 DeepSeek 认的 `output_config.effort` 未确认：工单里要用一次真实请求核实（例如本地代理抓请求体，或看 CLI 的调试输出）。核实不了就停下交回，不猜。
- effort 写进运行的 `version` 事件和 DAB 的运行条件（`evals/dab.py:654` 附近），条件不同的续跑要拒绝，和 model 一样。
- `classify_text` 自己的模型调用仍然关思考（工单 71），不受这个选项影响。
- 产品默认这一轮不改。等对比结果出来，再由人定产品要不要也用 `max`。

### 通用分析规则（105）

- 只改 `skills/sql-workflow/SKILL.md`（文件头的改动说明同步写上）。`ANSWER_RULE` 不改：它和评测提示词快照绑在一起，这一轮的差距也不在答案写法上。
- 要补的规则，各用一个不点名基准的例子写：
  1. 排名和"前 N"：按 `RANK()` 取，并列在第 N 名的全部列出；把第 50 行改成"多于 N 行时，只能是并列造成的"。不自己加并列的决胜规则。
  2. 限定词："至少、不少于"含等号，"多于、超过"不含；题目给的约束对最终结果每一条都成立，答前逐条核对。
  3. 时间窗：按题目说的那个事件的日期筛（例如"下单在某月"用下单日期，不用合同日期）；区间按完成它的那个事件算。
  4. 连接键：连接前看两边键的样子（前后缀、空格、大小写、类型），规范化后再连，并查匹配上的比例；比例低就先查原因，不直接用结果。
  5. 存在文本里的值：数字、列表、字典样子的字符串要解析后再用，不用子串匹配近似。
  6. 列的选择：有编码列也有文字标签列时，用题目字面说的那一个；答案里出现的名字从数据里原样取。
  7. 复算：最终数字换一条查法再算一次（例如先过滤再聚合 vs 先聚合再过滤、换一张表算总数）对上了再答。
- DUO、Stela 的原文不复制，只借思路。
- 新测试：`skills/` 下所有文件不出现 DAB 的 12 个数据集名（大小写不敏感）。名单写在测试里，测试文件本身不在 `skills/` 下。

### 分类工具（106）

- 新结果 `unresolved`：模型可以对某行回答"拿不准"（固定标记，写进系统提示），这行记 `unresolved`，不归入任何类别，返回里单独计数。和 `outside_categories`、`no_label` 分开。
- 只重跑没标上的行：参数指向上一次调用存下的标签结果；只对 `failed`、`no_label`、`unresolved`（可选）的行再调模型，已标上的行原样保留。合并后的结果照常存，返回里写明这次新调了多少行。
- 多列文本：可以给几列文本，每行的几列按列名拼在一起发给模型。不加"只给了标题"这类猜测性提示（tools return facts）。
- 行数上限、预算、花费计入、密钥遮蔽沿用工单 70；产品和 DAB 两个服务器行为一致。

## Testing Decisions

- 104：单元测试，假 SDK，看 `ClaudeAgentOptions.effort` 的值、`version` 事件、DAB 续跑在 effort 不同时拒绝；不给 `--effort` 时 options 和现在完全一样。真实请求核实由主会话做。
- 105：Skill 数据集名测试；`tests/test_skills.py` 继续通过。规则效果靠下面的验证运行看，不写单元测试。
- 106：照 `tests/test_classify.py` 的假模型写：`unresolved` 计数、只重跑没标上的行（假模型只收到这些行）、多列拼接；预算在重跑途中用完时照工单 70 的规则停。
- 每张都要 `make check` 通过。已有的验收测试不改；`tests/acceptance/test_chat_eval_unchanged.py` 变红就停下交回。

## 验证（主会话做，在服务器上跑，都是 no-context）

1. **effort 对比**（104 合入后）：在当时的 main 上，对 11-15 重跑里不到 5/5 的 20 道题，`--effort` 不给和 `max` 各 3 次（120 次）；另取 10 道原来 5/5 的题，两组各 1 次（20 次）。估算 $4–8，小额放行。
   - 留下 `max` 的条件：20 道题上通过次数比默认多，10 道题上退步不超过 1 次，单次花费不超过默认的 2 倍。否则 DAB 重跑用默认。
   - 两组都在现在的 main 上跑：工单 97–103 之后 DAB 的工具面变了，11-15 的结果不能当对照。
2. **105、106 合入后**：用第 1 步定下的 effort，105 跑 DEPS_DEV_V1、GITHUB_REPOS、PANCANCER_ATLAS、PATENTS、stockmarket、crmarenapro 里不到 5/5 的题各 3 次；106 跑 agnews 4 道题各 3 次；都对照第 1 步同一 effort 那组。总体不低于对照才算过；不过就停下交给人。
3. **全量重跑**：三张都合入、第 2 步过线后，经人确认，在同一个提交上跑 540 次（两个配置 × 54 题 × 5 次），估算 $14–25（`max` 会更贵）。
4. **提交**：新开一个 DAB 提交 PR，引用 #144；说明里并列写盲跑、第一次例外、这一轮的分数和每轮看过什么。Tuned prompt 一栏由人定。

## Out of Scope

- 策略复盘（Stela 的提示词没公开）。
- DUO 的数据集说明、按题说明，以及对着评分脚本校准的任何规则。
- `ANSWER_RULE`、产品默认 effort、data-eng-bench 重跑。
- 修 11-14 诊断里"题目或评分脚本的问题"那 23 次失败。

## 验证进度（主会话）

- 第 1 步（2026-11-21，2bc3f0e）：`max` 24/60 对默认 31/60，强题都是 10/10，花费 1.65 倍，不留 `max`，DAB 重跑用默认 effort。详见工单 104。
- 第 2 步（2026-11-21，69a7e2b）：总体 28/54 对 25/54，**过**。其中 105 那组 24/42 对 19/42，agnews 那组 4/12 对 6/12（作为事实记录，不影响判定）。详见工单 105、106。
- 第 3 步：等充值。余额 ¥92.50（约 $12.67），上次全量 $13.57；充值后在 69a7e2b 上用默认 effort 开跑，跑法照 `runs/f18b/par.py`。
