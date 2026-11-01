# 35 — DAB 算分：用 DAB 自带的 validate.py 算 Pass@1，并和 baseline 配对比较

Status: in-progress
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 35-dab-score)
Branch: worktree-35-dab-score

## Goal

给一份或两份 `evals dab` 的输出目录，一条命令完成以下几件事：

1. 用 DAB clone 里每道题自带的 `validate.py`，判断每次运行的答案对不对。
2. 按 DAB 官方口径算 Pass@1。
3. 输出逐个数据集、逐道题的通过数。
4. 给两份输出（baseline 与 `no-context`）做配对 bootstrap 比较。

## Authority

- May edit：`evals/`（新增子命令，比如 `dab-score`；或者在 `dab.py` 旁边加模块）、`tests/`（`tests/acceptance/` 除外）、`docs/setup.md`
- Must not edit：`src/`、`skills/`、`scripts/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none（真实运行由主会话在服务器上做）
- Spend cap：none（只算分，不调模型）

## Context

- 缺口：`evals/dab.py` 只写 `OUT/submission.json`（`[{dataset, query, run, answer}]`），不判对错。所以到现在还不知道 DAB 上的准确率。这一项属于遗漏（2026-11-01 发现）。
- DAB 的判分（`6ad9d68`）：
  - 入口是 `common_scaffold/validate/validate.py` 的 `validate(query_dir, llm_answer, reason)`：
    - 答案为空时直接判错；
    - 否则加载 `query_dir/validate.py`，调用它的 `validate(llm_answer)`，返回 `(is_valid, reason)`。
  - Pass@1 的算法（DAB README 的 Methodology 脚注）："the mean over datasets of each dataset's average per-query pass rate"。也就是：
    1. 每道题的通过率 = 通过的运行数 ÷ 运行数；
    2. 每个数据集取所属各题通过率的平均；
    3. 再对所有数据集取平均。
  - 缺的题按 0 算（README 脚注 ³）。
- 许可：DAB 没有许可证（ADR-0005、AGENTS.md），不能复制它的代码或文本。和 runner 一样，只按路径 import `--dab-root` 里的 clone（参考 `evals/dab.py` 的 `_import_from`）。测试夹具里的 `validate.py` 自己写，放在 `tests/fixtures/fake_dab/` 下（已有的假 DAB 夹具）。
- 配对比较：
  - 沿用 `evals/compare.py` 的思路（题目层面配对，95% 百分位区间，区间不含 0 才算显著）。
  - 统计量换成 DAB 的 Pass@1（先按数据集平均，再整体平均）。
  - 重抽样在每个数据集内对题目有放回地抽取，保证每个数据集都在。
  - 两份输出覆盖的 (数据集, 题) 不一致时拒绝，只报不一致的个数。
- 基础设施报错：`evals dab` 不把基础设施报错的运行写进 submission，这类运行要重跑。算分时要列出缺了哪些 (数据集, 题, 运行)，并且在两种模式里选一种：
  - 默认模式：缺的按 0 算，并给出提示；
  - `--strict` 模式：遇到缺项就以退出码 2 退出。
- DAB 上的比较对象：DAB 的工具服务器没有上下文工具，所以"最终版"是 `no-context`（只开 Skill 层），和 baseline 比（2026-11-01 与用户确认）。
- 已和用户定下：DAB 只有一套题，逐题结果只用于排查基础设施问题，不能用来改 Skill。在 `docs/setup.md` 里写明这一点。

## Acceptance

- [ ] 测试（用假 DAB 夹具）：
  - [ ] 单次运行判分走的是夹具里的 `validate.py`；空答案判错。
  - [ ] Pass@1 先按数据集平均、再整体平均，并有一个按题直接平均会得出不同结果的例子。
  - [ ] 缺项默认按 0 算并列出来；`--strict` 下以退出码 2 退出。
  - [ ] 配对 bootstrap：固定种子时结果可复现；两份输出覆盖的题不一致时拒绝；每个数据集都会被抽到。
  - [ ] 输出：终端汇总，外加一份 JSON 报告（逐个数据集、逐道题的通过数，Pass@1，比较结果）。
- [ ] `docs/setup.md`：命令用法、口径、"不用来改 Skill"的规定。
- [ ] `make check` 通过
- [ ] （主会话）真实运行：在服务器上对已有的 DAB 输出（`runs/dab21-*`、`runs/t23b-*`）跑一次，贴出逐题结论。

## Evidence required

单元测试输出；主会话的 `da-remote` 命令与输出。

## Stop conditions

- 需要复制 DAB 的代码或文本才能完成。
- DAB 某些题的 `validate.py` 签名和上面描述的不同，处理方式需要人来定。
- 第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清楚试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
