# 09 — Harbor 适配器

Status: done
Blocked by: 04
Spec: ../spec.md
Claimed by: claude-code (opus-5-5)
Branch: worktree-09-harbor-adapter

## Goal

data-eng-bench 的题能经 Harbor 用我们的 agent 跑，结果与 token、花费回填进 Harbor 结果；顺带用 5 题真实运行测出单次成本。

## Authority

- May edit: `evals/`（Harbor 适配器）、`src/data_agent/`（只限适配需要的入口）、`tests/`（除 `tests/acceptance/`）、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（本地 colima；批量留到服务器）
- Spend cap: $5

## Context

plan §5 Harbor 与 dbt 版本、§7 运行时注意事项（报错记 0、只对临时错误重试、关网页搜索、版本头）；ADR-0001（`BaseInstalledAgent` 的 `install()`/`run()`）；上游 `~/dev/AI/data-agent-refs/harbor`、`data-eng-bench/configs/`。

## Acceptance

- [ ] 单题经 Harbor 跑通并出分
- [ ] 网页搜索与抓取关闭
- [ ] 只对模型接口临时错误重试
- [ ] token 与花费回填
- [ ] 上传的轨迹与事件文件不含本机绝对路径和用户名（工单 03 的 workspace guard 拒绝消息会写入绝对路径）
- [ ] workspace guard 拒绝读 workspace 内的 `.env*` 等凭据文件（工单 03 已知限制）
- [ ] 没装 `git` 的环境里也能构建 wheel：`hatch_build.py` 捕获 `OSError` 回落 `unknown`（工单 03 已知限制）
- [ ] 5 题真实运行的单次平均成本写进 Comments，并更新 plan §7 的推算
- [ ] 5 题每题的峰值内存（`docker stats`）与用时写进 Comments，并更新 plan §7"服务器与并发"的并发推算（不改 `task.toml` 资源）

## Evidence required

Harbor 命令与结果摘要；5 题的成本统计。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3
- Review rounds: 5
- Model spend: $0.35（本轮无新 Harbor 运行）
- Outcome: 退回第 2 轮 2 条 P1 已修；da-review 无新问题
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `c208e3c`…`62b838f`）

- 2026-10-16 实测（第 3 步）：`PYTHONPATH=. uv run harbor run --config evals/configs/data-eng-bench-duckdb.data-agent.yaml --path <data-eng-bench>/tasks -i dbt-daily-order-summary -i dbt-fix-division-by-zero -i dbt-fix-daily-cohorts -i dbt-rfm-customer-segmentation -i dbt-session-attribution -k 1 -n 2`（DATA_AGENT_* 照 `make test-integration` 的方式加载）。Harbor 汇总：Trials 4、Exceptions 1、Mean 0.400，用时 13m55s。session-attribution 装 agent 时 PyPI 超时，修复后（`400c6c2`）单独重跑：reward 0，无报错。
- 5 题（开发集）：每次 $0.0349–0.0375，平均 $0.036；约 78 万输入 token（93% 缓存）+ 1.2 万输出。峰值内存 373–772 MB（`docker stats` 每 3 秒采样）。用时：装 agent 62–135 秒，agent 90–363 秒，验证 1–23 秒。reward 2/5。plan §7 已更新。
- **待人决定**：`max_budget_usd=$1` 由 SDK 按 Claude 单价判断（SDK 记约 $1，按价目表约 $0.035），5 次里 4 次在 36–48 轮被截停（terminal_reason budget_exhausted）。上限怎么改需要决定，建议另开工单。
- 第一轮实测发现的适配器问题已修：workspace 从 `dbt_models_duckdb` 改为 `/app`（任务会在 `/app/dbt_project` 等处建项目，这一轮 0/3）；容器里从 astral.sh/GitHub 下载 uv 两次 TLS 失败，改成经 PyPI 安装，并加大 uv 的重试与超时。装 agent 时的网络错误不在重试列表里，记为报错，按基础设施报错重跑。
- 上传前清理：复制真实 job 目录跑 `scrub`，18/47 个文件被改（home=47），之后本机 home 路径与用户名 0 处，result.json 都能解析。agent 自己的事件文件只有容器路径。
- 审查：da-review 第 1 轮无问题；eng:code-review 的 Standards 与 Spec 意见已修（截断的事件行会盖住真实错误、断连不重试、NOTICE、测试清理）。不修：scrub 汇总固定列出全部类别（没有对应规则时也输出 0）；YAML 重试列表与 `TRANSIENT_ERRORS` 重复，有测试保持一致。da-review 第 2 轮 P2（用户名与 JSON 键同名时会改坏键）已修，加了回归测试。`make check`：742 passed，8 xfailed。
- 超出 Authority 的改动：`hatch_build.py`（验收要求）、`NOTICE`（job config 改自 data-eng-bench 的 claude-code 配置）、`docs/plan.md`（验收要求）。

