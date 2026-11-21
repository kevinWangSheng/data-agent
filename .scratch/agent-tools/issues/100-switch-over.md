# 100 — 切换：去掉内置 Bash 和命令守卫，所有入口用沙箱

Status: done
Blocked by: 97, 98, 99
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-18
Branch: worktree-100-switch-over

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

- Attempts: 3（退回第 2 轮）
- Review rounds: 6
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: 已合并（PR #109，squash）；跑分验收过线，新 P1 由工单 102 修复（`4c67b5e`），CI 绿、Codex GitHub app 审查零问题。
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-18（工单会话，主会话抄录）：分支 `worktree-100-switch-over`，提交 `d5581b4`、`4f6f03a`（第 1 轮）、`50bde96`（第 2 轮）。
  - 改了什么：
    - 工具面：所有运行都有 Read/Glob/Grep/Write/Edit、MCP 数据工具、`sandbox_exec`，dbt 任务另有 `dbt_execute`；Bash 一律在 `disallowed_tools` 里，没带 `--network` 时 Web 工具也在。删了 `command_guard.py` 和它的测试。
    - workspace guard 只剩两条：不出 workspace、不碰凭据文件。
    - `--network` 经 `mcp-server --network` 传到沙箱，不带时容器 `--network none`。CLI 的 `--sandbox` 默认 `docker`，没有 docker CLI 时退出码 2。
    - 入口：
      - 网页运行和对话每一轮带 `--sandbox docker`，网页 dbt 运行再带 `--dbt-target scratch`，main 上坏掉的网页 dbt 运行就此修好；
      - Harbor 带 `--sandbox local`，不带 `--network`、`--dbt-target`；
      - `dab_server` 的 `execute_python` 换成共用的 `sandbox_exec`（docker 后端、断网、`python-data:3.12` 镜像、容器名带运行编号）。`query_database`、`classify_text` 的每个结果都带一个可以放进 `data_files` 的 id，原来的 `var_N` 变量和 `file_storage/` 都去掉了。`dab.run_once` 用 `docker_lifecycle` 清理容器。
    - local 后端的环境另外保留 `DB_TYPE`、`DUCKDB_PATH`、`VIRTUAL_ENV`、`DBT_*`，Harbor 的 `DBT_TARGET=dev` 因此能传到 `validate_project.py`。这些变量名里不能存放 Postgres URL。
    - docker 后端下，`${CLAUDE_SKILL_DIR}` 换成 `/skills/<name>`。
    - `dbt_execute` 在 scratch 下的工具说明写明临时 schema 一开始是空的，要选 `+<model>` 或用 `build`。
    - `sources.check_layout` 里"库文件必须在项目里"这条删了。
    - Skill：dbt-workflow 的两个脚本改在 `sandbox_exec` 里跑，dbt 改走 `dbt_execute`；加了临时 schema 的 `+` 规则，以及给 `validate_project.py` 设 `timeout_seconds: 360`（它自己的上限是 300 s）；dbt-write 的 `dbt deps` 改在 `sandbox_exec` 里跑；scan 关于 snapshot 的提示改成点名 `dbt_execute`。这些改动在文件头里注明了，也登记进 `NOTICE`。
    - 文档：AGENTS.md、CODING_STANDARDS.md、CONTEXT.md（新增 Sandbox、dbt_execute 两个词条）、README（需要 Docker，产品默认联网）已改；ADR-0002、ADR-0005 末尾加了"见 ADR-0010"；`data-eng-bench-duckdb.data-agent.yaml:43` 的注释已改写。
    - 测试：`tests/conftest.py` 让非集成测试里的 docker 调用都走现有的 `FakeDocker`；非验收测试按新的工具面和提示词更新。
  - 快照 `tests/fixtures/chat/eval-unchanged.json` 照人批准的例外重新生成，测试代码一字没改，测试通过。差异如下，**合入前由人审**：
    ```
    dab-query  tools / allowed: Read Glob Grep -> + Write Edit
    dab-query  disallowed: WebSearch WebFetch -> Bash WebSearch WebFetch
    dab-query  mcp: - execute_python, + sandbox_exec(command, timeout_seconds=300, data_files)
    harbor-dbt tools / allowed: - Bash
    harbor-dbt disallowed: WebSearch WebFetch -> Bash WebSearch WebFetch
    harbor-dbt mcp: + dbt_execute(command, select, exclude, full_refresh, threads), + sandbox_exec
    ```
    差异里没有澄清工具、`resume`、`CLAUDE_CONFIG_DIR`，也没有 Web 工具。
  - 不调模型的真实运行（`runs/t100/probe.py`，真实 Docker）：起 `data-agent mcp-server --sandbox docker`，先 `query_database`，再用 `sandbox_exec` 带 `data_files: [query_1]`。
    - 断网时：CSV 内容是 `id,name / 1,a / 2,b`，访问 URL 报 `Temporary failure in name resolution`。
    - 带 `--network`：DNS 能解析，连接建立了；之后的 TLS EOF 是本机代理到 colima 这段路径的问题。
    - 两个 probe 容器事后都按名字删掉了。
  - 审查：
    - 第 1 轮 `da-review main` 两条 P1，在 `4f6f03a` 修好，都配了测试：docker 运行的 workspace 里有 DuckDB 文件时拒绝运行；网页 dbt 拷项目时不带 `.env*`、密钥文件和指向它们的链接。
    - 第 2 轮 `eng:code-review main`，在 `50bde96` 修好：过时的 `dbt snapshot` 提示、README 的 dbt 规则、local 环境改用一个共用的判断、DAB 的 `Results` 命名、一处放错位置的注释。
    - 不修：
      - 保留 `--vars` 规则：`sandbox_exec` 里的 `dbt parse`、`dbt ls` 仍然适用，有测试锁住。
      - `skills/dbt-debugging/SKILL.md:61`（"re-run `dbt run --select`"）不在 Authority 里，要另开后续工单。
      - 启动时检查 Docker：这是对 spec 的一种读法。
      - 帮助文字重复、只有一个元素的 `DENIED_TOOLS`：小问题。
      - `--sandbox local` 能碰到库文件：ADR-0010 里已有这条已知限制。
  - 主会话对工单会话提的三点：
    - docker 运行拒绝 workspace 里的 DuckDB 文件：接受，这是 ADR-0010 第 2 点"沙箱不挂库文件"的落实。
    - 启动时检查 Docker：接受。比"第一次调用时报错"更严；产品本来就要求装 Docker（ADR-0010 Consequences）。
    - DAB 镜像里的 Python：到服务器上核对。
  - **给 101**：`.scratch/web-ui/spec.md:146` 的"守卫拒绝"分类已经没有 Bash 来源。另外 `docs/report.md:41` 还提到命令守卫，不在本工单的 Authority 里。
  - 不稳定的测试：`test_harbor_agent.py` 有 3 个、`test_packaging.py` 有 5 个，在从 PyPI 取 `hatchling` 失败时会挂，重跑能过。
