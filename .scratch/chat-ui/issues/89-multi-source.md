# 89 — agent 自己选库：多数据源

Status: in-review
Blocked by: 88
Spec: ../spec.md
Claimed by: claude (opus-5-5) 2026-11-17
Branch: worktree-89-multi-source

## Goal

`data-agent run` 能接收一份数据源清单，agent 看得到全部库、每次查询自己选库，每条查询的事件记下库名。只给一个库时（评测、单次运行），工具定义和行为和现在完全一样。

## Authority

- May edit: `src/data_agent/mcp_server.py`、`gateway.py`、`context_tools.py`、`db_facts.py`、`label_tables.py`、`tool_annotations.py`、`events.py`、`cli.py`、`agent.py`（只改数据源清单进选项和系统提示的部分）、`src/data_agent/web/sources.py`（只为把 TOML 解析挪成共用，不改格式和校验规则）；`skills/sql-workflow/`、`skills/duckdb-sql/`、`skills/postgres-sql/`（只加"多个库时先看库清单再选"这类通用说明，不写题目内容，ADR-0005）；`tests/`（新增单元测试）
- Must not edit: 工单 88 合入的验收测试和夹具（`tests/acceptance/test_chat_e2e.py`、`test_chat_api.py`、`test_chat_eval_unchanged.py`、`tests/fixtures/chat/`、`web/src/chat-view/` 里的验收测试、`web/e2e/`；唯一例外是本工单让某个测试转绿时删掉自己那个 `TICKET_NN` 标记）、其他已有的 `tests/acceptance/` 文件、`evals/` 的评测行为、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec 第 3、20 条。契约（参数名、事件里库名的字段）以工单 88 合入的为准。
- 先例：nao `apps/shared/src/tools/execute-sql.ts:29-34`（`database_id`，多库时必填）、`apps/backend/src/components/ai/system-prompt.tsx:335-337`、`:371-388`（两个库以上才在系统提示列库清单）；SP `query_database(sql, row_limit, connection_name=None)`（`SignalPilot/signalpilot/gateway/gateway/mcp/tools/query.py:152-157`）。路径相对 `~/dev/AI/data-agent-refs/`。
- 只配一个库时不注册库名参数，`mcp__data__*` 的工具清单和参数一字不变（评测配置不变一组会断言）。
- 跨库：分别查再合并，不做一条 SQL 跨库 join；每个库各走一遍只读网关的规则。
- 表目录缓存、数据库事实、标签查询按库分开，不能把 A 库的表报成 B 库的。
- 和 90 并行时都会改 `agent.py`、`cli.py`，后合并的一方 rebase。

## Acceptance

- [ ] 多库时每个查数和上下文工具都能按库名查到对的库；库名不存在时报错并列出可用的库名（单元测试）
- [ ] 每条查询事件记下库名；单库时事件照旧能被现有运行页读出（单元测试）
- [ ] 连接串不出现在参数、系统提示、事件里（单元测试）
- [ ] 评测配置不变一组（`tests/acceptance/test_chat_eval_unchanged.py`）一直通过
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实运行由主会话做（工单 agent 加载不了模型环境）：最终回复写好要跑的命令，主会话跑完把命令和真实输出抄进 Comments。命令：用工单 88 的两个夹具库跑一条要两个库才能答对的查数（场景 g 的问题），贴出答案和每条查询事件的库名；再用单个 `--duckdb` 跑一条现有快速上手的查数，确认和改之前一样。

## Stop conditions

- 单库时做不到工具定义一字不变。
- spec 自相矛盾，或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

### Attempt 1 (ticket agent, branch `worktree-89-multi-source`), final reply as given

Ticket 89 is implemented and committed on `worktree-89-multi-source` in two commits (`f413b94` and fix round `448c2cc`). The real-model run still has to be done by you, because a ticket agent can't load the model environment; the commands are below. I have not pushed anything.

`data-agent run --data-sources FILE` now gives the agent every source in the file. It reads the same TOML that `data-agent web --data-sources` reads, and cannot be combined with `--duckdb` / `--postgres-env`. With two or more sources:
- **Tools:** each query and context tool requires a `database` name. An unknown name is an error that lists the valid names.
- **Per database:** the table catalog cache, value dictionary and label tables are separate for each database. Label tables share one numbering so their files don't clash.
- **Prompt:** the system prompt lists the databases by name and kind.
- **Events:** every `sql` event records its `database`.

