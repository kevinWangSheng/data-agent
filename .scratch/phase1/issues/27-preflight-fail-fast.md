# 27 — 批量评测的预检、快速失败与运行记录

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (opus-5-5)
Branch: worktree-27-preflight-fail-fast

## Goal

`ablate` 在花钱、花时间之前先确认这批能跑完并出报告：账户余额够、每个配置冒烟 1 题能跑通、冒烟结果过得了 scrub；跑的过程中账户类错误或连续的基础设施报错会让整批停下找人；每次运行留一份记录（benchmark commit、机器标签），供 ADR-0008 判断旧结果能否复用。

## Authority

- May edit: `evals/ablate.py`、`evals/report.py`（只限错误分类）、`evals/` 下新模块、`scripts/da-remote`（`check` 的余额项、`start` 传机器标签）、`docs/setup.md`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试、`evals/scrub.py`（工单 26）、`src/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（服务器实测由主会话在合并后做）
- Spend cap: $2（冒烟实测）

## Context

- ADR-0008"跑之前预检、跑的过程中快速失败"与"复用旧版结果"两条。
- **已核实的 bug**：`ApiUsageLimitError` 在 `evals/report.py` 的 `NOT_RETRIED` 里（照 Harbor 默认排除列表），所以 `is_infra_error` 判它为否，报告把它当 agent 失败记 0；它也不在 `evals/ablate.py` 的 `CONFIG_ERRORS` 里，`ablate` 不停。dev-3 里 no-skill 60/90、no-verifiers 90/90 都是它，`ablate` 照常往下跑了约 5 小时；只因为 scrub 另有问题（工单 26）才没出表，否则 no-verifiers 0% 会被当成真结果。它表示账户额度或余额用完，和 `CONFIG_ERRORS` 一样是"要人处理"的错误。
- `da-remote check` 现在只测 `https://api.deepseek.com/` 能连上（`scripts/da-remote` 约第 272 行，HTTP 401 也算 ok），看不出余额。DeepSeek 有查余额的接口（官方 API 文档 "Get User Balance"，用之前核对路径与返回字段）；key 只经环境变量传，不打印、不写文件。
- 事件文件的 version 头已有 `git_commit`、`skill_version`、`config`、`layers`、`model`、`sdk_version`、`cli_version`（`src/data_agent/agent.py:786-794`）；Harbor 由 `uv.lock` 锁定，随 commit 确定。缺的是 benchmark commit（tasks 目录所在 clone 的 HEAD）和机器标签。机器标签用人起的名字（如环境变量给出），不写主机名或 IP（scrub 会删它们，plan §7）。
- 停 Harbor 时用它自己的停止方式；如果只能发信号，只对本次启动的那个进程，按 `~/.claude/rules` 的进程规则来。
- 快速失败的阈值（连续几个）自己定一个默认值并写明理由。

## Acceptance

- [ ] `ApiUsageLimitError` 让 `ablate` 停下找人（exit 2，说明是账户问题），不出报告；配修复前会失败的测试。续跑时这类 trial 会被重跑
- [ ] 跑某个配置的过程中，连续 N 个基础设施报错就停整批、exit 2；有测试
- [ ] 预检：`ablate` 在第一个 job 之前查余额（低于本批预算的剩余部分就停）、每个配置冒烟 1 题、对冒烟结果跑 scrub；任何一项不过就停，不起正式 job；有测试。可以用参数跳过冒烟（续跑时）
- [ ] `da-remote check` 报出余额（只报是否足够和数额，不打印 key）
- [ ] 每次 `ablate` 在输出目录写运行记录：benchmark commit、机器标签、开始时间；有测试
- [ ] 真实运行（Mac colima，开发集 1 题，`full` + `baseline`，k=1）：贴预检输出与运行记录内容
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令与输出（遮掉余额以外的账户信息）。

## Stop conditions

