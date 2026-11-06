# 61 — DAB 的 Mongo 关掉服务端 JavaScript，并加重启策略

Status: done
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）
Claimed by: claude-opus-5-5 (worktree 61-dab-mongo-noscripting)
Branch: worktree-61-dab-mongo-noscripting

## Goal

agent 发 `$where` 这类服务端 JavaScript 查询时，Mongo 返回错误，不再整库崩溃；万一进程退出，容器自己起来。

## 已和用户定下的（2026-11-06，grilling 第 2 轮 Q6）

- `scripts/da-remote` 的 `benchmarks` 子命令建 Mongo 容器时（`:454-455`）：`docker run` 加 `--restart unless-stopped`，mongod 参数加 `--noscripting`（保留 `--wiredTigerCacheSizeGB 0.5`）。
- 已存在的容器参数在创建时定死，`container()`（`:442-450`）只会 `docker start`。人已批准：合入后由主会话在服务器空闲时重建一次（`docker rm -f da-dab-mongo` 后重跑 `scripts/da-remote benchmarks`；必须用仓库里的 `scripts/da-remote`，PATH 上的 `~/.local/bin/da-remote` 是旧版），数据卷 `da-dab-mongodata` 保留；回滚是用旧参数再建。工单会话不碰服务器。
- Postgres 容器不在本工单范围。

## Authority

- May edit: `scripts/da-remote`、`tests/test_da_remote.py`
- Must not edit: `tests/acceptance/`、`evals/`、`src/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（重建和真实运行由主会话做）
- Spend cap: $0

## Context

- 证据（2026-11-06，主会话用 `da-remote exec` 只读查）：
  - `da-dab-mongo` 在 2026-11-02 04:49:58 退出，`OOMKilled=false`、exit 0、`RestartPolicy=no`；宿主机 22 天未重启。
  - 容器日志：`"ctx":"js"`、`Got signal: 4 (Illegal instruction)`，生命周期内只有这一次。
  - 触发：`runs/dabtrial-no-context/krama/query1/run0/events.jsonl` 在 04:49:58.249 发出 `{"collection":"files","filter":{"$where":"this._id.toString() === '...'"},"limit":1}`，56 ms 后崩溃；之后 agent 一直 "Connection refused"，同批 9 次 infra_error。
  - 服务器 CPU：Intel Xeon E5-2680 v2（有 AVX、无 AVX2）。"mongo:8 的 JS 引擎用了这颗 CPU 不支持的指令"是推断，未确认。
- `--noscripting` 是 mongod 的命令行参数（等价配置 `security.javascriptEnabled: false`），开启后 `$where`、`$function`、`$accumulator`、mapReduce 报错。实现时用 MongoDB 官方文档核对 mongo 8 仍支持此参数，引用链接写进 Comments。
- 现有测试 `tests/test_da_remote.py:806` 断言了 `--name da-dab-mongo` 和 `mongo:8`。

## Acceptance

- [ ] 新测试：`benchmarks` 新建 Mongo 容器时的 `docker run` 参数含 `--restart unless-stopped` 和 `--noscripting`（修复前会失败）。
- [ ] 现有 `da-remote` 测试不改语义，全部通过。
- [ ] `make check` 通过。

## Evidence required

- 测试输出。
- 主会话在合入后、服务器空闲时：重建容器，确认 `docker inspect` 的 `RestartPolicy=unless-stopped`、参数含 `--noscripting`；用 pymongo 发一条 `$where` 查询，确认返回错误且容器仍在运行；跑 DAB krama query1 × 1（`no-context`），exit 0。命令和输出贴进 Comments。

## Stop conditions

- mongo 8 不支持 `--noscripting`，或 DAB 的装载、工具依赖服务端 JS。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: done (PR #59 → 1f65e68); container rebuilt, $where returns an error, krama query1 exit 0 with 10/10 Mongo calls ok
- Human review minutes: —

## Comments

- 2026-11-06 ticket agent (claude-opus-5-5), branch `worktree-61-dab-mongo-noscripting`, commits f0ae2ef, 9c30191
  - Change: the Mongo container is created with `--restart unless-stopped`, and mongod gets `--noscripting` after `--wiredTigerCacheSizeGB 0.5` (`scripts/da-remote:454-457`).
  - Docs: the mongod v8.0 manual lists `--noscripting` ("Disables the scripting engine"): https://www.mongodb.com/docs/v8.0/reference/program/mongod/ . Its config equivalent `security.javascriptEnabled` disables `$where`, `mapReduce`, `$accumulator` and `$function`: https://www.mongodb.com/docs/v8.0/reference/configuration-options/ .
  - Stop condition: grep in `src/`, `evals/` and `scripts/` for `$where|$function|$accumulator|mapReduce|javascript` found nothing, and DAB loads data with `mongorestore`. 未确认: DAB's own tool code exists only on the server and was not checked; the krama run after the rebuild covers it.
  - Test: `-k no_server_javascript` fails against main (`'--restart' is not in list`) and passes on this branch. `tests/test_da_remote.py`: 122 passed before, 123 after; existing tests unchanged. `make check` (f0ae2ef): 1853 passed, 1 skipped, 38 deselected; lint, format and types clean. 9c30191 changes only the new test (file checks clean, 123 passed).
  - Real run: none (no server access by Authority).
  - `da-review main`: no findings.
  - `eng:code-review main`, Spec: no gaps, no scope creep.
  - `eng:code-review main`, Standards: fixed the test structure in 9c30191. Not fixing: no `-> None` on the test (neighbouring tests omit it too); an existing container only gets `docker start` and keeps its old flags, by design, since the rebuild belongs to the main session.

- 2026-11-06 main session: PR #59 CI green; Codex GitHub app on 9c30191: no findings. Squash-merged to main as 1f65e68.
  - Rebuild (server idle; repo `scripts/da-remote`). Before: `docker inspect` gave `restart=no cmd=["--wiredTigerCacheSizeGB","0.5"] image=mongo:8`, volume `da-dab-mongodata:/data/db`. Ran `scripts/da-remote exec -- docker rm -f da-dab-mongo`, then `scripts/da-remote benchmarks` → `da-dab-mongo: created and started`, `mongodb ok`. After: `running restart=unless-stopped cmd=["--wiredTigerCacheSizeGB","0.5","--noscripting"]`, same `da-dab-mongodata` volume. Rollback: rebuild without the two flags.
  - `$where` check: pymongo is not installed in the server's repo env, so I used mongosh in the container on a throwaway db `t61probe` (dropped afterwards): `find({$where:"this._id === 1"})` → `ERROR: Location6108304 no globalScriptEngine in $where parsing`. The container is still `running`, `RestartCount=0`.
  - DAB run: `scripts/da-remote setup 1f65e68`; `scripts/da-remote start t61-krama -- dab --dab-root ../da-benchmarks/DataAgentBench --dataset krama --query 1 --config no-context --runs 1 --out runs/t61-krama --budget 4` → `da-remote: evals exited 0`, spend $0.0886.
    - All 10 Mongo tool calls succeeded (`dab_tool_calls.jsonl`). There were no connection errors, and the container did not restart. mongorestore loaded `domain_docs_db.files` (1651 documents, 0 failures), so DAB's loading and tools do not depend on server-side JS.
    - The trial itself is `unfinished`: after 71 turns the model call failed with "Prompt is too long" (`terminal_reason: blocking_limit`). This is an agent context limit, not Mongo. It is recorded for the DAB formal eval (plan §10 item 18) and is outside this ticket.
