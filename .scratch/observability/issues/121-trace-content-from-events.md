# 121 — 开发集档 trace 补上 SQL 和模型文字（从我们自己的事件里取）

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 121-trace-content-from-events)
Branch: worktree-121-trace-content-from-events

## Goal

开发集档（`full`）的 Langfuse trace 里能看到每次工具调用的入参（`query_database` 的 SQL 等）和模型每一步的文字，满足 spec "验收"里"开发集档有 prompt、SQL、查询结果和模型回答"。留出集档（`structure`）一点不变。

## Authority

- May edit: `src/data_agent/tracing.py`、`src/data_agent/trace_relay.py`、`src/data_agent/agent.py` 里把事件交给导出的那几行、`src/data_agent/trace_check.py`（只加"有没有值"的显示）、对应的单元测试（`tests/test_tracing.py`、`tests/test_trace_relay.py` 或新文件）、`README.md` 的 "Traces in Langfuse" 一节
- 人授权的两处例外，其余验收测试一字不改：
  - 2026-11-23：`tests/acceptance/test_observability_switch.py` 的 `assert_usage_and_cost`（见 Context 第 4 条），只改它对父 span usage 的断言，cost 断言不变
  - 2026-11-24：`tests/acceptance/test_observability_relay.py` 的 `test_the_dev_tier_keeps_every_attribute_but_the_tokens_names_and_the_secrets`（见 Context 第 5 条），只在它的排除集合里加上开发集档要删的属性名（`user.id`、夹具里的 `host.*`、`os.*`），其余断言、夹具、输入一字不改
- `src/data_agent/agent.py:1836` 的 `text` 事件加 `parent=message.parent_tool_use_id`（`thinking` 那行同样加），和 `tool_start` 一致；只加字段
- Must not edit: `tests/acceptance/` 里除上面两处之外的一切、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、网页 `web/`
- Server access: none
- Spend cap: $1 of model calls（真实运行由主会话做）

## Context

- 人 2026-11-23 定（问"开发集档没有 SQL 和模型中间文字怎么办"，答"父进程从事件文件补"）。依据：spec "验收结果 2026-11-23"一节。
- 事实（`runs/otel-capture/scrubbed/README.md`，CLI 2.1.330 真实抓包）：CLI 不导出工具入参和模型输出文字，没有文档化的开关；开了内容开关只有 `user_prompt` 和 `tool.output` span 事件。工单 112 的"待人定"第 6 条（`.scratch/observability/issues/112-observability-backend.md`）。
- CLI 的 `claude_code.tool` 和 `claude_code.tool.execution` span 带 `tool_use_id` 属性（同一份抓包），和我们事件里 `tool_start.tool_use_id` 是同一个 id。`claude_code.llm_request` 没有能对上某条 assistant 消息的 id。
- 中转在本进程里（`trace_relay.py` `TraceRelay`，`rewrite`/`_rewrite_message` 改写每个批次），由 `tracing.RunExport` 启动；`RunExport` 拿到 run 的 secrets，事件流也在本进程（`agent.py` 的 `run`）。
- 做法（照这个方向，细节自己定，写成一处能改）：
  1. **工具入参**：开发集档下，`RunExport` 把每个 `tool_start` 的 `input`（按 `tool_use_id`）交给中转；中转转发 `claude_code.tool` span 时，按它的 `tool_use_id` 填 `langfuse.observation.input`（JSON 字符串，经 `Masks` 遮掉 secrets）。span 比对应事件先到时的处理（等一小段、或在 `finish` 时补一个带同一 `tool_use_id` 的观测）自己定，写进最终回复。
  2. **模型文字**：开发集档下，模型每条 assistant 消息的文字（事件里的 `text`；`thinking` 不带，人 2026-11-24 定）作为父 span 下的子观测（GENERATION 或 SPAN 自己定，名字如 `assistant.message`，按时间排），不尝试对到 CLI 的 `llm_request` 上；验证子 agent 的消息（`parent` 不为空）挂在同一层，带上 `parent` 便于区分。现在的 `text` 事件不带 `parent`（`agent.py:1836`），按 Authority 那一行补上。
  3. 留出集档：上面两条都不做，单元测试钉住（白名单之外的属性、`langfuse.observation.input/output` 一个都不出现）。
