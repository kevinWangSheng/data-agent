# 81 — 评测页和在线版第一屏

Status: ready-for-agent
Blocked by: 76（done）, 80（done）
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-16 (filled in by the main session; the worktree could not write the main checkout)
Branch: worktree-81-web-eval-page-and-first-screen

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

- Attempts: 1
- Review rounds: 2 (da-review 1 fix; eng:code-review 1 fix round)
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: returned ×1 (PR #93)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
- 2026-11-16 主会话：76 已合入（PR #91）。数据来源接口在 `web/src/data/source.ts`，静态实现读导出布局；76 加了运行切换和基础路径的默认运行（为了打开三条夹具），本工单可按在线版需要重做。工单 79 同时在做，两边都改 `web/`：本工单只动在线版的部分（第一屏、精选切换、评测页、trial 表格、不可用的新建运行），不改 79 的本地提交、取消、历史列表和后端数据来源；谁后合入谁 rebase 解决冲突。
- 2026-11-16 Claude Code (Opus 5.5)（fb3ec68、2ec83f4、9c20ebd）：
  - 基线 make check：pytest 2299 passed，vitest 152。
  - 新 `web/site-data.ts`（替换 `fixture-data.ts`）：`SITE_DATA=<导出目录>` 时 dev/preview 提供导出，build 时复制进 `dist/data`（不含 `index.json`）；两种都生成小的 `home.json`（精选运行加榜单和 DAB 汇总）；缺数据回 404。新 `src/data/{home,evaluation}.ts`、`src/eval-page/{EvalPage,AblationChart,trials,scores}`、`src/site/{HomeStrip,Link,NewRunUnavailable}`。`RunPage` 加 `autoplay`（`autoplaySpeed(total) = total / 25 s`，查数运行 0.58×）。`staticSource.listRuns()` 从 `home.json` 读精选运行，新 `home()`、`evaluation()`（404 → null）。唯一新依赖 recharts；图的颜色用 dataviz 校验器查过（浅 `#2348c8`、深 `#6f8af5`）。
  - **和计划不同的一处**：导出的 `index.json` 2.47 MB（含全部 trial）、`eval.json` 含 270 行 trial，第一屏都不能读；改为构建时从导出生成 `home.json`，第一屏只取它和要回放的那条运行；`eval.json` 和图表库只在评测页加载。主会话认可（只是前端怎么读导出，不改导出契约）。工单 82 构建要带 `SITE_DATA=<导出目录> pnpm --dir web build --base /data-agent/`。
  - 测试数据 `runs/81-site-in/export.sh`：三条原型运行作精选（`local_data` 或 `task_id`）；9 个 job（`u1-target`、`u1-full-rest`、`u2-rest` × full/baseline/no-verifiers）；board `f17/reports/20261112T155019.560470Z`；DAB `f18-score/report.json`；`--tasks-dir data-agent-refs/data-eng-bench/tasks` → "scrub: changed 0 file(s) of 548" / "export: 273 run(s) -> runs/81-site"。
  - 构建：`SITE_DATA=../runs/81-site pnpm build --base /data-agent/` ✓；主包 766 kB，EvalPage + recharts 单独 390 kB。
  - 浏览器（vite preview，截图在 worktree 的 `runs/81-shots/` 01–16）：第一屏 25.3 s 播完停在全部显示，地址仍是 `/data-agent/`，只请求 `home.json` 和 `query-shop` 的 `meta.json`、`events.jsonl`；切精选运行到 `/runs/<id>` 全部显示；筛 no-verifiers + Failed 得 29/270，和 `n_failed` 29 一致，URL 保留筛选，trial 事件点开才加载，后退回到筛选后的表；1280/1440 深浅色布局正常；400 宽第一屏条和评测汇总可读、无横向溢出；构建版控制台干净。
  - 没有留出集数据：`runs/81-site-in/check_holdout.py` 只报数量——trial 题目在留出集 0、在开发集外 0、留出集命名的运行目录 0、`eval.json`/`home.json` 里留出集 id 0。
  - make check（9c20ebd）：ruff 通过，basedpyright 0 errors，pytest "2299 passed, 1 skipped, 39 deselected"，vitest 187 passed。
  - da-review（Codex）：P2 DAB 对比没有区间（每个数据集一题）显示 "95% CI 0.00 to 0.00" → 2ec83f4 改说"题太少估不出"，测试修前失败。
  - eng:code-review，9c20ebd 修：留出集没有 trial 时 board 的 `accuracy: null`（`evals/board.py:396`）让评测页崩溃 → `fmtPct(null)` 显示 "—"、类型可空，测试修前失败，浏览器里拦截 `eval.json` 也确认；第一屏条和评测页共用分数格式；新 `web/site-data.test.ts`（提供的文件、`home.json`、缺 `eval.json`、`..` 越界、站点不读的文件）；不再把 `index.json` 复制进构建。
  - 不修（理由）：在线版历史列表页（79 在做列表组件；数据侧 `listRuns()` 已只返回精选，顶栏有精选切换）——**主会话：spec 要求在线版这一页列出精选运行，记为缺口，交给工单 79 rebase 到 81 之后一并做**；显著性不画在每根柱上（显著性属于配置之间的差，标在图下的贡献列表）；保留 `listRuns`（和 79 共用接口）；`rootOf` 和 `route.ts` 重复（76 的文件、79 也在改）；其余小重复；`autoplaySpeed` 的 `targetMs` 参数；DAB 表的 "Scored"/"Baseline" 标签（导出没有配置名，"scored" 是 `dab-score` 的候选，`evals/dab_score.py:253`）。
- **退回第 1 轮（PR #93 上 Codex GitHub app 对 9c20ebd 的 P2，主会话 2026-11-16 核实属实；只是让第一屏的要求不静默落空，由主会话直接定）**：`homeData`（`web/src/data/home.ts:17`）只留 `featured` 的运行；导出里没有精选运行（例如 `evals export` 只给 `--job`、没给 `--run`）时 `home.json` 里一条运行都没有，第一屏显示 "No runs to show"，`firstScreenRun` 里"退到第一条运行"的分支永远走不到。在线版第一屏必须自动回放一条运行（spec），所以修法：
  - `site-data.ts` 生成 `home.json` 时，导出里没有任何精选运行就让 build（和 dev/preview 启动）失败，报错写明要用 `evals export --run` 给精选运行；不退回到 trial。
  - 删掉 `firstScreenRun` 里走不到的退路（或者让它只在精选里挑），别留死代码。
  - 测试：`site-data.test.ts` 加"导出没有精选运行 → 报错"；现有测试照过。
  - 在原分支上接着修；修完重跑 `make check`、`SITE_DATA=… pnpm build` 和两道审查。
