# 75 — 运行视图模型

Status: done
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

- Attempts: 3
- Review rounds: 3
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted after 2 returns (PR #86; CI green, Codex no findings on dc8d96f)
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
- **退回第 1 轮（PR #86 上 Codex GitHub app 的 P2，主会话 2026-11-15 核实属实）**：`parseTable`（`web/src/run-view/model.ts:216`）遇到空行就当表结束；但网关的 `format_rows`（`src/data_agent/mcp_server.py:113`）把空字符串或全空白的值原样写成一行，单列结果里就是空行。例如 `value\n-----\n\nnext\n(2 rows)` 被解析成 0 行，后面的行全丢且不报错。修法：
  - 表只在 `(n rows)` 那一行结束；之前的空行是数据（单列时是一行空值；多列时按现有规则接到上一行最后一格）。
  - 没找到 `(n rows)` 行就返回 `null`，保留原文，不猜。
  - 单元测试（`model.test.ts`）：单列里有空值的行、中间和开头的空值、没有 `(n rows)` 时为 `null`；验收测试不改。
  - 在原分支 `worktree-75-run-view-model` 上接着修；修完重跑 `make check` 和两道审查。
- 2026-11-15 claude-code (opus-5.5)，退回第 1 轮修复（1fbcab0、52a779d、6ec5c96）：
  - `parseTable` 的 `(n rows)` 页脚从文字末尾按位置找，和 `format_rows`（`mcp_server.py:105-128`）一致：最后一行，后面跟 `[INCOMPLETE DISPLAY]` 说明时是倒数第二行。页脚以上全是数据：单列的空行是空值，多列的空行接到上一行最后一格；看起来像 `(n rows)` 的值仍是值。没有页脚，或行数对不上（相等；只有跟 INCOMPLETE 说明时可以更大）→ `null`，保留原文。
  - 单元测试（`model.test.ts`）：单列开头、中间、末尾的空值（含全空白）；多列空行接续；没有页脚 → `null`；像页脚的值（两种）；行数对不上 → `null`；INCOMPLETE 截断。删了自己第 0 轮单元测试里 `(4 rows)` 后一行虚构的 note（`format_rows` 不写）。主会话核对验收文件和 main 相比只有 `test.fails` → `test` 的标记。
  - `pnpm --dir web test` 60/60；make check exit 0：ruff 通过，basedpyright 0 errors，pytest 2202 passed, 1 skipped, 10 xfailed，vitest 60 passed。
  - 真实运行：`show.ts` 四个夹具的概况、阶段、答案和第 0 轮相同；新 `runs/scratch75/tables.ts`：每个夹具解析出的表数修前修后一样（cart 45/72、div 96/177、query-shop 11/12、new-fields 1/3），真实页脚都在末尾、都是 `(N rows)`，夹具里没有空值。
  - 最大夹具（1,221,975 字节，1,057 条事件）：整份 0.41 ms；全部前缀 174 ms，每帧 0.165 ms。
  - eng:code-review：两边都指出像 `(n rows)` 的值仍会提前结束表 → 52a779d 修；注释错字、页脚正则改命名常量。不修：`phasesOf`/`keyMomentsOf` 的可推导参数、`stepById.get(String(...))` 三处——第 0 轮代码、本轮不碰、不是 bug。
  - da-review（Codex）：1fbcab0 那次 20 分钟超时没结论；52a779d：P2 值等于上面行数时仍被当页脚 → 6ec5c96 修；6ec5c96：无意见。
  - 已知限制：0 行结果是网关的一句"Query returned 0 rows"，显示为文字而不是空表（spec："解析不了就保留原文"）。
- **退回第 2 轮（PR #86 上 Codex GitHub app 对 6ec5c96 的 P2，主会话 2026-11-16 核实属实；只是落实 spec"解析不了就保留原文"，由主会话直接定）**：`format_rows` 用 ` | ` 拼格、值里的换行原样输出，所以多列表里一个不在最后一列的多行值和新行分不开。例：行 `['first','value']`、`['x\ny','z']` 输出 `a | b\n-----\nfirst | value\nx\ny | z\n(2 rows)`，现在解析成 `['first','value\nx']`、`['y','z']`，行数还对得上，静默显示错数据。修法：
  - 多列表里出现格数少于表头的行（即第 1 轮"接到上一行最后一格"那条）→ 整张表 `null`，保留原文；格数多于表头的行同样 `null`（现状）。单列表的空行仍是空值。
  - 有 `[INCOMPLETE DISPLAY]` 说明时，从说明里取 "only the first N are shown" 的 N，要求解析出的行数等于 N（现在只要求小于总数，单列里多行值会被拆成多行而查不出）；取不到 N → `null`。
  - 单元测试：上面这个例子 → `null`；多列里最后一列的多行值 → `null`；INCOMPLETE 时行数和 N 对不上 → `null`、对得上 → 表。删掉或改写第 1 轮"多列空行接续上一格"那条单元测试（它测的正是被退回的规则）。验收测试不改。
  - 这是本 PR 最后一轮退回；修完重跑 `make check` 和两道审查，真实运行用 `runs/scratch75/tables.ts` 对四个夹具报修前修后的表数。
- 2026-11-16 claude-code (opus-5.5)，退回第 2 轮修复（796d785、b6d9d68、dc8d96f）：
  - `parseTable`（796d785）：多列表里格数和表头不同（少或多）的行 → 整张表 `null`，保留原文；单列表空行仍是空值。有 `[INCOMPLETE DISPLAY]` 时从说明取 "only the first N are shown" 的 N，解析行数必须等于 N，取不到 → `null`；没有说明时行数必须等于 `(n rows)`。
  - 单元测试 5 条新写或改写，修前都红：工单例子 → `null`；最后一列多行值 → `null`；多列空行 → `null`（改写第 1 轮"空行接续上一格"）；INCOMPLETE 行数和 N 不符 → `null`；没有 N → `null`。验收测试本轮未改（主会话核对和 main 相比只有 `test.fails` 标记）。
  - `tables.ts` 四个夹具修前修后表数相同（cart 45/72、div 96/177、query-shop 11/12、new-fields 1/3）；`show.ts` 四个夹具输出和第 0 轮逐字节相同。最大夹具整份 0.41 ms，全部前缀 170 ms，每帧 0.161 ms。
  - `pnpm --dir web test` 64/64；make check exit 0：ruff 通过，basedpyright 0 errors，pytest 2202 passed, 1 skipped, 10 xfailed，vitest 64 passed。
  - eng:code-review（796d785）：Spec 无缺漏；Standards：`shown` 改名 `expected`（b6d9d68）。不修：`SHOWN_COUNT` 照抄 `format_rows` 的措辞，措辞变了截断表退回原文（约定的退路），绑定两边要改 `src/data_agent/`，越界；截断时不查 `(n rows)` 总数和 N 的关系，`format_rows` 不会写 N ≥ 总数。
  - da-review（Codex）：796d785 那次 40 分钟没结论；b6d9d68：P2 `query_labels`（`mcp_server.py:306`，只读 SQL、返回 `format_rows`）没算查询，`classify_text` → `query_labels` 的运行只有一个"了解环境"阶段、答案没有依据的查询和表 → dc8d96f 修：`query_labels` 算查询工具（阶段分界和答案回退），`classify_text`（接收并执行 `sql`）结束了解阶段，新单元测试修前红。主会话核对 `query_labels` 确实返回 `format_rows`。dc8d96f：无意见。eng:code-review 没在 dc8d96f 上重跑。
- 2026-11-16 主会话：PR #86 CI 绿，Codex GitHub app 对 dc8d96f 无意见，squash 合并。
