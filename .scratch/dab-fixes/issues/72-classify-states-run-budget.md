# 72 — classify_text 写明运行的真实预算和已花金额

Status: done
Blocked by: —
Spec: ../../../docs/adr/0005-dab-integration.md（Update 2026-11-14）；工单 70、71 的 Comments
Claimed by: claude-opus-5-5 (worktree 72-classify-states-run-budget)
Branch: worktree-72-classify-states-run-budget

## Goal

`classify_text` 的工具描述写明本次运行的预算金额；每次返回的最后一行写明本次运行到目前为止花了多少、预算多少。产品服务器和 DAB 服务器行为一致。

## 已和用户定下的（2026-11-14）

- 人把"agnews 第 4 题的空答案要不要修、怎么修"交给独立上下文的 agent 决定，并说小修可以做。独立 agent 读了轨迹和代码后定：修，做法如本工单。
- 只改 `classify_text` 的描述和返回。提示、Skill、预算数值、停止逻辑都不动。
- 写进去的都是账本里的事实。不加成本估计、不拒绝调用、不留预留金额、不写怎么查询的建议。

## Authority

- May edit: `src/data_agent/classify.py`；`src/data_agent/mcp_server.py` 和 `evals/dab_server.py` 里注册 `classify_text` 的部分；`tests/test_classify.py`；`tests/test_dab.py`（只在描述的断言需要时）
- Must not edit: 提示（`QUERY_PROMPT`、DAB 任务提示、`TURN_LIMIT`、`ANSWER_RULE`）、`skills/`、`src/data_agent/configs/*.toml`、`ROW_LIMIT`、`classify()` 里的停止逻辑、`Spend`、`sdk_budget_usd`、`src/data_agent/tool_spend.py`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- 现象（复验，30b1021，`runs/v18t-no-context-agnews/agnews/query4/`）：三次运行里两次以 `error_max_budget_usd` 结束，没有答案。工具本身正常，每一行都拿到标签。
- 根因：agent 以为预算是 $83，实际是 $1。
  - 三次运行的思考里都写着 $83（run0 seq 26、73；run1 seq 26、65；run2 seq 67）。run1 在第一次 20,000 行调用之后算出"分完 127,600 行约 $1.9，没问题"，随后放弃了更省的做法。
  - $83 的来源：`sdk_budget_usd`（`src/data_agent/agent.py:270-280`）把交给 SDK 的兜底上限按价格比放大，deepseek-flash 下 $1.0 变成 $83.33；CLI 会给模型发 `USD budget: $used/$total; $remaining remaining` 的提醒。事件文件不记系统提醒，所以"思考里的 $83 来自这条提醒"是推断，依据是数值吻合、`tests/fixtures/subagent_transcript.jsonl:30` 和 CLI 里的字符串。
  - 真实预算不在 agent 的上下文里：提示里只有 `TURN_LIMIT`；工具描述最后一句只说 "count toward this run's spend and budget"，没有金额（`src/data_agent/classify.py:73`）；返回里只有这一次调用的花费（`classify.py:457-461`）。
- 工具能拿到这两个数：`Ledger.run_state()` 给 `budget_usd`，`Ledger.spent_usd(state)` 给运行自己的花费加所有工具调用（`src/data_agent/tool_spend.py:65-70, 96-98`）。账本在 SDK 启动前建好（`agent.py:582-584`），两个服务器注册工具时账本的环境变量已经设好（`mcp_server.py:278-287`、`evals/dab_server.py:445-452`、`agent.py:988`）。
- 撞到预算的那次调用之后运行立刻结束（`classify.py:326-332`、`agent.py:523-528, 604-614`），agent 读不到那次返回；所以预算要在之前的返回和描述里就能看到。两次失败的运行都先做过一次 5 行的试探调用。
- 一次 20,000 行调用的实际花费：描述字段 $0.4853，标题字段 $0.2929。

## 要写的文字

返回的最后一行，数字在最后一批结束后从账本读：

```
Run spend so far: $0.5012 of the run's $1.0 budget (US dollars at this run's model prices, this tool's calls included). The run ends with no answer once its spend passes the budget.
```

`DESCRIPTION` 的最后一句（`classify.py:73`）换成下面这段，金额在注册时从账本读：

```
The model calls are paid from this run's budget of ${budget} (US dollars at this run's model prices). Every reply ends with the run's spend so far against it. Once the spend passes the budget the run ends at once with no answer, so a call that reaches the budget leaves no turn to use its labels. This is the limit that ends the run: a "USD budget" system reminder, if you see one, is counted at other prices against a higher backstop and leaves this tool's calls out.
```

没有账本时工具本来就拒绝运行；这时描述里不写这段，工具照常注册。

## Acceptance

