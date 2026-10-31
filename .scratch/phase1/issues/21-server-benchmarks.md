# 21 — 服务器上备好两个榜单的数据与 DAB 数据库

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 21-server-benchmarks)
Branch: worktree-21-server-benchmarks

## Goal

一条 `da-remote` 命令把服务器备成能跑全量评测：data-eng-bench 题目、DAB clone 与数据、DAB 的 Docker 镜像、DAB 要的 Postgres 与 MongoDB；并在服务器上真实跑通 DAB 三类数据源（文件库、Postgres、MongoDB）各一题，量出内存占用。

## Authority

- May edit: `scripts/da-remote`、`evals/dab.py`、`evals/dab_server.py`（只限修 Postgres/MongoDB 数据集真实运行暴露的问题）、`tests/`（除 `tests/acceptance/`）、`docs/setup.md`
- Must not edit: `tests/acceptance/` 下的验收测试、`evals/harbor_agent.py`（工单 20 在改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: `da-remote` 现有子命令与本工单新增的子命令。服务器上允许：clone 公开仓库到 `$DIR` 旁的目录、跑 DAB 自带的数据下载脚本、`docker build`/`docker run`（容器只绑 127.0.0.1、用户级、可删）、在 `$DIR/runs/` 下跑评测。不允许 sudo、装系统包、改系统配置。
- Spend cap: $2

## Context

- 现状：服务器（6 核 / 7 GB / 98 GB 空闲）已有本仓库、`uv sync`、`da-remote setup/start/status/push/pull`（工单 17）；只推过一道题的副本做冒烟，没有完整题目，没有 DAB。
- data-eng-bench：公开，钉 commit `a3278ad`（plan §7 与 NOTICE）；`ablate`/全量要 `--tasks-dir <clone>/tasks`。
- DAB：钉 commit `6ad9d68`（本机 clone 在 `.claude/worktrees/13-dab-runner/runs/13/DataAgentBench`，只读参考）；没有许可证，只在服务器 clone 运行，不复制进本仓库。数据用 DAB 自带下载脚本，按 `dataset_manifest.tsv` 校验（`evals/dab.py:260-290`）。`execute_python` 要 `python-data:3.12` 镜像（`evals/dab.py:14-16`）。Postgres/MongoDB 连接经 `PG_HOST`、`PG_PASSWORD`、`MONGO_URI` 等环境变量（`evals/dab.py:78`），这些值放服务器 env 文件或运行时环境，不进仓库、不进输出（已在遮盖范围）。
- 工单 13 已知：Postgres 和 MongoDB 数据集没有真实跑过（13 的 Comments）；`ExecTool` 构造失败时已加载的 PG/Mongo 数据不清理。
- plan §7："DAB 要起 Postgres、MongoDB 和约 5 GB 数据，用量未测，放一台机器上"；服务器只有 7 GB 内存，和 Harbor 并发 3 不同时跑。

## Acceptance

- [ ] 新增 `da-remote` 子命令（如 `benchmarks`）：在服务器上 clone 或更新 data-eng-bench（`a3278ad`）与 DAB（`6ad9d68`），跑 DAB 数据下载并校验，构建 `python-data:3.12`，起（或确认已在跑）Postgres 与 MongoDB 容器（只绑 127.0.0.1、数据卷可删）；可重复执行，已就绪的步骤跳过；输出过 mask，不打印密码
- [ ] `da-remote check` 增加这些就绪项（题目目录、DAB 数据校验、镜像、两个数据库可连）
- [ ] 真实运行（经 `da-remote start`/`status`/`pull`）：DAB 文件库类一题、Postgres 类一题、MongoDB 类一题，各 `--config no-context --runs 1`，贴结果与 submission；跑时用 `docker stats`/`free` 记服务器内存峰值
- [ ] 真实运行暴露的 `evals/dab.py`/`evals/dab_server.py` 问题：修并配修复前失败的测试；修不了的写成已知限制
- [ ] data-eng-bench：服务器上 `--tasks-dir` 指向 clone 的 `tasks/`，用 `da-remote start` 跑开发集 1 题 × `baseline` × 1 证明可用（如工单 20 已合并，在其之后跑）
- [ ] 单元测试覆盖新子命令的参数校验与命令拼装（不连服务器）；`docs/setup.md` 更新
- [ ] `make check` 通过

## Evidence required

单元测试输出；`benchmarks`、`check` 的真实输出（已 mask）；三个 DAB 数据源各一题和 data-eng-bench 一题的命令、结果与内存峰值。

