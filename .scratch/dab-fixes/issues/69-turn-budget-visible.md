# 69 — 查询任务里 agent 能看见轮数预算

Status: done
Blocked by: —
Spec: ../../../docs/adr/0005-dab-integration.md（Update 2026-11-14）
Claimed by: claude-opus-5-5 (worktree 69-turn-budget-visible)
Branch: worktree-69-turn-budget-visible

## Goal

查询任务开始时 agent 知道本次运行的轮数上限，剩 10 轮和剩 3 轮时各收到一次提醒。Skill `sql-workflow` 第 6 节引用这个提醒，不再用没有定义的 N。

## 已和用户定下的（2026-11-14，grilling Q5、Q10、Q11、Q17–Q19）

- 第一步先取证：查清 SDK 自带的 CLI 卡 `--max-turns` 时数的是什么（见 Context 里的矛盾）。查不清，或者钩子里拿不到和它一致的剩余轮数，就停下交回，不用近似值。告诉 agent 一个错的剩余轮数比不告诉更糟。
- 告知方式：
  - 开头：查询任务的提示里写明本次运行的上限。
  - 过程中：剩 10 轮、剩 3 轮时各提醒一次，每个点只提醒一次。不做成每轮都提醒。
- 只对查询类任务，dbt 任务不变。
- 放在基础层：所有配置（含 baseline）都有，不受 `skill`、`verifiers`、`context` 开关影响。
- 上限数值不改：`query = 50`、`large = 75`。
- `skills/sql-workflow/SKILL.md` 第 6 节（`:114-120`）改写：按"提示开头给的上限"和"剩余轮数提醒"来说，去掉 `N`；保留"查询已经能跑且通过检查就立刻回答"这层意思。文件是从 signalpilot-plugin 复制来的，保留文件头的注释并在其中补一句本次修改（`NOTICE:39` 已登记）。

## Authority

- May edit: `src/data_agent/agent.py`、`evals/dab.py`、`skills/sql-workflow/SKILL.md`、`tests/test_agent*.py`、`tests/test_dab.py`、`tests/test_skills.py`，需要时新增一个测试文件
- Must not edit: `src/data_agent/configs/*.toml`（上限不改）、`tests/acceptance/`、dbt 任务的提示、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- 根因：`skills/sql-workflow/SKILL.md:116-118` 写"Turns 4 through (N-3)""Last 3 turns: Give the answer"，但 agent 从没被告知 N，也看不到当前是第几轮。no-context 的 270 次里有 12 次撞轮数上限、交了空答案。
- 上限在哪里设：`src/data_agent/agent.py:780`（`max_turns=config.max_turns[task.type]`）；DAB 路径在 `evals/dab.py:402-407` 用 `replace(build_options(...), max_turns=turns)` 覆盖，大库用 `large`（`evals/dab.py:202-203`）。钩子必须拿到实际生效的那个上限，不是 `build_options` 里的默认值。
- 钩子怎么组装：`build_options`（`src/data_agent/agent.py:686-787`），现有例子 `tool_timing_hooks`（`:799-826`）、`Spend.hooks`（`:397-435`）。
- SDK 事实（已装的 `claude_agent_sdk` 0.2.168，在 `.venv` 里）：钩子输出支持 `additionalContext`（`types.py:511-525`、`546-550`，PostToolUse、PreToolUse、UserPromptSubmit、Stop 等）；本仓库还没有钩子用过它。`max_turns` 由自带的 CLI 用 `--max-turns` 执行。
- 矛盾（必须先解决）：我们事件里记的 `num_turns` 和 CLI 卡上限用的不是同一个数。上限 50 的运行里，有正常结束的运行记了 56、63、70 轮。可能的原因：子 agent 的轮数、同一轮多个工具调用、或 `ResultMessage.num_turns` 的口径不同；都未确认。从自带 CLI 的代码（SDK 包里的打包文件）和 SDK 文档查清口径，写进最终回复并引用位置。
- 验证者：`uses_verifiers`（`agent.py:681`）为真时查询任务会派子 agent。要确认子 agent 的工具调用会不会触发主会话的钩子、会不会被算进轮数；提醒只发给主 agent。
- 工单 68 改同两个提示的结尾句，不是同一行；后合并的一方解决冲突。

## Acceptance