With one source the tool definitions are unchanged, and `test_chat_eval_unchanged.py` passes.

---

**To copy into the ticket**

Status: in-review

## Comments
- **Checks:** `make check` passed twice. After the first commit: 2335 passed, 1 skipped, 8 xfailed; ruff, format and basedpyright clean; web 225 passed. After the fix round: 2336 passed, 8 xfailed. The baseline before any change was 2309 passed, 8 xfailed. The 27 new tests are in `tests/test_multi_source.py` (server over stdio) and `tests/test_multi_source_run.py` (`agent.run` with a scripted SDK stream, plus the CLI). They cover all three unit-test acceptance items: every tool reaches the database it names, an unknown name is an error listing the names, each query event names its database, a one-source event keeps its old fields plus `database`, and no connection string appears in the server arguments, the prompts or the events.
- **Real run (main session):** run from the repo root with the model environment loaded:
  ```bash
  uv run python tests/fixtures/chat/build_sources.py runs/89-sources
  mkdir -p runs/89-ws runs/89-events
  uv run data-agent run --type query --config full --workspace runs/89-ws \
    --events-dir runs/89-events/g --data-sources runs/89-sources/sources.toml \
    --task "How many orders were placed by customers of each region?"
  # expected: North 13, South 10, East 8, West 6. Database per query event:
  grep '"type": "sql"' runs/89-events/g/*.jsonl
  uv run python -c "import duckdb; duckdb.connect('runs/shop.duckdb').execute(open('tests/fixtures/shop.sql').read())"
  uv run data-agent run --type query --config no-verifiers --duckdb runs/shop.duckdb \
    --workspace runs/89-ws --events-dir runs/89-events/single \
    --task "Which customer tier has the highest total amount of completed orders, and how much is it?"
  # README quickstart: Gold, 710.00
  ```
- **Decisions:**
  - A file with one source runs exactly like `--duckdb` / `--postgres-env`, under the source's name.
  - dbt with several sources is refused, since dbt is not part of the chat (spec 3).
  - With several sources the `task` event has `data_source: null`, as `fake_chat_run.py` writes.
  - The several-source server has no verifier tools; they check dbt runs, which have one database.
  - With several sources, the internal MCP server takes `--source NAME KIND LOCATION` and `--source-dictionary NAME FILE`. These carry DuckDB paths and Postgres variable names, never URLs.
  - Skills are unchanged. The guidance for several databases lives only in the system-prompt block, so single-database and evaluation prompts stay as they were.
- **Review, `da-review main`:** no actionable findings, both before and after the fixes.
- **Review, `eng:code-review main`, fixed in `448c2cc`:**
  - An `sql` event recorded whatever database name the model passed, even an unknown one; it now records only a real source name.
  - `build_server` and `build_multi_server` duplicated each other; both now use one `data_server`.
  - `Task.dialect` repeated `source_kind`; it is removed.
  - The `database()` factory is renamed `open_database`.
- **Review findings not fixed, with reasons:**
  - *Authority scope in `agent.py`:* every change there carries the source list into the options, the prompts or the events. The acceptance items need the event fields and the per-source URL checks and masking. The `database` field on the `dictionary` event tells the per-source builds apart. Mixed DuckDB/Postgres runs need both dialect Skills.
  - *`database_tools` rewrites `__signature__`:* that is the only way to keep single-source definitions identical without writing every tool twice. `tool_annotations.py` is in the ticket's Authority.
  - *`DataSource` is imported from `data_agent.web.sources` rather than moved:* moving it would need a re-export shim, because the ticket-88 tests import from `web.sources`.
  - *`Task` holds two forms (one database, or `sources`):* `evals/` builds `Task(duckdb=...)` and must not change.
  - *`add_database(server: bool)` takes a flag:* the alternative is argparse's private group type.
  - *The source kind is re-checked from argv:* the internal command checks its own input.
  - *The run page reader is not tested over the new event field (TS, `web/src/`):* that code is outside Authority, and the change only adds a field.

## Ledger
- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: —

### Main session, 2026-11-17: real run (commands as in the reply above, at 448c2cc)

