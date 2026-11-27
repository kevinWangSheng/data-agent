# 140 — 产品路径的三处安全默认：CLI dbt 写临时 schema、服务器级联网开关、隐藏别的运行的临时 schema

Status: in-review
Blocked by: —
Spec: 无（执行已有规矩的修正，不是新设计；人 2026-11-27 定先单独派，不等 product-config spec）
Claimed by: claude (opus-5.5) 2026-11-27
Branch: worktree-140-product-safety-defaults

## Goal

产品入口默认不写生产库、操作者能在服务器上统一关掉联网（IM 跟随）、agent 看不到别的运行留下的 `da_run_*` 临时 schema。评测入口的行为不变。

## Authority

- May edit：
  - `src/data_agent/cli.py`：`run` 的 `--dbt-target` 默认值；`web` 子命令加服务器级联网开关；
  - `evals/harbor_agent.py`：dbt 任务显式传 `--dbt-target task`（只这一处）；
  - `evals/jaffle.py`：`run` 命令显式传 `--dbt-target task`（只这一处，`:353-361`；2026-11-27 补）；
  - `src/data_agent/web/app.py`、`web/launcher.py`、`web/conversations.py`、`src/data_agent/im/adapter.py`：联网默认值从服务器设置来；
  - `src/data_agent/mcp_server.py`、`src/data_agent/db_facts.py`、`src/data_agent/context_tools.py`：目录查询排除别的运行的 `da_run_*`；
  - `tests/`（除 `tests/acceptance/`）；`docs/setup.md`、`README.md`：写明新的默认值和开关。
- Must not edit：`tests/acceptance/`、`skills/`、`src/data_agent/agent.py`、`src/data_agent/configs/`、`evals/` 里除 `harbor_agent.py`、`jaffle.py` 那两处以外的文件、`web/src/`（前端不动）、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access：none。
- Spend cap：工单 agent $0（会话里不能 source 模型的 env 文件）；需要真实运行的部分交给主会话。

## Context

审计取证：`docs/research/2026-11-27-product-config-audit.md` 第 1 节（1.1–1.3）。

1. **CLI 的 dbt 默认写生产库。** `src/data_agent/cli.py:344` `--dbt-target` 默认 `task`（项目自己的 profile，写进它指向的库）；帮助文字 `:66-70`。只有网页传 `scratch`（`web/launcher.py:178`）。当初理由只是"Harbor 不用另传"（`.scratch/agent-tools/spec.md:77`）。违反 AGENTS.md"对数据源的写只走 `dbt_execute`（产品里写到每次运行的临时 schema）"、ADR-0010 第 3 点。
   - 改法：CLI 默认 `scratch`；`evals/harbor_agent.py:388-399` 显式加 `--dbt-target task`（并改 `:395` 的注释）。`tests/test_harbor_agent.py:353-359` 现在断言不传这个参数，要改成断言传 `task`：评测行为不变。
   - 再查一遍还有没有别的入口靠这个默认值写题目库（`grep -rn "run --type dbt\|--type\", \"dbt" evals scripts src`），有就同样显式传。
2. **联网没有服务器级开关，IM 一律联网。** `web/app.py:52,61` 请求体默认 `network=True`；`im/adapter.py:302` 调 `conversations.send` 不传，落到 `conversations.py:97` 的默认 `True`。ADR-0010:32 写产品默认联网、"设置里可以关"。
   - 改法：`data-agent web` 加 `--network/--no-network`（默认开，照 ADR-0010）。关时：网页、对话、IM 的每次运行都不联网；请求体要求 `network=true` 时拒绝（409，说明服务器关了联网），不悄悄改掉。开时行为照旧。IM 的回合跟随服务器设置。
3. **别的运行的临时 schema 对 agent 可见（推断，先核实）。** `dbt_execute.py:51` `SCHEMA_PREFIX = "da_run_"`，临时 schema 有意不删（`.scratch/agent-tools/spec.md:153`）；目录查询只排除 `information_schema`、`pg_catalog`（`mcp_server.py:82`、`db_facts.py:41`），值字典的取样也走目录（`context_tools.py`）。
   - 先写一个测试核实：库里有 `da_run_other` 时，本次运行的 `list_tables`、目录、值字典是否列出它。核实为真再修。
   - 改法：排除 `da_run_` 开头、但不是本次运行自己那个（`scratch_schema(run_id)`）的 schema；本次运行自己的临时 schema 必须仍然可见（agent 要查自己刚建的模型）。DuckDB、Postgres 两种都要覆盖。

## Acceptance