- 2026-11-18：PR #109。
- 2026-11-18（主会话）真实运行，在工单分支 50bde96 上：
  - DAB 镜像：服务器上 `docker run --rm python-data:3.12 python3 --version` 输出 `Python 3.12.15`，supervisor 能跑。
  - 产品对话（本地网页后端 `data-agent web --port 8799`，`POST /api/conversations/<id>/messages`，联网）：题目要用 pandas 算标准差，再查 pandas 的最新版本号。结果 `completed`，$0.022。
    - `sandbox_exec` 用 `data/query_1.csv` 算出 112.569，和直接查库算的样本标准差 112.56899 一致。
    - WebFetch×2、WebSearch×1，答出 3.1.1。
  - 网页 dbt 运行（`POST /api/runs`，no-verifiers）：结果 `completed`，$0.015。
    - `sandbox_exec`×6：两个 dbt-workflow 脚本都在沙箱里跑通，validate 用了 `timeout_seconds: 360`。
    - `dbt_execute` 1 次，参数 `run --select +order_totals`，返回 exit_code 0，`target_schema: da_run_20261118t164407z_fdc90b_674f210e`。
    - 源库 `main` 跑前跑后都是 5 张表，`raw` 都是 4 张，`raw.orders` 都是 37 行。
    - 库文件在 `<运行目录>/db/`，profile 在 `<运行目录>/profiles/`，workspace 里两样都没有。