### 2026-10-16 第 7 步：PR #13 退回第 1 轮（主会话）

`62b838f` CI 两次全绿。Codex 3 条 P1，主会话已核实。在原分支 `worktree-09-harbor-adapter` 上修，修完提交，不 push、不 rebase（需要同步 main 时用 `git merge origin/main`）；各配修复前会失败的测试。

1. **[P1] `evals/harbor_agent.py:185`**：容器里用 `python -m pip install uv` 引导 uv，违反"只用 uv、不用 pip"。改成不经 pip 的方式：优先把本机 uv 的 Linux 发行包（与容器架构一致、版本 `UV_VERSION`、核对 sha256）经 Harbor 的上传接口放进容器；或用 astral 官方安装脚本加 `curl --retry-all-errors` 重试。两种都走不通就停下交回 `ready-for-human`，写清卡在哪。
2. **[P1] `evals/scrub.py:89`**：只按运行 scrub 的这台机器的 home 和用户名清理；在服务器上产出、在本机清理的 job，服务器路径（`/home/<user>/...`）会留下来，scrub 还报成功。改为：可通过参数传入产出机器的 home 与用户名（可多个）；清理后扫描残留的主机绝对路径（`/home/<x>/`、`/Users/<x>/`、`/root/`），有残留就退出码非 0 并列出位置；容器内路径（如 `/app`、`/logs`）不算。
3. **[P1] 越权改 `docs/plan.md`、`NOTICE`、`hatch_build.py`：不退回**。三处都是验收标准或许可规则要求的改动；本工单 Authority 视为包含它们。

修完 `make check`；用改后的安装方式经 Harbor 真实跑 1 题；对一份复制的真实 job 用"别的 home/用户名"参数跑一次 scrub 并展示残留检查。再跑一轮 `da-review main`。最终回复给出 Comments 与 Ledger（Attempts 2）和 `Status: in-review`。

### 2026-10-16 退回第 1 轮修复（commits `8d5faf1`…`eb3599e`）

- **P1 用 pip 装 uv**（`8d5faf1`）：本机用 `curl --retry 5 --retry-all-errors` 下载 uv 0.10.8 的静态 Linux 发行包（`uv-{aarch64,x86_64}-unknown-linux-musl.tar.gz`），核对代码里固定的 sha256（取自 release 的 `.sha256` 文件），再经 `environment.upload_file` 放进容器。容器里先跑 `uname -m` 选架构，然后执行 `tar -xzf … --strip-components 1`，用 `python3 -m venv --without-pip` 建 venv，最后 `uv pip install`。整个过程不经 pip。
  - 新增测试：sha256 不符时报错；未知架构时报错；install 脚本里没有 `-m pip`，上传的是固定的发行包。
  - 两个架构的真实发行包都已下载，sha256 核对通过。
  - 修复前这些测试会失败（4 errors）。
