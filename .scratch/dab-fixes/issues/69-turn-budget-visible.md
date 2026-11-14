# 69 — 查询任务里 agent 能看见轮数预算

Status: ready-for-agent
Blocked by: —
Spec: ../../../docs/adr/0005-dab-integration.md（Update 2026-11-14）
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