- 2026-11-18 PR #109：CI 两个 check 都 pass（50bde96）。
- 2026-11-18 **退回（第 1 轮）**：Codex GitHub app 审 50bde96 提了两条 P1，主会话真实运行发现两条，都对照代码核实属实（第 4 条是后来补的，上一个会话已停掉，重新派）：
  1. **（Codex P1）CLI 运行的 workspace 里有凭据文件时，docker 沙箱能读到。** `agent.py:2021` 附近只检查 DuckDB 文件在不在 workspace 里。直接对一个现有项目跑 CLI 时，不会经过 `prepare_workspace`（只有网页运行走这一步，它会去掉 `.env*` 和密钥文件），所以项目里的 `.env`、`.pgpass` 会随 workspace 一起挂进容器，`sandbox_exec` 用 shell 就能读。workspace guard 只拦内置文件工具，拦不住沙箱里的 shell。这违反 ADR-0010 第 2 点。
     - 要做的：照拒绝 DuckDB 文件的做法，workspace 里有凭据文件（判断用 workspace guard 现有的同一套规则）时拒绝 docker 运行，exit 2，报错写明是哪个文件；或者给出一个不含凭据文件的挂载。做法由你定。
     - 测试：旧代码下失败、修复后通过。
  2. **（Codex P1）local 后端把 `DBT_ENV_SECRET_*` 传进了沙箱。** `sandbox.py:79` 附近放行所有 `DBT_*`，其中包括 dbt 专门放密钥的 `DBT_ENV_SECRET_*`，`evals/jaffle.py` 的 Postgres 密码就在这里面。沙箱里的命令可以把它打印出来，或者绕过 `dbt_execute` 直接连库。
     - 要做的：沙箱环境不放 `DBT_ENV_SECRET_*`。只放已知不含密钥的具体变量，至少要排除这个前缀。`dbt_execute` 的 `task` 模式照旧能拿到 profile 需要的密钥变量，不受影响。
     - 测试：旧代码下失败、修复后通过。
  3. **（主会话真实运行）docker 沙箱里 workspace 的路径和提示词不一致。** 提示词只给出宿主机上的 workspace 路径（`agent.py:190`、`:223`），容器里 workspace 却挂在 `/workspace`。网页 dbt 运行里，agent 第一次调用 `sandbox_exec` 传的是宿主机路径（`python3 /skills/dbt-workflow/scan_project.py /Users/.../workspace`），返回 "No such file or directory"，第二次才改成 `/workspace`。每次 docker 运行都可能白费一轮。
     - 要做的：沙箱后端是 docker 时，在提示词里写明 `sandbox_exec` 里 workspace 的路径是 `/workspace`，沙箱命令里用 `/workspace` 或相对路径，内置文件工具（Read 等）用宿主机路径。local 后端两边路径相同，不用这一句。写法照通用规则，不针对任何题目。
     - 测试：docker 时提示词里有这一句，local 时没有。
     - 如果这一句改变了评测提示词，就重新生成 `eval-unchanged.json`：Harbor 是 local，DAB 是 docker，按实际情况判断。
  4. **（主会话服务器真实运行，50bde96，`runs/t100-deb`，dbt-fix-daily-cohorts full × 1）`dbt_execute` 用不了 data-eng-bench 的项目布局。**
     - 现象：题目的 `/app` 下同一层有 `dbt_models_duckdb` 和 `dbt_models_snowflake` 两个项目。`dbt_execute` 返回 "the workspace has several dbt projects at one level: dbt_models_duckdb, dbt_models_snowflake"，而且没有参数能指定用哪个项目。
     - agent 的绕法：在沙箱里读 data-agent 自己装好的源码（`sed -n ... site-packages/data_agent/dbt_execute.py`），把 `/app/dbt_models_snowflake/dbt_project.yml` 挪到 `/tmp`，跑完 dbt 再挪回来。这次 1/1 过了（$0.06），但每道 data-eng-bench 题都会这样，跑分验收会受影响。
     - 要做的：`dbt_execute` 加一个可选参数 `project_dir`，写 workspace 里的相对路径或绝对路径，必须在 workspace 里面。只有一个项目时可以不填，有多个时必须填。报错信息点出这个参数名，并列出候选项目。工具说明和 dbt-workflow Skill 写明怎么用。写法照通用规则，不针对任何题目（ADR-0005）。
     - 测试：两个项目并列时，不填 `project_dir` 就报错，报错里有参数名；填了就用那个项目；`project_dir` 在 workspace 外时拒绝。
     - 第 2 条修完后，沙箱读 `site-packages/data_agent` 源码这件事本身不用拦：local 后端的已知限制已经写在 ADR-0010 里。
  - 停掉的上一个会话在 worktree 里留下了没提交的测试改动（`tests/test_sandbox.py`、`tests/test_sandbox_cli.py`），先看一下，有用就接着用，没用就丢掉。
  - 修完以后，主会话在新提交上重跑 Harbor 单题，然后做跑分验收。
