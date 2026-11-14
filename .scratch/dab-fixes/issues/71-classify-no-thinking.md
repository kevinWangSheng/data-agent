# 71 — classify_text 的模型调用关掉思考

Status: done
Blocked by: —
Spec: ../../../docs/adr/0005-dab-integration.md（Update 2026-11-14）；工单 70 的 Comments
Claimed by: claude-opus-5-5 (worktree 71-classify-no-thinking)
Branch: worktree-71-classify-no-thinking

## Goal

`classify_text` 给模型发的每一批请求都关掉思考，输出里只有标签的 JSON。回复被输出上限截断时，工具如实说明是截断，不再只报"回复里没有 JSON"。

## 已和用户定下的（2026-11-14）

- 人同意按主会话的方案修：分类调用在请求体里加 `thinking: {"type": "disabled"}`。
- 只影响 `classify_text` 自己发的模型调用。主 agent 的会话（CLI）不动，它的思考照旧。
- 这是工具的通用缺陷，不是针对某道题：任何默认开思考的端点上，任何数据的分类都会这样。

## Authority

- May edit: `src/data_agent/classify.py`、`tests/test_classify.py`、`tests/fake_model.py`
- Must not edit: `skills/`、查询与 dbt 任务的提示、`src/data_agent/configs/*.toml`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`；`ROW_LIMIT` 的数值（主会话重新量过成本后定）
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- 现象（小范围验证，c1f5503，`runs/v18c-no-context-agnews`）：一次 `classify_text` 调用分类 6,696 行，134 次模型调用，441,052 个输入 token、612,244 个输出 token，$0.87；1,296 行记成 `failed`，原因 "unreadable reply: the reply has no JSON object"。三次运行都在工具里撞到单次运行 $1 预算，没有答案。
- 根因（主会话在本机复现，deepseek-flash，同一个端点）：模型默认开思考。50 条新闻一批：
  - 不带 `thinking` 参数：回复是一个 thinking 块（15,825–21,786 字符）加一个 text 块（842 字符），输出 3,735–4,940 个 token，`stop_reason` 是 `end_turn`。
  - 带 `extra_body={"temperature": 0, "thinking": {"type": "disabled"}}`：只有 text 块，输出 221 个 token。
  - 思考用光 `MAX_OUTPUT_TOKENS`（8,192）时回复里没有 text 块，`parse_labels` 报 "the reply has no JSON object"（`src/data_agent/classify.py:232`）。
- 调用在 `anthropic_model`（`src/data_agent/classify.py:95-125`）：`client.messages.create(..., extra_body={"temperature": 0})`。`thinking` 是 Anthropic Messages API 的正式参数，`{"type": "disabled"}` 是合法取值；用 SDK 的参数还是放 `extra_body`，看这个 SDK 版本的 `create()` 签名定。
- 回复的 `stop_reason` 现在没有被读。截断（`max_tokens`）和"模型没按格式回"是两种事实，报错要分开（工具返回事实，不返回猜测）。
- `tests/fake_model.py` 是假的模型端点，能看到请求体。

## Acceptance

- [ ] 新测试（假模型）：每一批请求的请求体里有 `thinking` 且 `type` 是 `disabled`，`temperature` 仍是 0。
- [ ] 新测试：假模型返回 `stop_reason: max_tokens` 且没有可读的 JSON 时，这一批的行记成 `failed`，工具返回的错误说明里写明回复在输出上限处被截断；和"回复里没有 JSON"的说法不同。
- [ ] 新测试：回复里带 thinking 块和 text 块时，只读 text 块（端点忽略这个参数时仍能工作）。
- [ ] 现有 `tests/test_classify.py` 全部通过；`make check` 通过。

## Evidence required

- 测试输出。
- 主会话在合入前做真实运行：用上面那批 50 条新闻样式的文本直接调工具，贴出模型调用次数、输入输出 token、花费；再用接近真实长度的文本量每千行成本，定 `ROW_LIMIT`。工单会话在最终回复里写明命令。

## Stop conditions

- 这个 SDK 版本没法把 `thinking` 发进请求体。
- 需要改提示、Skill 或 `ROW_LIMIT` 才能做下去。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0.09, main session's real runs (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- Implementation: d225620 + a6d5e44 (branch worktree-71-classify-no-thinking). Each anthropic_model request sets thinking={"type":"disabled"} (an SDK 2.8.0 create() parameter) with temperature 0 still in extra_body; Reply carries stop_reason; in run_batch, unreadable JSON with stop_reason=max_tokens → "unreadable reply: the reply was cut off at the output limit (8192 tokens) and has no readable JSON object", otherwise the old message.
- Tests: 4 new (thinking disabled with temperature 0 in every batch; max_tokens truncation reported separately; uncut reply with no JSON keeps the old message; with a thinking block only the text is read; the last already held before this change). Before the change the first two failed (red); after, tests/test_classify.py has 39 passed. make check: baseline 2159 passed/1 skipped → 2163 passed/1 skipped (after the review fixes too).
- Real run: not done (Server access: none; the worktree session cannot source the model env). For the main session to run:
  1) `make test-integration` (includes test_classify.py::test_the_run_model_classifies_the_samples, budget $0.05)
  2) Call classify_text directly on the 50 news-style texts from the ticket, and record model calls, input/output tokens and spend from the tool reply's "Model calls:" line; then measure cost per 1,000 rows on near-real-length text and set ROW_LIMIT.
- Review 1, da-review main (Codex): "No actionable correctness issues were found in the diff."
- Review 2, eng:code-review main: Spec found nothing missing or out of scope. Fixed in a6d5e44: (1) the truncation wording said "before its JSON was complete", but when thinking uses up the output there is no text at all, so it now just states that it was cut and has no readable JSON; (2) removed Reply.stop_reason's unused default value; (3) renamed the fake model's `reply` field to `rewrite` (it clashed with the handler method's name). Not fixed:
  - The bare "max_tokens" string also appears in agent.py: two places, same API constant; a shared constant would mean editing agent.py, outside Authority.
  - A cut-off reply whose JSON is broken for another reason is still reported as cut: truncation is the fact the endpoint gave, so reporting it that way is correct.
  - The thinking-block test is a characterization test: the old code already only read text blocks; the test pins that down.

### 主会话 2026-11-14：真实运行（a6d5e44，deepseek-flash，临时 worktree）
- 集成测试 `uv run pytest -q -s -m integration tests/test_classify.py`：1 passed；1 次模型调用，561 input、130 output token，$0.0003（关思考前同一测试输出 322 个 token）。
- 直接调工具，1,000 行新闻样式的文本（10 个主体 × 5 个事件 × 20 个结尾，各不相同，平均 141 字符；四个类别 World / Sports / Business / Science/Technology）：1,000 行全部拿到标签，20 次模型调用，36,610 input、5,766 output token，$0.0179，墙钟 7.3 秒。每行约 5.8 个输出 token；关思考前同样式的 50 条一批输出 3,735–4,940 个 token。
- 同样的文本重复 8 遍（约 220 token/行）：1,000 行全部拿到标签，39 次调用，218,417 input、6,472 output，$0.0733，墙钟 9.0 秒。
- `ROW_LIMIT` 维持 20,000：新闻长度的文本 20,000 行约 $0.36，在单次运行 $1 预算内；约 220 token/行的长文本约 $1.5，由运行预算拦停。工单 70 里主会话量的每千行成本作废，以这里为准。
- PR #79：CI 绿；Codex PR 审查（a6d5e44）没有意见；squash 合入 main（30b1021）。