- **P1 scrub 只清本机路径**（`8d5faf1`、`d4c7672`）：
  - 新增可重复的 `--home`、`--user` 参数，填产出 job 的那台机器的 home 和用户名；本机的始终包含在内。
  - 清理后扫描残留的 `/home/<x>`、`/Users/<x>`。有残留就列出 `文件:行: 路径`，退出码为 1。
  - JSON 转义（`\/`、`\u002f`）和嵌套在字符串里的 JSON 也会被解码后再查。这是 da-review 第 3 轮的 P2。
  - **与工单不同：`/root/` 不算主机路径。** 实测 job 里出现的 12 处 `/root/` 全是容器内路径（agent 在任务容器里以 root 运行，SDK 把过长的工具输出写到 `/root/.claude/projects/-app-…`），不含用户名；算进去的话每个真实 job 都会失败。
- **P1 越权**：按主会话结论不退回，没有改动。
- **da-review 第 4 轮 P1，已修**（`eb3599e`）：job config 里的 `override_setup_timeout_sec: 1500` 会被 data-eng-bench 榜单的静态检查拒收（`leaderboard/src/leaderboard/ci/static_analysis.py:69,128`；上游 `configs/` 也没设这一项）。已删除，并加测试保证 agent、verifier、environment 都不设被拒收的 override。修复前这条测试失败。
- **实测（第 3 步）**：命令是 `PYTHONPATH=. uv run harbor run --config evals/configs/data-eng-bench-duckdb.data-agent.yaml --path <data-eng-bench>/tasks -i dbt-fix-division-by-zero -k 1 -n 1`（DATA_AGENT_* 照 `make test-integration` 的方式加载）。

  | 运行 | 代码 | Exceptions | reward | 装 agent | agent 用时 | 花费 |
  |---|---|---|---|---|---|---|
  | 1 | `8d5faf1` | 0 | 0 | 79 s | 124 s | $0.0348 |
  | 2 | `eb3599e`，无 override | 0 | 0 | 125 s | 153 s | $0.0321 |

  - trial.log 里能看到 `tar -xzf /installed-agent/uv-aarch64-unknown-linux-musl.tar.gz` 和 `python3 -m venv --without-pip`，没有 `-m pip`。
  - 第 2 次运行的装 agent 在 Harbor 360 s 默认上限内。
  - 两次都以 `budget_exhausted` 结束（34 轮，SDK 记约 $1）。这是已有的待决定问题，不是本轮造成的。
- **scrub 实测**：复制 `jobs-5` 这个真实 job，把本机 home 换成 `/home/ci-runner`，模拟在服务器上产出。

  | 方式 | 退出码 | 结果 |
  |---|---|---|
  | 不带参数 | 1 | 列出 63 处残留 |
  | `--home /home/ci-runner --user ci-runner` | 0 | 改了 44 个文件中的 19 个，home=63；之后 `ci-runner` 和本机用户名都是 0 处 |

  本轮新跑的真实 job 不带参数清理：退出码 0，home=7。
- **审查**：
  - `da-review main` 第 3 轮：P2（转义藏住路径）已修。
  - 第 3 轮另有 P1：上传榜单需要 ATIF `trajectory.json`（`leaderboard/SUBMIT.md:123` 属实）。**不修**：这不在本工单验收标准里，适配器目前只产出自己的 JSONL。建议另开工单，写进上传榜单那张工单的验收标准。
  - 第 4 轮：P1 已修（见上）。最后一个提交 `eb3599e` 之后没再跑 da-review；修复已经用到第 2 轮，按规矩停下。
  - 本轮按主会话指示只跑 da-review，没有跑 eng:code-review。
- `make check`：750 passed，8 xfailed。
- `docs/plan.md` §7 网络一条已改为新的 uv 安装方式。

### 2026-10-16 主会话：交回时的判断与合并前集成