- [ ] 最终回复里有取证结论：CLI 按什么数轮数、依据的代码或文档位置、钩子里用什么量得到剩余轮数。
- [ ] 新测试：查询任务的提示（产品和 DAB 两条路径）含本次运行实际生效的上限；DAB 大库显示的是 `large` 的值。
- [ ] 新测试：钩子在剩 10 轮和剩 3 轮时各返回一次 `additionalContext`，文字里有剩余轮数；其他轮不返回；同一个点不重复。
- [ ] 新测试：上限小于等于 10 时行为有定义（例如只发还适用的那个提醒），不报错。
- [ ] 新测试：dbt 任务没有这个钩子，提示不变。
- [ ] 新测试：三个配置开关任意组合下，查询任务都有这个钩子。
- [ ] `skills/sql-workflow/SKILL.md` 第 6 节不再出现 `N`；`tests/test_skills.py` 通过；Skill 里没有基准专属内容。
- [ ] `make check` 通过。

## Evidence required

- 测试输出。
- 主会话在合入前做真实运行（工单会话读不到模型的环境变量）：本地用一个低上限的临时配置跑一道查询题，从 `events.jsonl` 核对三件事：提醒确实进了模型的上下文；"剩 3 轮"的提醒之后，CLI 正好在第 3 轮后以 `error_max_turns` 结束（或 agent 在那之前交了答案）；提醒各只出现一次。工单会话在最终回复里写明这次运行该怎么配、该看事件里的哪个字段。

## Stop conditions

- 查不清 CLI 的计数口径，或钩子里拿不到和它一致的剩余轮数。
- `additionalContext` 在这个 SDK 版本里到不了模型上下文。
- 需要改上限数值或改 dbt 任务才能做下去。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 3
- Model spend: $0.05, main session's real runs (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted (returned once)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

### 取证结论（工单会话，分支 worktree-69-turn-budget-visible：5306be9、14dac99、a98147c）

对象是自带 CLI 2.1.301（`claude_agent_sdk/_bundled/claude`，在二进制里搜内嵌的 JS 原文）。

- **CLI 怎么数轮**：一轮是主线程上一次调了工具、工具也跑了的模型回复。
  - 计数从 1 开始（`turnCount:1`）。
  - 每轮工具跑完后算 `rr=Jo+1, Xr=M&&rr>M?M:void 0`；超过上限就以 `error_max_turns` 结束，`num_turns` 报的是 `turnCount`，也就是上限+1。
  - 计数只在两处增加：工具轮之后，和 Stop 钩子拦下收尾时（`Ce=Jo+1`）。
  - 截断续写、只有思考的回复、拒答重试、工具调用解析失败的重试，都原样带着旧计数（`{...St,...}`；里面的 `Ar` 不含 `turnCount`）。
  - 所以第 k 轮的工具跑完后，还剩 M−k 次回复，含下一次。
- **56/63/70 怎么来的**：正常结束时的 `num_turns` 用的是另一个数 `Et`。它每遇到一条 `user` 类型消息加一（`if(u.type==="user"){...Et++`），一条工具结果就是一条，所以一轮里调几个工具就多算几次。
- **实测对得上**：`runs/f19-dab` 里撞上限的 5 次运行，主线程回复都正好 50 次（按每次回复一个 thinking 块数），`num_turns` 都是 51。
- **钩子里怎么得到剩余轮数**：`TurnBudget.observe` 按到达顺序给主线程里带工具调用的回复（按 `message_id`）编号，工具调用 id 对应到这个编号；PostToolUse / PostToolUseFailure 钩子算 `剩余 = 上限 − 编号`。
  - 270 次运行里共 8484 个钩子事件，每个都晚于同一工具的 `tool_start`。万一钩子先到、还没看到那次回复，就不提醒，下一轮按实际剩余补上，不会猜数。
- **到达模型**：`additionalContext` 会被渲染成一条 meta user 消息（`"<hookName> hook additional context: ..."`），能进模型上下文。
- **子 agent**：查询任务现在不派子 agent（`uses_verifiers` 只对 dbt 为真）。即使有，带 `parent_tool_use_id` 或 `agent_id` 的调用也不计数、不提醒。

### 实现与审查（工单会话）


