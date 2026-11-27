# 150 — 清理：审计第 6 节（`verify_model_values` 残留、`VERIFIER_MAX_TURNS` 重复、过时注释、`trace-check --runs-dir`；`large = 75` 保留）

Status: done
Blocked by: —
Spec: ../spec.md（工单表 150："清理：审计第 6 节"）
Claimed by: claude (implement session, 2026-11-27)
Branch: worktree-150-cleanup

## Goal

审计第 6 节的清理做完（五条里 `large = 75` 一条按 Q8 保留）：已不注册的 `verify_model_values` 的代码删掉；`VERIFIER_MAX_TURNS` 只定义一处；`classify.py` 过时的"provisional"注释改掉；`large = 75` 保留（`decisions.md` Q8）；`trace-check` 加 `--runs-dir`。行为不变（`trace-check` 多一个可选参数，不给时照旧搜 `./runs`）。

## Authority

- May edit：
  - `src/data_agent/verifier_tools.py`：删 `verify_model_values` 的代码（`:720` 起那一段）和文件头、`:24` 等处对它的描述（`:4-5` 说"its code below stays until the value verifier's prompt is removed"，先核实这个提示词确实已经没有）。
  - `src/data_agent/im/progress.py`（`:39` 的工具名列表）：只在核实它只是显示用、旧事件文件里出现这个名字时删掉不影响显示后再改；不确定就保留并在 Comments 写理由。
  - `src/data_agent/verifiers.py`（`:33-35`）、`src/data_agent/verifier_v2/hooks.py`（`:72-73`）：`VERIFIER_MAX_TURNS` 留一处，另一处 import。
  - `src/data_agent/classify.py`（`:37-38` 的注释）：只改注释。
  - `src/data_agent/trace_check.py`（`RUNS_DIR` `:30`，`find_events` `:37`、`check` `:143` 已有 `runs_dir` 参数）、`src/data_agent/cli.py`（`trace-check` 子命令 `:353-358`，调用 `:392-396`）：加 `--runs-dir`。
  - `tests/`（除 `tests/acceptance/`）：`tests/test_verifiers.py:72-73`、`:131`，`tests/test_verifier_v2_hooks.py:17`，`tests/test_dbt_skills.py:486`、`:647-650` 等随之调整；新行为（`--runs-dir`）配测试。
- Must not edit：`tests/acceptance/`；`src/data_agent/configs/`、`run_config.py`、`evals/`（146 在改，见 Context `large = 75`）；`web/src/`（`web/src/run-page/tools.ts:17` 也列了 `verify_model_values`，是前端显示旧运行用的，不动）；`src/data_agent/web/static/`（构建产物）；`skills/`；其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none。
- Spend cap：$0（不调模型）。

## Context

行号以 main `e494e49` 为准。审计 `docs/research/2026-11-27-product-config-audit.md` 第 6 节。

- **`verify_model_values`**：文件头说它不再注册（`verifier_tools.py:4-5`，ADR-0009），代码从 `:720` 起；`im/progress.py:39` 的列表里还有它。验收测试里只有文档字符串（`test_seam_b_mcp_server.py:13`）和"不在工具列表里"的断言（`test_verifier_v2.py:707`），删代码不影响它们。
- **`VERIFIER_MAX_TURNS`**：`verifiers.py:35` 和 `verifier_v2/hooks.py:73` 各定义一次，都是 50；`tests/test_verifiers.py:73` 断言两者相等。
- **`classify.py:37-38`**：注释写"provisional; the main session sets it from the measured cost per 1,000 rows"，工单 70 已定 20,000。
- **`large = 75`**：6 份 `src/data_agent/configs/*.toml` 的 `[max_turns]` 都有 `large = 75`，只有 DAB 用（`evals/dab.py:224-225`）。Q8 定它保留在 `[limits]`（DAB 用），本工单不删、不改这些文件。
- **`trace-check`**：只搜 `./runs`（`trace_check.py:30`）；`find_events`、`check` 已经收 `runs_dir`，只差 CLI 参数。
- **并行工单**：145 改 `classify.py`、`verifiers.py`，143、144 改 `cli.py`；后合入的一方 rebase。

## Acceptance

