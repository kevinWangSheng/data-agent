# 03 — `data-agent run` 查数任务端到端

Status: in-review
Blocked by: 02
Spec: ../spec.md
Claimed by: claude-opus-5-5 (claude code)
Branch: worktree-03-run-query-task

## Goal

对夹具 DuckDB 提一个业务问题，`data-agent run` 给出答案，并写出事件文件（版本头、序号、结果含 token 与花费）。

## Authority

- May edit: `src/data_agent/`（CLI、agent 主循环、事件流、运行配置的最小版本）、`skills/`（sql-workflow、duckdb-sql）、`tests/`（除 `tests/acceptance/`）、`NOTICE`、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $2

## Context

ADR-0001 与其 Update（模型可配置、`DATA_AGENT_*` 只传给 SDK 子进程、`setting_sources=["user"]`）；plan §3 主循环与事件流、§5 SDK 锁精确版本；spec「Implementation Decisions」CLI 接口与事件流；上游 signalpilot-plugin 的 sql-workflow、duckdb-sql。

## Acceptance

- [ ] 接缝 A 中查数任务与事件文件相关的验收测试转绿（`make test-integration`）
- [ ] `data-agent run --config` 接受 6 个配置名（`baseline`、`full` 等，名字照 spec）；本工单只需最小占位，各名字都按全开处理，层开关由 05 实现
- [ ] 单次运行有 `max_budget_usd`
- [ ] 事件文件写入前遮盖已知密钥值与端点
- [ ] `make check` 通过

## Evidence required

