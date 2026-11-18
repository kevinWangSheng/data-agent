# 100 — 切换：去掉内置 Bash 和命令守卫，所有入口用沙箱

Status: ready-for-agent
Blocked by: 97, 98, 99
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

所有运行（产品、data-eng-bench、DAB）都用同一套工具面：`sandbox_exec`、`dbt_execute`（dbt 任务）、Read/Glob/Grep/Write/Edit、MCP 数据工具，带 `--network` 时再加 Web 工具。内置 Bash 和 `command_guard.py` 去掉。提示词、Skill、规则和文档都改成新边界。跑分验收在本工单做。

## Authority

- May edit: `src/data_agent/agent.py`（工具列表 `agent.py:103-115`、dbt 和查数提示词 `:202-216` 附近、把 `--network` 接到沙箱网络）；`src/data_agent/cli.py`；删除 `src/data_agent/command_guard.py` 和 `tests/test_command_guard.py`；`src/data_agent/workspace_guard.py`（只保留"不出 workspace"和"不读写凭据文件"）；`src/data_agent/web/launcher.py`、`src/data_agent/web/conversations.py`（带 `--sandbox docker`，dbt 运行带 `--dbt-target scratch`）；CLI 的 `--sandbox` 默认改成 `docker`；`evals/harbor_agent.py`（`--sandbox local`，不加 `--network`、不加 `--dbt-target`）；`evals/dab.py`、`evals/dab_server.py`（DAB 不启动 `data-agent run`，`dab_server` 里的 `execute_python` 改成调用同一个沙箱模块的 `sandbox_exec`：docker 后端、断网、DAB 的 `python-data:3.12` 镜像；查询结果变量并入 `data_files`）；`skills/dbt-workflow/`、`skills/dbt-write/`（"在 Bash 里跑 dbt"改成 `dbt_execute`、两个脚本改在 `sandbox_exec` 里跑，文件头注明改动）；`NOTICE`；`AGENTS.md`、`CODING_STANDARDS.md`、`CONTEXT.md`（按 spec "规则与文档"一节）；`docs/adr/0002-*.md`、`docs/adr/0005-*.md`（只在末尾加"见 ADR-0010"）；`README.md`（写明产品默认联网、需要 Docker）；`tests/`
- 例外（人 2026-11-17 批准，照快照测试的通行做法覆盖，旧版本留在 git 历史里）：可以重新生成 `tests/fixtures/chat/eval-unchanged.json`，因为本工单是 ADR-0010 定下的、有意改评测工具面；`tests/acceptance/test_chat_eval_unchanged.py` 的代码一字不改。新旧快照的差异贴进 Comments，合入前由人审。
- Must not edit: 已有的验收测试（`tests/acceptance/`）的断言、夹具和输入（上一条的快照除外）；`web/`（前端是工单 101）；其他工单的文件；`.claude/`
- Server access: `da-remote`（跑分验收时，由主会话执行）
- Spend cap: 验收按 spec：开发集约 $7–15，data-eng-bench、DAB 各 20 次以内

## Context