余额接口与文档不符或需要额外权限；花费到上限；需要人决定的事；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 3
- Model spend: $0
- Outcome: returned round 1 fixed (run record checked before reusing jobs); paid smoke pending on the server (main session)
- Human review minutes: —

## Comments

- 2026-10-31 工单 agent（分支 worktree-27-preflight-fail-fast：c0f74ef、13935e7、c6b422b）：
  - make check：1223 passed, 1 skipped（shellcheck 未装）。
  - 无 key 真实运行：记录写出（benchmark_commit a3278ad1…、machine mac-colima、git_commit c0f74ef…）；预检 exit 2 "cannot read the model account's balance (DATA_AGENT_API_KEY is not set); no job starts"。
  - da-review 第 1 轮：修了 --fail-fast < 1 被拒、DA_REMOTE_HOST 是地址时标签打码。不修 "P1 smoke spend not reserved"：余额须覆盖 budget − spent，冒烟花费计入同一 OUT 下的预算，冒烟后余额仍覆盖剩余。
  - eng:code-review 第 2 轮：修了冒烟遇用量上限用账户措辞、记录先于请求检查写出、改名 harbor_run_args / prepared。不修（判断题或超出 Authority 的重构）：ablate.py 约 700 行、preflight 可拆模块；benchmark_commit 不用 git describe；两处余额检查逻辑相似；finished_at 单独读（harbor.py 不在 Authority）。
  - 已知限制：`da-remote check` 用 DeepSeek 的 is_available 判"够"；不加 --skip-smoke 时每次都冒烟所有配置；完整主机名做 DA_REMOTE_HOST 不打码，只打码 IP；CNY 固定按 7.3 换算。
- 2026-10-31 主会话付费真实运行（worktree 27，c6b422b，按 Makefile 方式 source env）：
  `DATA_AGENT_MACHINE=mac-colima uv run python -m evals ablate --tasks-dir ~/dev/AI/data-agent-refs/data-eng-bench/tasks --manifest runs/27-real/manifest.json --out runs/27-real/out2 --budget 2 --config full --config baseline --trials 1 --n-concurrent 1`
  ```
  record: runs/27-real/out2/records/20261101T045849.027784Z.json
  spend: $0.0000 over 0 event file(s); budget $2, stop at 80% = $1.6000
  balance: 79.63 CNY (about $10.91), covers the $2.00 left of --budget
  ablate: cannot fill the wheel cache for the tasks' containers: cannot probe ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0: ... Head "https://ghcr.io/v2/.../manifests/1.0.0": EOF
  exit 2
  ```
  记录：{"started_at": "2026-11-01T04:58:49Z", "benchmark_commit": "a3278ad1…", "machine": "mac-colima", "git_commit": "c6b422b…", "model": "the job config's default", "configs": ["full","baseline"], "smoke": true}。
  余额接口对真 API 已验证。冒烟没跑成：本机走代理 127.0.0.1:1087 连 ghcr.io SSL 失败（直连 401 正常），colima 本地没有该基础镜像。属于环境问题，不是本工单的 bug；冒烟成功路径待补。
