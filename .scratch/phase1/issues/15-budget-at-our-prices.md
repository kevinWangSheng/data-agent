# 15 — 单次花费上限按本项目价目表计

Status: done
Blocked by: 07
Spec: ../spec.md
Claimed by: claude (worktree 15-budget-at-our-prices)
Branch: worktree-15-budget-at-our-prices

## Goal

运行配置的 `max_budget_usd` 按本项目价目表（`agent.py` 的 `PRICES`，含验证子 agent 的 token）算出的真实花费生效：超过就停下，事件文件记为预算耗尽。SDK 自带的 `max_budget_usd` 只作后备，不再在真实花费约 $0.035 时截停非 Claude 模型。

## Authority

- May edit: `src/data_agent/`（花费统计与停止逻辑、运行配置读取）、`evals/dab.py`（只限接上同一套上限）、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`src/data_agent/configs/*.toml` 里的数值（$1 不变）
- Server access: none
- Spend cap: $1

## Context

- 来源：工单 09 实测（见 09 Comments）：SDK 按 Claude 单价判断 `max_budget_usd=$1`，DeepSeek 下 5 次 data-eng-bench 运行有 4 次在 36–48 轮被截停（`terminal_reason` budget_exhausted），按价目表只花了约 $0.035。人决定（2026-10-16）：用自己的价目表执行上限，SDK 上限只作后备。
- spec「运行配置」：单次 `max_budget_usd`（flash 先设 $1），user story 19（每次运行有花费上限）。
- `src/data_agent/agent.py`：`PRICES`、`cost_usd` / `sdk_cost_usd` / `cost_basis` 的算法（约 :129、:336–378）；工单 07 改为按 SDK 的分模型用量计入子 agent。
- 不在价目表里的模型（如 Claude）SDK 本身计价正确，仍用 SDK 的上限。

## Acceptance

- [ ] 价目表里的模型：运行中按价目表累计花费（含子 agent），超过配置的 `max_budget_usd` 即停止；结果事件 `terminal_reason` 记为预算耗尽，`cost_usd` 为停止时的花费。允许的超出量：最后一次主线程模型调用 + 当时正在运行的验证子 agent（人决定 2026-10-16）。主线程用 `include_partial_messages` 的 `message_delta` 精确计量（含 output）；验证子 agent 在 `SubagentStop` 时读 `agent_transcript_path` 精确计入；只在主线程两次调用之间检查上限。
- [ ] 依赖 CLI 子 agent 记录文件的行格式：解析失败时不静默当 0，记进事件并退回到 SDK 的 model_usage；配一个用真实格式样本的测试，格式变了能发现。
- [ ] DAB runner（`evals/dab.py`）用同一套上限与后备值。
- [ ] 价目表里的模型：传给 SDK 的 `max_budget_usd` 是后备值，不会在价目表花费远低于上限时截停（测试断言后备值与换算方式）。
- [ ] 不在价目表里的模型：行为不变，SDK 上限即配置值。
- [ ] 单元测试用假消息流覆盖以上三条；修复前会失败。
- [ ] `make check` 通过

## Evidence required

真实 dbt 运行两次（接缝 A 的 DBT_TASK，full 配置）：一次临时把上限设成 $0.005（命令行或临时配置，不改提交的配置文件）证明会按价目表停下；一次用默认 $1 证明不再在 ~40 轮被截停。贴两份结果事件。

## Stop conditions

SDK 拿不到运行中的逐轮用量、无法在运行中停止（要写明查过的 SDK 文档与源码位置）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3
- Review rounds: 3
- Model spend: ~$0.44（之前 ~$0.41；本轮真实运行 $0.029）
- Outcome: PR #16 退回第 1 轮已修
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（分支 worktree-15-budget-at-our-prices，提交 4e90e26，不可合并）

