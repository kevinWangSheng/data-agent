# 86 — 运行视图模型：思考、叙述和时间

Status: ready-for-agent
Blocked by: 75（done）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

运行视图模型多出页面要的三样东西：主 agent 的思考和叙述条目、每步和每次验证派出的时间、每条事件的时间（回放用）。只加字段和一个纯函数，现有输出不变。工单 76 依赖它。

## Authority

- May edit: `web/src/run-view/`（除 `acceptance/` 外；`model.ts` 的类型可以加字段，不改、不删现有字段）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/`、`tests/fixtures/web/`）、`src/data_agent/`、`evals/`、`web/` 里 `run-view/` 以外的文件、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "运行视图模型"一节新加的"思考和叙述""时间"两条（2026-11-16 人定）。
- 起因：工单 76 认领后停下（见 76 的 Comments）。`RunView`（`model.ts` 的 `RunView`）没有 thinking 和中间的 text，`buildRunView` 只拿最后一条 text 当答案退路；`Step` 只有 `seq`、`durationMs`，`VerifierDispatch` 没有时间。plan §2 硬约束"思考默认收起"、spec user story 15、左栏缩略时间轴、回放"按时间截断的前缀"和"跳过空闲间隔"都需要。
- 要加的：
  - `RunView` 一个按 seq 排的列表，每条 `{kind: 'thinking' | 'text', seq, atMs, text}`，只含主 agent 的（验证者的事件里没有记录）。最终答案那段 text 也在列表里，页面自己决定不重复显示；`answer` 事件不进列表。
  - `Step.startMs`；`VerifierDispatch.startMs`、`endMs`（还没结束为 `null`）。
  - 一个导出的纯函数，给每条事件相对时间（如 `eventTimes(events): (number | null)[]`），回放按它截前缀、算空闲间隔。
  - 时间都是相对运行第一条事件 `ts` 的毫秒数；`ts` 缺失或解析不了时为 `null`。
- 参考（只在本机）：样板 B 的 `Detail.tsx:332-349`、`Outline.tsx:71-88, 206-300`（`/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/b/src/`）。
- 性能：最大夹具整份转换和全部前缀重算的耗时不能明显变差（工单 75 的数：整份 0.41 ms，全部前缀 170 ms）。
- 只用 pnpm，不用 npm。

## Acceptance

- [ ] `model.test.ts` 单元测试，用 `tests/fixtures/web/` 的真实夹具：思考和叙述条数和事件里一致（主 agent 的）；每步 `startMs` 不递减、和对应 `tool_start` 的 `ts` 一致；验证派出的起止时间和 `verifier_start`/`verifier_end` 一致，进行中的为 `null`；`eventTimes` 长度和事件数一致；`ts` 缺失 → `null`
- [ ] 前端验收测试不改且全过；现有单元测试不改且全过
- [ ] 最大夹具的耗时写进最终回复
- [ ] `make check` 通过

## Evidence required

`pnpm --dir web test` 输出；`make check` 输出；对四个夹具打印新字段的条数和时间范围（真实运行）。

## Stop conditions

- 要改或删现有字段才做得出来。
- spec 自相矛盾或缺一个需要人定的决定。
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

- 2026-11-16 主会话：人定开本工单（不重开 75，也不放宽 76 的 Authority）。
