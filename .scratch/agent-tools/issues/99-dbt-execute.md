# 99 — `dbt_execute`：连库执行 dbt 的唯一入口

Status: done
Blocked by: 97
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 99-dbt-execute)
Branch: worktree-99-dbt-execute

## Goal

dbt 任务带 `--sandbox` 时，agent 多一个 MCP 工具 `dbt_execute`：参数结构化、命令白名单照 SignalPilot，在宿主机上执行 dbt。`--dbt-target scratch` 时用 workspace 外生成的 profile，写到每次运行专用的临时 schema；`task`（CLI 默认）时照题目的 profile 写题目的库。网页 dbt 运行的 workspace 里不再有库文件和 `profiles.yml`。不带 `--sandbox` 时行为不变。

## Authority

- May edit: 新增 `src/data_agent/dbt_execute.py`；`src/data_agent/mcp_server.py`（注册工具）；`src/data_agent/cli.py`（`run` 和 `mcp-server` 的 `--dbt-target scratch|task`，CLI 默认 `task`）；`src/data_agent/agent.py`（只改把工具和参数加进选项与 MCP server 启动参数的部分）；`src/data_agent/web/launcher.py`（网页的 dbt 运行：拷项目时跳过库文件和 `profiles.yml`，库文件拷到 `<运行目录>/db/`，生成的 profile 写到 `<运行目录>/profiles/`）；`src/data_agent/web/sources.py`（只在布局检查需要跟着改时动，测试同步）；`src/data_agent/sandbox.py`（只加 stub profile 相关的接线）；`NOTICE`（复制 SignalPilot 代码时）；`tests/`（新增测试）；`tests/fixtures/`（新增小 dbt 项目夹具，可复用已有的）
- Must not edit: `command_guard.py`、`workspace_guard.py`、`skills/`、`evals/` 的评测行为、提示词文本、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "`dbt_execute`""凭据与只读"两节。ADR-0010 第 2、3 点。
- 上游（路径相对 `~/dev/AI/data-agent-refs/SignalPilot/signalpilot/gateway/gateway/`）：`mcp/tools/dbt_execute.py:1-67`（工具形状、临时 schema、凭据只在执行环境）；`standalone_chat/dbt_executor.py:103-109`（命令白名单、选择器正则、输出上限 20000、时限 900）、`:583-602`（`build_dbt_argv`）。SignalPilot 是 Apache-2.0：复制的代码保留版权头，登记 `NOTICE`。
- 我们是单机，workspace 本来就在宿主机上，不需要 SignalPilot 的 `sync_from_agent_sandbox`。
- 现在网页的 dbt 运行把整个项目连同 DuckDB 文件拷进 workspace（`launcher.py:390-402`），`profiles.yml` 里的相对路径靠文件在项目里的位置（`sources.py:144-148`）。移出后由生成的 profile 指向 `<运行目录>/db/` 里的库文件（spec "凭据与只读"一节）。
- 运行编号用 `--events-dir` 的目录名（和工单 97 一致），临时 schema 叫 `da_run_<运行编号>`。
  - 注意：97 实际合入的沙箱运行编号是 `<events-dir 目录名>-<8 位 hex>`（`SandboxRun.of_run`，为避免同名的并发运行互删容器，见工单 97 Comments 偏离第 2 条）。
  - 临时 schema 用不用这个后缀、`-` 等字符怎么变成合法的 schema 名，由你定，在 Comments 写明理由。
- `--dbt-target` 的 CLI 默认是 `task`（现在的行为，Harbor 不用改）；网页后端传 `scratch` 是工单 100 的接线，本工单只提供选项和 `launcher.py` 的布局。
- 临时 schema 运行结束后保留（照 SignalPilot，spec Further Notes）。
- 现有 dbt 运行仍用 Bash 跑 dbt，本工单不动；切换到 `dbt_execute` 是工单 100 的事。
- 本机 Docker 是 colima，集成测试前先 `colima start`。colima 只挂 `$HOME`，`tmp_path` 下的目录挂进容器是空的；真实 Docker 的集成测试把 workspace 建在 `$HOME` 下（见工单 97 Context）。
- 依赖 97 的沙箱（stub profile 放在沙箱镜像里，dbt parse/compile 在沙箱里跑）。

## Acceptance

