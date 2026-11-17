# 88 — 对话界面：验收测试（真模型端到端、浏览器、三组不调模型的）

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-code (opus-5-5)
Branch: worktree-88-chat-acceptance-tests

## Goal

spec "Testing Decisions" 列的验收测试全部写好：真模型端到端 a–d、f、g、一条 Playwright 浏览器主流程、对话视图模型、后端对话接口、评测配置不变。还没实现的行为标预期失败（`xfail(strict=True)` / `test.fails`）；评测配置不变一组现在就通过。对话的契约（Context 第 1–6 项）写在代码里，函数体和接口实现留空。本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit:
  - `tests/acceptance/test_chat_e2e.py`、`tests/acceptance/test_chat_api.py`、`tests/acceptance/test_chat_eval_unchanged.py`（新建）；
  - `tests/fixtures/chat/`（新建）：端到端用的两个小 DuckDB 夹具库（如订单库和客户库：订单库带一个会改变结果的歧义，如收入含不含退款；场景 g 要两个库的数据才能答对）和它们的数据源配置文件、手写事件夹具、对话索引夹具、假的单轮子进程；
  - `web/src/chat-view/`（新建）：视图模型的类型、函数空壳（抛出未实现）、vitest 验收测试；
  - `web/e2e/`、`web/playwright.config.ts`（新建）：Playwright 主流程；
  - `web/package.json`、`web/pnpm-lock.yaml`：只加 `@playwright/test`；
  - `Makefile`：只为让 `make test-integration` 也跑 Playwright 主流程、加装浏览器的目标；`make check` 和 CI 不跑真模型；
  - `src/data_agent/web/app.py`：只给 `create_app` 加带默认值的参数（签名），不加行为。
- Must not edit: `src/data_agent/` 的其他文件和 `evals/` 的实现代码、已有的 `tests/acceptance/` 文件、`tests/fixtures/` 里已有的文件（已合入的测试在用）、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0（工单 agent 加载不了模型环境；真模型测试由主会话跑，见 Context）

## Context

- spec：`.scratch/chat-ui/spec.md`。"Testing Decisions" 逐条列了每组要断言的行为和判定规则，照它写；"决定"第 3、8、9、14–20 条是行为来源（2026-11-17 晚改：对话只做查数，不选类型和数据源，agent 自己选库；dbt 不进对话，原来的场景 e 和改动文件、diff、补丁相关内容都删了）；"写工单前先验证"记了 resume 和插话的探针结果。
- 先例：
  - 真模型验收：`tests/acceptance/test_seam_a_run_cli.py`（`pytestmark = pytest.mark.integration`，夹具 `tests/fixtures/shop.sql`）。
  - 不调模型的验收：工单 73 `.scratch/web-ui/issues/73-web-acceptance-tests.md` 和它合入的 `web/src/run-view/acceptance/run-view.test.ts`、`tests/acceptance/test_web_api.py`、`tests/fixtures/web/fake_run.py`；`src/data_agent/web/app.py:53` `create_app`、`src/data_agent/web/launcher.py:40` `StartRun`。
- 本工单要定下、并在最终回复里列出供人审的契约：
  1. 对话目录和索引文件的格式（spec 第 17 条：一段对话一个目录，一条用户消息一次运行、一个事件文件）。
  2. 新事件和字段：澄清事件（问题加 2–5 个选项，nao `shared/src/tools/clarification.ts:3-24`）、插话送达事件、每个事件文件记下所属对话和第几轮（第 17 条）、每条查询记下查的是哪个库（第 20 条）。旧文件没有这些字段。
  3. 对话视图模型的 TypeScript 类型和函数签名：输入索引加各轮事件（可以只是前缀），输出对话视图；每轮的卡片复用 `web/src/run-view/model.ts` 的运行视图模型，不重写。
  4. 后端接口：列出对话（单次运行显示成一轮、不能追问）、新建对话（不带类型和数据源）、发消息（空闲时开新一轮，运行中送 stdin）、取消。
  5. `data-agent run` 新增的参数：数据源清单（沿用 `--data-sources` 的 TOML，第 20 条）；第二轮起传上一轮的 session id 和对话目录（第 14、16 条）；一个对话模式开关，打开时才有澄清工具和 stdin 插话，默认关，评测和单次运行走默认（第 19 条）。session id 从上一轮事件文件里读（现在写在 `src/data_agent/agent.py:907`）。
  6. stdin 消息的字段（spec 第 9 条的通道：每行一个 JSON，子进程转成 `client.query()`；SP 带 uuid 对账，`notebook-server/signalpilot/_server/ai/claude_agent.py:355-408`）。