- Two sources, "How many orders were placed by customers of each region?": "East: 8 orders; North: 13 orders; South: 10 orders; West: 6 orders.", exit 0 (expected North 13, South 10, East 8, West 6). `sql` events: `customers` (`SELECT customer_id, region FROM crm.customers`), `orders` (counts per customer), `orders` (combines the two results with an inline VALUES list; no cross-database join). `cost_usd` $0.0044, 6 turns.
- One source (`--duckdb runs/shop.duckdb`, no-verifiers, README quickstart): "Gold has the highest total amount of completed orders: 710.00.", exit 0, $0.0046.
- Merge: conflicts with ticket 90 in `src/data_agent/agent.py` and `src/data_agent/cli.py` (`git merge-tree`). 90 merges first; 89 then rebases onto main.

### Main session, 2026-11-17: rebase onto main

退回：不是审查问题。工单 90（PR #99）已合入 main，和本分支在 `src/data_agent/agent.py`、`src/data_agent/cli.py` 冲突。把本分支 rebase 到最新 `origin/main` 上解决冲突，保留两边的行为：90 的 `ClaudeSDKClient`、`--chat` / `--conversation-dir` / `--turn` / `--resume`、`session_id` 和 `restored_sdk_cost_usd`；本工单的 `--data-sources`、`database` 参数和事件字段。`--chat` 加 `--data-sources` 要能一起用（对话 turn 的参数见 ticket 88 契约第 5 项）。解决后 `make check`，`da-review main` 看 rebase 后的整个 diff；最终回复给出新的提交和主会话要重跑的真跑命令。

### Attempt 2 (ticket agent, rebase onto main; commits 07a5e4e, 321faf2), final reply as given

I rebased ticket 89 onto `origin/main`, resolved the conflicts and kept both sides. It is now two commits on `worktree-89-multi-source`: `07a5e4e` and `321faf2`. `make check` passed, and `da-review main` found nothing to fix in the rebased diff. Nothing is pushed. The real-model run still has to be done by the main session; the commands are below.

---

**To copy into the ticket**

Status: in-review

## Comments
### Attempt 2 (ticket agent): rebase onto main after ticket 90
- **Rebase:** `git fetch`, then a rebase onto `origin/main`. The branch was rebased on `2ab6110` first. Main then gained two doc-only commits (`f05bb75` AGENTS.md, `564dd3b` ticket 95), so I rebased again onto `564dd3b`. `git diff 426728a 321faf2 -- src tests web evals` is empty, so the checks below still hold. Commits: `07a5e4e` (was `f413b94`) and `321faf2` (was `448c2cc`).
- **Conflicts resolved, keeping both sides:**
  - `agent.py`, `build_options`: the options keep 90's `CLAUDE_CONFIG_DIR` session env and `resume=turn.resume`, and take 89's `system_prompt(task, config)`.
  - `agent.py`, `run`: 89's checks of each source's Postgres variable come first, then 90's `turn.config_dir.mkdir` and `restored_cost` read.
  - `agent.py`, `task` event: it records `data_source` (null with several sources) together with 90's `conversation` / `turn` fields. 89's `source_names` follows.
  - `cli.py`: the import takes both `ConversationTurn` and `source_gateway`. `turn = conversation_turn(...)` runs before the choice between `--data-sources` (`file_task`) and the single-database `Task`. `run(..., turn=turn)` is unchanged.
  - 90's `session_id` and `restored_sdk_cost_usd` in the `result` event are untouched.
- **New tests for `--chat` with `--data-sources`** (ticket 88 contract item 5), in `tests/test_multi_source_run.py`:
  - `test_a_conversation_turn_runs_on_every_source`: `agent.run` with two sources and `ConversationTurn(c1, 2, resume="sid-1")`. It checks `options.resume`, `CLAUDE_CONFIG_DIR`, that the server requires `database`, a `task` event of `data_source: null, conversation: "c1", turn: 2`, and a `sql` event naming its database.
  - `test_a_conversation_turn_takes_a_file_of_several_sources`: the CLI with `--data-sources` and `--chat --conversation-dir --turn 2 --resume sid-1`. The run gets both sources and the turn.
  - The CLI test fake `fake_run` now accepts the `turn` keyword that 90 added; the two existing CLI tests failed on it after the rebase.
- **Checks:** `make check` on the rebased tree (log in `runs/89-make-check.log`):
  - ruff: "All checks passed!"; 316 files already formatted; basedpyright "0 errors, 0 warnings, 0 notes".
  - pytest: `2359 passed, 1 skipped, 63 deselected, 8 xfailed`; web: 241 passed.
  - Before the full run, `test_chat_eval_unchanged.py`, `test_conversation_turn.py`, `test_run_cli.py`, `test_agent_outcomes.py` and both multi-source files passed (108 passed).
