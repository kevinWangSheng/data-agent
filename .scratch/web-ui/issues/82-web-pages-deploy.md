# 82 — 发布到 GitHub Pages

Status: ready-for-agent
Blocked by: 74（done）, 79（done）, 81（done）
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
- 2026-11-16 主会话：依赖都已合入。构建的做法（工单 81、79 定）：`SITE_DATA=<导出目录> pnpm --dir web build --base /data-agent/`（`web/site-data.ts` 从导出生成 `home.json`、把导出复制进 `dist/data`，不含 `index.json`；导出没有精选运行时 build 直接失败）。在线版的地址有 `/data-agent/`（第一屏）、`/data-agent/runs`（历史列表，工单 79）、`/data-agent/runs/<id>`、`/data-agent/eval`，直接打开都要靠 404 回退页。流程从 release 下载数据包后解压成导出目录再设 `SITE_DATA`。本地验证可以用工单 81 worktree 里的导出 `/Users/shenghuikevin/dev/AI/data-agent/.claude/worktrees/81-web-eval-page-and-first-screen/runs/81-site`（只读，不复制进仓库）。工单 87（答案旁的结果表）同时在做，改的是 `web/src/run-view/`、`web/src/run-page/`，本工单不碰这两处。