- 2026-11-18（工单会话，退回第 1 轮，主会话抄录）：提交 `970856e`、`5b819ec`、`f94b4cd`，已 push。上一个会话留下的两个测试文件是对的，保留并提交了。
  1. **凭据文件**：`agent.py` 的 `check_no_credentials` 和 DuckDB 的检查放在一起。workspace 里有凭据文件时，docker 运行以 exit 2 退出，报错写明是哪个文件。
     - 判断规则用 workspace guard 现有的 `workspace_guard.credential_files`：凭据文件名、凭据目录，以及指向这两者的链接。
     - da-review 之后另加一条：`profiles.yml` 里直接写了密钥也算。密钥指 password、token、private_key 等键的字面值、写在下一行的值、`env_var()` 的默认值；读不出来的 profile 也算。只用 `env_var('X')` 的不算。
     - 测试在旧代码下失败。
  2. **`DBT_ENV_SECRET_*`**：local 沙箱的命令拿不到这个前缀的变量。`dbt_execute` 的 `task` 模式自己组环境，照样拿得到。测试在旧代码下失败。
  3. **docker 下的路径说明**：后端是 docker 时，查数和 dbt 的提示词加一句 `DOCKER_PATHS_RULE`：`sandbox_exec` 里 workspace 是 `/workspace`，文件工具用宿主机路径。local 后端不加。DAB 一直加，因为 DAB 只用 docker。docker 和 DAB 的测试在旧代码下失败。
  4. **`project_dir`**：`dbt_execute` 新增这个参数，写 workspace 里的相对路径，或 workspace 内的绝对路径。
     - 有多个项目又没填时，报错写出参数名并列出候选项目。
     - 指到 workspace 外、软链接指出去、目录里没有 `dbt_project.yml` 的，都拒绝。
     - 写成 `/workspace/...` 时，报错提示改用相对路径。
     - 工具说明、dbt 提示词、dbt-workflow Skill 都写了用法，措辞是通用的（ADR-0005）。Skill 另写了"不要挪动 `dbt_project.yml`"，文件头注明了改动。
  - 快照：唯一的变化是 `harbor-dbt` 的 `dbt_execute` 多了一个参数 `"project_dir": {"default": "", "title": "Project Dir", "type": "string"}`。快照不记录提示词，所以 `dab-query` 没变。**合入前由人审**。
  - 不调模型的真实检查：
    - CLI docker 运行、workspace 里有 `ws/.env`：exit 2，报 "the workspace holds credential files (.env), which the docker sandbox would mount"，也没有建 events 目录。
    - local 沙箱、环境里有 `DBT_ENV_SECRET_X`：`env | grep ^DBT_` 只打印出 `DBT_TARGET=dev`。
    - 真实 dbt、两个项目并列：不填 `project_dir` 时报错并列出候选；`project_dir="../"` 被拒绝；`project_dir="dbt_models_other"` 返回 exit_code 0，PASS=3。
  - 审查：
    - da-review 一条 P1：`profiles.yml` 里写着明文密码时仍会被挂载。在 `5b819ec` 修好。
    - eng:code-review 修了：`env_var()` 的默认值和写在下一行的值也算密钥；读不出的 profile 按有凭据处理；`project_dir` 遇到沙箱路径时给提示；拒绝时的报错建议改用 `env_var()`；docstring、`PROFILES_FILE`、软链接检查、关键字参数、命名几处清理。
    - 不修：
      - `--postgres-env DBT_ENV_SECRET_*` 现在能当成 Postgres URL 的变量名用。这个名字传不到沙箱命令里，无害。
      - `executor.execute` 的位置参数列表保持原样，调用处已经改用关键字参数。
      - 改了 `sandbox.py`、`dbt_execute.py`、`mcp_server.py`，这三个不在 Authority 里，但退回的第 2、4 条直接点名了它们。
  - 已知限制：项目跑过 `dbt deps` 后，如果某个包带着样例 dotenv 文件（dbt_utils 有 `integration_tests/.env/`），CLI 的 docker 运行会被拒绝，用户得手动删掉这些文件。网页运行拷项目时会跳过它们，不受影响；Harbor 用 local 后端，也不受影响。**要人定：保持拒绝，还是挂载时跳过或遮住这些文件。**
  - `make check`：ruff、basedpyright 0 errors，2499 passed、2 skipped，前端测试通过。