- [ ] `data-agent run --type dbt` 不带 `--dbt-target` 时，dbt_execute 用 `scratch`（测试：解析出的参数与 dbt_execute 拿到的 target）。
- [ ] Harbor 的 dbt 命令显式带 `--dbt-target task`；评测行为不变（改后的 `tests/test_harbor_agent.py`）。
- [ ] `data-agent web --no-network`：网页提交、对话回合、IM 回合都不联网；请求要求联网时得到 409；不带开关时行为与现在相同。
- [ ] 第 3 项：先有一个修复前会失败的测试证明问题存在（或证明不存在，此时在 Comments 写明，不改代码）；修复后别的运行的 `da_run_*` 不出现在 `list_tables`、目录、值字典里，本次运行自己的临时 schema 仍在。
- [ ] `docs/setup.md`（和 README 若提到）写明新默认值与开关。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出；三项各自的测试名与结果；第 3 项"修复前失败"的测试输出。真实运行（一次 `data-agent run --type dbt` 写到临时 schema、一次 `data-agent web --no-network` 的提交被拒）交给主会话：在最终回复里写出命令。

## Stop conditions

- 隐藏临时 schema 会让验证器、`dbt_execute` 或 agent 自己的模型查不到。
- 要改 `src/data_agent/agent.py`、`web/src/` 或 `tests/acceptance/` 才能做到。
- 发现别的评测入口靠 `task` 默认值，而改它要动 Authority 以外的文件。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 1（da-review 修了 1 轮，第 2 轮没有问题；code-review 修了 1 轮）
- Model spend: $0
- Outcome: 三项都实现了；Postgres 的集成测试和两次真实运行留给主会话
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：审计（`docs/research/2026-11-27-product-config-audit.md`）查出的三处评测行为漏进产品。人定先单独派，不等 product-config spec。
- 2026-11-27（工单 agent，opus-5.5）：碰到停止条件"发现别的评测入口靠 `task` 默认值，而改它要动 Authority 以外的文件"，没写代码就停下了。
  - **已核实（读代码）：**
    - `evals/jaffle.py:353-361` 拼的 `data-agent run` 命令不带 `--dbt-target`，也不带 `--sandbox`（所以用默认的 `docker`，dbt_execute 会挂上）。build 题走 `--type dbt`（`:45` `KINDS = {"query": "query", "build": "dbt"}`）。
    - 每道题的 schema 是 `jaffle_<case id>`（`:343`），由题目工作区里的 `profiles.yml` 指定（`:344-345`）。判分在这个 schema 里查模型（`:376` `grade_model(gateway, schema, ...)`）。
    - `scratch` 写到 `scratch_schema(run_id)` = `da_run_<run id>`（`src/data_agent/dbt_execute.py:73-78`、`DbtRun.of_run` `:217-222`）。
  - **推断（没真实运行过）：** 默认值改成 `scratch` 后，jaffle 的 build 题会把模型建到 `da_run_*`，判分去 `jaffle_<id>` 找就找不到，所以会全挂。`check_outside` 不会报错：profiles 在 `case_dir/events/profiles`，工作区在 `case_dir/jaffle_shop`。
  - **其他扫描：**
    - `evals/`、`scripts/` 里只有 `harbor_agent.py` 和 `jaffle.py` 这两个 dbt 入口。
    - `evals/` 里没有直接调用 `agent.run(...)` 的（引用 `data_agent.agent` 的都只拿 `git_commit`、`ConfigError` 之类）。另外，`agent.py:2134` 自己的默认值也是 `"task"`，改 CLI 默认值碰不到它。
    - `tests/acceptance/` 和 `tests/test_eval_jaffle.py` 都没有钉住 `task` 默认值。
  - **基线 `make check`（未改动）：** exit 0。pytest 3461 passed、3 skipped、79 deselected；web vitest 30 个文件、448 个测试全过。
  - 第 2 项（服务器级联网开关）、第 3 项（隐藏别的运行的 `da_run_*`）和这个阻塞无关，主会话可以决定拆票还是一起放行。
  - **建议修改 Authority：** 加一条"`evals/jaffle.py`：在 run 命令里显式传 `--dbt-target task`（只这一处）"。只改这一行就够，这次扫描没发现别的入口。
