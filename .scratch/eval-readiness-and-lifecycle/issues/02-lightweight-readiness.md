# 02 — 增加轻量 readiness 检查

Status: done
Blocked by: 01 — 固定评测生命周期的验收接缝; 04 — 固化 Stage 1/Stage 2 闸门和结果合并
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 02-lightweight-readiness)
Branch: worktree-02-lightweight-readiness

## Goal

在大批量评测启动前，用低成本检查发现明显的环境、资源、超时、缓存和异常全量重派问题；检查保持浅而有界，不变成另一轮完整评测。

## Authority

- May edit: `evals/ablate.py`、相关 `evals/` helpers、对应测试文件；必要的本地运行说明
- Must not edit: acceptance tests from ticket 01 after they are merged、`src/data_agent/`、holdout sampling、其他工单文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none for implementation; any real evidence must be a bounded, human-approved local or single-task check
- Spend cap: $0 for implementation tests; no batch evaluation

## Context

- Spec: `../spec.md`
- Existing preflight checks identity, balance, wheels, one smoke task per configuration, scrub, and fail-fast infrastructure errors.
- The current smoke path uses one development task and therefore may miss task-specific long-tail behavior such as `pos-operations` redispatch.
- 重派告警复用 04 在 `evals/process.py` 里的 `full_redispatches` 计数，不另写一套规则（人定 2026-11-06）。
- 冒烟 trial 在 deadline 自停判告警，不阻断（人定 2026-11-06，ADR-0008:140）。
- Readiness must classify systemic environment/execution failures as blockers and isolated efficiency findings as warnings.

## Acceptance

- [ ] The preflight performs the existing identity, balance, smoke, scrub, and fast-fail checks without weakening them.
- [ ] The preflight adds bounded resource and timeout sanity checks that do not start a full evaluation batch.
- [ ] The preflight can inspect available evidence for obvious repeated uncached work and abnormal full-model redispatch without requiring a model call.
- [ ] A systemic readiness failure prevents the first real job from starting.
- [ ] An isolated efficiency warning is recorded with its reason and does not masquerade as a correctness failure.
- [ ] The checks have an explicit bounded time/cost policy and do not run one probe per task or per trial.
- [ ] Tests from ticket 01 pass unchanged.
- [ ] `make check` passes.

## Evidence required

Test output and a no-model demonstration of blocker versus warning behavior. If a real single-task check is needed, record the exact command and cost separately; do not start Stage 2.

## Stop conditions

- The proposed check requires broad profiling or a full model batch.
- A readiness check cannot distinguish a systemic failure from an isolated warning.
- The check needs to alter agent or verifier behavior rather than evaluator orchestration.
- The second review round still finds a contract or cost-bound violation.

## Failure handling

Set `Status: ready-for-human` and add a Comment with the check that was attempted, its observed output, and why the bounded readiness contract could not be implemented safely.

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0
- Outcome: 已实现；01 的 3 条验收测试转绿
- Human review minutes: —

## Comments

- 实现：新增 `evals/readiness.py`，`evals/ablate.py` 在 preflight 里调用。原有的余额、wheel、冒烟、scrub、request/record、fail-fast 检查不变。
  - 阻断项（在第一次 Harbor 运行前停下，退出 2）：
    - agent_timeout_sec 不大于 agent 的 150 s 自停余量。
    - `docker info` 失败或无响应。
    - Docker 内存低于 并发 trial 数 × 1762 MiB（2026-11-05 审计里单 trial 最高峰值）。不用 DuckDB 的 2 GiB 上限：没有 trial 达到过，而且会让 Mac 上 1.9 GiB 的 Colima 连 1 个 trial 都跑不了。
    - Docker data root 所在磁盘的剩余空间低于 并发 trial 数 × 4 GiB（DuckDB temp 上限）。data root 不在本机时（Colima）不测磁盘，也不列为已检查。
    - 并发数 = --n-concurrent、job config 里的值、续跑 job 保存的 n_concurrent_trials 三者取最大，再以 job 的 trial 数为上限。--n-concurrent 小于 1 拒绝。
  - 告警只来自每个配置唯一的冒烟 trial（不另加探针）：deadline 自停、工具语句超时、全量重派（复用 04 的 `process.trial_metrics` `full_redispatches`）、prompt cache 命中率低于 50%（迄今所有 dev 运行 0.938–0.989）。每条写进 `OUT/readiness/<stamp>.json`，并以 `readiness` {clean, checked, warnings} 带进 ablation.json/.md；重跑时冒烟再次发现的同一告警只出现一次。
  - 01 的三条 strict-xfail 测试转绿，只删了 xfail 标记（主会话核对 diff，另外只有 import 行加了 readiness）。新增 autouse fixture `machine` 伪造 Docker/磁盘探测，和已有的 `account`、`prefetched` 一样。
