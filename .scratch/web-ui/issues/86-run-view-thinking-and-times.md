# 86 — 运行视图模型：思考、叙述和时间

Status: done
Blocked by: 75（done）
Spec: ../spec.md
Claimed by: Claude Code (claude-opus-5-5), 2026-11-16
Branch: worktree-86-run-view-thinking-and-times

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

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted (PR #90; CI green, Codex no findings on 6a2c63b)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-16 主会话：人定开本工单（不重开 75，也不放宽 76 的 Authority）。
- 2026-11-16 工单 agent（6c5cf86 实现、6a2c63b 审查修复）：
  - 新加（`web/src/run-view/model.ts`，现有字段不改）：`RunView.narration`（每条 `thinking`、`text` 一条 `{kind, seq, atMs, text}`，按 seq；含最终答案那段 text，不含 `answer` 事件；`thinking`/`text` 都没有 `parent`，所以都是主 agent 的）；`Step.startMs`；`VerifierDispatch.startMs`、`endMs`；导出纯函数 `eventTimes(events)`。时间是相对第一条事件 `ts` 的毫秒数，缺失或读不出为 `null`。
  - `pnpm --dir web test` 92 passed（原 64 不改，新增 28）；typecheck 通过；验收测试和夹具没动。make check exit 0：ruff 通过，basedpyright 0 errors，pytest 2296 passed, 1 skipped，vitest 92 passed。
  - 真实运行 `runs/scratch86/all.sh`（ms，全部 0 个 null）：query-shop eventTimes 59 条 0..16948，narration 8（7/1），steps 12；cart 473 条 0..791698，narration 52（40/12），steps 122，派出 1 为 307391..530945、派出 2 为 580707..778528；div 1057 条 0..3007914，narration 115（85/30），steps 270，派出 10 次；new-fields 45 条 0..44000，narration 2（0/2），steps 11，派出 24000..28000、35000..39000。
  - 现有输出不变：`runs/scratch86/same.ts` 把 main 的 `model.ts` 和新的比，去掉新字段后四个夹具全部前缀（60、474、1058、46 个）0 个不同。
  - 性能（最大夹具，新旧交替各 5 次）：整份 0.42 → 0.49 ms；全部前缀 193 → 207 ms（每帧 0.18 → 0.20 ms），多出约 400 次 `Date.parse`，没做缓存。主会话判定不算明显变差（每帧远低于 16 ms）。
  - 两个 spec 没写的默认，主会话 2026-11-16 认可并写进 spec：第一条事件 `ts` 读不出时所有时间为 `null`；派出的 `startMs` 在 `verifier_start` 之前为 `null`。
  - da-review（Codex）：无意见。eng:code-review：Standards 无硬违规；`summarize` 自己算时间、和新时间函数重复 → 已修（输出不变）；测试夹具加载代码和验收测试重复 → 不修（验收文件不能改）；text/thinking 两行 push 相似 → 不修。Spec：无缺失、无多做；缺 `ts` 的叙述和派出没测 → 已补。6a2c63b 没再审一轮（已用全部前缀比对和 make check 验证）。
- 2026-11-16 主会话：PR #90 CI 绿，Codex GitHub app 对 6a2c63b 无意见，squash 合并。
