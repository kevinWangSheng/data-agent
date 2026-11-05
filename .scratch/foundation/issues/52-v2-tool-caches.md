# 52 — v2 工具缓存快照 YML 和工作区模型文件，单次调用不再重读不变的东西

Status: in-progress
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

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