- 2026-11-18（主会话）Harbor 单题复跑，在 f94b4cd 上（`runs/t100-deb2`，dbt-fix-daily-cohorts full × 1）：1/1 通过，$0.05。`dbt_execute` 调了两次，都带 `project_dir: "dbt_models_duckdb"`，都返回 exit_code 0。没有挪动文件，也没有去读 data-agent 的源码；守卫拒绝 0 次。
- 2026-11-18 **退回（第 2 轮，也是这个 PR 的最后一轮）**，PR #109 在 f94b4cd 上：
  1. **CI 失败**（ubuntu）：`tests/test_workspace_guard.py::test_credential_files_lists_a_credential_directory_and_a_link_to_one`，结果 `assert [... 'outside/.env'] == [... 'ws/settings']`，1 failed、2499 passed。`credential_files` 返回的列表没有排序，Linux 和 macOS 的目录遍历顺序不同。要做的：返回固定的顺序（例如排序后返回），或者测试按集合比较；还要看清楚 `outside/.env` 是不是本来就该列出（这条测试的注释写的是"不进入目录"）。
  2. **（Codex P1，审 f94b4cd，主会话核实属实）`profiles.yml` 的明文密钥只靠正则查**：`workspace_guard.py:44-47` 的 `PROFILE_SECRET_KEY` 只匹配行首、不带引号的键。像 `"password": example` 这样带引号的键（合法的 YAML），或者整份写成 JSON、flow 风格的 profile，都查不出来，docker 运行照样会把它挂进容器。要做的：先把 YAML 解析成映射，再检查带密钥的键（项目里有 `yaml_lite` 等现成的解析器可用，不加新依赖），读不出来的照旧按有凭据处理。测试覆盖带引号的键、JSON 格式、flow 映射，都要在旧代码下失败、修复后通过。
  - 第 1 轮另外两条 Codex P1 在 f94b4cd 上又显示了一次，是旧意见挂到了新提交上，已经修过，不用再处理。
  - 修完以后，主会话在新提交上跑跑分验收。第二轮修完还不过，按 Stop conditions 交回人。
- 2026-11-18（工单会话，退回第 2 轮，主会话抄录）：提交 `1914572`、`69c2ee4`、`3104ed8`，已 push。
  1. CI 顺序：`credential_files` 排序后返回。新测试用倒序的 `os.walk` 替身，在旧代码下失败。原测试不改就能过；`outside/.env` 本来就该列出（它是一个名叫 `.env` 的目录，只列目录本身，不进去列里面的文件）。
  2. （Codex P1）profile 改成先解析再检查：先按 JSON 解析，不行再用 `yaml_lite`，然后递归检查带密钥的键。带引号的键、整份 JSON、flow 映射、列表里的密钥，这几种都有测试，都在旧代码下失败。读不出来、解析失败、嵌套太深的，都按有凭据处理。
  - da-review 三条 P1，都已修，都配了在旧代码下失败的测试：
    - 用别的名字链接到外部 `profiles.yml` 的文件，网页拷项目时不再拷进去，`credential_files` 也会查出来；
    - 密钥键按名字里包含的词来认，`s3_secret_access_key`、`db_password` 这类都算；
    - 键里有反斜杠的按密钥处理。
  - eng:code-review 修了：
    - 误报：以 `_path`、`_file`、`_uri`、`_url` 结尾的键、`keyfile` 路径、布尔开关，都不再算密钥；
    - 漏报：密钥键下面的列表算密钥；
    - `RecursionError` 按有凭据处理；
    - 三处判断合成 `named_or_linked`，改用 `realpath`，遇到链接成环不再崩溃。
  - 不修：
    - 用 YAML 锚点或标签的 profile，`yaml_lite` 读不出，按有凭据处理，docker 运行会被拒绝。这是有意从严，换解析器要加依赖。**要人定**：保持拒绝，还是把 PyYAML 加为直接依赖。
    - 写在其他值里的密钥（如 `dsn: postgresql://u:pw@h/db`）查不出来，旧代码也查不出。
    - 用任意名字链接到一个不叫 `profiles.yml` 的 profile 文件：本轮的两条 P1 都没提。
    - 测试重复、`workspace_guard` 依赖 `verifier_v2.yaml_lite`：小问题，挪到共用模块属于重构。
  - 不调模型的真实检查：workspace 里放一份 JSON 格式、写着明文 password 的 `profiles.yml`，CLI `--sandbox docker` 运行 exit 2，报 "the workspace holds credential files (profiles.yml), which the docker sandbox would mount"，没有建 events 目录。
  - `make check`：2525 passed、2 skipped，basedpyright 0 errors，前端 295 passed。
  - 本轮只改了凭据文件的判断，工具面和提示词都没动，`eval-unchanged.json` 不用重新生成。
  - 两轮退回都已用完。之后 Codex 再有意见，照第 7 步记成已知限制，或者写进后续工单。
