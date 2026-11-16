# 75 — 运行视图模型

Status: in-review
Blocked by: 73, 83
Spec: ../spec.md
Claimed by: claude-code (opus-5.5)
Branch: worktree-75-run-view-model

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-15 claude-code (opus-5.5)（worktree-75-run-view-model：56501e6、4adcfc8；PR #86）：
  - 测试：52/52（43 验收 + `web/src/run-view/model.test.ts` 9 条单元：Skill 三种情况——有名字、`[]`、缺字段给 `null`；结果表边界；任务字段逐个回退到 notes；没有 `tool_timing` 时没有耗时）。主会话核对：验收文件和 main 相比只有 `test.fails(` → `test(`、`test.fails.each(` → `test.each(`。
  - make check exit 0：ruff 通过；basedpyright 0 errors；pytest 2202 passed, 1 skipped, 10 xfailed（其他工单的验收测试）；vitest 52 passed。
  - 最大夹具（`dbt-division-by-zero`，1,219,980 字节，1,057 条事件，270 次调用）：整份转换 0.39 ms；1,057 个前缀全部重算共 179 ms，每帧 0.17 ms，没做增量。
  - 真实运行：`node --experimental-strip-types runs/scratch75/show.ts <夹具>`，四个夹具（审查修复前跑，修复不改这些输出）：query-shop orient(1) explore(10) answer(1)，success，答案依据 `call_01_UEDIF…1428`，5 列 × 12 行；cart orient(20) explore(43) build(11) self-check(3) verify1(19) fix1(7) verify2(19)，FAIL 后 PASS，9 个关键时刻；div 10 次派出，9 次 FAIL 后 PASS，状态按评分为 failure；new-fields 派出关联到 `t-a1`、`t-a2`，答案 `t-q1`，1 列 × 1 行。
  - da-review（Codex）：无意见。
  - eng:code-review，4adcfc8 修：概况和阶段各自算任务类型、可能不一致 → 只算一次；任务字段逐个回退到 notes；`durationMs` 不再用时间戳差兜底（spec 只配 `tool_timing`，界面分不出估算和实测）；`mcp__data__` 前缀一个常量、去掉未配 Agent 调用的共用 helper、改名。不修："`buildRunView` 太长"（一遍扫过、每种事件一小段，拆开只多跳转）。
- agent 交主会话的两点（主会话 2026-11-15 定，只是落实已定的规则）：
  1. 守卫与 `unknown` 的区分：守卫自己的拒绝也以 `PreToolUse:<tool> hook error` 开头，验收测试要求它们是 `guard`。按工具区分：落在 Bash、Read、Glob、Grep、Write、Edit（守卫注册的工具，`agent.py:994-1005`）上记 `guard`，其他记 `unknown`。主会话核对：验证者的 `deny_dispatch` 只在 Agent 上（`verifier_v2/hooks.py:361`），`guard_query` 虽然不带 matcher（第 362 行），但只对 `mcp__*` 查询工具生效（第 255 行），所以这个划分是准的。spec 已按此改写。
  2. spec 没写的四个默认（已写进 spec"旧事件文件缺新字段时的退路"）：旧文件"最后一段文本"只在有 `result` 后才算答案；旧文件的 `verifier_dispatch` 配给最近一次还没配上的 Agent 调用；没记录任务类型时，没有 Write/Edit 也没有验证的算查数运行；查数运行的"回答"阶段从答案依据的查询开始。
