# 136 — LiveSQLBench：标准答案到手后，官方对拍与"每道题都能判分"的检查

Status: needs-info
Blocked by: 128（done，`deb64e2`）；另要人把标准答案文件放到服务器，见 Comments
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

用标准答案证明两件事，之后才能跑留出集里程碑全量：
- 我们的 soft EX 和官方评测脚本在同一批答案上判分一致（spec "验收"`:83`，"评分"一行 `:37`："结果一致才算接入完成"）；
- 180 道题每道都能判分：拿标准 SQL 当预测交上去，每道都判对（oracle）。

## Authority

- May edit：
  - `evals/livesqlbench.py` 和 128 的验证脚本：只修对拍暴露的差异，修复要配一个修复前会失败的单元测试；
  - `scripts/da-remote`：只限答案文件就位后重新生成任务这一步；
  - 新建对拍和 oracle 用的脚本；
  - `tests/`（除 `tests/acceptance/`）；
  - `docs/setup.md`；
  - `NOTICE`：调用或复制上游评测代码时登记。
- Must not edit：
  - `tests/acceptance/`；
  - 127 的契约（名字、参数、返回类型）；
  - `evals/manifest.json`、`evals/livesqlbench_databases.json`：划分已定；
  - `src/`、`skills/`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access：工单 agent 没有，原因同 128（Context 第 7 项）。主会话 push 分支后，用 `scripts/da-remote setup`、`benchmarks`、`start`、`status`、`pull` 跑。
- Spend cap：$0。不调模型：oracle 和对拍都不跑 agent。对拍用的 agent 预测取自 128 冒烟和以后开发集运行已有的 `pred.json`。

## Context

1. **前提**：128 已合入。人已把邮件里的答案文件放到服务器上 128 `docs/setup.md` 写明的位置，`da-benchmarks/` 下。
   - 工单 agent 和主会话都不读、不打印、不取回答案文件的内容（spec Q1 `:19-20`，U7）。
   - 答案文件不进本机、git 和 `runs/` 的取回内容。
2. **重新生成任务**：重跑 `scripts/da-remote benchmarks`。128 的逻辑是：有答案文件就生成带标准答案的任务，`[metadata]` 标明"有标准答案"。`check` 显示 180 个任务都有标准答案。
3. **oracle（每道题都能判分）**：
   - 127 的契约把隐藏字段限制在验证阶段的文件里，所以不能假定 Harbor 的 oracle agent 或者 `solution/` 里有标准 SQL。
   - 做法：在服务器上，对每道题起它的库镜像，用评测入口 `evaluate(payload, payload["sol_sql"], execute, out)` 判分，执行器用验证阶段的写库角色。
   - 期望 180/180 判对。判错的题：
     - 开发集的题可以按题号报，带上判错的类别：执行出错、结果为空、结果不同；
     - 留出集只报数目和类别（ADR-0004）。
4. **对拍**：在同一批答案上分别跑官方评测和我们的 soft EX，比较两边的 reward。
   - 官方评测：上游 `LiveSQLBench-CLI/adapters/livesqlbench/template/tests/evaluator.py` 加 `evaluation/src/`，@ `e15cd22`，在服务器的容器里跑，环境照上游 `test.sh`。
   - 我们的：128 的验证脚本。
   - 两批答案：
     - (a) 180 道题的标准 SQL（同第 3 项）；
     - (b) 开发集 trial 的 `pred.json`：128 在 agent 跑完后把它拷进 trial 的 agent 日志目录（128 Context 第 9 项 1b）。至少用 128 冒烟留下的那些。
   - 要求两边的 reward 逐题一致。不一致时：
     - 开发集的题报题号，以及我们这边没对上的那一条规则（不含 SQL 和结果）；
     - 留出集只报数目。
   - 上游评测会把每条查询（含标准 SQL）打进日志（`evaluation/src/db_utils.py:302-304`）。它的输出只留在服务器上 `runs/` 以外的目录，跑完删掉，不进 `da-remote pull` 的范围。我们这边的输出照 U7，不含答案。
5. spec 原文是"拿到标准答案后补成集成测试"（`:74`）。答案不进 git，CI 跑不了，所以这里的"集成测试"是一条服务器上的命令：`scripts/da-remote start lsb-parity -- …`，可以重复跑，输出只有汇总和开发集的题号。另配不连库的单元测试，用替身执行器测比较逻辑。
6. 136 完成以后，留出集里程碑全量（spec `:46-55`）才能开跑，要人批准范围和花费（花费按 128 冒烟测出的单题成本估）。

## Acceptance

- [ ] 服务器上 180 个任务都带标准答案（`check` 的输出）。
- [ ] oracle：180/180 判对；没有全对就逐类修，或写明原因交回。
- [ ] 对拍：两批答案上，官方评测和我们的 soft EX 的 reward 逐题一致，贴一致的数目。修复都配了单元测试。
- [ ] 上游评测的输出不在 `runs/` 里，`pull` 回来的内容里也没有。检查标准 SQL 哨兵的办法照 128 的口令检查：只打印次数。
- [ ] 验证脚本自身失败的 trial（128 改成只写 `diagnostics.json` 的 `outcome: verifier_error`、不写 reward，Harbor 记 `RewardFileNotFoundError`）在全量里怎么处理：现在它在 `evals/report.py` 的 `NOT_RETRIED` 里，不重试、算 0。改成可重试或排除出分母，二选一，开工前在对齐会话定（可能要把 `evals/report.py` 加进 Authority）；实现配测试。来源：128 第 1 轮退回交回第 3 项（人 2026-11-25 定）。
- [ ] `make check` 通过。

## Evidence required

主会话在服务器上跑的命令和（已遮盖的）输出：`benchmarks`、`check`、oracle、对拍；一致的数目；开发集里不一致的题号和规则（如果有）；留出集只贴数目。

## Stop conditions

- 答案文件的格式和上游根目录 `integrate_gt_data.py:47` 的字段（`sol_sql`、`test_cases`、`external_knowledge`）对不上。
- 对拍不一致的原因在上游一侧：上游评测自己的 bug，或者和它的 docstring 不符。停下交人定以谁为准。
- 要改 127 的契约或验收测试。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-24（主会话建票）：从 128 拆出。128 不需要答案就能完成；本工单只做需要答案的两项。
  - `needs-info` 是在等人：标准答案邮件。
  - 128 done、人把答案文件放到服务器之后，主会话把 Status 改成 `ready-for-agent`。

- 2026-11-25（主会话）答案文件已就位：人用 `scp` 传到 `da-server:/root/da-benchmarks/livesqlbench-answers.jsonl`，主会话改成 mode 600。只查了结构，没读内容：270 行，270 个不同的 id，全部能解析；每行恰好有 `instance_id`（str）、`sol_sql`、`test_cases`、`external_knowledge`（都是 list）四个字段；覆盖公开数据全部 270 题，其中 180 道 Query 全部覆盖，没有公开数据之外的 id。字段名和 Context、Stop conditions 预期的一致。
  - 重跑 `scripts/da-remote benchmarks`："tasks: 180 in /root/da-benchmarks/livesqlbench/tasks, 180 with a gold answer"。`scripts/da-remote check`：0 failure(s)，"LiveSQLBench tasks: 180 …, 180 with a gold answer"。
  - 派工前还剩一项待对齐：验证脚本自身失败的 trial 怎么记分（见 Acceptance）。