- 触发停止条件"SDK 拿不到运行中的逐轮用量"（部分）。查过：claude_agent_sdk 0.2.168 的 types.py（AssistantMessage.usage、StreamEvent、TaskProgressMessage/TaskUsage、ResultMessage.terminal_reason、SubagentStop 的 agent_transcript_path）、_internal/message_parser.py（assistant 的 usage 取自 message.usage）、query.py（aclose 会关掉 CLI）、自带 CLI 的计价代码（未知模型按回退表 XI 计：$5/$25，缓存写 $6.25，缓存读 $0.50）。
- 实测（见下方命令，full 配置，seam A 的 DBT_TASK，上限只在内存里替换，不改提交的配置）：
  - 上限 $0.005：按价目表 $0.0059 停下，result 事件为 `subtype error_max_budget_usd, terminal_reason budget_exhausted, cost_usd 0.0059241, output_tokens 0`。output 为 0 暴露了问题：流里每条 AssistantMessage 的 usage 是响应开头的值，DeepSeek 下 output_tokens 恒为 0。
  - 3 次 $1 探测都正常跑完（$0.036、$0.031、$0.040，SDK 记 $0.82/$0.71/$0.92），没有被截停。
  - 开 include_partial_messages 后，主线程每次调用的 message_delta 带完整用量（含 output）。子 agent 没有流事件：流里只转发它的部分 tool_use 块，最后一次调用（报告）不转发；Agent 工具结果的 usage 只是最后一次调用；task_progress 的 total_tokens 推不出逐次 output（推出 27841，实际 13796）。
  - CLI 写在磁盘上的子 agent 记录（.../subagents/agent-<id>.jsonl）里，每个消息 id 的最后一条 usage 加起来 = 13796，与 model_usage 完全一致。SubagentStop hook 会给出 agent_transcript_path；文件路径字段在 SDK 有类型，文件里每行的格式没有文档。
- 4e90e26 的状态：Spend 计量、在价目表内用后备上限（flash $83.3，v4-pro 为上限×25/3.96）、停止并写预算耗尽 result、单元测试（make check 812 passed）。缺陷：输出 token 记为 0；检查放在同一响应的两条消息之间，会在调用中途停。不可合并。
- 需要人决定：上限能否允许超出"最后一次模型调用 + 当时在跑的验证子 agent"，并依赖子 agent 记录文件的格式。推荐：可以。做法：主线程用 message_delta 精确计量，验证子 agent 在 SubagentStop 时读 agent_transcript_path 精确计入，只在主线程两次调用之间检查上限。本题验证子 agent 每次约 $0.017。
- 另有缺口：evals/dab.py 有自己的消息循环，DAB 运行没有价目表上限（SDK 上限仍是 $1）。evals/ 不在本工单 Authority 内，建议另开工单。
- 未做：两道审查（停在实现阶段）。
- 命令：`zsh /tmp/ticket15_run.sh <worktree> <name> <budget>`（照 make test-integration 的方式加载 env，调用 agent.run，并把每条消息的用量记到 runs/ticket-15/<name>/usage.jsonl）。

### 2026-10-16 主会话：人的决定，继续第 2 次尝试

人决定（2026-10-16）：上限允许超出"最后一次主线程调用 + 当时在跑的验证子 agent"，可以依赖子 agent 记录文件格式；DAB runner 并入本工单（Authority 已加 `evals/dab.py`）。验收标准已按此更新。在原分支上接着做（4e90e26 的输出计 0、调用中途停两个缺陷一并修），不 push、不 rebase（需要同步 main 时 `git merge origin/main`）。完成后照工作流程跑 `make check`、真实运行（$0.005 上限一次、默认 $1 一次；DAB stockindex 一题一次）、两道审查。

### 2026-10-16 工单 agent 第 2 次尝试（分支 `worktree-15-budget-at-our-prices`，提交 `2cd6e66`、`4453652`、`cfa3f2f`；已 `git merge origin/main`，未 push）