- 开发集档已有的东西保持：父 span 的 output 仍是最终答案；`tool.output` span 事件照旧转发。
- 导出关闭时不做任何事（不缓存事件）。
- 第 4 条，token 不重复（人 2026-11-23 定"父 span 只留 cost"）：验收运行发现父 span 带全程 `usage_details`，每个 GENERATION 又各带自己的，Langfuse 的 token 合计会重复；cost 不重复（只有父 span 有 cost）。改法：父 span 不再设 `langfuse.observation.usage_details`，全程 token 合计放进父 span 的 metadata（例如 `langfuse.observation.metadata.usage_total`，键名和 result 事件一致：`uncached_input_tokens`、`cached_input_tokens`、`output_tokens`），`cost_details` 照旧。两个档都这样做（留出集档白名单里的父 span 字段相应调整，只换名字不多字段）。`assert_usage_and_cost` 改成：父 span 没有 `usage_details`；metadata 里的合计等于 result；cost 断言不变。spec 里描述父 span 带 usage 的那句（`.scratch/observability/spec.md`，搜 usage）由主会话改，不在本工单。
- 第 5 条，开发集档也不带能认出机器的属性（人 2026-11-24 定，来自验收运行的发现：开发集档带 `user.id`、`terminal.type` 和主机/系统资源属性）：开发集档在中转里删掉 `user.id` 和所有层（含资源属性）上的 `host.*`、`os.*`；`terminal.type` 不在这次范围内，保留。删除名单写成一个常量。留出集档本来就删，不变。spec 对应的句子已由主会话改。

## Acceptance

- [ ] 单元测试：开发集档下，转发后的 `claude_code.tool` span 带 `langfuse.observation.input`，值是对应 `tool_start.input`（SQL 原文可见），并被 secrets 遮罩（测试里放一个假 secret 进 SQL）
- [ ] 单元测试：开发集档下，父 span 下有每条 assistant 文字的子观测，顺序与事件一致，验证子 agent 的带 `parent`
- [ ] 单元测试：留出集档下，同样输入不产生入参属性、不产生文字子观测（修复前就该成立的那部分也要钉住）
- [ ] 单元测试：span 早于事件到达的情况，按你选的做法有测试
- [ ] 单元测试：开发集档转发后没有 `user.id`、`host.*`、`os.*`（含资源属性），其余属性照旧；改后的 dev-tier 验收测试通过
- [ ] 父 span 不带 `usage_details`，token 合计在 metadata 且等于 result，cost 不变（改后的 `assert_usage_and_cost`，两个档）
- [ ] 其余已合入的 `tests/acceptance/test_observability_*.py` 一字不改、全部照旧通过
- [ ] `make check` passes

## Evidence required

- `make check` 完整输出；`uv run pytest tests/acceptance/test_observability_*.py -v`。
- 真实运行交主会话：最终回复写好命令（一次 shop.sql 查询运行，`--trace-tier full`，Langfuse 变量开），以及主会话该在 `trace-check` / Langfuse 界面里看什么。`trace-check` 不取 io，如需它显示"入参有没有值"，可在 `trace_check.py` 里加（只打印有没有、不打印值），算在本工单 Authority 内。

## Stop conditions

- 需要改已合入的验收测试才能做。
- 发现某条做法会让留出集档多出任何字段。
- 第二轮审查还不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2 + 退回 1 轮（`da-review` ×3，`eng:code-review` ×1）
- Model spend: $0 (no model calls)
- Outcome: —

## Comments

- 2026-11-24（工单 agent 最终回复，主会话照抄）：

**Stopped after the second review round with one finding still open.** Codex's second round (`runs/121/da-review-2.txt`) reports a P2, confirmed by reading the code:
- **The defect:** `RunExport._clean` (`src/data_agent/tracing.py:476-477`) masks the values of a tool input but keeps its JSON object keys as they are.
- **Why it leaks:** the fallback `tool.input` observation goes straight to Langfuse through `ParentExporter`. It never passes the relay's second masking pass, so a known secret used as a key reaches Langfuse.
- **Suggested fix:** use `self._clean(str(k))` for the keys, plus a regression test that puts a secret in a key and takes the fallback path (no CLI span takes the input).