- 2026-10-31 主会话：PR #27 CI 绿。退回：Codex GitHub app P1（evals/ablate.py:467）属实——`ensure_job` 在同一 --out 下续跑/复用已有 job 时只调 `check_conditions`（只比代码与 Skill 版本，evals/ablate.py:272-297），不读运行记录；换了 benchmark checkout 或机器后会在同一个 job 里混入不同版本的题。要求：续跑或跳过已有 job 前，把当前 benchmark commit 和机器标签与 OUT 下最近一份运行记录比对，不一致就 Stop（exit 2）并说明；配修复前会失败的测试。"两周内"条件不在本工单（属于工单 29 的跨版本复用）。
- 2026-10-31 用户决定：本工单的付费冒烟改在服务器上用 da-remote 跑（本机代理拉不下 ghcr 镜像），由主会话在修复后做。
- 2026-10-31 工单 agent（退回第 1 轮，08cedad、fd0e1e8）：
  - 修法：`run()` 写本次记录前调 `check_record`：OUT 下有 `jobs/*/config.json` 时，把当前 benchmark commit 和 `DATA_AGENT_MACHINE` 与 `OUT/records/` 最新一份比对；不一致、benchmark dirty 或不在 git 里、没设机器标签、有 job 没记录，都 Stop（exit 2），不写新记录（所以"最新一份"始终是建 job 那次）。检查在所有 `ensure_job` 之前，覆盖续跑与跳过。
  - 测试：`test_jobs_from_another_benchmark_or_machine_are_not_reused`（6 种参数，修复前第二次调用跳过已完成 job 返回 0）、`test_a_refused_benchmark_change_does_not_block_going_back`、`test_a_new_out_records_whatever_benchmark_it_runs_on`。改了本分支自己的 `test_every_invocation_writes_a_run_record`（dirty 那步改到新 OUT）；"每次 ablate 写运行记录"收窄为"每次通过 check_record 的调用"，主会话接受（被拒的调用不该留记录）。
  - make check：1231 passed, 1 skipped。
  - 真实运行（无 key）：OUT 里放一个 job 和 machine=mac-colima 的记录；`DATA_AGENT_MACHINE=server-1` → exit 2 "the jobs under runs/27-reject/out were run under other conditions (machine 'mac-colima' there, 'server-1' now, ...); use another --out"，无新记录；`mac-colima` → 通过记录检查进入预检，停在没有 key；不设 → exit 2 "DATA_AGENT_MACHINE is not set, so this machine cannot be matched ..."。
  - da-review：不修 P1"--skip-smoke 只许续跑时用"（工单写的是人手动给的参数）；不修 P1"超过两周不复用"（属工单 29）；P2"冒烟 trial 事件首行坏 JSON 时 has_versions 抛 traceback 而不是 exit 2"属实但是第一轮就有的代码（evals/ablate.py 约 419 行），记为已知限制。
  - eng:code-review：Standards 修了 docstring、变量名、`check_record` 只收 out/tasks_dir、常量改名；Spec 修了两次都没设标签时 None==None 放行。不修：check_record/check_request 结构相似；record 用 dict；规则在三处各写一遍；Harbor 写 config.json 前死掉的半成品 job 不触发检查（未确认）；benchmark_commit 用 --untracked-files=no。

- 2026-10-31 主会话服务器真实运行（用户决定改在服务器跑）：`da-remote setup fd0e1e8`；`scripts/da-remote start t27-smoke -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest runs/t27-smoke/manifest.json --out runs/t27-smoke --budget 2 --config full --config baseline --trials 1`（manifest 同 runs/27-real，dev=holdout=cohort-retention-matrix）→ exit 0：
  ```
  record: runs/t27-smoke/records/20261101T055002.304412Z.json
  spend: $0.0000 over 0 event file(s); budget $2, stop at 80% = $1.6000
  balance: 79.62 CNY (about $10.91), covers the $2.00 left of --budget
  wheels: CPython 3.12, glibc 2.41, x86_64: 34 wheels in /root/.cache/data-agent/wheels, 1.1 s
  smoke: full ok (reward 0, $0.1464)
  smoke: baseline ok (reward 0, $0.0506)
  | full | 0.0 | ... | 1 | 0.1606 |   | baseline | 0.0 | ... | 1 | 0.0591 |
  ```
  总花费 $0.4167（上限 $2），内存峰值 2097 MB。已知限制：holdout 运行的 `da-remote pull` 只取汇总，不含 `records/`；工单 29 用开发集运行（工单 28 后整份取回），不受影响。
- 2026-10-31 主会话：PR #27 CI 绿。Codex GitHub app（fd0e1e8）P1 "复用前拒绝超过两周的运行记录"——不修：ADR-0008 的两周条件针对改动验收时复用上一合入版本的结果（工单 29），不针对同一 --out 下续跑本次 job；退回条目已写明。合并。