**做法**（`src/data_agent/agent.py`、`evals/dab.py`）
- **主线程计量**：开 `include_partial_messages`，按 `message_start` / `message_delta` 精确计量，output 计入（修掉第 1 次"输出计 0"）。
- **验证子 agent 计量**：在 `SubagentStop` 时读 `agent_transcript_path`。实测发现 hook 触发时，CLI 还没写完子 agent 最后一次调用的最终用量行；现在等到每次调用的最终行（带 `stop_reason`）出现再读，最多等 5 s。修复前 $1 运行少计 2662 个 output token，正好是两个验证子 agent 最后一次调用（1269 + 1393）。修复后运行中的计数与 SDK 的 `model_usage` 逐项相等。
- **检查时机**：只在一次用了工具的主线程调用结束之后、且没有子 agent 在跑时检查上限（修掉第 1 次"调用中途停"）。最终答案即使越过上限也保留；并行的两个验证子 agent 都跑完才停。
- **SDK 上限**：价目表内的模型，传给 SDK 的是后备值（flash 为上限 × 0.50/0.006）；不在价目表的模型仍用配置值。
- **记录文件格式**：解析失败（缺文件、非 JSON、缺 usage、没有调用、等不到最终行）时写一条 `error`（`source: spend`）事件；本次运行余下部分关掉价目表上限，由 SDK 结束运行，结果按 SDK 的 `model_usage` 计价。测试用真实格式样本 `tests/fixtures/subagent_transcript.jsonl`：CLI 2.1.301 的真实记录，路径和长文本已脱敏。
- **DAB runner**：用同一套上限与后备值，被截停的运行记为 `unfinished`，答案为空。
- **停止时关闭 SDK 会话**：`agent.query` 在返回前关掉 SDK 会话。SDK 自带的 `query()` 不关内层 generator，要等事件循环的终结器去关。

**检查**：`make check` 通过：822 passed, 15 deselected，basedpyright 0 errors。

**真实运行**：dbt 用接缝 A 的 DBT_TASK、full 配置，上限只在内存里替换，不改提交的配置文件。命令为 `zsh /tmp/t15/run.sh <worktree> <name> <budget>`，它像 `make test-integration` 那样加载 env，再调用 `agent.run`。
- **上限 $0.005**（提交 `4453652`）：
  `{"subtype": "error_max_budget_usd", "is_error": true, "num_turns": 1, "terminal_reason": "budget_exhausted", "uncached_input_tokens": 19753, "cached_input_tokens": 0, "output_tokens": 176, "cost_usd": 0.0061371, "sdk_cost_usd": null, "cost_basis": "price_table", "errors": ["spend $0.0061 at the price table passed the $0.005 budget"]}`
- **默认 $1**（提交 `4453652`）：正常跑完，不再在约 40 轮截停：
  `{"subtype": "success", "is_error": false, "num_turns": 22, "terminal_reason": "completed", "uncached_input_tokens": 47645, "cached_input_tokens": 303488, "output_tokens": 15366, "cost_usd": 0.034553628, "sdk_cost_usd": 0.774119, "cost_basis": "price_table", "errors": []}`
  运行中的计数为 (47645, 303488, 15366)，与上面的结果事件一致。
- **上限 $0.02**（提交 `cfa3f2f`）：两个验证子 agent 都结束后才停：`budget_exhausted`，`cost_usd` 0.02832，`num_turns` 11。
- **DAB stockindex query 2，默认上限**：
  `DOCKER_HOST=unix://$HOME/.colima/m1-kind/docker.sock uv run --env-file ~/.config/data-agent/env --with-requirements evals/dab-requirements.txt python -m evals dab --dab-root <13 的 clone> --dataset stockindex --query 2 --runs 1 --config full --out runs/ticket-15-a2/dab-out2 --budget 1`
  结果：`finished`，exit 0，`cost_usd` 0.009443，`sdk_cost_usd` 0.217442。