一次真实 `data-agent run` 的命令、答案和事件文件前几行与最后一行（遮盖后）；本工单花费。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3 (2 reworks after 退回)
- Review rounds: 2 (da-review; eng:code-review); reworks not re-reviewed, per the 退回 notes
- Model spend (tokens × ADR-0001 upper list prices): rework 2 ≈ $0.016 (2 manual runs $0.0062 from their result events + 1 `make test-integration` ≈ $0.01, 未确认: the integration runs' events were not kept). Rework 1 $0.010; first attempt ≈ $0.02 (re-estimated). Total ≈ $0.05.
- Outcome: seam A query, event-file and write-block tests are green; make check passes; 退回 #1 (4 items) and 退回 #2 (3 items) fixed
- Human review minutes: —

## Comments

### 2026-10-16 claude-opus-5-5 — implementation evidence (branch worktree-03-run-query-task, commits 5bcef5e, 0b0c7df, 9e2d76b)

Real run (fixture DuckDB built from tests/fixtures/shop.sql; DATA_AGENT_* loaded the same way `make test-integration` loads them):

```
data-agent run --task "What is the total amount of all completed orders? Order status values are inconsistent: the same status can be spelled or capitalised differently. Answer with a single number." --type query --workspace runs/manual/ws/dbt_shop --duckdb runs/manual/ws/shop.duckdb --config full --events-dir runs/manual/out/events
exit=0
stdout: "The answer is 1459.75. ... **Answer: 1459.75** ... Assumptions made: \"Completed\" orders includes statuses spelled COMPLETE, Completed, and completed ..."
ground truth: SELECT sum(amount) FROM raw.orders WHERE lower(trim(status)) IN ('completed','complete') -> 1459.75
```

First lines and last line of the event file (already masked; grep for the endpoint host and for REDACTED both return 0 hits, so nothing secret appeared in this run):

```
{"seq": 0, "type": "version", "ts": "2026-10-16T08:03:22.006644+00:00", "git_commit": "49a5e5ae0d1679b9d5acab03d3d1969e87f54f9f-dirty", "skill_version": "7f9df527caec", "config": "full", "model": "deepseek-flash", "sdk_version": "0.2.168", "cli_version": "2.1.301"}
{"seq": 1, "type": "status", ..., "state": "started", "model": "deepseek-flash", "tools": ["Glob", "Grep", "Read", "mcp__data__list_tables", "mcp__data__query_database"], "mcp_servers": [{"name": "data", "status": "connected", "source": "dynamic"}]}
...
{"seq": 17, "type": "result", "ts": "2026-10-16T08:03:36.593517+00:00", "subtype": "success", "is_error": false, "num_turns": 5, "terminal_reason": "completed", "uncached_input_tokens": 7470, "cached_input_tokens": 21632, "output_tokens": 947, "cost_usd": 0.07184099999999999}
```

Write request ("Delete every order whose status is pending from raw.orders"): exit 0. The agent said it could not delete through the read-only tool, and raw.orders is unchanged (pending 6 rows, 37 total).

`make test-integration` on the final commit: `3 passed, 448 deselected, 12 xfailed` (the 12 xfails belong to other tickets). `make check`: `425 passed, 15 deselected, 23 xfailed`.

Design choices:
- Skills (sql-workflow and duckdb-sql, copied verbatim, recorded in NOTICE) are preloaded into the system prompt. The workflow is therefore loaded before the first turn and does not depend on the model calling a Skill tool.
- The SDK runs with `setting_sources=["user"]`, `skills=[]` and `strict_mcp_config`.
- Query tasks get only Read, Glob and Grep plus the `data` MCP tools, with `permission_mode="dontAsk"`. They have no Bash, Write or Edit, so the gateway is the only path to the data.
- `DATA_AGENT_*` is mapped to `ANTHROPIC_*` only inside the SDK subprocess. Every model slot is set to DATA_AGENT_MODEL, and any inherited ANTHROPIC_API_KEY or OAuth token is blanked so it is never sent to another endpoint.
- Exit codes: 2 is a config error or an infra error (no result, an HTTP API error, or `terminal_reason == api_error`); 1 is an unfinished run such as max turns or budget; 0 is success.
- The event file always ends with a `result` event.

Review findings left unfixed, with reasons:
- **cost_usd and max_budget_usd are in Claude-price units.** This is a finding for human/ticket 11. The bundled CLI prices deepseek-flash at Claude (Opus-tier) rates: tokens × Opus prices ≈ $0.0716, against the reported $0.0718. The literal spec is met ("取自 SDK ResultMessage"), but the spend tracker (ticket 11) needs a real price table or the provider's own billing. This is documented at `result_fields` in agent.py.
- **Text events hold whole TextBlocks, not stream deltas** (`include_partial_messages` is off). That is enough for scoring and attribution. Deltas belong with the UI work (scheduled later; out of scope for phase 1, per spec Out of Scope).
- **Run configs are a Python placeholder, not config files in git.** This is planned for ticket 05 (the ticket asks for a 最小占位).
- **sql-workflow is kept verbatim.** It includes "Common Benchmark Traps" and the Write result.sql/csv step, as the spec's "从 signalpilot-plugin 复制" asks. It has no task ids or expected values. QUERY_PROMPT overrides the result-file step for query runs. Whether to trim it is a call for failure attribution (ticket 14) or a human.
- **The bare endpoint host is masked as well as the full URL.** I'm keeping this on purpose, because error text often shows only the host. Its scope is limited to DATA_AGENT_BASE_URL's host.
- `thinking` events, `cli_version`, the wheel-packaged Skills and `__main__.py` are small additions. They are kept because thinking helps failure attribution, cli_version pins the CLI the SDK bundles, and the wheel fixes da-review finding 1 (an installed agent had no Skills).
- **Planned gaps:**
  - `--postgres-env` is not added yet (ticket 08).
  - `--type dbt` exits 2 with "not supported yet" (ticket 04).
  - `git_commit` is "unknown" when the package is installed from a wheel (ticket 09 may pass it in).

### 2026-10-16 主会话：退回：PR #4 上 Codex 的 3 条意见（已核实）＋ 花费口径

同一分支接着修，只修下面 4 条，能测的各配一个修复前会失败的测试；`make check`、`make test-integration` 跑一次、提交（push 由主会话做）；不跑新一轮审查。

1. `src/data_agent/agent.py:154`：装成 wheel 后（Harbor 就这样跑）从文件位置找 git commit 会得到 `unknown` 或别的仓库的 commit。构建时把源码 commit 写进包里（例如 hatch 构建钩子生成版本文件），运行时优先读它，源码 checkout 下仍可用 git。
2. `skills/sql-workflow/SKILL.md:149`：「"scored points" in F1 = points > 0」是针对某道榜单题的提示，违反 ADR-0005。删掉或换成不针对具体领域的说法；在文件头/NOTICE 注明对上游的修改。
3. `skills/sql-workflow/SKILL.md:76-77` 等：示例调用 `mcp__signalpilot__query_database(connection_name=…)`，本项目实际是 `build_server()` 暴露的工具（如 `mcp__data__query_database(sql, row_limit)`）。把 Skill 里的工具名和参数改成本项目真实存在的；还没有的工具（`explore_column`、`schema_overview`、`describe_table` 等）若 spec 里归工单 06，就照 spec 的工具名写并注明由 06 提供，否则删掉。
4. 花费口径：SDK 的 `total_cost_usd` 按 Claude 价计，不是 DeepSeek 实际花费。事件文件 `result` 里的 `cost_usd` 改为按 token 数（未缓存输入、缓存读、输出分开）乘每个模型的单价算；单价表取 ADR-0001 Update 的官方价，取区间上限（不低估）。SDK 原数保留为 `sdk_cost_usd`。单次 `max_budget_usd` 仍按 SDK 口径（Claude 价更高，所以更严）。未知模型：`cost_usd` 记为 SDK 原数并在事件里标明。Ledger 的花费按新口径重填。

### 2026-10-16 claude-opus-5-5 — 退回返工 (branch worktree-03-run-query-task, commit c9ba929; not pushed)

Each item has a test that failed before the fix (red output checked: `'unknown' == '9e2d76b…-dirty'`, `('signalpilot','query_database')` in the Skill, `KeyError: 'sdk_cost_usd'` ×2).

1. **Build commit in the wheel.** `hatch_build.py` is a hatch custom build hook. It writes `git describe --always --dirty` into the wheel as `data_agent/BUILD_COMMIT` and skips editable builds. `agent.git_commit()` reads that file first and falls back to git in a checkout. Test: `tests/test_packaging.py::test_installed_wheel_reports_the_commit_it_was_built_from` builds the wheel, extracts it and compares against git. Limit: a wheel built from an sdist (no .git) records `unknown`. Harbor installs from a directory or a wheel, so this hook does not cover sdists.
2. **F1 hint removed.** It is replaced with a domain-neutral example ("customers who ordered" = at least one order). Each Skill file has a modification note at its top, and NOTICE now says "modified as noted at the top of each file".
3. **Tool names.** The Skill now uses `mcp__data__query_database` with `sql` and `row_limit`, plus `list_tables`. The spec's ticket-06 tools are discover / hydrate / dictionary search / join candidates, and none of them is explore_column, describe_table or the others. So those references were removed. Where removing a reference would leave a gap in the workflow, the step now uses `query_database` instead (for example, SELECT DISTINCT for a column's values, and MIN/MAX in place of `get_date_boundaries` in duckdb-sql). Test: `tests/test_skills.py` checks that every `mcp__*__*` name in the Skills is one of the server's tools, and that no SignalPilot-only tool name appears.
4. **Cost basis.** `result.cost_usd` = (uncached × input + cache read × cached_input + output × output price) / 1M, from `PRICES` in agent.py, which uses the ADR-0001 Update upper prices. deepseek-flash is 0.30 / 0.006 / 1.2. deepseek-v4-pro is 1.32 / 1.32 / 3.96: its cache-read price is not given there, so it is charged at the input price, which does not understate the cost. `sdk_cost_usd` keeps the SDK figure. `cost_basis` is `price_table` or `sdk`. An unknown model (Claude included, which the SDK prices correctly) gets the SDK figure with `cost_basis: "sdk"`. `max_budget_usd` still caps the SDK figure. Tests are in `tests/test_agent_outcomes.py`.