- [ ] 白名单外的命令（`show`、`run-operation`、`run; rm -rf /`）被拒绝；`select` 以 `-` 开头被拒绝；`threads` 超出 1–8 被拒绝（单元测试）
- [ ] argv 里 `--project-dir` 在 workspace 内，不经过 shell（单元测试）
- [ ] `--dbt-target scratch` 时生成的 profile 用 `da_run_<运行编号>` schema，`--profiles-dir` 在 workspace 外；`task` 时用题目的 profile（单元测试）
- [ ] 小 dbt 项目夹具上真实跑一次 `dbt run`：模型落在临时 schema，原 schema 不变（单元测试，用 dev 依赖组里的 dbt）
- [ ] 产品运行的 workspace 里没有库文件和带连接信息的 profile；沙箱里找不到它们（单元测试 + 一条真实 Docker 的集成测试）
- [ ] 不带 `--sandbox` 时工具清单和现在一字不变（单元测试）
- [ ] `make check` 通过

## Evidence required

`make check` 输出、集成测试输出。真实模型运行由主会话做：最终回复写好命令，用 `--sandbox docker` 和 `--dbt-target scratch` 跑一个本地 jaffle_shop 的 dbt 小任务，并在提示里要求 agent 用 `dbt_execute`；贴出工具调用、临时 schema 里的表、原 schema 没变的查询结果。

## Stop conditions