## Stop conditions

服务器要 sudo、装系统包或改系统配置；内存不够同时放下 DAB 数据库（需要人决定加机器或分批）；DAB 数据下载或校验失败且原因在 DAB 侧；需要人决定的事；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger
- Attempts: 1
- Review rounds: 1 (Codex: 3 findings, all fixed; eng:code-review: 6 fixed, 5 not fixed with reasons)
- Model spend: $0.18 (DAB $0.1250, data-eng-bench $0.0524)
- Outcome: done，PR #22 squash 合并（0 轮退回）; all acceptance items met; branch `worktree-21-server-benchmarks`, commits 6ab01ec, ee6a84b, ad4939d
- Human review minutes: —

## Comments

### 2026-10-20 主会话：建票

用户确认：全量运行前把剩余准备做完，真正跑全量前找用户。本工单与工单 20（`evals/harbor_agent.py`）不重叠，可并行。

### 2026-10-20 工单 agent：实现（6ab01ec、ee6a84b、ad4939d，主会话抄录）

**What changed**
- New `da-remote benchmarks` subcommand. It can be rerun, and skips steps that are already done:
  - Clones data-eng-bench@`a3278ad` and DAB@`6ad9d68` into `~/da-benchmarks/` next to the repo, without pulling Git LFS files.
  - Runs DAB's own `download.sh` through `uv run --with huggingface_hub`. It checks every file's sha256 on every run (about 75 s for 13 GB).
  - Builds `python-data:3.12`.
  - Starts `postgres:17` on 127.0.0.1:15432 and `mongo:8` on 127.0.0.1:37017. Their data volumes can be deleted, and both containers mount the DAB directory read-only at the same path.
  - Writes the database settings to `~/.config/data-agent/dab.env` (mode 600) with a random password. The password is passed to docker only through the environment and is never printed.
  - Uses `bin/psql` and `bin/mongorestore` wrappers that run those tools inside the containers via `docker exec`, because the server must not get system packages.
- `check` reports the benchmark readiness items:
  - the two pinned commits and the task count;
  - DAB data sizes against the manifest (size only, which is fast; `benchmarks` does the sha256 check);
  - the image and `dab.env`;
  - both databases reachable from the host, checked with psycopg2 and pymongo the way DAB's tools connect.
- `start ... -- dab` adds `--with-requirements evals/dab-requirements.txt` and reads `dab.env`. If `dab.env` is missing, it refuses to start.
- `start` samples memory every 5 s into `memory.log`, and `status` shows the peak. This is how the memory peak gets recorded without a server shell.
- `memory.log` is never pushed, and summary-only (`ablate`) pulls leave it on the server because container names can name holdout tasks.
- `docs/setup.md` is updated.

**Problems the real runs exposed, and their fixes**
1. **The `psql` wrapper swallowed the MCP handshake.** It used `docker exec -i`, so docker took the MCP server's stdin and the server stayed `pending`. The first `bookreview` run became an infra_error with no data tools. Fixed with no `-i` and stdin from `/dev/null`. The test `test_the_loader_wrappers_leave_the_mcp_stream_alone` fails against the old wrapper.
2. **`dab_commit`'s `git diff` timed out and crashed the run.** The server's git-lfs filter re-cleans the 13 GB of data, which caused an uncaught `TimeoutExpired` (`dab21-file`). One attempt to refresh the git index also dropped the SSH session, most likely from memory pressure. Two fixes:
   - `evals/dab.py` `_git` now treats a timeout as unknown, recorded as `-dirty`, instead of crashing. The test `test_dab_commit_counts_a_git_that_times_out_as_dirty` failed before the fix.
   - `benchmarks` turns the LFS filter driver off in the DAB clone's own `.git/config` only. The `filter=lfs` attributes stay, so `dab.py` still checks the data against the manifest as ticket 13 designed. `git diff` now takes about 7 s.

**Real output, masked**
- `da-remote benchmarks`, first run: all 36 files downloaded (`Downloaded: 36, already matched: 0, failed: 0 / All datasets present and verified.`), the image was built, both containers were created and started, and `postgres ok` / `mongodb ok` printed.
- Rerun: every step printed `already`, and download.sh reported `already matched: 36`.
- `da-remote check`: `0 failure(s)`, including:
  - `data-eng-bench at a3278ad`, `tasks: 103`
  - `DataAgentBench at 6ad9d68`
  - `DAB data: all 36 files ... sizes match`
  - `image python-data:3.12`
  - `DAB database settings present, mode 600`
  - `postgres and mongodb reachable`