Checks on c9ba929: `make check` → `429 passed, 15 deselected, 23 xfailed`. `make test-integration` → `3 passed, 452 deselected, 12 xfailed`.

Real run (same command as the first comment, events dir `runs/manual/out2/events`): exit=0, answer `1459.75` (matches ground truth). Event file, first and last line (`grep -c REDACTED` = 0):

```
{"seq": 0, "type": "version", "ts": "2026-10-16T08:34:37.612595+00:00", "git_commit": "c9ba929a3540af44015639cfbc592b4be6ab5787", "skill_version": "70479a64d38f", "config": "full", "model": "deepseek-flash", "sdk_version": "0.2.168", "cli_version": "2.1.301"}
{"seq": 32, "type": "result", "ts": "2026-10-16T08:34:47.198577+00:00", "subtype": "success", "is_error": false, "num_turns": 9, "terminal_reason": "completed", "uncached_input_tokens": 7941, "cached_input_tokens": 30080, "output_tokens": 991, "cost_usd": 0.0037519799999999994, "sdk_cost_usd": 0.07952000000000001, "cost_basis": "price_table", "errors": []}
```

### 2026-10-16 主会话：退回（第 2 次，最后一轮）：PR #4 上 Codex 对 c9ba929 的 3 条意见，已核实

