# 101 — 运行页显示 `sandbox_exec` 和 `dbt_execute`

Status: done
Blocked by: 100, 103
Spec: ../spec.md
Claimed by: Antigravity subagent (ticket-101), 2026-11-21
Branch: worktree-101-run-page-tools

## Goal

运行页和对话页把两个新工具显示清楚：`sandbox_exec` 显示命令（等宽高亮）、退出码、输出（可折叠）、传入的 `data_files` 和产出文件；`dbt_execute` 显示子命令、select/exclude 和输出。"守卫拒绝"分类去掉 Bash。界面上加联网开关（默认开，关掉时启动的运行不带 `--network`，后端参数在工单 98）。旧事件文件照旧能显示。人 2026-11-17：界面基本做完，之后的界面调整统一放进这张工单，调整量预计不大。

## Authority

- May edit: `web/`（不含已有验收测试的断言）；`tests/`（只为事件夹具新增样本）
- Must not edit: `src/`、`evals/`、`skills/`、已有验收测试的断言和输入、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "网页"一节。`.scratch/web-ui/spec.md:146`（拒绝种类的分法）。
- 前端工单流程（AGENTS.md）：第 5 步不跑 `da-review`，只跑 `eng:code-review`；第 7 步由主会话派一个上下文干净的 Opus 5.5 子 agent 审 PR。
- 先例：nao 的工具结果组件（`nao/apps/frontend/` 下 execute-sandboxed-code 相关组件，路径自己找，路径相对 `~/dev/AI/data-agent-refs/`），只参考形式，不照搬外观（plan §2）。
- 工单 100 的 Comments 会列出拒绝分类的具体变化。

## Acceptance

- [x] 用工单 100 之后真实运行的事件文件做夹具：两个新工具按上面的字段显示（前端单元测试）
- [x] 旧事件文件（含 Bash 调用和 guard 拒绝）照旧能显示（前端单元测试）
- [x] 联网开关：默认开；关掉后提交的运行和对话，后端收到的参数里没有联网（前端单元测试）
- [x] 浏览器里打开一次真实运行的回放，截图贴进 Comments
- [x] `make check` 和前端检查通过

## Evidence required

检查输出、截图。

## Stop conditions

- 事件里缺显示所需的字段（要改 `src/` 才能拿到）：停下交回。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 2（前端只读审查 Round 1 报 0 P1 / 3 P2 / 3 P3，在 `e74e4a8` 修复 5 条、P3-3 记已知限制；Round 2 复核 0 issues found）
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: 已合并（PR #111，squash）；两项 GitHub CI 全绿，前端只读审查 Round 2 通过（0 issues found）。
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-21（主会话）：开工前核对源码命中两处 Stop condition（`web/app.py` 尚未暴露 `network` 参数；`sandbox.py` 未记录 `outputs/` 产出文件）。人决定单开前置工单 [103](103-web-network-and-sandbox-outputs.md) 补齐这两处 `src/` 改动，合入后再继续 101。同时人确认：保留旧事件（无 `rejected_by` 字段）的 Bash -> `guard` 退路以保证旧夹具和验收测试 `run-view.test.ts:355` 继续通过，并更新过时文案（如 `Shell (dbt only)`）。
- 2026-11-21（工单 101 子代理，分支 `worktree-101-run-page-tools`，提交 `42c8e26`，主会话抄录）：
  - **改动说明**：
    1. `sandbox_exec` 展示（`Detail.tsx` 与 `Turn.tsx` 共用 `ToolPanels.tsx` / `tool-views.ts`）：轻量 shell 语法高亮（`chat-code.tsx` `language="shell"`）+ 复制按钮；展示 `timeout_seconds`、`data_files`；退出码区分成功/非零/超时；`stdout`/`stderr` 可折叠展示并高亮 `[OUTPUT CUT]`；解析工单 103 新增的 `output_files:` 列表及 `[OUTPUT FILES CUT]` 提示。
    2. `dbt_execute` 展示（运行页与对话页共用）：展示子命令、`select`/`exclude`/`project_dir`/`full_refresh`/`threads`、退出码、`target_schema`、`run_results` 汇总、`failures` 与可折叠输出；`inputLine` 拼出 `dbt <cmd> --select ...` 摘要。
    3. 工具分类、表解析与“守卫拒绝”兼容：`tools.ts` / `index.css` 新增 `sandbox` (`SBX`) 与 `dbt` (`DBT`)，`Shell (dbt only)` 改为 `Shell (legacy)`；`outline.ts` 与 `model.ts` 识别新工具；`parseTable` 兼容 `query_database` 末尾 `[result id: ...]` 行；保留旧事件 Bash -> `guard` 退路（验收测试 `run-view.test.ts:355` 通过），新工具面下仅文件工具归 `guard`。
    4. 联网开关：`data/source.ts`、`local/NewRun.tsx`、`chat-page/Conversation.tsx` 均增加默认开启的 `Network access` 开关，关闭时向后端提交 `network: false`。
    5. 夹具与测试：新增 `tests/fixtures/web/agent-tools/events.jsonl` 与 `web/src/run-page/tool-views.test.ts`，补充 `model.test.ts`、`outline.test.ts`、`source.test.ts`。
  - **检查与测试**：`make check` 全通过（ruff/format/basedpyright 0 errors，pytest 2539 passed, 2 skipped，web typecheck 0 errors，web test 20 files / 317 passed）；`pnpm --dir web build` 通过。
  - **浏览器回放验证（Headless Chromium via Playwright，1440×900，0 console error / 0 pageerror）**：验证运行页 `sandbox_exec`（正常 + 截断）、`dbt_execute` 及对话页工具展开与 `Network access` 开关交互（取消勾选后 `POST /api/runs` 与 `POST /api/conversations/.../messages` 均发送 `"network": false`），4 张截图保存在工单 worktree 的 `runs/101/`。
- 2026-11-21（前端独立审查 Round 1 & 修复 `e74e4a8`）：
  - 审查结果：0 P1、3 P2、3 P3。已修复 3 P2 + 2 P3（`Detail.tsx` `<StepDetail key>`、`ResultView` 仅解析成功时显示 `raw result`、`Conversation.tsx` `steer` 模式禁用联网开关并标 `(next turn)`、`ToolPanels.tsx` 截断计数 `N+`、复合 key 与长 chip 溢出），补 3 条单测，`tsc` 0 errors、`vitest` 20 files / 320 passed、`vite build` 通过。
  - 已知限制（不修）：`tool-views.ts` 解析 `sandbox_exec` 输出时，若 `stdout` 恰好含独立一行 `stderr:` 会按纯文本边界切分；属于纯文本协议固有边界，不改 `src/`。
- 2026-11-21（主会话）：PR #111 在 `e74e4a8` 上 Round 2 只读前端复核通过（0 issues found），两项 GitHub CI 全绿。已 `gh pr merge 111 --squash` 合入 `main`，工单 101 标为 `done`。