- **Review, `da-review main` on the whole rebased diff:** "No actionable regressions were identified." Codex also checked in memory that the single-source tool schemas equal `tests/fixtures/chat/eval-unchanged.json`, that all nine multi-source tools require `database`, and that dictionaries route to the right database. `eng:code-review` was not run again: the return note asked for `da-review` only, and the rebase changed no behaviour beyond joining the two sides.
- **Real run for the main session to redo**, from the repo root with the model env loaded, on `321faf2`:
  ```bash
  uv run python tests/fixtures/chat/build_sources.py runs/89-sources
  mkdir -p runs/89-ws runs/89-events runs/89-conv/c1
  # 1. Two sources, single run (scenario g): expected North 13, South 10, East 8, West 6
  uv run data-agent run --type query --config full --workspace runs/89-ws \
    --events-dir runs/89-events/g --data-sources runs/89-sources/sources.toml \
    --task "How many orders were placed by customers of each region?"
  grep '"type": "sql"' runs/89-events/g/*.jsonl
  # 2. Two sources as a conversation turn (--chat with --data-sources), then a resumed turn 2
  uv run data-agent run --type query --config full --workspace runs/89-ws \
    --events-dir runs/89-events/chat1 --data-sources runs/89-sources/sources.toml \
    --chat --conversation-dir runs/89-conv/c1 --turn 1 \
    --task "How many orders were placed by customers of each region?" </dev/null
  SID=$(grep '"type": "result"' runs/89-events/chat1/*.jsonl | uv run python -c "import sys,json; print(json.loads(sys.stdin.read().split(':',1)[1] if sys.stdin else '')['session_id'])")
  uv run data-agent run --type query --config full --workspace runs/89-ws \
    --events-dir runs/89-events/chat2 --data-sources runs/89-sources/sources.toml \
    --chat --conversation-dir runs/89-conv/c1 --turn 2 --resume "$SID" \
    --task "Which region has the fewest?" </dev/null
  grep -h '"type": "\(task\|sql\|result\)"' runs/89-events/chat*/*.jsonl
  # 3. One source, README quickstart: expected Gold, 710.00
  uv run python -c "import duckdb; duckdb.connect('runs/shop.duckdb').execute(open('tests/fixtures/shop.sql').read())"
  uv run data-agent run --type query --config no-verifiers --duckdb runs/shop.duckdb \
    --workspace runs/89-ws --events-dir runs/89-events/single \
    --task "Which customer tier has the highest total amount of completed orders, and how much is it?"
  ```
  - For run 2, look at three things. Turn 2's `task` event should carry `conversation: "c1", turn: 2, data_source: null`. Its `sql` events should name a database. Its `result` event should carry `session_id` and `restored_sdk_cost_usd`.
  - If the `SID=` one-liner trips, take `session_id` from the `result` line of `runs/89-events/chat1/*.jsonl` by hand.

## Ledger
- Attempts: 2
- Review rounds: 2 (round 2: `da-review` after the rebase, nothing to fix)
- Model spend: $0
- Outcome: —

The background job that waited for `make check` hit its time limit and was stopped. Nothing is lost: `make check` itself had already finished with exit code 0 (2359 passed, 8 xfailed; web 241 passed; full log in `runs/89-make-check.log`). The watcher most likely never ended because its `pgrep -f 'make check'` check matched its own command line. The ticket text in my previous reply stands as written.

### Main session, 2026-11-17: real run after the rebase (at 321faf2)

- Two sources, single run: "North: 13 orders; South: 10 orders; East: 8 orders; West: 6 orders", exit 0. `sql` events: orders, customers, orders.
- Two sources as a conversation (`--chat --data-sources`):
  - Turn 1: "North: 13, South: 10, East: 8, West: 6", exit 0. Its `task` event has `data_source: null, conversation: c1, turn: 1`, and its `sql` events name customers, orders, orders, orders.
  - Turn 2 (`--resume`), "Which region has the fewest?": "West: 6", exit 0, answered from the session history with no query.
  - Both turns' `result` carry the same `session_id`. Turn 2 has `restored_sdk_cost_usd` 0.077142 and its own `cost_usd` $0.0003.
- One source (README quickstart, no-verifiers): "Gold tier has the highest total amount of completed orders: 710.00.", exit 0.
- (The first attempt at the chat part failed because the main session's script did not create the workspace dir. That was a script error, not a code one; rerun above.)
