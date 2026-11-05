# 52 — v2 工具缓存快照 YML 和工作区模型文件，单次调用不再重读不变的东西

Status: in-review
Blocked by: —（服务器验证要用 51 的计时，排在 51 合入后）
Spec: ../../../docs/research/2026-11-05-foundation-latency-audit.md（§3）
Claimed by: claude (worktree 52-v2-tool-caches, 2026-11-05)
Branch: worktree-52-v2-tool-caches

## Goal

`grain_facts`、`contract_check`、`model_diff`、`compare_by_key` 一次调用的 Python 开销降到 SQL 本身的量级：快照这一侧在整个进程里只读一次，工作区的模型文件映射只在目录有变化时重算。工具接口、输出格式和语义不变。

## 已和用户定下的（2026-11-05，grilling Q8、Q18）

- 快照（运行前生成，之后不变）按进程缓存：`snapshot_documents(snapshot)`，以及 `model_diff` 用到的快照侧数据（文件列表、文本、config）。
- 工作区的 `model_files`（模型名 → 文件路径）按各目录的 mtime 判断是否过期：新增、删除、改名文件都会改目录 mtime，名字到路径的映射只受这几种操作影响。不用 SignalPilot 的"最大文件 mtime + 文件数"指纹。
- 工作区文件的内容不缓存，每次照常读（`model_diff` 的工作区一侧）。
- 缓存加锁，防止并发调用重复计算（MCP 把同步工具放进线程池并行跑）。

## Authority

- May edit: `src/data_agent/verifier_v2/project.py`、`src/data_agent/verifier_v2/db.py`、`src/data_agent/verifier_v2/model_diff.py`、`src/data_agent/verifier_v2/contract.py`、`src/data_agent/verifier_v2/grain.py`、`src/data_agent/verifier_v2/snapshot.py`、`tests/test_verifier_v2_tools.py`、新的测试文件
- Must not edit: `tests/acceptance/`、`src/data_agent/verifier_v2/hooks.py`、`dispatch.py`、`stop.py`、`src/data_agent/agents/`、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（服务器真实运行由主会话跑）
- Spend cap: $0

## Context

- 取证（本地 main，pos-operations 工作区 2413 个文件，快照 33 个 YML，`retail.duckdb`）：
  - `grain_facts` 单次 0.56 s：SQL 0.24–0.33 s，快照 YML 0.21 s（`project.py:285`），`model_name` 遍历工作区 0.14 s（`project.py:341` → `model_files` → `files_under`）。
  - `contract_check` 1.5–2.4 s，内部完整调一次 `model_diff`（`contract.py:42`）。
  - `model_diff` 0.8–0.95 s，两棵树各 4735 次 `read_text` 和 `model_config`。
  - 21 线程一批：不缓存 4.0–4.6 s，只缓存快照 2.4 s，只缓存 `model_files` 1.9 s，两个都缓存 0.5–0.6 s（monkeypatch 模拟，`runs/t47-screen/foundation-audit/a1/cached.txt`）。
- 服务器上 t47-screen 的 `grain_facts` "p50 78 s"是一批调用在线程池里抢 GIL 排队（`mcp/.../func_metadata.py:164`），单次约 3.5–4 s。
- 快照在 MCP 服务器启动前生成（`agent.py:189-194`、`verifier_tools.py:1494`）。
- 先例：SignalPilot `gateway/dbt/cache.py:11-73`（缓存思路；失效方式按上面的决定）。
- 测量脚本：本机 `runs/t47-screen/foundation-audit/a1/par.py`（21 线程一批）、`prof.py`；工单 50 的 `measure50.py` 说明了本地数据怎么准备。

## Acceptance

- [ ] 现有 v2 工具测试（DuckDB 和 Postgres）不改一字全部通过。
- [ ] 新测试：同一快照连调两次，第二次不再解析 YML（修复前会失败）。
- [ ] 新测试：工作区新增、删除、改名一个模型文件后，下一次调用能看到变化；只改文件内容时，`model_diff` 看到新内容。
- [ ] 新测试：多个线程同时调用，缓存只算一次，结果一致。
- [ ] 本地复测：pos-operations 工作区 21 线程一批 `grain_facts` ≤1 s；`contract_check`、`model_diff` 单次各记下前后对比。
- [ ] `make check` 通过。

## Evidence required

- 测试输出和本地复测数（前后对比表）。
- 主会话在 51 合入后跑服务器 `pos-operations` × 1（full）：`grain_facts` 单次 `duration_ms` ≤5 s。命令和输出贴到 Comments。

## Stop conditions

- 必须改工具接口、输出格式或比较语义，或者现有测试。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2 (Fable in place of Codex; eng:code-review)
- Model spend: $0 (no paid runs)
- Outcome: implemented, local target met; server verification pending
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（45ba80d、9b7f37b、16a6048）