同一分支接着修，只修这 3 条，能测的各配一个修复前会失败的测试；`make check`、`make test-integration` 一次、提交（push 由主会话做）；不跑新一轮审查。这是最后一轮，之后的新意见记成已知限制。

1. `src/data_agent/agent.py:210`：`Read`/`Glob`/`Grep` 无范围授权，绝对路径和 `../` 能读到 workspace 外（`~/.ssh`、env 文件）。照 Anthropic Agent SDK 的做法：用 `PreToolUse` hook 对这三个工具检查路径参数，解析后（含 symlink）必须在 `task.workspace` 内，否则拒绝；配测试覆盖绝对路径、`../`、symlink。
2. `skills/sql-workflow/SKILL.md:130` 一带：「Common Benchmark Traps」整节以及其他讲评测器行为（容差比较、答案 CSV 格式约定）的内容属于针对榜单的提示（ADR-0005），删掉；与数据库无关的通用 SQL 检查（JOIN 扇出、NULL 判断等）可以留在别的节里。文件头的修改说明同步更新。
3. `skills/duckdb-sql/SKILL.md:14`：整数除法写反了。DuckDB 里 `/` 是浮点除（`5 / 2 = 2.5`），`//` 是整除；照官方文档改正。

### 2026-10-16 claude-opus-5-5 — 退回 #2 返工 (branch worktree-03-run-query-task, commit cea4109; not pushed)

Each item has a test that failed before the fix: the guard tests failed on import (no guard existed) and the wiring test failed with `PreToolUse` absent from `options.hooks`; the two Skill tests run against the c9ba929 Skills fail with `('sql-workflow', 'benchmark')` and on the missing `` `5 / 2 = 2.5` `` line.

1. **Workspace guard.** New `src/data_agent/workspace_guard.py` is a `PreToolUse` hook that `build_options` installs for `Read|Glob|Grep`. It resolves each path argument against the workspace with `Path.resolve()`, which follows symlinks: Read's `file_path`, the `path` of Glob and Grep, Glob's `pattern` and Grep's `glob`. The call is denied unless every path stays inside `task.workspace`. For a glob, the fixed part before the first wildcard must resolve inside, and no `..` may follow a wildcard. Tests are in `tests/test_workspace_guard.py`. Allowed cases: relative and absolute paths inside the workspace. Denied cases: `~/.ssh`, `../` and `ws/../`, a symlinked file, a symlinked directory, Glob with `path` or `pattern` outside, `models/../../*.env`, and Grep `path`/`glob` outside.
2. **Benchmark content removed from sql-workflow.** "Common Benchmark Traps" is gone, together with its tolerance, column-naming and CSV-format items. The "Saving Output" section (result.sql/result.csv and their CSV format) is gone too, and "verify the CSV" / "SAVE" now say result / answer. The general SQL checks (fan-out, NULL, date format, case sensitivity, interpretation) remain as section 7, "Common SQL Mistakes". The header note and the description were updated. Because the Skill no longer mentions result files, QUERY_PROMPT's "do not save result.sql or result.csv" line was dropped. Test: no Skill body contains benchmark, evaluator, tolerance, result.csv or result.sql.
3. **DuckDB division.** The Skill now says `/` is float division (`5 / 2 = 2.5`) and `//` is integer division (`5 // 2 = 2`), per the DuckDB docs. The header note records the change. The test checks those two statements in DuckDB 1.2.2 as well as the Skill text.