- 真模型端到端（a–d、f、g）：
  - 起真的 `data-agent web`（本机地址、独立端口、独立运行目录），全程经 HTTP 接口，和 Playwright 主流程用同一组契约。
  - 每个场景跑 3 次（参数化），3 次都过才算过。后端配两个夹具库，a–d、g 都不指定库。
  - 答案数值和正确口径在夹具里写死，比较带容差，照 seam A 的写法。夹具库和问题自己写，不用榜单题目、不碰留出集（ADR-0004、ADR-0005）。
  - a–d、g 标 `xfail(strict=True)`。f 走现有的 `data-agent run`，2026-11-17 主会话已用样例真跑 3 次全过，不标预期失败（作守门）；本工单写成正式测试后，主会话合并前再真跑一次确认。
- 预期失败按转绿的工单分标记（照工单 73 的 `TICKET_NN = pytest.mark.xfail(...)` 写法），转绿的工单只删自己那个标记：
  - `TICKET_93`：后端接口一组、真模型端到端 a–d、g（93 是对话后端，依赖 89–92 都合入，所以端到端在它合并时一起转绿）；
  - `TICKET_94`：对话视图模型一组（vitest `test.fails`）；
  - `TICKET_95`：Playwright 主流程。
- 假子进程：另写在 `tests/fixtures/chat/`，不改 `tests/fixtures/web/fake_run.py`。它要能按脚本写事件、读 stdin 并写送达事件、把收到的参数（session id、对话目录、数据源清单）记下来供断言、按门文件结束；测试用自己的 `start_run` 以 stdin 管道启动它。
- 评测配置不变：DAB 直接调 `build_options`（`evals/dab.py:417`），Harbor 走 `data-agent run`（`evals/harbor_agent.py`）。断言这些入口给出的选项里没有澄清工具、`resume` 为空、`env` 里没有 `CLAUDE_CONFIG_DIR`，工具、`allowed_tools`、`disallowed_tools`、权限设置，以及 MCP 服务列出的工具清单和每个工具的参数（第 20 条：单库时不出现库名参数），都和本工单开始时的 main 一致（查数和 dbt 各一份，按评测用的配置）。不调模型，现在就通过。
- 只用 pnpm、uv。

## Acceptance

- [ ] spec "Testing Decisions" 每一组列出的每条行为都有断言
- [ ] 不调模型的视图模型、后端接口两组当前都以预期失败的方式通过检查，没有被跳过的；评测配置不变一组当前通过
- [ ] 每个预期失败的测试，失败原因都是功能还没做（如对话接口返回 404/405、视图模型抛出未实现），不是夹具或服务没起来：起服务的夹具先断言进程活着、接口可达；最终回复贴每组一个用 `--runxfail` 跑出的真实失败原因（2026-11-17 样例里，xfail 曾把"运行目录不存在、服务没起来"藏成预期失败）
- [ ] 不调模型的三组都临时写参考实现（不提交）验证过：去掉预期失败标记后能通过；每组至少一个反向检查会失败（如第二轮子进程没拿到上一轮的 session id、评测入口多了澄清工具、单库时工具多了库名参数）
- [ ] 真模型端到端和 Playwright 主流程：`uv run pytest tests/acceptance/test_chat_e2e.py --collect-only` 和 `pnpm --dir web exec playwright test --list` 列出全部用例；`make check` 不跑它们
- [ ] 夹具里没有密钥、地址、主机名（`python -m evals scrub` 输出 0 处改动）
- [ ] Context 第 1–6 项契约都写在代码里，并在最终回复里逐项列出，供人审
- [ ] `make check` 通过

## Evidence required