- 测试证据：make check → "All checks passed!"，2033 passed、1 skipped（基线 2007 passed、3 xfailed）。
- 无模型演示，本机真实探测（`readiness.check_environment`）：
  - `PASS 1 [] ({'concurrent_trials': 1, 'docker_memory_bytes': 2054307840, 'docker_root': '/var/lib/docker', 'free_disk_bytes': None, ...}, ['agent_timeout', 'docker_memory'])`
  - `BLOCK 2 [] -> Docker has 1.9 GiB of memory; 2 concurrent trials need 3.4 GiB (a trial peaked at 1.72 GiB): ...`
  - `BLOCK 1 ['agent_timeout_sec=100'] -> agent_timeout_sec=100 leaves the agent no time: it stops itself 150 s before its trial deadline, ...`
  - （这个演示在第 2 轮之前跑；第 2 轮没改环境检查代码。）
- 无模型演示，`evals ablate` CLI 配假 Harbor/余额（在 4e99cd5 和 a3539ca 之间跑，所以下面的阻断例子不反映第 1 轮对磁盘/并发的修改）：
  - 告警：`readiness warning: the full smoke trial on dbt-alpha re-dispatched every model ... (full redispatch)`；退出 0；2 次冒烟、2 个 job；ablation.md 写 "Readiness: not clean, 1 warning ..."。
  - 阻断（Docker 4 GiB、3 个并发）：`ablate: readiness: Docker has 4.0 GiB of memory; 3 concurrent trials need 5.2 GiB ...; no job starts`；退出 2；0 次冒烟、0 个 job。
  - 不需要也没有跑真实单题。
- 审查第 1 轮，da-review（Codex）：
  - P1 并发数没校验、和 Harbor 实际用的不一致：已修（a3539ca）。
  - P2 磁盘测的是 OUT 而不是 Docker 存储：已修（a3539ca）。
  - P2 留出集输出带题目名：不修。冒烟题永远是 manifest 的 dev[:1]，是 dev 题；冒烟输出按设计会写题名（smoke() docstring），不会出现留出集题目。
- 审查第 2 轮，eng:code-review（Standards 和 Spec）：
  - 已修（139f764）：ablate docstring 写成 "OUT's free disk"；重跑时告警重复；`record` 的返回值没用；测试里 GIB 重复定义。
  - 不修：n_concurrent_trials 用正则读（是我们自己的扁平 config，缺 key 时带说明停下）；告警用 dict 不用 dataclass（直接序列化成 JSON，和 process/report 一致）；模块做四件事（仍能一遍读完）；--skip-smoke 时不重读已完成批次的 trial（那是逐 trial 检查，工单禁止；04 的效率闸门覆盖每个 dev trial）。
- 已知限制：
  - 不检查重复的相同工具调用（"反复扫描项目"）：Bash 里改完再 `dbt run` 这类重复是正常的，会误报。
  - 超时阻断只拦不大于 150 s 余量的设置；偏紧但更大的值只会表现为冒烟 deadline 告警（ADR-0008：告警，不阻断）。
  - `checked` 列出任何一次已记录的 preflight 跑过的所有检查。
- 合并（2026-11-07）：PR #71 squash 为 7c01a16。CI 两项全绿；Codex GitHub app 对 139f764 审查完成、无意见。
