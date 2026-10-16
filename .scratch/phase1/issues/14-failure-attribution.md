# 14 — 失败归因报告

Status: ready-for-agent
Blocked by: 03, 10
Spec: ../spec.md
Claimed by: claude (opus-5-5)
Branch: worktree-14-failure-attribution

## Goal

对开发集失败题，一条命令让 agent 起草报告：每题一个 FM1–FM4 标签、引用相关 Skill 原文、给出通用修改建议；不自动改 Skill。

## Authority

- May edit: `evals/`、`src/data_agent/`（只限归因入口）、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $2

## Context

CONTEXT 的 Failure mode、Failure attribution loop；plan §3「失败 → 改 Skill」；ADR-0005（只用 FM1–FM4 的分类思路；judge 提示自己写，不照抄 DAB）。只读开发集，绝不读留出集。

## Acceptance

- [ ] 对夹具或真实开发集失败题产出报告
- [ ] 建议不含任何题号、表名等榜单专属内容
- [ ] 只读开发集
- [ ] `make check` 通过

## Evidence required

一份真实报告；花费。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0.078（agent 运行 $0.0346；judge $0.0089 + $0.0157 + $0.0189）
- Outcome: 实现完成；夹具 job 出真实报告；`make check` 绿
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `428df6f`、`7567624`、`ed1b3a3`）

**实现**：`python -m evals attribute JOB_DIR --manifest M --out DIR [--tasks-dir D] [--budget USD]`（`evals/attribute.py`，注册于 `evals/__main__.py`；`tests/test_eval_attribute.py` 15 个测试，用假 judge）。
- 只读开发集：读每个 trial 的 `result.json` 判定所属题；不在 `manifest["dev"]` 的只计数，不打开事件、验证输出、题面（测试把留出集事件文件 chmod 0 证明）。
- judge：每道开发集失败题一次无工具的 Claude Agent SDK 调用，读轨迹、全部 Skill、题面与验证输出；`DATA_AGENT_*` 配置；提示自写，不抄 DAB。
- 标签 FM1–FM4，措辞同 CONTEXT。引用必须逐字出现在所点名的 Skill 里。
- 建议不得含 manifest 题号、运行 SQL 里的表或列、轨迹/题面/验证输出里的 snake_case 或 `schema.table` 名；Skill 已用的词放行。不合格重试一次，仍点名具体对象则扣下建议。
- 按调用计费，到预算不再调用（含重试）；基础设施错误不归因；已知密钥打码；输出 `attribution.json` 与 `report.md`，不写 `skills/`。

**真实运行**：没有真实开发集 Harbor job，用 shop 夹具跑真 agent 4 次，包成 Harbor 形状的 job（`runs/14/`，不进 git）。agent 花费 $0.0346（`python -m evals spend runs/14/events --budget 2`）；4 题对 3 题，"How many customers are repeat buyers?" 答 10，期望 7。
```
$ python -m evals attribute runs/14/job --manifest runs/14/manifest.json --tasks-dir runs/14/tasks --out runs/14/report
attribute: 1 of 1 dev failures attributed, $0.0189 spent -> runs/14/report
```
报告标 FM4，逐字引用 `sql-workflow` 的 "**Case sensitivity**: Use the correct case-insensitive function for your backend"；找到真实原因（`LOWER(status)='completed'` 漏掉 `'COMPLETE'`）；建议不含表名与题号（列出状态列的不同取值、归并到规范状态、答前核对计数）。job 里另放一个带哨兵事件文件的留出集失败题，`grep HOLDOUT runs/14/report/*` 无结果。

**审查**：`da-review main` 4 条全修（`7567624`，各配测试）：重试可超预算；重试抛错时丢花费；只出现在 `list_tables` 输出里的表名未拦；报错文本未打码。`eng:code-review main` 修 5 条（`ed1b3a3`）：docstring 误称不打开留出集 `result.json`；FM2/FM4 措辞超出 CONTEXT；lazy judge 改用 `functools.cache`；两个魔数改常量；删多余的 `Failure` dataclass。

**不修的理由**：模块约 570 行，各节独立，拆分会打散一个功能；`mask` 不复用私有的 `EventLog._mask`；测试里 `sys.path` 插入（假 judge 无法经子进程注入，`pyproject` 不在 Authority）；`check_reply` 返回元组与重复的 entry dict 属小问题；名称检查不拦 Skill 也用的单词表名或只以裸名出现的表名——Skill 词汇按政策是通用的，其余靠提示规则与人审。

**已知限制**：引用对照当前 Skill（运行用的版本不同时报告会注明）；预算在重试中途耗尽时丢弃首次回复；真实开发集报告要等有 Harbor 开发集 job（09/12 之后）。

### 2026-10-16 第 7 步：PR #7 退回第 1 轮（主会话）

退回：原因如下，在原分支 `worktree-14-failure-attribution` 上修，修完提交，不 push。

1. **CI 失败（合并后）**：PR 的 merge 检查 basedpyright 报 `evals/attribute.py:422:26 Argument missing for parameter "skills_dir"`。工单 05（PR #6）已合并，`skill_version()` 现在要 `skills_dir`。先 `git rebase origin/main`，再按 05 的运行配置取 Skill 目录。
2. **Codex P1（`evals/attribute.py:222`）属实**：`specific_names` 把 Skill 里出现过的词从全部名字里减掉，连运行 SQL 和 schema 输出里观测到的表名、列名也一起放行（例：Skill 含 `orders`，"always query orders" 不会被拦）。改为：从 SQL 与 schema 工具输出观测到的源标识符一律拦，不受 Skill 词表豁免；Skill 词表只豁免从自由文本里正则抓到的 snake_case。配一个修复前会失败的测试。
3. **Codex P1（`evals/attribute.py:329`）属实**：运行的版本头 `skill_version` 与当前 Skill 不同时，judge 看到的、引用核对用的都是当前 Skill。改为：版本不一致且拿不到该版本 Skill 时拒绝归因（记 error，不出标签），不只是报告里加提示。配测试。
4. **Codex P2（`evals/attribute.py:363`）属实**：最后一次重试后引用仍未逐字核对通过（`verified` 为假、`problems` 为空）时，条目被当成完成的归因保存。改为：最终仍未核实的引用使该条归因无效（`failure_mode=None` 并写 error）。配测试。
5. **Codex P2（`evals/attribute.py:301`）属实**：预算表只在调用前看已花费，单次调用不受剩余额度约束（两次 $1.20 在 $2 预算下都放行），SDK judge 还拿到整笔预算当单次上限。改为把剩余额度作为每次调用的上限传给 judge。配测试。

修完重跑 `make check`、真实运行一次 `python -m evals attribute`（同之前的夹具 job），再跑一轮 `da-review main`；最终回复给出要追加的 Comments 与 Ledger（Attempts 2）。
