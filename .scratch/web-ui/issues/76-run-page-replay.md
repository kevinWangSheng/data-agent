# 76 — 运行页：三栏布局和回放（读静态文件）

Status: ready-for-agent
Blocked by: 75（done）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

浏览器里能打开三条夹具运行：三栏布局、关键时刻、跳转筛选、回放控件、深浅色，长运行不卡。数据从静态文件读，经一个数据来源接口，后面的工单再加后端的实现。

## Authority

- May edit: `web/`（除验收测试外）；`NOTICE`（复制了上游文件时）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、`src/data_agent/`、`evals/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "运行页"和"总体"两节；硬约束在 `docs/plan.md` §2 "UI/UX 硬约束"。
- 布局和视觉照样板 B（只在本机）：`/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/b/`，截图在 `/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/b/screenshots/` 和 `/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/compare/shots/b-*.png`。样板代码不直接搬进来：页面只能通过工单 75 的视图模型拿数据，不自己读事件。
- 和样板 B 不同的三处：没选中步骤时中栏显示关键时刻；阶段用视图模型的细分；回放控件有跳过空闲间隔和显示全部，从链接打开默认显示全部。
- 可以从 SignalPilot 复制的小文件（SQL 高亮、SQL 排版、Markdown、回放控件）：位置见 `docs/research/2026-11-01-upstream-web-ui.md` 的"可复制"；保留版权头并登记 `NOTICE`。不复制它的时间线组件和配色。
- 客户端路由要能在非根路径下工作（在线版的基础路径是 `/data-agent/`）。
- 开发时的数据：把 `tests/fixtures/web/` 的三条运行作为静态文件提供。
- 只用 pnpm，不用 npm；只用 uv，不用 pip。

## Acceptance

- [ ] 三条夹具运行都能打开，spec "运行页"和 plan "UI/UX 硬约束"的每一项都满足
- [ ] 回放：播放、暂停、倍速、跳过空闲、显示全部都真实操作过一遍
- [ ] 键盘能在步骤间移动并展开收起
- [ ] 270 次调用的运行展开到默认状态时滚动和选中不卡
- [ ] 1280 和 1440 宽、深浅色下没有重叠、横向溢出、看不清的对比度；400 宽时第一屏摘要能读
- [ ] 浏览器控制台没有报错
- [ ] `make check` 通过

## Evidence required

用无头浏览器（Playwright）对三条运行、深浅色各截一张图，路径写进最终回复；回放和键盘操作的操作记录；`pnpm --dir web build` 和 `make check` 输出。

## Stop conditions

- 视图模型缺页面需要的东西（停下交回，不在页面里绕过它）。
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