- 2026-11-27（主会话）：建票时漏查 `evals/jaffle.py`，是建票的遗漏。Authority 补上 `evals/jaffle.py` 那一处（显式传 `--dbt-target task`），Status 回 `ready-for-agent`，在原分支 `worktree-140-product-safety-defaults` 上接着做全部三项。
- 2026-11-27（工单 agent，opus-5.5，第 2 次）：三项都做完了，分支 `worktree-140-product-safety-defaults`，3 个提交：`e2744e8`、`d8dc248`（da-review 第 1 轮的修复）、`cf57d35`（code-review 第 1 轮的修复）。
  - **第 1 项，CLI 的 dbt 默认改成 `scratch`**
    - `src/data_agent/cli.py`：`run --dbt-target` 默认 `scratch`，帮助文字改了。
    - `evals/harbor_agent.py` 和 `evals/jaffle.py` 各显式传 `--dbt-target task`，评测行为不变。
    - 再扫了一遍 `tests/`（含 `tests/acceptance/`）、`evals/`、`scripts/`：没有别的入口靠 `task` 默认值。
    - `tests/acceptance/test_chat_eval_unchanged.py::test_harbor_dbt_run_gets_the_same_options` 不带 `--dbt-target` 调 `run --type dbt`，现在走 `scratch`，测试照样通过。
    - 测试：
      - `test_dbt_execute_wiring.py::test_run_passes_the_dbt_target_and_defaults_to_scratch`：CLI 交给 `run` 的 `dbt_target`。
      - 同一文件的 `test_a_dbt_run_with_a_sandbox_passes_scratch_and_its_profiles_dir`：`scratch` 到 mcp-server 参数这一段。
      - `test_harbor_agent.py::test_a_trial_runs_its_commands_in_the_task_container`：改成断言传 `task`。
      - `test_eval_jaffle.py::test_the_run_command_has_dbt_write_the_cases_own_schema[build|query]`：新加。
  - **第 2 项，服务器级联网开关**
    - `data-agent web --network/--no-network`，默认开。
    - 请求体的 `network` 改成 `bool | None`：不传就跟随服务器；服务器关了联网时传 `true` 返回 409（提示里写明服务器用了 `--no-network`）；传 `false` 一律不联网。
    - 判断只有一处：`Launcher.network_for`。
    - IM 的 `ConversationsLike.send` 去掉了 network 参数，adapter 什么都不传，所以 IM 回合跟随服务器。
    - 决定：服务器关联网时，带 `network=true` 的 steer 也返回 409（测试里有）。
    - 测试：`tests/test_web_network_switch.py`，13 个：网页提交、对话回合、IM 回合三种情况，开关开和关两种；外加 CLI 的 `--no-network`（409）、不带开关（400，没有数据源）、`--network`（400）。
  - **第 3 项，隐藏别的运行的 `da_run_*`**
    - 先核实问题存在：修复前跑 `uv run pytest tests/test_run_scratch_schemas.py -m "not integration"`，8 个失败。其中 4 个是行为失败（另外 4 个是修复前还没有 `own_schema` 参数报的 TypeError）：
      - `test_list_tables_shows_no_other_runs_schema` 和 `test_the_server_of_a_run_keeps_the_schema_its_run_id_names`（走 stdio）：`AssertionError: assert 'da_run_other_5678' not in '...'`，列表里出现了 `da_run_other_5678.stage_da_run_other_5678 (base table, ~4 rows, 1 cols)`。
      - `test_the_catalog_shows_no_other_runs_schema`、`test_the_dictionary_samples_no_other_runs_schema`：discover 和值字典里有 `da_run_*` 的 ref。
    - 修法：
      - `db_facts.HiddenSchemas` 生成一个 SQL 条件，用 `left(lower(schema), n) <> 'da_run_'`，不用 LIKE（LIKE 里 `_` 是通配符，会把 `da_runxy` 也排掉）。
      - 这个条件加在 `LIST_TABLES_SQL` 和 `load_catalog` 的 SQL 里，所以临时 schema 不占 MAX_ROWS 的行数。
      - 本次运行的 schema 由 `mcp-server --run-id` 经 `scratch_schema()` 得出，它本身和它的 `<schema>_<custom>`（dbt 的自定义 schema）都保留。
      - `agent.py` 里的 `DictionaryBuild(gateway)` 不带 run id，所以排掉全部 `da_run_*`。它在运行写任何东西之前就建好了，不影响本次运行。
      - `verifier_v2` 自己调的 `load_catalog` 不过滤。
      - 没有触发停止条件：本次运行自己的模型（含自定义 schema）仍能在 `list_tables`、`describe_table`、值字典里找到。
    - 修复后：DuckDB 的 10 个测试全过，包括 stdio 端到端的 `test_the_server_of_a_run_keeps_the_schema_its_run_id_names`、`test_the_runs_own_custom_schemas_stay_and_other_runs_go`、`test_hidden_schemas_take_only_plain_names`。
    - **Postgres 没跑过。** 本会话没有 `DATA_AGENT_TEST_POSTGRES_URL`；想起一个临时 Postgres 容器，Docker 报 `no space left on device`。合并前主会话必须跑，并且要通过：`DATA_AGENT_TEST_POSTGRES_URL=... uv run pytest tests/test_run_scratch_schemas.py tests/test_db_facts.py -m integration -q`。
  - **文档**：`docs/setup.md`、`README.md` 写了 dbt 的新默认、`--no-network`、临时 schema 的隐藏。
  - **`make check`（最终 HEAD `cf57d35`）**：exit 0。ruff 通过；basedpyright 0 errors；pytest 3488 passed、3 skipped、81 deselected；web vitest 30 个文件、448 个测试全过。
  - **审查**
    - `da-review main` 第 1 轮：P2，本次运行的 dbt 自定义 schema（`<own>_<custom>`）被隐藏了。属实，已修（`d8dc248`），配了回归测试。
    - `da-review main` 第 2 轮（最终 HEAD）：没有问题。
    - `eng:code-review main`，Standards：没有违反成文规范。判断项里只修了一条：空前缀会隐藏所有 schema，现在会报错（`cf57d35`）。不修的：
      - `DictionaryBuild` 拿 `own_schema`、自己建 `HiddenSchemas`：`agent.py` 不传参数就是对的默认值，`HiddenSchemas` 是内部细节。
      - `other_runs`、`own_schema` 两个只有一行的包装：它们给 `da_run_` 规则和 `--run-id` 规则各起一个名字，并写了理由。
      - `HiddenSchemas.prefix` 做成参数：`db_facts` 不能导入 `dbt_execute`（`verifier_v2` 导入 `db_facts`，会循环导入）。
      - `own_schema`、`network` 层层传参：跟现有的 `mcp_servers` 传法一致。
    - `eng:code-review main`，Spec：没有阻塞问题，所有改动都在 Authority 内。不修的：
      - `test_a_dbt_run_with_a_sandbox_writes_to_the_task_by_default` 的名字现在有误导：它测的是 `agent.run` 自己的默认值，`agent.py` 不在 Authority 里。
      - 第 1 项的验收由两个串起来的测试覆盖，没有一个从头到尾的测试。
      - 服务器关联网时，对不存在的对话发 `network=true` 返回 409 而不是 404：小问题。
      - 行数估计和键的查询不过滤：它们只在已经过滤的表上用，输出里看不到别的运行的表。
  - **已知限制**
    - 前端（`web/src`，不在 Authority）的联网勾选框默认勾上，每次请求都带 `network:true`。所以 `--no-network` 时，网页的默认提交都会得到 409，要先取消勾选，`docs/setup.md` 和 README 写了。要改得另开前端工单，例如让 `/api/options` 告诉前端服务器的设置。
    - 隐藏只在目录这一层：agent 用 `query_database` 点名查，仍能读到别的运行的 schema。工单要求的就是目录这一层。
  - **交给主会话的真实运行**
    1. `uv run data-agent run --type dbt --workspace <dbt 项目副本> --duckdb <工作区外的 db> --config full --events-dir <工作区外的目录> --task "..."`：不带 `--dbt-target`。看事件里 dbt_execute 写的是 `da_run_<run id>`。
    2. `uv run data-agent web --runs-dir <目录> --data-sources sources.toml --no-network`，然后 `curl -s -X POST localhost:8765/api/runs -H 'content-type: application/json' -d '{"task":"t","task_type":"query","data_source":"<名字>","config":"full","network":true}'`：应返回 409。不带 `network` 再提交一次：应返回 201，运行参数里没有 `--network`。
  - 本地 `main` 已经走到 `e494e49`；三点 diff 对比的是合并基点，只含本工单的 3 个提交。
- 2026-11-27（主会话）：抄入第 2 次的回复；push 分支、开 PR。合并前还要：Postgres 集成测试（本机 Docker 磁盘满，起不了临时 Postgres）、上面两次真实运行、CI 和 Codex GitHub app 的意见。
- 2026-11-27（主会话）：Postgres 集成测试在服务器上跑（本机 Docker 磁盘满）。`da-remote setup cf57d35`；服务器上另起临时容器 `postgres:17`（只监听 127.0.0.1:55440、随机密码不打印、跑完删除，不碰 DAB/LiveSQLBench 的库）；`uv run pytest tests/test_run_scratch_schemas.py tests/test_db_facts.py -m integration -q` → `14 passed, 37 deselected in 10.09s`。之后确认无残留容器，`da-remote setup` 回到 main（`ecc3fe4`）。合并前还剩：工单里的两次真实运行、CI 和 Codex GitHub app 意见。