`make check` 的完整输出；`pnpm --dir web test` 的输出（每个对话验收测试的名字和预期失败）；`uv run pytest tests/acceptance/test_chat_api.py tests/acceptance/test_chat_eval_unchanged.py -v` 的输出；端到端和 Playwright 的用例清单；参考实现和反向检查的结果；脱敏命令的输出；最终回复里的契约清单，以及交给主会话的真跑命令（f 场景和整套 `make test-integration`）。每个场景的夹具答案附一条算出它的 SQL，供人核对。

## Stop conditions

- spec 的某条行为没法在约定的接缝上测，或者要改 Authority 以外的实现代码才能测。
- 评测配置不变一组写不出来，或者写出来现在就不通过。
- spec 自相矛盾，或者缺一个需要人定的决定（契约的具体字段名不算，由本工单定、人审）。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3
- Review rounds: 2
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: returned twice (round 1: f keyword check; round 2: Markdown around 'completed'); accepted at human review, merged PR #97
- Human review minutes: — (filled by the human at merge, optional)

## Comments

### Attempt 1 (ticket agent, branch `worktree-88-chat-acceptance-tests`, commits 38c259f, 5284b6e, 7676c2b)

- **What was built**
  - `tests/acceptance/`:
    - `test_chat_api.py`: 8 tests, all `TICKET_93`.
    - `test_chat_eval_unchanged.py`: 2 tests, passing now.
    - `test_chat_e2e.py`: scenarios a, b (ambiguous / plain), c, d, f, g (two databases / one database), 3 runs each, 24 cases. f has no marker; everything else is `TICKET_93`.
  - `web/src/chat-view/`: `model.ts` holds the types and a stub; `acceptance/chat-view.test.ts` has 16 `TICKET_94` (`test.fails`) tests.
  - `web/e2e/chat.spec.ts`: one unmarked test ("the server serves the built UI") and the main flow, marked `TICKET_95` (`test.fail`).
  - Other changes: `web/playwright.config.ts`, `web/e2e/tsconfig.json`, `@playwright/test@1.65.1` (the lockfile change is Playwright packages only), and Makefile targets: `test-integration` now builds the UI, type-checks the e2e files and runs Playwright; new `playwright-install` target.
  - `tests/fixtures/chat/`:
    - `orders.sql` / `customers.sql`; the DuckDB files are built at test time by `build_sources.py`.
    - `sources.toml` (data-source config).
    - `fake_chat_run.py` (fake single-turn subprocess).
    - `conversation/`: hand-written 5-turn fixture.
    - `eval-unchanged.json`: snapshot taken from main.
  - `src/` is unchanged: `create_app` needed no new parameter.
- **Commands run and their output**
  - `make check`:
    - ruff: "All checks passed!" and "313 files already formatted".
    - basedpyright: 0 errors.
    - pytest: "2309 passed, 1 skipped, 63 deselected, 8 xfailed".
    - web typecheck passed; vitest: "Tests 225 passed | 16 expected fail (241)".
  - `uv run pytest tests/acceptance/test_chat_api.py tests/acceptance/test_chat_eval_unchanged.py -v`: the 8 API tests XFAIL and the 2 eval tests PASSED.
  - `pnpm --dir web test`: the 16 chat-view tests are expected fails (turns / clarification / steers / cost / databases / single runs / prefix).
  - Test lists: `pytest tests/acceptance/test_chat_e2e.py --collect-only -m integration` gives 24 cases; `playwright test --list` gives 2 tests. `make check` runs neither (24 integration cases deselected).
- **Real failure reason per group, with markers lifted**
  - API: `--runxfail` gives `AssertionError: {"detail":"Method Not Allowed"} / assert 405 == 201`.
  - E2E (a–d, g): I started a real `data-agent web`. The fixture first checks that the process is alive and `/api/runs` returns 200; the failure is then `405 == 201` on `POST /api/conversations`.
  - View model: `Error: buildConversationView is not implemented yet (ticket 94)`.
  - Playwright, with Chromium installed and the UI built: "the server serves the built UI" passes. The main flow fails with `locator.click: Timeout 60000ms … waiting for getByTestId('new-conversation')`.
  - Setup failures can no longer hide as expected failures: the xfail markers accept only `AssertionError`, and the fixtures raise `RuntimeError` when the server does not come up.
