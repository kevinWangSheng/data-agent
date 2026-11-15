# 79 — 本地版前端：提交、实时过程、取消、历史列表

Status: ready-for-agent
Blocked by: 76, 78
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`data-agent web` 启动后，在页面上填任务、选类型、数据源和配置后提交，过程实时出现，可以取消，历史列表能翻；构建好的前端随 Python 包发布，使用的人不需要 Node。

## Authority

- May edit: `web/`（除验收测试外）；`src/data_agent/` 的 web 服务模块（提供前端文件）；`pyproject.toml`、打包相关配置；`tests/test_packaging.py`；README 的本地启动一节
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、agent 的运行逻辑、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- spec "运行页"（本地版多出的部分）、"历史列表"、"总体"里"一套前端，两个数据来源"。
- 数据来源接口加后端的实现：列出运行、读事件加 SSE 续读（断线后按序号接着读，不丢不重）、提交、取消、数据源和配置列表。
- 运行中的页面用和回放同一个渲染路径：事件追加后重算视图模型。
- 提交失败（数据源不存在、已有运行在跑）要显示后端给的原因。
- 打包：构建好的前端文件进 wheel；仓库里不提交构建产物，由打包或 CI 步骤生成。做法先看 `tests/test_packaging.py` 和现有的打包配置。
- 只用 pnpm 和 uv。

## Acceptance

- [ ] 提交、实时显示、刷新后续读、取消、历史列表都在浏览器里真实操作过
- [ ] 历史列表显示任务、类型、配置、状态、用时、花费、开始时间，按开始时间倒序
- [ ] 从 wheel 安装后 `data-agent web` 能打开页面（打包测试覆盖）
- [ ] 浏览器控制台没有报错
- [ ] `make check` 通过

## Evidence required

`make check` 输出；用无头浏览器对着工单 78 的后端加假子进程走一遍提交、实时显示、取消，截图路径写进最终回复。真调模型的一次由主会话做。

## Stop conditions

- 打包方式需要改 CI 或发布流程里本工单没被授权的部分。
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
