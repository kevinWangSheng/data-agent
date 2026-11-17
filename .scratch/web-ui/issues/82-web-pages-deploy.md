# 82 — 发布到 GitHub Pages

Status: done
Blocked by: 74（done）, 79（done）, 81（done）
Spec: ../spec.md
Claimed by: claude-code (ticket agent, 2026-11-16)
Branch: worktree-82-web-pages-deploy

## Goal

`https://kevinwangsheng.github.io/data-agent/` 能打开在线版，直接访问某条运行的地址也能进；main 更新后自动重新构建发布。

## Authority

- May edit: `.github/workflows/`（新增 Pages 的构建发布流程）；`web/`（基础路径、404 回退页）；README（在线版的链接）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、`src/data_agent/`、`evals/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "在线版的发布"和"在线版的数据放哪"（Q35、Q41）。
- 工单 agent 做：构建流程（下载 release 里的数据包 → 用 `/data-agent/` 基础路径做静态构建 → 发布到 Pages）、404 回退页、README 链接。数据包的 release 标签名作为流程里的一个固定值，写进最终回复。
- **主会话做，不在工单 agent 的范围里**：工单 74 合入后重跑或重选 3 条精选运行（真调模型，小额）；跑导出命令（带 `--tasks-dir`，否则 270 个没有 `task` 事件的 trial 只显示题目 id；精选运行的 `notes.json` 要写 `task_id` 或 `local_data: true`，见工单 80、84）；把数据包上传到 release；在仓库设置里开启 Pages。这些是对外操作。
- 流程不需要任何密钥；数据包是公开 release 的附件。
- 数据包不存在时构建要明确失败，不发布空站点。
- 只用 pnpm。

## Acceptance

- [ ] 本地用同样的基础路径做静态构建并起一个静态文件服务，根路径和 `/data-agent/runs/<id>` 都能打开
- [ ] 流程文件通过 pre-commit；流程里没有密钥、没有服务器地址
- [ ] 发布后（主会话做完对外操作）在线地址能打开，README 的链接正确
- [ ] `make check` 通过

## Evidence required

本地静态构建和打开两个地址的命令与输出或截图；`make check` 输出。发布后的检查由主会话补进 Comments。

## Stop conditions

- Pages 的构建需要仓库设置或权限上工单 agent 做不了的改动。
- spec 自相矛盾或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0.0061 (main session's re-run of the featured query run)
- Outcome: accepted (PR #96; CI green, Codex no findings on 603d9c1); published 2026-11-17
- Human review minutes: — (filled by the human at merge, optional)

## Comments
- 2026-11-16 主会话：依赖都已合入。构建的做法（工单 81、79 定）：`SITE_DATA=<导出目录> pnpm --dir web build --base /data-agent/`（`web/site-data.ts` 从导出生成 `home.json`、把导出复制进 `dist/data`，不含 `index.json`；导出没有精选运行时 build 直接失败）。在线版的地址有 `/data-agent/`（第一屏）、`/data-agent/runs`（历史列表，工单 79）、`/data-agent/runs/<id>`、`/data-agent/eval`，直接打开都要靠 404 回退页。流程从 release 下载数据包后解压成导出目录再设 `SITE_DATA`。本地验证可以用工单 81 worktree 里的导出 `/Users/shenghuikevin/dev/AI/data-agent/.claude/worktrees/81-web-eval-page-and-first-screen/runs/81-site`（只读，不复制进仓库）。工单 87（答案旁的结果表）同时在做，改的是 `web/src/run-view/`、`web/src/run-page/`，本工单不碰这两处。
- 2026-11-16 工单 agent（603d9c1）：
  - `.github/workflows/pages.yml`：push 到 main 和手动触发；公开 HTTPS 下载数据、不用 token，`curl --fail` 缺数据就停；解压到 `$RUNNER_TEMP/site`，`SITE_DATA=… pnpm --dir web build --base /data-agent/`，upload-pages-artifact@v5.0.0 → deploy-pages@v5.0.1；只有 deploy job 有 pages/id-token 写权限；没有密钥和服务器地址。固定值：release 标签 `site-data`、附件 `site-data.tar.gz`，打包 `tar -czf site-data.tar.gz -C <导出目录> .`。
  - `web/spa-fallback.ts`：只在静态构建里把 `index.html` 复制成 `404.html`；测试 `web/spa-fallback.test.ts`（真的在 `/data-agent/` 下 vite build，404.html 和 index.html 一样，资源带基础路径）。README 第 10 行加在线版链接。
  - 本地：81 的导出构建 419 ms，`dist/404.html` 和 index.html 逐字节相同，资源在 `/data-agent/assets/`，`dist/data` 273 条运行；模仿 Pages 的本地服务（有文件给文件，否则 404.html + 404）：`/data-agent/` 200，`/data-agent/runs`、`/data-agent/runs/dbt-cart-abandonment`、`/data-agent/eval` 都是 404 + 应用，`data/home.json` 200；无头浏览器四个页面都正常（第一屏回放带分数、dbt 运行页、3 条精选列表、评测页），控制台只有文档本身预期的 404。缺 release 时 `curl --fail …` exit 56。
  - make check：ruff、basedpyright 0 errors；pytest 2307 passed / 1 skipped；vitest 221 passed；pre-commit 通过。
  - da-review（Codex）：无意见。eng:code-review：Spec 0、Standards 0 违规。不修：Pages 构建不在 PR 上跑（建议后续在 check.yml 加 `pnpm --dir web build`）；根目录测试和 tsconfig 文件列表手写（原来就这样）；vite 插件没有单独测试（本地构建和浏览器覆盖）；pnpm/node 设置和 check.yml 重复两份；测试临时目录不删（同 site-data.test.ts）。
  - 合并前提：合并会触发 Pages 流程；release `site-data` 和 Pages 没准备好时第一次会在下载那步失败、不发布。主会话先做对外操作再合并。
- 2026-11-17 主会话，对外操作（人 2026-11-16 批准"按计划做"）：
  1. 精选的查数运行在 main（692f409）上重跑：`uv run data-agent run --task "Which customer tier has the highest total amount of completed orders, and how much is it?" --type query --workspace runs/site-publish/query-run/ws --duckdb …/shop.duckdb --data-source-name shop --config no-verifiers --events-dir runs/site-publish/query-run/events` → 64 条事件，含 `task`、`answer`、`version.skills` `["sql-workflow","duckdb-sql"]`，success，14 turns，17 s，$0.0061，答案 "Gold tier … 710.00"。两条 dbt 精选沿用原型（`dbt-cart-abandonment`、`dbt-division-by-zero`，notes 带 `task_id`）。
  2. 导出 `bash runs/site-publish/export.sh`（3 条精选 + `u1-target`、`u1-full-rest`、`u2-rest` × full/baseline/no-verifiers + board f17 + DAB f18 + `--tasks-dir`）→ "scrub: changed 0 file(s) of 548; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0" / "export: 273 run(s)"。留出集检查只报数量：548 个文件里留出集题目 id 出现 0 次；本地静态构建后 trials 270，在留出集 0、在开发集外 0，留出集命名的运行目录 0，`eval.json`/`home.json` 里留出集 id 0，board 的留出集只有 accuracy/n_succeeded/n_tasks/n_trials。
  3. `tar -czf site-data.tar.gz -C runs/site-publish/site .`（25 MB）→ `gh release create site-data site-data.tar.gz`：https://github.com/kevinWangSheng/data-agent/releases/tag/site-data，公开下载 HTTP 200，26503618 字节。`gh api -X POST repos/kevinWangSheng/data-agent/pages -f build_type=workflow` 开启 Pages（build_type workflow，public）。
  4. 合并 #96 → pages 流程 run 43999401365：build success、deploy success。
  - 在线检查：`/data-agent/` 200、`data/home.json` 200；`/data-agent/runs`、`/data-agent/runs/dbt-cart-abandonment`、`/data-agent/eval` 都是 404 状态 + 应用（Pages 用 404.html 回退，预期）；无头浏览器打开 `runs/dbt-cart-abandonment` 是完整运行页（结果表标 "last query before the answer · #103"），`eval` 显示两个榜单总分（data-eng-bench 64.1% ± 1.6；DAB 0.7421）、留出集和全量只有汇总、消融图和贡献（都不显著）；控制台唯一的错误是文档自己的 404。README 第 10 行链接正确。截图 `runs/site-publish/live-cart.png`、`live-eval.png`（不进 git）。
  - 更新数据：重新导出、`gh release upload site-data site-data.tar.gz --clobber`，再手动触发 pages 流程。