- [ ] 新测试（假模型）：`run.json` 里花费 0.25、预算 1.0 时，返回的最后一行写的花费是 0.25 加这次调用按价格算的金额，预算是 1.0。
- [ ] 新测试：第二次调用返回的花费包含第一次调用的花费。
- [ ] 新测试：因预算停下的调用，返回里的花费大于等于预算，原有的 "not classified" 那一行保留。
- [ ] 新测试：两个服务器的工具描述都含账本里的预算金额和"运行结束且没有答案"那句话。
- [ ] 新测试：没有账本时服务器仍然注册这个工具。
- [ ] 现有测试全部通过；`make check` 通过。

## Evidence required

- 测试输出。
- 主会话在合入后做真实运行（no-context，修复提交上）：
  - agnews 第 4 题 ×3、第 2 题 ×3。过线：第 4 题三次都有答案，没有 `error_max_budget_usd`；第 2 题三次都调了工具并给出答案。读第 4 题的轨迹，记下思考里引用的是 $1.0 还是 $83。
  - 十道原本通过的题各 1 次（描述在每次运行的工具列表里）。过线：至少 9/10；失败的读轨迹看有没有提到预算。
  - 只做一轮。第 4 题仍有空答案就记成已知限制，把 SDK 提醒的问题交给人，不再改措辞。

## Stop conditions

- 任一服务器在注册工具时读不到 `run.json`（这时只交付返回里的那一行，并在最终回复里写明）。
- 需要改提示或 Skill 才能做下去。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0.90, main session's real runs (local $0.003, server validation about $0.90; data-agent model calls only)
- Outcome: accepted
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- Implementation: 79632f9 + aef9017 (branch worktree-72-classify-states-run-budget). Every classify_text reply ends with the "Run spend so far" line, figures read from the ledger after the last batch; on both servers the description's last sentence is replaced by the budget paragraph, amount read from the ledger at registration. Without a ledger the tool is still registered and the description has no budget paragraph.
- TDD: on the original code 4 of the new or extended tests failed (reply line, budget-stop spend ≥ budget, description on both servers). The 2 no-ledger tests pass on old and new code; they guard that the tool is still registered. After the change tests/test_classify.py: 44 passed. make check after the review fixes: 2168 passed, 1 skipped.
- Review 1, da-review main (Codex): "No actionable correctness issues were found."
- Review 2, eng:code-review main: Spec: every requirement implemented, nothing out of scope; one gap, the DAB no-ledger test did not check that the "no answer" sentence is absent, fixed in aef9017. Standards: duplicated ledger lookup in setup_from_env and description() fixed by extracting run_ledger() (aef9017). Not fixed:
  - run_spent_usd / run_budget_usd travel together: minor, they sit next to the existing budget field.
  - The name `after`: reads clearly in context.
  - Repetition across the four description tests: one test per acceptance item.
  - Budget printed as a raw float ("$1.0"): follows the existing convention and the ticket's wording.
  - The description says "Every reply ends with the run's spend"; error replies (ToolError) do not carry the line. The ticket fixes this wording.

### 主会话 2026-11-14：真实运行与合入
- 本机真实运行（aef9017，deepseek-flash，baseline，20 篇文章的样本库）：`uv run data-agent run --type query --config baseline --duckdb articles20.duckdb ... --task "How many of the articles are about sports, by the meaning of their text? Use the classify_text tool ..."`，退出码 0，答案 Sports 5。工具返回的最后一行："Run spend so far: $0.0025 of the run's $1.0 budget (US dollars at this run's model prices, this tool's calls included). The run ends with no answer once its spend passes the budget."。说明真实运行里工具读得到 `run.json`。
- PR #80：CI 绿；Codex PR 审查（aef9017）没有意见；squash 合入 main（cc8cdef）。
- 服务器复验（cc8cdef，no-context，`runs/v18u-no-context-*`，实付约 $0.90）：
  - 十道原本通过的题各 1 次：10/10，过线。
  - agnews 第 4 题 ×3：没有 `error_max_budget_usd`（花费 $0.75、$0.28、$0.39）。两次给出答案（North America，判错），一次（run0）上下文写满、没有答案。按"第 4 题三次都有答案"这条没过，原因不是预算。
  - agnews 第 2 题 ×3：三次都调了工具并给出答案（17/111、15/111、16/111），1/3 通过。
  - 思考里引用的预算：三次第 4 题运行都同时提到 $1.0 和 $83，并说明 $1.0 是这个工具的预算（run0 seq 19："$1.0 separate from USD budget $83"）。"$83 来自 CLI 提醒"由此得到印证。
  - 轨迹诊断（sonnet agent，推断）：答错是分类波动，不是预算文字造成的；第 4 题 Africa 和 North America 只差 2–7 篇。
- 按本工单"只做一轮"：不再改措辞。第 4 题因反复验证写满上下文而没有答案，记成已知限制，写进 HANDOFF 交给人。