- **Reference implementations and reverse checks** (written temporarily, not committed; `src/` restored and `git status src/` clean)
  - View model: 16/16 pass on the reference. Each mutant fails:
    - cards built without the run notes: 4 fail;
    - clicked option not recorded: 1 fails;
    - steer placed after the wrong step: 2 fail.
  - API: 8/8 pass on the reference, re-checked after the review-round-2 contract change. Each mutant fails:
    - turn 2 gets no session id: 2 fail;
    - only the first data source is passed: 1 fails;
    - a message while running is refused instead of steered: 2 fail.
  - Eval unchanged. Each source mutant fails:
    - `query_database` gains a `database` parameter with one source: the Harbor test fails;
    - `allowed_tools` gains the clarification tool: both fail;
    - `sdk_env` sets `CLAUDE_CONFIG_DIR`: both fail.
- **Scrub**: `python -m evals scrub tests/fixtures/chat` gives "changed 0 file(s) …; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0". gitleaks passed in pre-commit.
- **Fixture answers and the SQL that gives each** (all recomputed with DuckDB):

  | Scenario | Answer | SQL |
  |---|---|---|
  | a, turn 1 | 11 | `SELECT count(*) FROM sales.orders WHERE status='refunded'` |
  | a, turn 2 | 6 | `… AND year(order_date)=2024` |
  | b / f, gross | 2121.75 | `SELECT sum(amount) FROM sales.orders WHERE year(order_date)=2024` |
  | b / f, net | 1474.50 | `… AND status<>'refunded'` |
  | b, plain question | 1034 | `SELECT order_id FROM sales.orders ORDER BY amount DESC LIMIT 1` |
  | c | top 3: C01 9, C02 7, C03 6 (C04–C08 must not appear) | `SELECT customer_id, count(*) n FROM sales.orders GROUP BY 1 ORDER BY n DESC` |
  | d | 14 | `SELECT count(*) FROM crm.customers` |
  | g, both databases | North 13, South 10, East 8, West 6 | orders `JOIN` customers `USING (customer_id)`, `GROUP BY region` (the agent queries each database and combines) |
  | g, one database | North 3, South 2, East 4, West 5 | `SELECT region, count(*) FROM crm.customers GROUP BY 1` |
  | Playwright follow-up | 12 net / 18 gross | 2024 order count |

- **Review round 1 (da-review / Codex), both fixed** (`5284b6e`)
  - The region check accepted swapped counts. Each region's count must now be the first number after its own name.
  - The live-card check used a missing answer as proof the turn was running. It now compares when the card appeared with the turn's `result` timestamp.
- **Review round 2 (eng:code-review)**
  - Fixed (`7676c2b`):
    - A turn now counts as running until its process exits; the contract says so and the tests wait for it.
    - Setup failures are no longer counted as expected failures (see the failure-reason section).
    - Added a dbt single run on the API side.
    - The Playwright files are type-checked inside `test-integration`.
    - Small cleanups: a shared `FORM` constant, `states` renamed to `mentions`, a misplaced SQL comment moved.
  - Not fixed, with reasons:
    - The answer constants are repeated in Python and TypeScript. They are two languages with no shared runtime; comments point to the fixture.
    - `make_dab` and a few helpers are copied. Their natural home, `seam_support.py`, is an existing acceptance file outside Authority (ticket 73 kept copies for the same reason).
    - `make check` does not type-check `e2e/`. That would need a `package.json` script or `make check` change, both outside Authority.
    - The single-number check (`mentions`) matches any occurrence of the number, as seam A does.
    - `surface()` is explained by its docstring.

### Contracts for human review (from attempt 1)

The backend contracts (items 1, 2, 4, 5, 6) are in the docstring of `tests/acceptance/test_chat_api.py`; the view-model types (item 3) are in `web/src/chat-view/model.ts`.

1. **Conversation directory**: `<runs_dir>/conversations/<id>/` holds:
   - `conversation.json`: `{id, created_at, turns:[{turn, run_id, message, steers:[{id,text}]}]}`;
   - `workspace/`, shared by all turns;
   - `claude-config/`, used as `CLAUDE_CONFIG_DIR`.
   Each turn is an ordinary run in `<runs_dir>/<run_id>/`, so the existing run list, events, SSE and run page all work on it.