- 实现：src/data_agent/agent.py（TurnBudget、TURN_LIMIT、build_options(turn_budget=)、run() 喂消息）；evals/dab.py（大库的 large 上限通过 TurnBudget 传进 build_options，不再用 replace 覆盖 max_turns；task_prompt 带上限）；skills/sql-workflow/SKILL.md 第 6 节去掉 N、文件头注释补一句。
- 低上限的行为：到不了的提醒点直接不发（上限 8 只在剩 3 时提醒；上限 ≤3 不提醒），按工单例子"只发还适用的那个提醒"。
- 越出 Authority：tests/test_task_file.py 逐字固定查询提示，只在 QUERY_PROMPT_BEFORE 补了上限这一行。
- 第 3 步真实运行：工单会话读不到模型环境变量，没跑。主会话怎么跑见下面的 Evidence。
- 审查第 1 轮：
  - Spec：用户级 Stop 钩子拦下收尾会让 CLI 多算一轮，预算却不算，剩余轮数就偏多。已修：一次没调工具的主线程回复后面如果又来了回复，预算就停止提醒（是重试还是 Stop 拦截，消息流里分不出来）。f19-dab 里 270 次只有 1 次会因此停止提醒。另外 reminder 改名 take_reminder。
  - Codex P2（"每次 API 往返都算一轮"）：不属实。CLI 源码里截断续写是 {...St,maxOutputTokensRecoveryCount:_o+1}，不加 turnCount；它说的场景已由上一条停止提醒处理。
  - Standards 不修：build_options 在没传预算时自建一个，保证任何查询构建都带钩子（验收"任意组合都有钩子"）；turns 和 budget 一起传、测试里 hook_input 重复，都是小问题。
- 审查第 2 轮：Codex P2，turn_budget 事件记了本机的 transcript 绝对路径，而事件日志是可公开的。已修，改记 session_id。第 2 轮后没有新的意见。

### 主会话 2026-11-14（PR #77）
- 接受越出 Authority 的 `tests/test_task_file.py` 改动（68 也改了同一个逐字快照）。
- 合并 main（含 68）进分支：b3f1b70，只有 `tests/test_task_file.py` 一行注释冲突，两句都留；`make check` 2120 passed，1 skipped。
- 真实运行（deepseek-flash；临时 worktree 里把 `query` 上限改成 12，不提交；库由 `tests/fixtures/shop.sql` 建）。首个提交 5306be9 和合并后的 b3f1b70 各跑一遍：
  - 每次回复调 1 次工具、要求不停（baseline）：5306be9 上 `turn_budget` 在第 2、第 9 次回复各一条（剩 10、剩 3），之后正好 3 次回复，`error_max_turns`，`num_turns` 13。b3f1b70 上提醒位置相同，agent 在第 10 次调用后自己交了答案。
  - 每次回复并行调 2 次工具、要求不停（baseline）：两个提交上都是 24 次调用、12 次回复后 `error_max_turns`，提醒在第 2、第 9 次回复（第 3、第 17 次调用之后）各一条。上限数的是带工具调用的回复，不是调用次数。
  - 普通查询题（no-context）：只有"剩 10"一条提醒，6 次带工具的回复后正常作答；`num_turns` 记 13/14 但正常结束，和取证里 `Et` 的口径一致。
  - 提醒进了模型上下文：模型的思考里引用了它（"3 turns left. Used 9. So turns 10, 11, 12 remain"）。
  - 发现：提醒原文 "N turns left, counting this response" 有歧义。b3f1b70 的两次运行里模型把 "this response" 理解成刚跑完工具的那次回复，于是认为只剩 2 次（实际还剩 3 次）。偏差在保守一侧，但数是错的。
  - 花费：6 次运行合计约 $0.03。
- PR #77：CI 绿；Codex PR 审查（b3f1b70）没有意见。

### 退回：提醒原文的剩余轮数有歧义（主会话 2026-11-14，第 1 轮）
- 原因：`REMINDER_TEXT` 写 "{left} turns left, counting this response"。提醒是跟在工具结果后面进上下文的，模型读到时"this response"可以指刚跑完工具的那次回复，也可以指它接下来要写的那次。b3f1b70 上的两次真实运行里，模型的思考是 "3 turns left counting this response means after this one there are 2 more" 和 "Turn budget warned 3 turns left including that prior response"，少算了一次；5306be9 上那次理解对了。工单要求不告诉 agent 错的剩余轮数。
- 要改的：只改 `REMINDER_TEXT` 的措辞（需要时连同 `TURN_LIMIT` 和 Skill 第 6 节里对应的说法、相关测试），让"还能再发几次带工具调用的回复"只有一种读法，不依赖"this response"指哪一次。计数逻辑不动。
- 分支已合入 main（含 68），最新 b3f1b70，已 push；在这个分支上接着提交，不要 rebase。
- 最终回复里给出新的原文；真实运行仍由主会话做。