- spec 全文，尤其"工具面""提示词与 Skill""规则与文档""验收"。ADR-0010。
- Skill 写法不能针对任何榜单题目（ADR-0005）。
- 已知限制写进 ADR-0010 Consequences 已有的那条：跑 data-eng-bench 时 `local` 后端能直接写题目的 DuckDB 文件。
- `.scratch/web-ui/spec.md:146` 里"守卫拒绝"的分类会变，只记进 Comments 交给工单 101，本工单不改 `web/`。
- 97–99 交给本工单的事（主会话 2026-11-18 汇总，细节见各工单 Comments）：
  - **网页 dbt 运行在 main 上是坏的，本工单要修好**：99 已经让网页 dbt 运行的 workspace 里不放 `profiles.yml` 和库文件，但 `launcher.py` 还没带 `--sandbox docker --dbt-target scratch`。人 2026-11-18 接受这段时间坏着，由本工单接上。
  - local 后端（Harbor）的命令环境是白名单（`sandbox.py` 的 `LOCAL_ENV`），可能缺题目要的变量（`DB_TYPE`、`VIRTUAL_ENV`、`DBT_PROFILES_DIR`）。在 Harbor 单题真实运行里核对，缺的就补上。
  - DAB 的 `python-data:3.12` 镜像里 Python 是什么版本未确认，换成同一个沙箱模块时要确认 `sandbox_supervisor.py` 能在里面跑（它只用标准库，用 `start_new_session`，不需要 3.11）。
  - 在临时 schema 里只选一个模型（`run --select m`）会因为上游模型不在里面而失败（99 的真实运行两次都是先失败，改用 `+m` 才成功）。Skill 和工具说明要写明：用 `+<model>` 或 `build` 把上游一起建出来。写法照通用规则，不针对任何题目（ADR-0005）。
  - `dbt_execute` 在 `task` 模式只放行 `DBT_*`、Postgres 变量和 profile 里 `env_var()` 点名的变量。模型或 `dbt_project.yml` 里用 `env_var()` 读的其他变量，以及 `PGPASSWORD`、AWS 这类凭据，到不了 dbt。跑分时遇到真实题目需要哪个，就把它加进白名单，并写进 Comments。
  - `src/data_agent/web/sources.py` 的 `check_layout` 还写着"库文件必须在项目里"，现在网页运行会把库文件移出去，这条要删掉或改写措辞（99 没改）。
  - `evals/configs/data-eng-bench-duckdb.data-agent.yaml:43` 的注释说 Web 工具在"每次 data-agent run"都关，对产品已经不成立，按规则与文档一节改写（98 记下的）。
  - 运行编号的格式是 `<events-dir 名>-<8 位 hex>`（97），临时 schema 是 `da_run_<编号，转小写，非 [a-z0-9_] 的字符换成 _>`（99），scratch profile 固定叫 `da_run`，命令行用 `--profile da_run` 指定。
  - 本机 colima 只挂 `$HOME`，真实 Docker 的测试把 workspace 放在 `$HOME` 下（见 97 Context）。

## Acceptance

- [ ] 所有运行的工具列表里没有内置 Bash，`disallowed_tools` 里有；查数任务有 Write、Edit（单元测试）
- [ ] 带 `--network` 时沙箱联网，不带时 `--network none`（单元测试）
- [ ] 各入口：`launcher.py`、`conversations.py` 带 `--sandbox docker --network`，dbt 运行另带 `--dbt-target scratch`，网页后端不会用 `local`；Harbor 带 `--sandbox local`；DAB 的 `dab_server` 用 docker 后端、断网；评测入口都不开网络（单元测试，98 的测试继续通过）
- [ ] CLI 不带 `--sandbox` 时用 `docker`；没有 Docker 时第一次调用报错，退出码 2（单元测试）
- [ ] workspace guard 仍拒绝凭据文件；`command_guard` 的引用全部移除（单元测试 + `make check`）
- [ ] `tests/acceptance/test_chat_eval_unchanged.py` 在重新生成的快照上通过；快照差异只有 ADR-0010 带来的工具面变化（去掉 Bash，加上 `sandbox_exec`、`dbt_execute`，`disallowed_tools` 的变化），没有澄清工具、`resume`、`CLAUDE_CONFIG_DIR`，也没有 Web 工具
- [ ] DAB 的 `execute_python` 换成 `sandbox_exec` 后，`tests/test_dab.py` 通过（按需更新非验收测试）
- [ ] 跑分验收（主会话做）：开发集 full 每题 3 次，点估计不低于当前 main，回归集不退步；data-eng-bench、DAB 各一次 20 次以内的小范围验证，不低于上次正式结果在同一组题上的成绩
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实运行和跑分由主会话做：最终回复写好命令——一条 data-eng-bench 单题（Harbor，`--sandbox local`）、一条 DAB 单题（`--sandbox docker`）、一条产品对话（网页后端，联网）；跑分验收的命令和预计花费。

## Stop conditions

- 跑分验收不过（开发集点估计低于 main，或回归集退步）：停下，Comment 写清楚哪些题变差，交给人。
- 要改已有验收测试的断言才能过。
- spec 自相矛盾，或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: — (filled by the human at merge, optional)

## Comments