**Implementation (commits 45ba80d, 9b7f37b, 16a6048).**
- **Snapshot YML:** `snapshot_documents(snapshot)` is cached per process, behind a lock.
- **`model_diff` snapshot side:** each snapshot project's model files, text and resolved config are read once per process (`model_diff._snapshot_models`).
- **Workspace `model_files`:** cached per (project, model-paths). It is walked again when any of these changes:
  - the mtime of a directory the last walk listed (each mtime is taken before that directory is listed);
  - the mtime of a directory on the way from the project down to a model-paths directory (so a model-paths directory created later is seen);
  - a symlinked model file: whether it is still a file inside the project.
- **Racy rule (added beyond the ticket text):** a directory whose mtime is within 2 s before the walk is not trusted, the same rule git uses for "racily clean" index entries. File times on Linux use a coarse clock, so without it a file added in the same tick as a walk would stay invisible until the next change. The cost is that directories the agent is actively writing get re-walked; results are never stale.
- **Not cached:** workspace file contents, and `dbt_project.yml` (still parsed on every call).
- **`compare_by_key`:** no change needed. It reaches project files only through `model_diff` and `model_files`, which are now cached.
- **Existing tests:** none changed. `model_files` uses its own walker on a cache miss, so the existing `files_under` call-count test still counts per call.

**Tests (`tests/test_verifier_v2_caches.py`, 9 tests).**
- Same snapshot called twice: the second `grain_facts` / `model_diff` reads no snapshot file.
- Add, delete and rename a model file, a new subdirectory, and a model-paths directory created later are all seen on the next call. These cases are detected through the mtime comparison: a mutant without it fails 2 tests.
- Editing only file content: `model_diff` sees the new content.
- A symlinked model whose target is deleted drops out.
- A file added without a visible mtime change is still seen (the racy rule).
- 16 threads at once: the YML documents are parsed once, the model directory is walked once, and all results are equal.
- These failed before the fix: the parse-once, walk-once and concurrency tests (4 failing), the racy test, and the symlink test.

**`make check`:** `1708 passed, 1 skipped, 38 deselected, 1 xfailed`, exit 0 (`runs/t52/check-r2.txt`).

**Local re-run.** Command: `uv run python runs/t52/measure.py <a1 scratchpad: ws/ snap/ work/retail.duckdb> <label>`, on pos-operations with model `int_pos__order_daily_summary`.

| | before | after (final code) |
|---|---|---|
| `grain_facts`, 21 threads (wall) | 4.81 / 5.36 / 6.57 s | 0.45 / 0.45 / 0.53 s |
| `grain_facts`, single (median) | 0.35 s | 0.26–0.37 s (mostly SQL) |
| `model_diff`, single (median) | 0.97 s | 0.17 s |
| `contract_check`, single (median) | 1.09 s | 0.16–0.20 s |

**Reviews.**
- **Codex unavailable, Fable review.** `da-review main` failed with "Selected model is at capacity", so a read-only `fable` subagent reviewed instead. No high-severity findings. Fixed in 9b7f37b:
  - the add/remove/rename tests only exercised the racy fallback, not the mtime comparison;
  - a symlinked model whose target was deleted stayed cached, which is a regression to `OSError`;
  - an absolute model-path made the cache stat every directory up to `/`;
  - the racy test depended on timing.
- **Not fixed (Fable):**
  - `dbt_project.yml` is still parsed on every call: outside the ticket's cache set, and the target is met.
  - The `model_files` cache has no size limit: bounded by the number of projects.
- **eng:code-review, Spec:** all acceptance items met. The racy rule and the symlink re-check were judged necessary for correctness, not scope creep.
- **eng:code-review, Standards:** no hard violations. Fixed in 16a6048:
  - `_taken` renamed `_kept`, without the double negation;
  - `current()` renamed `still_valid()`;
  - one shared walk-counting helper in the tests.
- **Not fixed (Standards):**
  - Moving the cache into its own module: Authority allows only new test files.
  - A shared `once_per_key` helper: the three caches differ (`model_files` revalidates), so it would be speculative.
  - A cache-clearing fixture for tests: every key is a unique `tmp_path`.
  - The single `files_under` delegate was flagged as a Middle Man: it is kept on purpose, because callers and an existing test patch it.

**Pending (main session):** the Postgres integration tests (`make test-integration`), and the server `pos-operations` × 1 (full) run after 51 merges, with `grain_facts` single `duration_ms` ≤5 s.

### 2026-11-05 主会话

- Postgres 集成测试：在本机 Postgres 17 新建 scratch 库 `da_t52_scratch`，在 16a6048 上运行 `DATA_AGENT_TEST_POSTGRES_URL=postgresql://localhost/da_t52_scratch uv run pytest -q -m integration tests/test_verifier_v2_tools.py`，结果 `2 passed, 50 deselected`。注意：env 文件里没有 `DATA_AGENT_TEST_POSTGRES_URL`，所以 `make test-integration` 不会自动跑到这两个测试。
- 服务器运行 pos-operations × 1 排在 51 合入之后。