- **DAB 上限 $0.002**（`/tmp/t15/dab_cap.py`，内存里替换上限）：`unfinished`，`budget_exhausted`，`cost_usd` 0.00234，提交文件里 answer 为 `""`，没有残留容器。DAB clone 未改动（数据库文件的修改时间仍是工单 13 下载时的 07:14）。

**审查**
- **第 1 轮 `da-review main`（Codex）**，两条 P2 都属实，已在 `4453652` 修：
  - 停止时没等 SDK 关闭。改为 `agent.query` 在返回前关掉会话，配了测试。
  - 记录文件解析失败后，截停结果会低报花费。改为读取失败即交给 SDK 结束，按 `model_usage` 计价。
- **第 2 轮 `eng:code-review main`**，在 `cfa3f2f` 修了三处：`Tokens` 改为带字段名的 NamedTuple；`Spend.check()` 自己置位停止标记，不再由 `capped` 从外面改；`between_calls` 补了注释。不修的几条及理由：
  - dbt 与 DAB 的消息循环有重复：DAB 早就有意照搬 `run`，合并它们是本工单之外的重构。
  - `agent.py` 涨到约 740 行，`Spend` 一套可以拆成独立模块：同属重构，建议另开工单。
  - `Spend.result_fields` 与 `result_fields` 手写了同一组键：只有两处，暂不抽象。
  - 停止时的 `num_turns` 计的是主线程调用次数，SDK 的计法约为它的 2 倍；`sdk_cost_usd` 为 null，因为 SDK 没有出结果。记为已知限制。
  - `_internal.client.InternalClient` 是 SDK 私有接口：SDK 版本已固定，行为有测试，升级 SDK 时要复查。
  - 审查者说 $0.02 运行里有一个验证子 agent 被中途截掉：那次运行在"子 agent 在跑时不检查"的修复之前；在 `cfa3f2f` 上重跑，两个都跑完了。
  - `cap-1` 那次运行计数与 SDK 不一致：就是上面说的记录文件写入竞争，已修。
- **已知限制（不修）**：被截停时，Python 退出阶段会往 stderr 打一段 `Exception ignored ... Event loop is closed`。原因是 SDK 0.2.168 终止 CLI（退出码 143）之后，把它的 stdout 管道留给垃圾回收，回收发生在事件循环关闭之后。SDK 自带的 `query()` 也一样；进程已经结束，事件和结果都正常。

**待人决定**：记录文件读取失败后，价目表上限会在本次运行余下部分关掉，只剩 SDK 后备上限。flash 的后备是 $1 × 83.3；按实测 SDK 与价目表约 17–23 倍的比例，相当于价目表价格下的约 $3.6–5。另一种做法是保持价目表上限，但截停时花费会少报，Codex 已把这种做法判为问题。现在照人的原话"退回到 SDK 的 model_usage"实现。是否接受这个更松的上限，需要人确认。

**主会话判断（待人决定那条）**：接受"记录文件读取失败后本次运行退回 SDK 后备上限"。理由：只在 CLI 改记录格式时发生、每次都写 `error` 事件、批量花费跟踪（工单 11）在总预算 80% 时停整批，单次放松的风险有界；另一种做法会少报花费。交人在最终审查时复核。

### 2026-10-16 第 7 步：PR #16 退回第 1 轮（主会话）

`cfa3f2f` CI 两次全绿。Codex 1 条 P1，属实：

1. **`src/data_agent/agent.py:319`**：计费用的 `SubagentStop` hook 不过滤子 agent 类型；CLI 的内部活动/压缩摘要子 agent 也会触发 `SubagentStop`（上游 issue anthropics/claude-code#91910），读不到记录文件就把 `transcript_failed` 置位，本次运行余下部分价目表上限失效。照旁边 `verifier_hooks` 的做法，只对 `agent_type in VERIFIER_NAMES` 读记录文件；其他子 agent 的用量不在这里计（由结束时的 SDK `model_usage` 对账，若对账出差额写进结果事件）。配修复前会失败的测试。

