# 75 — 运行视图模型

Status: ready-for-agent
Blocked by: 73, 83
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

前端那组验收测试全绿：一个纯函数把事件列表（可以是前缀）和可选的运行说明转成运行概况、步骤、验证派出、阶段、关键时刻、最终答案。

## Authority

- May edit: `web/src/`（除验收测试外）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、`src/data_agent/`、`evals/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "运行视图模型"一节是全部规则，包括阶段怎么分、状态的来源、旧文件的五条退路。
- 类型和签名由工单 73 定，不改；需要改时停下交回。唯一例外（2026-11-15 人定）：`RunView.skills` 改成 `string[] | null`。
- 拒绝种类（2026-11-15 人定）：`tool_end` 有 `rejected_by` 就用它；没有时，不论新旧文件，都按 spec"旧事件文件缺新字段时的退路"里那条从结果文字分辨（守卫/网关措辞 → 对应种类；`PreToolUse:<tool> hook error` → `'unknown'`；其他 → `null`）。新文件里验证者 hook 的拒绝（`deny_dispatch`、`deny_query`）因此和旧文件一样显示为 `'unknown'`。
- 注入的 Skill：`RunView.skills` 读 `version.skills`（工单 83 写），原样取名字列表。`version` 没有 `skills` 字段（旧文件）时为 `null`，表示未记录；`[]` 表示 Skill 层关掉。不从工具调用、`layers.skill` 或任务类型推断。验收测试不测这一项，本工单在 `web/src/` 里另加单元测试覆盖三种情况（有名字、`[]`、缺字段）。
- 参考（只在本机，不复制文件）：样板 B 的事件配对、验证报告解析、结果表解析在 `/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/b/src/`；A 的阶段细分在 `/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/a/src/`。
- 性能：1.2 MB、270 次调用的那条运行，整份转换要快到回放时每帧重算前缀也不卡；需要时做增量。
- 只用 pnpm，不用 npm；只用 uv，不用 pip。

## Acceptance

- [ ] 前端验收测试全部通过，`xfail` 标记已删，测试内容一字未改
- [ ] 对最大的夹具，整份转换的耗时写进最终回复
- [ ] `make check` 通过

## Evidence required

`pnpm --dir web test` 的输出；`make check` 输出。

## Stop conditions

- 某个验收测试和 spec 的文字矛盾。
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