Checks on cea4109: `make check` → `455 passed, 15 deselected, 23 xfailed`. `make test-integration` → `3 passed, 478 deselected, 12 xfailed`.

Real runs (`runs/manual/run.sh`, which loads DATA_AGENT_* the same way `make test-integration` does). Both ran on the working tree that became cea4109, which is why `git_commit` reads `c9ba929…-dirty`:

- Guard probe. A canary file `runs/manual/ws/canary.txt` sits one level above the workspace `ws/dbt_shop`. The task asked the agent to Read `../canary.txt` and the same file by absolute path, then count raw.orders. exit=0. Both Reads were denied (`tool_end` with `is_error: true`: "PreToolUse:Read hook error: Only files inside the workspace …/ws/dbt_shop can be read; denied: …/ws/canary.txt"). The canary text appears 0 times in the event file. The answer was 37 rows, which is correct.
  `{"seq": 15, "type": "result", ..., "num_turns": 4, "terminal_reason": "completed", "uncached_input_tokens": 7099, "cached_input_tokens": 13312, "output_tokens": 849, "cost_usd": 0.0032283719999999993, "sdk_cost_usd": 0.063376, "cost_basis": "price_table", "errors": []}`
- Standard question (same command as the first comment): exit=0, answer `1459.75`, which matches ground truth. `grep -c REDACTED` = 0.
  ```
  {"seq": 0, "type": "version", "ts": "2026-10-16T08:45:24.030778+00:00", "git_commit": "c9ba929a3540af44015639cfbc592b4be6ab5787-dirty", "skill_version": "3c62ed4f0db4", "config": "full", "model": "deepseek-flash", "sdk_version": "0.2.168", "cli_version": "2.1.301"}
  {"seq": 18, "type": "result", "ts": "2026-10-16T08:45:30.697642+00:00", "subtype": "success", "is_error": false, "num_turns": 5, "terminal_reason": "completed", "uncached_input_tokens": 7327, "cached_input_tokens": 20736, "output_tokens": 519, "cost_usd": 0.002945316, "sdk_cost_usd": 0.059978000000000004, "cost_basis": "price_table", "errors": []}
  ```

Known limits (recorded, not fixed, per the "last round" note):
- The guard checks arguments, not results. A recursive Glob such as `**/*` could list the *names* of files under a symlinked directory inside the workspace, if the CLI's Glob follows symlinks (未确认). Reading or grepping those files by path is still denied. Whether Grep follows symlinks during a recursive search inside the workspace is 未确认 (ripgrep's default is not to).
- The deny message includes local absolute paths, and that text lands in the event file. These are not secrets, and masking still applies.

### 2026-10-16 主会话：GitHub 审查轮数到上限，以下记为已知限制，不再返工

PR #4 上 Codex 对 cea4109 的 3 条意见（已核实）：
1. `src/data_agent/workspace_guard.py:41`：workspace 内的 `.env*` 等凭据文件仍可读。转工单 09（Harbor 任务 workspace 由它准备、轨迹由它上传）。
2. `hatch_build.py` 在仓库根目录，超出本工单 Authority。主会话接受：Hatch 构建钩子惯例放在根目录，内容只写构建 commit。
3. 从 sdist 构建的 wheel 记 `unknown`。不处理：项目不发布 sdist。
另：本轮 agent 自报的已知限制（递归 Glob 可能经 workspace 内 symlink 列出文件名；拒绝消息把本机绝对路径写进事件文件）。绝对路径转工单 09。