**Runs.** All four were started with `da-remote start` and read back with `status` and `pull`. The three DAB runs used `--dab-root ../da-benchmarks/DataAgentBench ... --config no-context --runs 1`.

| Run | Source type | Result | DAB tool calls (ok / failed) | Cost | Host memory peak (DB containers) |
|---|---|---|---|---|---|
| `dab21-file2` stockindex q1 | files | finished, exit 0 | sqlite 2/0, duckdb 9/0 | $0.0098 | 1227 MB |
| `dab21-pg2` bookreview q1 | Postgres + SQLite | finished, exit 0 | postgres 8/0, sqlite 5/0 | $0.0117 | 1264 MB (postgres 50 MiB) |
| `dab21-mongo` civic_unstructured q1 | MongoDB + SQLite | finished, exit 0 | mongo 3/0, sqlite 6/0 | $0.0762 | 1286 MB (mongo 62 MiB) |
| `deb21b` data-eng-bench dev task `dbt-daily-order-summary`, at main `58bec8d` (after ticket 20) | — | full 100.0 ($0.0388), baseline 100.0 ($0.0136) | — | $0.0524 | 1491 MB |

- Submissions:
  - stockindex: "399001.SZ — Shenzhen Component Index, 1.83%/day"
  - bookreview: "The 2020s, avg rating 4.66"
  - civic_unstructured: "9 projects ... above $500,000"
- Host memory is out of 7940 MB total, and includes about 1 GB of other services already on the machine.
- The data-eng-bench run used `ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest runs/deb21b/manifest.json --config full --config baseline --trials 1`, with a temporary manifest whose holdout list is that one dev task. `ablate` refuses to run without `full` as the reference, so baseline could not run alone.

**Reviews**
- **`da-review` (Codex).** The first attempt hit "model at capacity"; the rerun completed. All three findings were fixed in `ee6a84b`:
  - P1: `benchmarks` now requires the probe to exit 0 and print both `ok` lines.
  - P2: the size-only shortcut is gone, and `download.sh` hashes every run.
  - P2: `start` resets `memory.log`.
- **`eng:code-review`.** Fixed in `ad4939d`:
  - the stale `dab.py` comment;
  - type annotations on the embedded probe;
  - the duplicated grep pipeline;
  - the Mongo port and the `dab.env` path written in one place each;
  - `pinned` renamed to `at_pin`.
- **Not fixed, with reasons:**
  - New tests lack `-> None`: this matches the rest of `test_da_remote.py`.
  - `da-remote` is getting long (644 lines): moving the probe out would mean a new module outside this ticket's Authority.
  - The repeated `probe && code` pair: already reduced to the shared `dbs_ready` helper.
  - `check` validates DAB data by size only: a full sha256 pass takes 75 s and runs in `benchmarks`.
  - A git timeout is recorded as `-dirty`: deliberate, it fails safe on reruns.

**Known limitations**
- Peak memory for loading the large datasets has not been measured: imdb `movies.sql` is 1.6 GB into Postgres, and krama `files.bson` is 530 MB into Mongo. Measure these before running the full DAB set.
- When ticket 13's `ExecTool` fails during construction, the Postgres or Mongo data it already loaded is not cleaned up. The real runs did not hit this (the failed `bookreview` run was the stdin problem), and the next load of the same dataset drops the database first.
- If `dab.env` is deleted but the `da-dab-pgdata` volume is kept, the password no longer matches. Delete the volume along with it.
- The `dab.py` timeout fix has not run on the server, because the server is at main. The LFS-filter change avoids the timeout in practice.
- A `benchmarks` run whose probe never succeeds waits 60 s before failing. That path has no unit test; the shared `dbs_ready` check is tested through `check`.

### 2026-10-20 主会话：PR #22 Codex 意见核实

CI 绿（ad4939d）。Codex P1"`dab.env` 没设 `PG_DB`，DAB 默认连 `test` 库，新服务器上会失败"——不属实：DAB@6ad9d68 里 `PG_DB` 只在 `common_scaffold/config.py:26`、`tools/db_utils/db_config.py:26` 定义，没有任何代码使用；加载器连 `postgres` 库后 `CREATE DATABASE <数据集名>`（`postgres_utils.py:31-41`），查询连各数据集库。真实运行 `dab21-pg2`（bookreview，postgres 8/0 调用成功）也证明不需要。不修。

已 `gh pr merge 22 --squash`（9da3477），本机 `~/.local/bin/da-remote` 已更新。
