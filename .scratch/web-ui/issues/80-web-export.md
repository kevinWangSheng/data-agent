# 80 — 导出命令：把选定的运行转成在线版的静态数据

Status: ready-for-agent
Blocked by: 73
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

一条命令把选定的运行导出成在线版读的静态数据（每条运行的事件文件和运行说明、索引、评测页的汇总），强制脱敏，拒绝留出集的运行。导出命令的验收测试全绿。

## Authority

- May edit: `evals/`（新增导出模块和子命令）；`tests/`（新增的单元测试）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、`evals/scrub.py` 的脱敏规则（只调用，不改）、`src/data_agent/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "导出命令和在线版数据"和"评测页"两节。
- 输入：精选运行的事件目录和各自的说明（标题、失败原因等）；开发集统一评估的 Harbor 任务目录（每个 trial 的事件文件和评分结果）；评测清单。输出目录的结构由本工单定，写进最终回复，工单 76、81 的静态数据来源照它读。
- 运行说明的字段：id、标题、任务、类型、数据源名字、评分结果、失败原因、是否精选。事件文件里有 `task` 事件时从它取，没有时从 Harbor 的题目目录取。
- 评测页的汇总：两个榜单的总分和误差、各配置的通过率和误差、开发集每个 trial 一行。这些数字从现有的评测代码算（`evals/report.py`、`evals/metrics.py`、`evals/dab_score.py`），不另写一套算法。
- **留出集**：对照清单，任何属于留出集的 trial 都拒绝导出并以非零退出；留出集和全量只输出汇总数字。实现和测试都不读、不打印留出集的逐题失败细节（ADR-0004）；测试用假清单。
- 脱敏不能关：没有跳过脱敏的参数。
- 只用 uv。

## Acceptance

- [ ] 导出命令的验收测试全部通过，`xfail` 标记已删，测试内容一字未改
- [ ] 没有任何参数或环境变量能跳过脱敏或留出集检查
- [ ] `make check` 通过

## Evidence required

`make check` 输出；真实运行一次：对 `/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/data/runs/` 的三条运行导出到一个临时目录，贴出命令、输出目录的文件列表和索引文件的内容。

## Stop conditions

- 汇总数字没法从现有评测代码得到，需要新的统计口径。
- spec 自相矛盾或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
