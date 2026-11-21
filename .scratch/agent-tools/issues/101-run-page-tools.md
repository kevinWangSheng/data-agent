# 101 — 运行页显示 `sandbox_exec` 和 `dbt_execute`

Status: ready-for-agent
Blocked by: 100, 103
Spec: ../spec.md
Claimed by: —
Branch: —

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

- [ ] 用工单 100 之后真实运行的事件文件做夹具：两个新工具按上面的字段显示（前端单元测试）
- [ ] 旧事件文件（含 Bash 调用和 guard 拒绝）照旧能显示（前端单元测试）
- [ ] 联网开关：默认开；关掉后提交的运行和对话，后端收到的参数里没有联网（前端单元测试）
- [ ] 浏览器里打开一次真实运行的回放，截图贴进 Comments
- [ ] `make check` 和前端检查通过

## Evidence required

检查输出、截图。

## Stop conditions

- 事件里缺显示所需的字段（要改 `src/` 才能拿到）：停下交回。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-21（主会话）：开工前核对源码命中两处 Stop condition（`web/app.py` 尚未暴露 `network` 参数；`sandbox.py` 未记录 `outputs/` 产出文件）。人决定单开前置工单 [103](103-web-network-and-sandbox-outputs.md) 补齐这两处 `src/` 改动，合入后再继续 101。同时人确认：保留旧事件（无 `rejected_by` 字段）的 Bash -> `guard` 退路以保证旧夹具和验收测试 `run-view.test.ts:355` 继续通过，并更新过时文案（如 `Shell (dbt only)`）。