- `/root/` 不算主机路径：接受（是任务容器的 home，不含用户名）。
- 上传 data-eng-bench 榜单需要 ATIF `trajectory.json`（`leaderboard/SUBMIT.md:123`）：属于遗漏，现有工单都没覆盖，交人在最终审查时决定开工单。
- 预算上限：人已决定（2026-10-16），由工单 15 做。
- **合并前集成**：工单 07 已合并，本分支与 main 在 `tests/test_packaging.py` 冲突（`git merge-tree` 实测）。在原分支上 `git merge origin/main`，解决冲突（两边的测试都保留），`make check` 通过后提交，不 push、不 rebase。不需要真实运行与审查。

### 2026-10-16 合并前集成

- 在原分支上 `git merge origin/main`（不 rebase、不 push），合并提交 `9770e34`（父提交 `eb3599e`、`8eeabf1`）。
- `tests/test_packaging.py` 冲突：两边的测试都保留（本分支 `test_wheel_builds_without_git_and_reports_an_unknown_commit`，main 的 `test_installed_wheel_loads_the_verifier_agents`），共用 `build_and_extract_wheel`，不用改。
- 合并没报冲突但类型检查不过：工单 07 给 `build_options` 加了必填参数 `emit`，本分支的 `test_web_search_and_fetch_are_never_available`（`tests/test_agent_outcomes.py:148`）没传，basedpyright 报 `Argument missing for parameter "emit"`。照 main 测试的写法补上空的 `emit`（`lambda *_, **__: None`）。合并后的 `agent.py` 两边的改动都在（`disallowed_tools=WEB_TOOLS`、`api_error_status` 和 verifier 的 hooks/agents）；`evals/dab.py` 也调 `build_options`，所以 DAB 运行同样禁用 web 工具。
- `make check`：ruff、format 通过，basedpyright 0 errors，`858 passed, 15 deselected in 98.92s`；pre-commit 通过。按工单要求没有做真实运行，也没有审查。

### 2026-10-16 第 7 步：PR #13 退回第 2 轮（主会话；最后一轮）

`9770e34` CI 两次全绿。Codex 2 条 P1，都对应本工单验收标准。在原分支上修，修完提交，不 push、不 rebase；各配修复前会失败的测试。

1. **[P1] `evals/harbor_agent.py:110`**：`terminal_reason == "api_error"` 且没有 HTTP 状态时一律转成 `ApiConnectionClosedError` 并重试，模型请求超时也被重试；验收要求"只对模型接口临时错误重试"，plan §7 明确排除超时。区分超时与断连（看 SDK/CLI 给出的错误信息或类型），超时不重试。
2. **[P1] `evals/scrub.py:117`**：`--user` 只清路径段里的用户名，JSON 值或普通文本里的用户名（`{"worker": "ci-runner"}`、`running as ci-runner`）留下且 scrub 成功退出；验收要求上传的轨迹不含用户名。清理 JSON 字符串值和普通文本里作为完整单词出现的用户名（JSON 键保持不变，避免第 2 轮那种改坏键的问题）；之后残留检查也查用户名，有残留退出码非 0。

这是最后一轮；之后的新意见记为已知限制。修完 `make check`，对复制的真实 job 用 `--user` 跑一次 scrub 展示结果，再跑一轮 `da-review main`（新意见只记录）。不需要新的 Harbor 真实运行。最终回复给出 Comments、Ledger（Attempts 3）和 `Status: in-review`。

### 2026-10-16 Return round 2 fixes (commit `c418f9c`)

