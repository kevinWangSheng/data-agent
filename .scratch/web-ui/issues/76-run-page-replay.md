# 76 — 运行页：三栏布局和回放（读静态文件）

Status: in-review
Blocked by: 75（done）, 86（done）
Spec: ../spec.md
Claimed by: claude (opus-5-5), 2026-11-16 second attempt
Branch: worktree-76-run-page-replay

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

- Attempts: 2
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: in-review
- Human review minutes: — (filled by the human at merge, optional)

## Comments
- 2026-11-16 claude（opus-5-5）：认领后停下（停止条件"视图模型缺页面需要的东西"），没写代码、没提交。分支 worktree-76-run-page-replay（无提交）。
  - 缺口 1：RunView 不含 thinking 和中间的 text（夹具里 thinking 7/40/85 条、text 1/12/30 条）；buildRunView 只取最后一条 text 做答案退路（`model.ts:262-433`）。plan §2 硬约束"思考默认收起"、user story 15 都需要；样板 B 把两者作为独立条目显示（Detail.tsx:332-349）。
  - 缺口 2：Step 只有 seq 和 durationMs，VerifierDispatch 没有时间；左栏缩略时间轴在样板 B 里按每步起止时间放（Outline.tsx:206-300）。
- 2026-11-16 主会话核实两条属实（`model.ts:140-154` 的 RunView 字段；spec 第 24、48 行，plan 第 44 行）。另补一条缺口 3：回放的"跳过空闲间隔"要每条事件的时间，页面不能自己读事件。改视图模型类型属工单 73 的契约，交人定。
- 2026-11-16 主会话：人定开工单 86 补视图模型的思考/叙述条目、每步和每次验证派出的时间、每条事件的时间（spec "运行视图模型"新加两条）；76 依赖 86，86 合入后重新派发，页面只用这些新字段，不自己读事件。
- 2026-11-16 claude (opus-5-5)，第 2 次（worktree-76-run-page-replay：decfc54、3049dcc；分支本已和 origin/main 平齐）：
  - 三栏运行页：左栏时间轴、筛选（All / SQL / Rejects & errors / Verifier）、阶段大纲（思考和文本夹在步骤之间）；中栏没选中时是关键时刻，否则是步骤、思考/文本、阶段或验证的详情（逐模型逐项结论、上次以来主 agent 的改动、完整报告、验证者自己的调用）；右栏结果、花费和用时、答案和结果表、所有结论、任务、配置（含 Skill 和上下文工具次数）。数据经数据来源接口：静态版读导出布局，dev/preview 由 Vite 插件提供三条夹具；链接在 `/data-agent/` 下可用。从 SignalPilot 复制 SQL 高亮、SQL 排版、Markdown、回放控件，保留版权头并登记 `NOTICE`。页面只读视图模型（回放按事件数截前缀除外）。
  - 证据（headless Playwright）：截图在 worktree 的 `runs/web-76/`（不进 git）：三条运行 × 深浅色 × 1280/1440，另加 `cart-dark-verify2-1440.png`、`div-dark-400.png`。主会话看过 div 浅色 1440 和 cart 深色验证 #2，布局、结论网格、diff、答案和结果表正常。
  - 回放（查数运行）：从链接打开默认全部显示；1× 播放到 0:01/0:14，暂停保持；跳过空闲开关总时长 0:16 ↔ 0:14 且停在同一事件；10× 续播到 0:05；中途"显示全部"有效；50× 重播结束在全部显示；122 次调用的运行 1000× 341 ms 播完。
  - 键盘（270 次调用）：j/k 和方向键移动，→ 展开，← 回到分组再收起，v/e 跳下一次验证/拒绝或报错，1–4 切筛选，Esc 回关键时刻。
  - 长运行：全部展开约 408 行，滚动中位 16.6 ms/帧、最差 23.7 ms；选中 2 帧内重绘（约 34 ms）。
  - 布局：1280、1440 深浅色无横向溢出、顶栏无重叠、控制台无报错或警告；三条运行第一屏都能看到答案和结果表第一行；文字对比度最低 4.79:1；400 宽摘要可读。生产构建挂在 `/data-agent/` 下深链接（步骤和筛选）、切运行、浏览器后退、基础路径都正常。
  - `pnpm --dir web build` 通过；make check：ruff 通过，basedpyright 0 errors，pytest 2299 passed / 1 skipped，web typecheck，vitest 152 passed。
  - da-review（Codex，decfc54）：无意见（第一次 20 分钟超时后重跑）；没审 3049dcc。eng:code-review，3049dcc 修：时间轴直接读事件的 `seq`（spec 不允许）→ 改从视图模型取；在 state updater 里调 `setPlaying`（第一版修法让回放在末尾循环，浏览器检查发现后修好）；`sectionStats` 移到有测试的 helper 旁并补测试；阶段和验证视图对"改动"的定义不一致 → 共用一个；其他小修（section id helper、右栏显示检查名、270 次调用运行的第一屏、过期错误条、标题退路、复制头补全、过时 docstring）。
  - 不修（理由）：几处小的重复条件和 helper（各自局部且短）；`Detail.tsx` 630 行（五个视图共用局部 helper）；工具列表保留 `verify_model_values`（ADR-0009 之前的事件文件还有）；运行切换和基础路径默认运行和 81 重叠，但现在打开三条运行需要，81 可重做；额外快捷键服务 user story 8。
  - 已知：主 JS 包 761 kB（gzip 239 kB），主要是 CodeMirror SQL 解析和 Markdown；Vite 有警告；SQL 排版按需加载。