- 做不到"产品运行的 workspace 里没有凭据"而不改 Authority 以外的文件。
- spec 自相矛盾，或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3（退回第 2 轮）
- Review rounds: 6
- Model spend: $0.034 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: merged (PR #108 → 28db976)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-18（工单会话，主会话抄录）：分支 `worktree-99-dbt-execute`，提交 `005e048`、`57dbcf5`、`b379dc3`。
  - 检查：
    - `make check` exit 0：2559 passed、2 skipped（基线 2497）。
    - 集成测试 `tests/test_sandbox_docker.py -m integration` 13 passed。新增的 `test_a_web_dbt_runs_database_and_profiles_are_out_of_the_sandboxs_reach` 在容器里搜 `*.duckdb*`、`profiles.yml` 和库文件的绝对路径，只找到 stub profile `/tmp/stub-profiles/profiles.yml`（里面不指向任何库）；`../db`、`../profiles` 不存在；`dbt parse` 仍然能跑。
    - 真实 dbt：`test_a_real_dbt_run_materializes_into_the_scratch_schema_only` 在 `dbt_shop` 夹具上 success=3，模型落在临时 schema，`raw` 的行数不变。
  - 决定：
    - schema 名：`da_run_` 加上完整的沙箱运行编号（`<events-dir 名>-<8 位 hex>`），转小写，`[a-z0-9_]` 以外的字符都换成 `_`。保留 hex 后缀，是为了让同名的两次运行不会共用一个 schema。超过 63 个字符（Postgres 的上限）时截掉中间、保留 hex 尾巴。代价是从网页的运行编号推不出 schema 名，但每次 `dbt_execute` 的回复第一行都是 `target_schema: …`，会记进事件文件。
    - profile：`scratch` 时由 MCP server 在每次调用前写到 `<events-dir>/profiles/profiles.yml`。格式是 JSON（JSON 也是合法的 YAML，不用加依赖），权限 0600。Postgres 照 SignalPilot 从连接串变量生成。events 目录在 workspace 里面的运行，开始前就被拒绝。
    - dbt 的环境变量：`scratch` 只给普通变量（PATH、HOME 等），模型里的 `env_var()` 读不到模型 key 和数据库 URL。`task` 另外传 `DBT_*` 和 Postgres 变量，和以前 dbt 看到的一样；项目里有 `profiles.yml` 时加 `--profiles-dir <project>`；在项目目录里执行。
    - launcher：dbt 运行拷项目时，任何深度的 `profiles.yml` 都不拷；库文件按文件身份判断，不管从哪条路径能找到它都不拷，所以通过链接目录也带不进来。库文件和它的 `.wal` 放到 `<运行目录>/db/`。
    - 改了已有测试：`tests/test_web_submit.py` 里布局相关的断言（库文件现在在 `cwd.parent/db/…`，没有 `profiles.yml`）。这是布局改动的必然结果；`tests/acceptance/` 没动。
  - 审查：
    - Codex 第 1 轮：
      - 已修：嵌套的 `profiles.yml` 会被拷进来；库文件能通过链接目录带进来（`launcher.py`）。
      - 已修：dbt 在新会话里启动，运行取消后还在跑。现在它在 server 的进程组里，由 `dbt_execute.py` 自己的 runner 执行；有测试覆盖进程组和 900 s 后杀掉。
      - 不修，记为已知限制：写到临时 schema 以外的地方（snapshot 的 `target_schema`、自定义的 `generate_schema_name`、hooks、`run_query`）。模型能执行任意 SQL，argv 和 profile 拦不住，只有数据库权限能拦（例如只能访问临时 schema 的 Postgres 角色），这超出本工单和 spec 的范围。SignalPilot 也一样。DuckDB 的库是每次运行一份的副本，影响的只是这份副本。工具说明已改成照实说明，不再写"从不碰生产"。
    - eng:code-review：
      - 已修：
        - `run --dbt-target scratch` 不带 `--sandbox` 时原来被静默忽略，现在拒绝，exit 2；
        - 截断后的 schema 名可能出现 `__`；
        - "在 workspace 外"的检查写了两遍，合成一个 `check_outside`；
        - `PROFILES_FILE` 定义了两次；
        - `threads: 4` 是魔法数；
        - 用 `run_id: ""` 表示没有编号；
        - 变量名 `full` 含义不清。
      - 不修：
        - `run_dbt` 和 `sandbox.run_process` 大量重复：合并要给 `run_process` 加参数，而 `sandbox.py` 在本工单的 Authority 里只许改 stub profile 的接线；
        - `sources.py` 的 `check_layout` 还写着库文件必须在项目里：spec 说数据源配置的要求可以不变，**交 100 决定删掉或改写**；
        - `cli.server_dbt` 自己检查参数组合，为的是报错时能点出具体参数名；
        - 工具的五个参数原样往下传：MCP 工具签名要求参数写明。
  - **要人定：99 和 100 之间，网页的 dbt 运行会坏。** launcher 已经不往 workspace 放 `profiles.yml` 和库文件，但在 100 之前不会带 `--sandbox docker --dbt-target scratch`。所以 main 上网页的 dbt 运行仍用 Bash 跑 dbt，又找不到项目的 profile，会退回去读 `~/.dbt`，或者直接失败。CLI、Harbor、DAB 不受影响。
- 2026-11-18（主会话）真实模型运行，在工单分支 b379dc3 上，deepseek-flash：
  - 命令：`uv run data-agent run --type dbt --config no-verifiers --workspace ~/.cache/da-99-run/run/workspace --duckdb ~/.cache/da-99-run/db/shop.duckdb --events-dir ~/.cache/da-99-run/run --sandbox docker --dbt-target scratch --task "Add a model marts/order_totals with one row per customer_id and the sum of amount, then materialize it. Run dbt only with the dbt_execute tool."`（项目是 `dbt_shop` 夹具，库由 `tests/fixtures/shop.sql` 建）。
  - 结果：exit 0，$0.017。
    - `dbt_execute` 调了两次。第一次 `run --select order_totals`：`target_schema: da_run_run_454a621c`，exit_code 1，因为临时 schema 是空的，上游模型还不在里面。第二次 `run --select +order_totals`：exit_code 0。
    - 跑完后 `da_run_run_454a621c` 里有 `order_totals`、`stg_customers`、`stg_orders`。`main` 跑前跑后都是 5 张表，`raw` 都是 4 张，`raw.orders` 都是 37 行。
    - profile 写在 `run/profiles/profiles.yml`，在 workspace 外；workspace 里没有 `profiles.yml`。
    - Bash 调了 3 次（dbt-workflow 的两个脚本和一次 `find`），本分支还保留着 Bash，100 会去掉。
  - **给 100**：在临时 schema 里，选择器只写模型本身会因为上游模型不存在而失败。Skill 和工具说明要写明用 `+<model>` 或 `build`，把上游一起建出来。
- 2026-11-18：PR #108。
- 2026-11-18 PR #108：CI 两个 check 都 pass（b379dc3）。人定（2026-11-18）：审查过了就照常合入，马上派 100；99 和 100 之间网页 dbt 运行会坏，接受。
- 2026-11-18 **退回（第 1 轮；Codex GitHub app P2，审 b379dc3；主会话对照代码核实属实）：生成 Postgres profile 时没有解码库名。** `dbt_execute.py:134` 的 `"dbname": (parts.path or "/").lstrip("/")` 没有 `unquote`，而上两行的 user、password 都做了。连接串里库名带百分号编码时（如 `/warehouse%20dev`，libpq 解释成 `warehouse dev`），scratch 模式的 dbt 会去连字面上的 `warehouse%20dev`，连不上，而只读网关对同一个数据源查询正常。
  - 要做的：去掉开头的 `/` 后做 `unquote`。补一个单元测试，库名带 `%20`，旧代码下失败、修复后通过。顺便看看 host 和查询参数还有没有同类问题。
- 2026-11-18（工单会话，退回第 1 轮，主会话抄录）：提交 `e1ed0f6`、`c3713e1`、`db500d2`，已 push。
  - 修法：`postgres_output` 不再用 `urlparse`，改用 libpq 的 `conninfo_to_dict` 解析连接串。只读网关用的也是这个解析器（`gateway.py:251-253`），所以两边对同一个 URL 不会有分歧。dbname 和 host（包括百分号编码的 socket 目录）都按 libpq 的方式解码。
    - 测试 `test_the_postgres_profile_decodes_the_url_as_libpq_does`：库名用 `warehouse%20dev`，host 用 `%2Fvar%2Frun%2FPg`。旧代码下失败（`'warehouse%20dev' != 'warehouse dev'`），修复后通过。
  - 查询参数：profile 现在保留 `sslrootcert`、`sslcert`、`sslkey`、`connect_timeout`，这几个是 libpq 和 dbt-postgres 都有的键。`options`、`service` 这类只有 libpq 认的键仍然不保留。
  - 多个 host、端口不合法、未知的键：现在报 `DbtExecuteError`，到 agent 那里是 `ToolError`。报错信息不引用 libpq 的原始错误（里面可能带密码），也不挂原始异常；测试 `test_a_postgres_parse_error_does_not_carry_the_password` 覆盖这一点。这是 Codex 在 `c3713e1` 上指出的 P2，已修。
  - 审查：
    - da-review（`e1ed0f6`）：没有意见。
    - eng:code-review 修了：`NOTICE` 里 `_url_parts` 的出处登记；多 host 和解析错误原样抛出；丢掉 TLS 参数。
    - 不修：`str(params.get(...) or "")` 的写法重复出现，抽成公共 helper 就要改本修复范围以外的 `events.py`；host 或 dbname 为空时照旧传 `""`，这是改动前就有的行为。
  - 不稳定的测试：`test_dbt_past_its_limit_is_killed_and_its_output_returned`（`57dbcf5` 加的，不是验收测试）约 15 次里失败 2 次。机器忙时，充当 dbt 的新 Python 进程在 1 s 的时限内来不及输出。时限改成 5 s，"30 s 内被杀"的断言不变。
  - 检查：`make check` exit 0，2565 passed、2 skipped；`tests/test_dbt_execute.py` 连跑 3 次都是 47 passed。改的是纯函数，Docker 和真实 dbt 的路径没动，没有重跑集成测试。
- 2026-11-18 **退回（第 2 轮，也是这个 PR 的最后一轮；Codex GitHub app P2，审 db500d2；主会话对照代码核实属实）：`task` 模式滤掉了题目 profile 要用的环境变量。**
  - 问题：`dbt_execute.py:248-252` 在 `task` 时只在 `local_env` 白名单之外再放行 `DBT_*` 和 Postgres 变量。`evals/jaffle.py:58-62` 生成的题目 profile 用了 `env_var('DA_JAFFLE_PORT')`、`DA_JAFFLE_USER`、`DA_JAFFLE_DBNAME`，这几个都会被滤掉，profile 渲染失败。以前 Bash 里跑的 dbt 能看到它们。100 把 dbt 切到 `dbt_execute` 以后，jaffle 的评测就会坏。
  - 要做的：`task` 模式下，题目 profile 需要的环境变量要能到 dbt。做法由你定，写明理由：
    - 照 spec，`task` 就是"现在的行为"，可以给 dbt 和以前 Bash 里 dbt 一样的环境；
    - 或者从 profile 里解析出 `env_var()` 引用的名字，只放行这些。
    - `scratch` 模式的白名单不变。
  - 测试：用 jaffle 那种 profile（引用 `DA_JAFFLE_*`），`task` 模式下这些变量能到 dbt 的环境；旧代码下失败、修复后通过。
  - 第二轮修完还不过，按 Stop conditions 交回人。
- 2026-11-18（工单会话，退回第 2 轮，主会话抄录）：提交 `d887788`、`e1ee204`、`fc87f14`。
  - 修法：`task` 模式下，dbt 额外拿到 profile 文件里 `env_var('NAME')` 引用的那些变量。profile 文件照 dbt 自己的顺序找：先项目里的 `profiles.yml`，再 `DBT_PROFILES_DIR`（相对路径从项目目录算起），最后 `~/.dbt`。`DBT_*` 和 Postgres 变量照旧放行，`scratch` 模式的白名单不变。
    - 不照搬 Bash 时的完整环境，是因为 MCP server 的环境里有模型 key（`tool_env`），任何模型里的 `env_var()` 都能读到它。
    - 以 `DATA_AGENT_`、`ANTHROPIC_`、`CLAUDE_` 开头的变量一律不放。项目的 `profiles.yml` 在 agent 能编辑的 workspace 里，不排除的话 agent 可以在 profile 里点名模型 key 把它拿到。
  - 测试：
    - `test_task_passes_the_variables_the_tasks_profile_reads`：jaffle 那样的 profile，用单引号、双引号、带默认值、`env_var (` 几种写法引用变量，profile 分别放在项目、`DBT_PROFILES_DIR`、相对路径的 `DBT_PROFILES_DIR`、`~/.dbt` 四处。旧代码下三个 `DA_JAFFLE_*` 的断言都失败，修复后通过。
    - `test_a_task_profile_cannot_name_the_runs_model_credentials`：profile 点名模型凭据也拿不到。
  - 真实 dbt：`task` 模式，profile 用 `env_var('DA_R2_DB_PATH')` 取 DuckDB 路径，exit 0，3/3 个模型 OK。
  - 审查：
    - da-review（`d887788`）两条 P2，已在 `e1ee204` 修，各配一个旧代码下失败的测试：
      - `dbt_project.yml` 里用了 YAML 锚点时，读项目配置读不出东西，profile 名退成 `default`，dbt 找不到。现在 scratch profile 一律叫 `da_run`，命令行用 `--profile da_run` 指定；七个允许的子命令都认 `--profile`。
      - 相对路径的 `--dbt-profiles-dir` 在两处按不同的目录解析。现在传给 dbt 的是绝对路径。
    - eng:code-review（`e1ee204`）已在 `fc87f14` 修：
      - profile 能点名模型 key；
      - 相对路径的 `DBT_PROFILES_DIR` 从错的目录解析；
      - 正则漏了 `env_var (`；
      - 项目 profile 的检查写了两遍；
      - docstring 说的比代码做的多。
  - 已知限制：
    - 模型或 `dbt_project.yml` 里用 `env_var()` 读的变量，只有 `DBT_*` 开头或在 profile 里点过名的才能到 dbt；那个变量带默认值时，dbt 会悄悄用默认值。
    - libpq 自己读的变量（`PGPASSWORD`、`PGSSLMODE`）、adapter 直接读的凭据（AWS、MotherDuck）到不了 dbt。
    - 动态拼出来的变量名（`env_var(prefix ~ 'X')`）认不出来。
    - agent 在 profile 里点名的其他宿主机变量仍然能拿到，只排除了模型凭据。
    - dbt 会不会展开 `DBT_PROFILES_DIR` 里的 `~` 未确认。
    - 这些都不影响 jaffle、Harbor 和现有测试；**100 要是遇到真实题目需要其中某个变量，再加进白名单**。
  - 检查：`make check` exit 0，2572 passed、2 skipped；`tests/test_dbt_execute.py` 54 passed，真实 dbt 的 scratch 测试跑了（没有跳过）。
- 2026-11-18（主会话）在 fc87f14 上重跑真实模型运行（profile 改名为 `da_run`，scratch 路径变了），命令同上一次，workspace 在 `~/.cache/da-99-run2`：exit 0，$0.017。`dbt_execute` `run --select order_totals` 返回 exit_code 1（上游模型还不在临时 schema），`+order_totals` 返回 exit_code 0。跑完 `da_run_run_f07bebe1` 3 张表，`main` 5 张、`raw` 4 张，`raw.orders` 37 行，都和跑前一样。
- 2026-11-18 PR #108：CI 两个 check 都 pass（fc87f14）；Codex GitHub app（审 fc87f14）："Didn't find any major issues"。squash 合入 main，28db976。
