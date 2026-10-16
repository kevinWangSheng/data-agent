# 15 — 单次花费上限按本项目价目表计

Status: ready-for-agent
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

- Attempts: 1
- Review rounds: 0
- Model spend: ~$0.11（5 次真实运行）
- Outcome: —
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