2. **New event fields** (old files have none of them):
   - `task` gets `conversation` and `turn`.
   - `status` (started) and `result` get `session_id`.
   - `clarification`: `{tool_use_id, question, options (2–5)}`. The tool is `mcp__data__clarification`. A turn that ends with it writes no `answer` event, and its `result` is a success.
   - `steer_delivered`: `{id, text}`.
   - `sql` gets `database`, also when there is only one source.
   - The query and context tools get a `database` parameter only when there are several sources.
3. **View model**: `buildConversationView(index: ConversationIndex, runs: Record<run_id, {events, notes?}>): ConversationView`.
   - Per turn: `run` is exactly `buildRunView(events, notes)`, plus `status`, `message`, `answersClarification`, `clarification{question, options, chosen}`, `steers{delivered, seq, afterStep}` and `queries{step, database}`.
   - For the whole conversation: `singleRun`, `canFollowUp`, `running`, `costUsd`.
4. **Routes**:
   - `GET /api/conversations` and `GET /api/conversations/{id}`. A row is `{id, single_run, created_at, running, turns:[…, status]}`; a single run, dbt included, is a one-turn row.
   - `POST /api/conversations {}` returns 201.
   - `POST …/messages {text}`:
     - when the conversation is idle: 201 `{kind:"turn", turn, run_id}`;
     - when it is running: 202 `{kind:"steer", run_id, id}`;
     - 409 when another conversation or the run form is running;
     - 4xx for a single run or an empty text.
   - `POST …/cancel`.
   - `running` stays true until the turn's process exits.
5. **`data-agent run` arguments for a turn**: `--type query --config full --data-sources FILE --chat --conversation-dir DIR --turn N [--resume SID]`.
   - `--data-sources` uses the same TOML as `data-agent web`, and is exclusive with `--duckdb` / `--postgres-env`.
   - The session id comes from the `status` event of the latest earlier turn; a cancelled turn counts.
6. **stdin**: one JSON object per line, `{"id": uuid, "text"}`. `steer_delivered` repeats the `id`.

## Points for the human to decide at review

1. The eval snapshot pins tool names and input schemas only, not tool descriptions, because the ticket text only asks for the tools and their parameters. The spec also says "工具定义不变" (tool definitions unchanged); pinning descriptions too would be stricter.
2. Today `session_id` is only written in the `turn_budget` event (`agent.py:907`). Ticket 90 has to add it to `status`.
3. Product consequence of contract item 4: a message sent before the turn's process exits becomes a steer. The page needs to wait for `running` to turn false before the next send (the Playwright flow does).
4. The Playwright contract with the page is a set of `data-testid`s (`new-conversation`, `chat-input`, `chat-send`, `turn`, `turn-message`, `step-card`, `clarification`, `answer`), listed in the header of `chat.spec.ts`. Playwright output goes to `runs/playwright`, which git ignores.
5. Chat turns always use the `full` config. The DAB eval snapshot uses `no-context`, the config the DAB tests use.
6. Scenario f's question is new to this ticket ("What was the total revenue from orders placed in 2024?"). The sample the main session ran 3 times on 2026-11-17 was not available to me, so f needs a fresh real run.

### Main session, 2026-11-17: real run of scenario f

- Command (in the 88 worktree at 7676c2b): `uv run pytest tests/acceptance/test_chat_e2e.py -m integration -k test_f -v` with the model env loaded.
- Result: 2 passed, 1 failed ([1]). Log: main-session scratchpad `t88-f.log`.
- The failing run answered `$2,121.75` (the gross figure, which the test accepts), with no clarification, and wrote this assumption: "revenue is the sum of `amount` for every order whose `order_date` falls in 2024, with all order statuses included; no other tables or adjustments were applied." The answer states how refunded orders were treated without using the word "refund", and `assert "refund" in answer.lower()` (`tests/acceptance/test_chat_e2e.py:446`) rejected it.

退回：场景 f 的"写出假设"只认 "refund" 这个词，模型用 "all order statuses included" 也写清了退款的口径，被误判失败；spec 要求 3 次都过，这个检查会让守门测试随措辞偶发变红。改成按外部行为判断答案说明了退款或订单状态的口径（例如认 refund/refunded 或 status/statuses，或者答案是净额时不要求另写），不要放宽到任何文字都能过；改完由主会话再真跑 f 三次。其余部分不变。