- [ ] `verify_model_values` 的代码和对它的描述删掉（`grep -rn verify_model_values src/data_agent --include='*.py'` 只剩确有理由保留的，写在 Comments）。
- [ ] `VERIFIER_MAX_TURNS` 只定义一处。
- [ ] `classify.py` 的注释和 20,000 一致。
- [ ] `data-agent trace-check --runs-dir DIR <run id>` 在 `DIR` 下找事件文件；不给时照旧 `./runs`；有测试。
- [ ] 已合入的验收测试照旧通过。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出；`git diff main --stat`；一次 `uv run data-agent trace-check --runs-dir <目录> <不存在的 run id>` 的输出（找不到时的报错点名目录；不需要 Langfuse）。

## Stop conditions

- 删 `verify_model_values` 的代码会让别的测试或功能失败，而不只是删掉对它的测试。
- 要改 Authority 以外的文件。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: in-review
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：审计第 6 节；不依赖别的工单，但和 145、146 改同一批文件。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：`large = 75` 保留，不再是本工单的清理项。
- 2026-11-27 (ticket agent): done.
  - **`verify_model_values`**: I first checked that the value verifier's prompt is gone. `src/data_agent/agents/` holds only `verifier.md`, and `VERIFIER_NAMES = ("verifier",)`.
    - Deleted `verify_values` and every helper and constant only it used, about 620 lines. That includes `Relations.models_named`, which nothing called.
    - Removed the 21 tests of that code in `tests/test_verifier_tools.py`, plus their dead fixtures.
    - Rewrote the module docstring. The `ref()`/`source()` resolution note, which `map_columns` still uses, moved into the `map_columns` bullet.
    - No other test or feature broke.
  - **The grep that remains** finds only `im/progress.py:39`. I kept it on purpose: the `CHECK` set copies `web/src/run-page/tools.ts:17`, which this ticket forbids editing, and taking the name out would change how events from older runs are labelled. It now has a comment saying so.
  - **`NOTICE:18`** still lists `verify_model_values`. `NOTICE` is outside Authority, so I left it; whoever owns it should drop that name.
  - **`VERIFIER_MAX_TURNS`** is now defined once, in `verifiers.py:35`; `verifier_v2/hooks.py` imports it. The test now checks that `hooks.py` has no definition of its own; it would fail on the old code.
  - **`classify.py:37-40`**: the comment now matches ticket 70 (20,000 rows, about $0.28 for short texts; long texts are stopped by the run budget).
  - **`trace-check --runs-dir DIR`**: added to the CLI and passed through `trace_check.main`. Without the flag it still searches `./runs`. Two new tests in `tests/test_trace_check.py` drive it through `cli.main`: found at any depth under DIR, the error names DIR, and the default is unchanged.
  - **Known limit**: `check()` tests the Langfuse settings before it looks for the event file. With the Langfuse variables unset, you get the settings error, not "not under DIR". I kept that order because the ticket says behaviour is unchanged, so the evidence run used placeholder variables. Looking up the file first is a one-line swap if the main session wants it.
  - **`make check`**: exit 0. Ruff clean, basedpyright 0 errors, pytest 3442 passed, 3 skipped, web 448 passed. That is the 3461 baseline, minus 21 deleted tests, plus 2 new ones. After the second commit I re-ran only the touched tests, 105 passed; the full suite did not run again.
  - **`git diff 07985b5...HEAD --stat`**: 10 files changed, 81 insertions, 1080 deletions.
  - **Real run** (placeholder Langfuse variables, nothing contacted):
    ```
    $ DATA_AGENT_LANGFUSE_PUBLIC_KEY=pk DATA_AGENT_LANGFUSE_SECRET_KEY=sk DATA_AGENT_LANGFUSE_BASE_URL=http://127.0.0.1:9 \
      uv run data-agent trace-check --runs-dir /tmp/150-cleanup/runs-demo run-does-not-exist
    trace-check: no event file 'run-does-not-exist', and no run-does-not-exist.jsonl under /tmp/150-cleanup/runs-demo/
    exit=2
    ```
  - **Reviews**:
    - `da-review` (Codex): no actionable regressions. It confirmed that none of the 33 removed definitions has a caller left, and that the CLI passes `--runs-dir` through.
    - `eng:code-review`, Spec axis: nothing missing, no scope creep. It flagged the Langfuse ordering, recorded above.
    - `eng:code-review`, Standards axis: no hard violations. Fixed: the stale test name and its no-op assertion, and the missing comment in `progress.py`. Not fixed: `NOTICE` (outside Authority); `main()`'s `runs_dir` default (existing callers use it); `UNUSED UPSTREAMS` in tests (already dead before this branch).
- 2026-11-27（主会话）：PR #148 CI 绿，Codex GitHub app 审 `021ade6` 没有意见，squash 合并。