**What was built:**
- **Tool inputs:** with `--trace-tier full`, the run hands each `tool_start` input, masked and serialized as JSON, to the relay (`trace_relay.ToolInputs`). The relay sets it as `langfuse.observation.input` on the `claude_code.tool` span with the same `tool_use_id`.
- **Span before event:** if no span takes an input (the CLI's span reached the relay before the event did, or never arrived), `finish` sends a `tool.input` observation under the run's span with the same `tool_use_id`. It sits beside the CLI's tool span, not inside it.
- **Model text:** each `text` event becomes an `assistant.message` child of the run's span. Each child has a `sequence` attribute so order survives equal timestamps, and a verifier's message carries `parent`.
- **Event fields:** `text` and `thinking` events now carry `parent` (`agent.py`).
- **Machine attributes:** the dev tier drops `user.id`, `host.*` and `os.*` on every layer. The list is a single constant, `MACHINE_ATTRIBUTES`.
- **Parent span tokens:** the run's span no longer sets `usage_details`. The token total goes in `langfuse.observation.metadata.usage_total`, with the `result` event's keys, on both tiers; cost is unchanged.
- **trace-check:** it now asks Langfuse for `io` and prints `input: yes/no  output: yes/no` for each observation, never the values.
- **Failures:** if handling the tool inputs or texts fails, `finish` writes an `error` event (`source: trace`), and the run's span is still sent.
- **Held-out tier:** none of this applies there, and tests pin it.

**Checks:**
- `make check` exit 0: 2861 passed, 2 skipped, web 442 passed (`runs/121/check-3.txt`).
- `uv run pytest tests/acceptance/test_observability_*.py -v`: 62 passed (`runs/121/acceptance.txt`, before the review rounds). The focused set after round 2 (observability acceptance plus `test_tracing` / `test_trace_relay` / `test_trace_check`): 125 passed.
- The real-relay test fails in the event-before-span case if the line that hands the inputs to the relay is removed.

**Acceptance-test edits (both within the ticket's authorization):**
- `assert_usage_and_cost`: only the parent span's usage assertion changed; the cost assertion is unchanged.
- The dev-tier relay test: only `user.id`, `host.name`, `host.arch`, `os.type` and `os.version` were added, inline in its exclusion set.

**Reviews:**
- **Codex round 1, P2: `evals dab --trace` is not wired, so DAB traces get no inputs or text.** Not fixed: `evals/dab.py` is outside the Authority. Proposed follow-up ticket: in `evals/dab.py:552`, pass `export.observing(log)` to `record_message`, with a test.
- **`eng:code-review`:** nothing blocking.
  - Fixed in b4b3cb0: the stale docstring; a failure while building children no longer drops the run's span; one shared `mask_shapes` function; a span that already has an input now claims the call, so no duplicate is sent.
  - Fixed in e3fcbb1: a test with the real relay in both arrival orders; failures now become an `error` event; the inline names in the acceptance test.
  - Not fixed, with reasons:
    - Separate exact-name and prefix sets for machine attributes: the ticket asks for one constant.
    - Passing the inputs through the relay's constructor: the acceptance stand-in `FakeRelay`'s constructor signature is fixed, so the run sets an attribute instead.
- **Codex round 2, P2:** the key-masking gap above, not fixed (round limit).

**Known limit:** if the relay claims an input for a batch that Langfuse then rejects, or that `stop` abandons, that input is lost and no fallback is sent.

**待人定:**
1. Whether `thinking` should be sent too; by default it is not.
2. Whether to fix the key-masking gap on this branch as a third round, or in a new ticket.

**Real run (for the main session; Langfuse variables and the model env set):**

```bash
uv run python -c "import duckdb; c=duckdb.connect('runs/121/shop.duckdb'); c.execute(open('tests/fixtures/shop.sql').read())"
mkdir -p runs/121/ws
uv run data-agent run --task "How many customers are in each membership tier?" --type query \
  --workspace runs/121/ws --duckdb runs/121/shop.duckdb --config full \
  --events-dir runs/121/events --trace-tier full --sandbox local
uv run data-agent trace-check runs/121/events/<run>.jsonl
```

What to look for:
- `claude_code.tool` (`query_database`) shows `input: yes`; in the Langfuse UI the SQL is visible and masked.
- `assistant.message` observations appear under `data-agent.run`, in event order.
- No `user.id`, `host.*` or `os.*` anywhere, including the resource attributes.
- `data-agent.run` shows no usage but shows cost.
- Not yet verified:
  - Whether Langfuse shows `metadata.usage_total` as parsed JSON or as a plain string.
  - Whether the v2 observations API accepts `io` in `fields`. If it answers an error, trace-check fails.


Nothing has changed since my last report. `runs/121/check-3.txt` passed: exit 0, pytest 2861 passed and 2 skipped, web 442 passed.

Ticket 121 is still at `Status: ready-for-human`. Codex's second review found that a secret used as a JSON key in a tool input could reach Langfuse unmasked. I haven't fixed it, because both allowed fix rounds are used.

The fix is a one-line change at `src/data_agent/tracing.py:477`, plus a regression test. The open decision is whether to run a third round on this branch for that fix, or move it to a new ticket.

- 2026-11-24（主会话）退回：原因——Codex 第 2 轮的 P2 属实（主会话对照代码核实）：`RunExport._clean`（`src/data_agent/tracing.py:476-477`）遮值不遮键，`tool.input` 回退观测不经过中转的二次遮罩，秘密做 JSON 键时会原样到 Langfuse。在原分支上修：键也过 `self._clean(str(k))`（或同等做法），加回归测试（秘密放在键里、走回退路径）；再跑 `make check` 和一次 `da-review main` 确认。
  - 待人定 1（`thinking`）已由人 2026-11-24 定：不带，维持现状。
  - Codex 第 1 轮 P2（`evals dab --trace` 没接上，DAB trace 没有入参和文字）不在本工单 Authority 内，主会话另开后续工单，本轮不做。

- 2026-11-24（工单 agent 退回第 1 轮最终回复，主会话照抄）：

- 2026-11-24 (ticket agent, returned round 1):
  - **Fix:** `RunExport._clean` (`src/data_agent/tracing.py:476-478`) now masks the keys of a tool input as well as its values (`self._clean(str(k))`, nested objects included). The docstring now says keys are masked too.
  - **Regression test:** `tests/test_tracing.py::test_a_secret_used_as_a_key_in_a_tool_input_is_masked_on_the_fallback_path`. The input is `{"sk-test": {"nested sk-test": "SELECT 1"}}`, and no CLI span takes it, so it goes out as the `tool.input` observation.
    - It failed before the fix: `'sk-test' is contained here: {"sk-test": {"nested sk-test": "SELECT 1"}}`.
    - After the fix the input arrives as `{"[REDACTED]": {"nested [REDACTED]": "SELECT 1"}}`.
  - **Focused tests:** `test_tracing`, `test_trace_relay`, `test_trace_check` and the observability acceptance tests: 126 passed. That is the 125 from before plus the new test.
  - **`make check`:** exit 0. pytest 2862 passed, 2 skipped; web 442 passed (`runs/121/check-4.txt`).
  - **`da-review main` (round 3):** "No actionable regressions found". Codex also ran its own in-memory check, on both tiers, that a secret used as a key is masked on the fallback path; it passed (`runs/121/da-review-3.txt`).
  - **Known limit:** if two keys in one input mask to the same text, only one of them stays in the `tool.input` value. The secret still does not leak.
  - **Unchanged:** the real-run command and what to look for are the same as in the previous Comment. That run is still for the main session to do.

- 2026-11-24（主会话）真实运行，121 分支 `2f75051`，worktree 里跑，产出在该 worktree 的 `runs/121real-2f75051/`（不进 git）：
  - 命令：`data-agent run --type query --config full --sandbox local --trace-tier full --duckdb <shop.sql 建的 DuckDB> --task "Which customer has the highest total order amount, and how much is it?"`（Langfuse 变量和模型变量从 env 文件读）。exit 0，答案 "Avery Lane: 438.75"，花费 $0.0058。事件末尾 `… text, answer, trace, result`；`trace` 事件 `forwarded_spans: 24`、`forwarded_llm_requests: 5`。
  - `data-agent trace-check`：26 个观测。`query_database` ×5 和 `list_tables` ×1 都是 `input: yes`；`assistant.message` ×1（事件里 `text` 也是 1 条），`output: yes`；`data-agent.run` 是 `usage: none  cost: yes`，metadata 里有 `usage_total`（`cached_input_tokens, output_tokens, uncached_input_tokens`）；全部观测的属性名里 `user.id`、`host.*`、`os.*` 出现 0 次。
  - 没看的：Langfuse 界面里 SQL 原文和遮罩的实际值（trace-check 只打印有没有值）；`metadata.usage_total` 在界面里是 JSON 还是字符串。
  - 后续：`evals dab --trace` 的入参和文字另开工单 134。