### Attempt 2 (ticket agent, round-1 return fix, branch `worktree-88-chat-acceptance-tests`, commits 7d09452, af8bff8, 16252b2)

- **What changed** (only scenario f's assumption check, `tests/acceptance/test_chat_e2e.py`):
  - `assert "refund" in answer.lower()` is replaced by `STATED_REFUND_TREATMENT` (defined next to `GROSS_2024` / `NET_2024`, `test_chat_e2e.py:75-80`). It accepts an answer that names:
    - refunds (`refund`);
    - order statuses (`\bstatus(es)?\b`, e.g. "all order statuses included");
    - orders by the other status value (`completed orders`, or `orders … completed` within 30 characters in the same sentence).
  - "completed" on its own does not count ("The analysis is completed").
  - No net-figure exemption: spec line 101 asks every answer to state its assumption, and the return listed the exemption only as an example ("例如"). A net answer must name the refund treatment too.
- **Predicate check** (local, no model calls), against the final regex:
  - Accepted: the rejected real answer from the main-session run ("…with all order statuses included…"), "excluding refunded orders", "Revenue from completed orders…", "I counted only orders marked completed", "regardless of order status".
  - Rejected: "Total revenue in 2024 was $2,121.75.", "$1,474.50 net revenue.", "…summing amount over orders placed in 2024.", "The analysis is completed. Total revenue…", "Query completed. Revenue from orders placed in 2024: …".
  - The old check rejected the real answer (`"refund" in …` → False).
- **make check** (after the last commit, 16252b2):
  - ruff: "All checks passed!" and "313 files already formatted".
  - basedpyright: "0 errors, 0 warnings, 0 notes".
  - pytest: "2309 passed, 1 skipped, 63 deselected, 8 xfailed".
  - vitest: "Tests 225 passed | 16 expected fail (241)".
- **Collection**: `uv run pytest tests/acceptance/test_chat_e2e.py --collect-only -m integration -k test_f` lists f[1], f[2], f[3] (3/24).
- **Real run**: not run. Ticket agents cannot load the model env. For the main session, in the 88 worktree, with the model env loaded:
  - `uv run pytest tests/acceptance/test_chat_e2e.py -m integration -k test_f -v`
  - then the whole `make test-integration` before merge.
- **Reviews**
  - da-review on 7d09452: no findings.
  - eng:code-review on 7d09452:
    - Spec: a net answer worded as "completed orders" failed. Fixed in af8bff8 (adds `completed`).
    - Standards (judgement calls):
      - The comment was repeated at the constant and the assertion. Fixed: one comment, at the constant.
      - The constant sat beside `choose()`'s word lists. Fixed: moved next to the revenue answers.
      - Bare `status` is loose (e.g. a schema description "…amount, status"). Not fixed: the return explicitly allowed status/statuses.
  - da-review on af8bff8: bare `completed` accepted "The analysis is completed." Fixed in 16252b2 (`completed` only next to orders).
  - da-review on 16252b2 (P2, confirmed locally, **not fixed**): Markdown between the words fails, e.g. "only **completed** orders" or "only `completed` orders". This keeps a wording-dependent failure in the three-run gate. Not fixed because the 2-round fix limit was reached. Suggested one-line fix: allow `[*_`\s]*` between `completed` and `orders` (and in the `orders … completed` branch).

### Main session, 2026-11-17: round 2 return

退回：Codex 在 16252b2 上的 P2 属实（主会话读了 `test_chat_e2e.py:78-80`）：`\bcompleted orders?\b` 不允许两个词之间有 Markdown，“only **completed** orders”、“only `` completed `` orders”（completed 带反引号）会被误判，仍是随措辞变红。改法：匹配前去掉答案里的星号、下划线和反引号，并给这两句各补一条本地谓词检查。只改这一处。这是本 PR 最后一轮退回。

### Attempt 3 (ticket agent, round-2 return fix, branch `worktree-88-chat-acceptance-tests`, commit a099ac2)

- **What changed** (only scenario f's check, `tests/acceptance/test_chat_e2e.py:75-83` and `:454`):
  - New `MARKDOWN_MARKS = re.compile(r"[*_`]")`. The assertion now strips these marks from the answer before matching: `STATED_REFUND_TREATMENT.search(MARKDOWN_MARKS.sub("", answer))`.
  - In `STATED_REFUND_TREATMENT`, the space in `completed orders?` became `\s+`. Without it, "only `` completed `` orders" still fails, because stripping the backticks leaves two spaces between the words.
  - The comment at the constant says the Markdown marks are stripped first.
- **Local predicate check** (no model calls; a script outside the repo imports the module's constants and applies the same strip as the assertion):
  - Before the fix (at 16252b2), 4 sentences that should pass were rejected:
    - "counting only **completed** orders"
    - "counting only `completed` orders"
    - "counting only `` completed `` orders"
    - "from _completed_ orders"
  - After the fix, all 10 sentences that should pass are accepted:
    - the 4 above;
    - the main session's real answer ("…with all order statuses included…");
    - "excluding refunded orders";
    - "Revenue from completed orders";
    - "only orders marked completed";
    - "regardless of order status";
    - "only **orders** marked `completed`".
  - After the fix, all 5 sentences that should fail are still rejected:
    - "Total revenue in 2024 was $2,121.75."
    - "$1,474.50 net revenue."
    - "…summing `amount` over orders placed in 2024."
    - "The analysis is **completed**. …"
    - "Query `completed`. Revenue from orders placed in 2024: …"
- **make check** (after a099ac2):
  - ruff: "All checks passed!", "313 files already formatted".
  - basedpyright: "0 errors, 0 warnings, 0 notes".
  - pytest: "2309 passed, 1 skipped, 63 deselected, 8 xfailed".
  - vitest: "Tests 225 passed | 16 expected fail (241)".
- **Collection:** `uv run pytest tests/acceptance/test_chat_e2e.py --collect-only -m integration -k test_f -q` lists f[1], f[2] and f[3] (3/24).
- **Real run:** not run, because ticket agents cannot load the model env. For the main session, in the 88 worktree with the model env loaded:
  - `uv run pytest tests/acceptance/test_chat_e2e.py -m integration -k test_f -v`
  - then the whole `make test-integration` before merge.
- **Reviews:**
  - `da-review 16252b2` (only the new commit): no findings. Codex ran 9 checks of its own on the pattern ("only **completed** orders", "only `` completed `` orders", "only _completed_ orders", "completed\norders", "orders marked **completed**", plus negative cases), and all passed.
  - eng:code-review: not run this round, because the return asked for da-review on the new commit only.

### Main session, 2026-11-17: real runs at a099ac2

- Scenario f, 3 runs: `uv run pytest tests/acceptance/test_chat_e2e.py -m integration -k test_f -v` → "3 passed, 21 deselected in 42.94s".
- `make playwright-install && make test-integration` (pytest half): "1 failed, 10 passed, 2318 deselected, 21 xfailed, 31 errors". All 32 non-passes are existing Postgres tests (`test_postgres.py`, `test_db_facts.py`, seam B `[postgres]`, `test_verifier_v2_tools.py`, `test_eval_jaffle.py::test_reset_drops_only_case_schemas`) failing with `KeyError: 'DATA_AGENT_TEST_POSTGRES_URL'`: this machine has no local test Postgres configured. None is a chat test. The chat e2e a–d, g are the 21 xfailed; f passed.
- Because pytest failed, make stopped before the Playwright half; ran it by hand (`pnpm --dir web build:local`, `tsc -p e2e/tsconfig.json`, `playwright test`): "2 passed (1.1m)": the served-UI test passed, the main flow failed as expected (`test.fail`, ticket 95).
- Model spend for these runs: not read from event files (f runs write to pytest tmp dirs); small (6 single-turn query runs on a 2-table fixture).

### Main session, 2026-11-17: PR #97

- https://github.com/kevinWangSheng/data-agent/pull/97 , CI `check` green.
- Codex GitHub app (after `@codex review`) on a099ac2: "Didn't find any major issues."
- Merge waits for the human review of the tests and contracts (spec: 工单 88 人审过才合并).
- 2026-11-17 human review: merge as-is. Decided: the eval-unchanged snapshot pins tool names and input schemas only (not descriptions); a message sent before the turn's process exits is a steer; chat turns use the `full` config. Squash-merged PR #97.
