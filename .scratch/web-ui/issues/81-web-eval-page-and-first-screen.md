# 81 — 评测页和在线版第一屏

Status: ready-for-agent
Blocked by: 76, 80
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

静态构建的站点里有：自动回放的第一屏（带榜单分数和精选运行切换）、不可用的"新建运行"、评测页三块、能点进回放的开发集 trial 表格。

## Authority

- May edit: `web/`（除验收测试外）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、`src/data_agent/`、`evals/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "评测页"、"运行页"里关于在线版第一屏的部分、"历史列表"里在线版的部分。
- 数据从工单 80 的导出目录读，经静态数据来源。评测页汇总是 `DIR/eval.json`，结构见 `evals/export.py` 的模块文档；本地做测试数据时导出命令要带 `--tasks-dir`，否则 trial 的任务只显示题目 id。
- 第一屏：自动回放短的查数运行，跳过空闲间隔，约 20–30 秒播完后停在全部显示；带两个榜单分数的摘要条和评测页入口；能切换到其他精选运行。不做宣传式落地页。
- "新建运行"在在线版显示为不可用，并写明本地怎么启动。
- 评测页：两个榜单的总分；消融条形图带误差线（recharts），差别不显著的照实标出；开发集表格可按配置和成败筛选，每行链接到回放；留出集和全量只有汇总并写一句原因；DAB 按数据集小结并链接到 release。画图前先读 `dataviz` skill（如果可用）。
- 270 行的表格和各自的事件文件按需加载，不在首屏一次取完。
- 只用 pnpm。

## Acceptance

- [ ] 用工单 80 导出的数据做静态构建，第一屏、精选运行切换、评测页、trial 回放都在浏览器里真实打开过
- [ ] 评测页里没有任何留出集的逐题数据
- [ ] 深浅色、1280 宽以上没有布局问题；400 宽时第一屏摘要和评测汇总能读
- [ ] 浏览器控制台没有报错
- [ ] `make check` 通过

## Evidence required

无头浏览器截图（第一屏回放中和播完、评测页、筛选后的表格，深浅色），路径写进最终回复；`pnpm --dir web build` 和 `make check` 输出。

## Stop conditions

- 导出的数据缺页面需要的字段（停下交回，由工单 80 补）。
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