### 退回第 1 轮的修复（工单会话，dbc31d8、46f7e5a，基于 b3f1b70，未 rebase）
- 改动：只改措辞，计数逻辑没动。`REMINDER_TEXT`（src/data_agent/agent.py:708-717）把剩余轮数落在"下一次回复"上，并明说刚跑完工具的那次回复已经用掉了自己的一轮。新原文见上。Skill 第 6 节 :118 改为 "Reminders say how many turns are left, your next response included"。`TURN_LIMIT` 不变。
- 测试：tests/test_turn_budget.py 新增两个。
  - `test_the_reminder_counts_from_the_next_response`：原文没有 "this response"，带 "your next response included"、"3 more tool-calling responses"、"already used its turn"。修改前失败过。
  - `test_a_reminder_at_one_turn_left_is_singular`：剩 3 的提醒点到得晚的话，可能在剩 1 时才发，此时用单数。
- 检查：`make check` 2122 passed，1 skipped；basedpyright 0 errors。
- 审查：
  - da-review b3f1b70：没有意见。Codex 的沙箱跑不了 pytest，审查只看了 diff。
  - eng:code-review 的 Standards、Spec 两项都报了同一处：单数时原文是 "1 more response that call tools"，动词不一致。已修，改用 "tool-calling response(s)"，并配了上面那个单数测试。
  - Standards 还提到代码注释写的是审查经过，已改成写不变式；变量 `one` 改名 `singular`。
  - 没修的：
    - "原文三种说法重复、偏长"：是有意的消歧，有没有用要看真实运行。
    - "测试用子串锁定原文"：这是提示的约定测试，可以接受。
    - Spec 提的 "before then" 两种读法：两种读法下该做的事一样，不影响计数。
- 第 3 步真实运行：没跑，工单会话读不到模型的环境变量。配法同上次：临时 worktree 里把 `query` 上限改成 12，不提交，跑一道查询题。在 events.jsonl 里看三处：
  - `turn_budget` 事件的 `text`：应该在第 2、第 9 次回复后各一条。
  - 模型的思考里对剩余次数的理解：应该是剩 3 次，不是 2 次。
  - 结束时是 `error_max_turns`，还是提前作答。
- 新原文（剩 3 时）："Turn budget: 3 turns left, your next response included: you can send 3 more tool-calling responses, and after the last of them the run ends with no answer. The response whose tool results you are reading has already used its turn. Give your final answer, a response with no tool calls, before then."

### 主会话 2026-11-14：退回修复后的真实运行
- 合并 main（含 70）进分支：a48261f，无冲突。
- 在 a48261f 上重跑（deepseek-flash，上限临时 12，不提交），4 次，合计约 $0.02：
  - 每次回复 1 次调用、要求不停：提醒在第 2、第 9 次回复各一条；之后模型的思考依次是 "3 turns left"、"2 turns left"、"1 turn left"，数对了；正好 3 次回复后 `error_max_turns`，`num_turns` 13。
  - 每次回复并行 2 次调用、要求不停：24 次调用、12 次回复后 `error_max_turns`；提醒在第 3、第 17 次调用之后；模型在第 22 次调用后写 "One turn left"，数对了。
  - 要求用尽轮数但必须作答：模型读到剩 3 的提醒后写 "I can send 3 more tool-calling responses but the last ends with no answer. So I should do at most 2 more tool calls, then final answer"，做了第 10、11 次调用后正常作答（答案 "Last k run: 11"）。这是上限 12 时能作答的最多调用数。
  - 普通查询题（no-context）：只有"剩 10"一条提醒，正常作答。
- 结论：b3f1b70 上少算一次的问题在这 4 次里没有再出现。
- PR #77：a48261f 上 CI 绿，Codex PR 审查没有意见；squash 合入 main（c1f5503）。