在原分支上修，修完提交，不 push、不 rebase。修完 `make check`、默认 $1 真实 dbt 运行一次，再跑一轮 `da-review main`。最终回复给出 Comments、Ledger（Attempts 3）和 `Status: in-review`。

#### 2026-10-16 Ticket agent: PR #16 return round 1 (branch `worktree-15-budget-at-our-prices`, commits `355330f`, `cb60043`; not pushed)

**The Codex P1 fix** (`355330f`, `src/data_agent/agent.py`, `evals/dab.py`)
- The spend hooks now only act on `agent_type in VERIFIER_NAMES`, the same check `verifier_hooks` uses.
  - Only verifiers are added to `running` at `SubagentStart`.
  - At `SubagentStop`, any other subagent returns straight away. Its transcript is not read, `transcript_failed` is not set, and no `error` event is written.
- Reconciling with `model_usage`: when the SDK ends a run on a model in the price table, the new `ended_by_sdk` → `Spend.unmetered` compares the cap's own count with the tokens in `model_usage`.
  - If they differ, the result event gets an extra `unmetered_cost_usd` field: the difference priced from the price table.
  - If they match, the field is left out.
  - After a transcript read failure the field is also left out, because that case already has its own `error` event.
  - `evals/dab.py` uses the same function.
- New tests, both of which failed before the fix:
  - `test_other_subagents_leave_the_cap_on`: before the fix the run was not stopped (`finished=True`).
  - `test_spend_the_cap_did_not_count_is_in_the_result`: before the fix, `KeyError: 'unmetered_cost_usd'`.
  - `test_result_has_no_gap_when_the_cap_counted_everything` is the matching case where the counts agree.

**Checks**: `make check` passes: 826 passed, 15 deselected; basedpyright 0 errors.

**Real run** (seam A's DBT_TASK, full config, default $1; run on `355330f`)
- Command: `zsh /tmp/t15/run.sh <worktree> r3-default-1 1`
- Result event:
  `{"subtype": "success", "is_error": false, "num_turns": 21, "terminal_reason": "completed", "uncached_input_tokens": 44736, "cached_input_tokens": 303872, "output_tokens": 11385, "cost_usd": 0.028906032, "sdk_cost_usd": 0.660241, "cost_basis": "price_table", "errors": []}`
- The cap's own count, `Tokens(44736, 303872, 11385)` (11 main-thread calls, 10 verifier calls, `transcript_failed False`), equals the SDK's `model_usage`, so there is no `unmetered_cost_usd`.
- Both verifiers started and finished, and there was no `error` event.
- `cb60043` only changes behaviour when a response ends with `max_tokens`. This run had none (every main-thread `message_delta` was `tool_use` or `end_turn`), so I did not run it again.

**Review**
- **First `da-review main`**, one P2, valid, fixed in `cb60043`.
  - The problem: a main-thread response cut off at `max_tokens` without a tool call was treated as a final answer. The cap was then not checked before the CLI's automatic follow-up call, so spend could keep going past the cap.
  - The fix: a `message_delta` with `stop_reason == "max_tokens"` also marks the point between two calls where the cap is checked.
  - Test: `test_response_cut_at_its_output_limit_is_not_a_final_answer`, which failed before the fix with `finished=True`.
- **Second `da-review main`** (on `cb60043`): "No actionable regressions were identified."

**Known limitation (not fixed)**: if the cap stops the run while one of the CLI's own subagents has spent tokens, that spend is missing from the reported `cost_usd`. The SDK never sends its own result in that case, so there is no `model_usage` to compare against. Only runs the SDK ends get `unmetered_cost_usd`.

### 2026-10-16 第 7 步：PR #16 合并（主会话）

`cb60043` CI 两次全绿；Codex 复审 `has_any_findings: false`。合并。worktree 保留（`runs/ticket-15*` 有实测证据），人看过后删。