- 2026-11-18 PR #109：CI 两个 check 都 pass（3104ed8）；Codex GitHub app（审 3104ed8）："Didn't find any major issues"。
- 2026-11-18（主会话）在 3104ed8 上复跑 Harbor 单题（`runs/t100-deb3`，dbt-fix-daily-cohorts full × 1）：0/1，$0.048。
  - 评分器有 2 条失败：`test_day_0_retention_is_100_percent`（352 个 cohort 的 day_0 不是 100%）、`test_retention_rates_between_0_and_1`。失败在 SQL 逻辑上。
  - 工具这边正常：`dbt_execute` 调了两次，都带 `project_dir: "dbt_models_duckdb"`，都 exit 0；没有挪文件、没有读 data-agent 源码；守卫拒绝 0 次。
  - 第 2 轮只改了 docker 后端的凭据检查，Harbor 用 local 后端，走不到这些代码。同一题在 50bde96、f94b4cd 上都是 1/1，所以判断是模型波动，由跑分验收的 3 次重复来确认。
- 代码已经定在 3104ed8。下一步：跑分验收（开跑前问人），然后由人审快照差异，再合入。
- 2026-11-20（主会话，`worktree-100-switch-over`，`d51f863`）：PR #109 两项 CI 通过。开发集新版 63/90、main 59/90，12 道回归题均 3/3；DAB 15/20 对历史同题 13/20，DEB 11/18 对历史同题 11/18。人已确认快照和之前列出的限制。挂载前凭据检查、local 沙箱环境过滤和评测快照相关测试 229 passed、1 skipped。
- 2026-11-20（主会话，合入前重审）：[当前提交的新 P1](https://github.com/kevinWangSheng/data-agent/pull/109#discussion_r4647906246) 已用临时 profile 和测试值复核。标准 YAML 解析能读到明文凭据，当前 `holds_literal_secret` 返回 false，`credential_files` 返回空列表。原因是 `yaml_lite` 将合法的紧凑行内映射解析成错误的键。该配置会通过 Docker 工作区的凭据预检。两轮退回额度已用完，暂停合入，交人决定后续修复范围。
- 2026-11-20（主会话）：人确认单开 [工单 102](102-profile-flow-credentials.md)，由独立子代理修复。PR #109 等待该修复验证。
- 工单 102 在独立本地分支提交 `4c67b5e`。CLI 预检、`make check` 和两道审查已完成，修复进入 `in-review`。
- 2026-11-20（主会话）：经人同意，将 `4c67b5e` 快进合入 `worktree-100-switch-over` 并推送。GitHub 已显示该提交为 PR #109 的当前头。
- 2026-11-21（主会话）：PR #109 在 `4c67b5e` 上两项 CI 全绿；评论 `@codex review` 后 Codex GitHub app 返回 "Didn't find any major issues"。已 `gh pr merge 109 --squash` 合入 `main`，工单 100、102 标为 `done`。