- **P1: timeouts were retried** (`evals/harbor_agent.py`, `src/data_agent/agent.py`)
  - **Evidence.** I searched the bundled Claude CLI binary for its error-message formatter.
    - A request that times out ends with a result message `API Error: Request timed out…`, or `API Error: No response from API (waited …)`.
    - A dropped connection ends with `Connection dropped (ECONNRESET)`, `Connection closed before the response finished`, `Unable to connect to API…` or `Connection error.`.
    - Harbor's exception text truncates output, so the adapter can't reliably see the message there.
  - **Event change.** The `result` event gains an `api_error` field: the CLI's message when `terminal_reason == "api_error"`, otherwise `null`. Final answers are never copied into it.
  - **Adapter change.** A failure with no HTTP status is turned into `ApiConnectionClosedError` (retried) only when `api_error` matches the dropped-connection pattern (`DISCONNECTED`). Everything else keeps Harbor's own classification and is not retried. That covers timeouts, SSL errors, and older event files that have no `api_error` field.
  - **Tests.** 4 dropped-connection messages are retried; 5 cases are not (2 timeout wordings, no-response, SSL, missing message). The result event records the message and does not copy an answer. Before the fix, 6 of these tests failed.
- **P1: `--user` only removed the name inside paths** (`evals/scrub.py`)
  - **Scrubbing.** The user name is now also removed as a whole word, case-insensitive, in JSON string values and plain text. `ci-runners` and `xci-runner` are left alone. JSON keys are still never rewritten (the earlier bug where keys were broken).
  - **Leftover check.** After scrubbing, the check also looks for the name as a whole word outside JSON keys. If it finds one it lists `file[:line]: name` and exits with code 1. Markers like `[redacted-user]` are ignored by the check.
  - **Tests.** Value and plain-text removal; a name left as a key inside JSON that a tool printed is reported and fails the run; a top-level JSON key is neither reported nor changed.
  - **Existing test changed.** `test_scrub_keeps_json_keys_that_spell_the_user_name` used to expect the value `"name": "agent"` to stay. It now expects `[redacted-user]` while the key `agent` stays, because the ticket requires values to be scrubbed. This is not an acceptance test.
  - Before the fix, 3 tests failed.
- **Real scrub run.** I copied the real job `runs/09/jobs-r1c` to `runs/09/scrub-r2`.
  - **Setup.** I replaced the local home with `/home/ci-runner` and the local user name with `ci-runner`. The real job only contains the name inside paths, so I also added Codex's two examples: `"worker": "ci-runner"` in `result.json` and the line `running as ci-runner` in `trial.log`. That gives 9 occurrences of `ci-runner`.
  - **Command.** `uv run python -m evals scrub runs/09/scrub-r2 --home /home/ci-runner --user ci-runner`
  - **Output.** `changed 6 file(s) of 14; redactions: ... home=7 user=2`, exit code 0.
  - **Result.** Afterwards, 0 occurrences of `ci-runner` and 0 of the local user name. The log line reads `running as [redacted-user]`, the field reads `"worker": "[redacted-user]"`, and every `.json` file still parses.
  - **Speed.** On the larger `jobs-5` copy (44 files, 956 KB) the scrub took 0.75 s.
- **Checks.** `make check`: ruff passes, basedpyright 0 errors, `869 passed, 15 deselected`. Pre-commit passes. As the ticket said, no new Harbor run was done.
- **Review.** `da-review main` round 5 (Codex): "No actionable defects introduced by this diff were found". No new findings to record.
- **Known limits, not fixed:**
  - A user name that appears as a JSON key is kept and not reported, as the ticket requires.
  - Harbor's own pattern for `Stream idle timeout` / `Response stalled mid-stream` still maps to `ApiResponseStalledError`, which stays in `TRANSIENT_ERRORS` and is retried. This is unchanged from before. I didn't find the current CLI producing these messages for our runs (not confirmed).

### 2026-10-16 第 7 步：PR #13 合并（主会话）

`c418f9c` CI 两次全绿。Codex 唯一意见：适配器不写 ATIF `trajectory.json`，有 reward 的 trial 不能上传榜单（`docs/research/2026-09-21-harbor-signalpilot-dab.md:68,100-102`）。这是此前已记的遗漏（不在本工单验收标准里），交人在最终审查时决定开工单；合并。worktree 保留（`runs/09/` 有实测证据），人看过后删。
