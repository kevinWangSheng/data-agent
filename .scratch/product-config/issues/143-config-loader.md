# 143 — 配置加载：分层合并、`${VAR}`、未知键、明文密钥、`config show`、`--config` 路径、三个旧参数并入

Status: done
Blocked by: 141
Spec: ../spec.md（"配置文件与分层（Q1）""分节（Q2）"）
Claimed by: claude (opus-5-5) 2026-11-27
Branch: worktree-143-config-loader

## Goal

`config.toml` 有类型地加载：代码默认值 < 用户级 `~/.data-agent/config.toml` < 项目级 `<工作区>/.data-agent/config.toml`（或显式 `--config <路径>` / `DATA_AGENT_CONFIG`，只读这一个）< 环境变量 < 命令行参数，`[policy]` 最上；未知键、明文密钥、缺变量报错并点名文件和键。`data-agent config show` 打印每个键的值和来源。`data-agent run --config` 改收配置文件路径，可写多次（后面覆盖前面），显式 `--config` 优先于 `DATA_AGENT_CONFIG`；过渡期 `run` 和 `mcp-server` 的 `--config` 同时接受路径和旧消融名，146 改完评测入口后删掉旧名。`--data-sources`、`--mcp-servers`、`--im` 去掉，内容从 `[sources.*]`、`[mcp]`、`[im.*]` 读。141 合入的 `TICKET_143` 验收测试（新文件和 141 改写过的已有文件）全部转绿（只删标记）。

## Authority

- May edit：
  - `src/data_agent/config.py`（141 的空壳）：加载、合并、校验、来源、TOML 全文与 sha256。spec Q2 全部分节的类型定义都放这里（键名、类型、默认值），各节的**行为**由 144–148 接。
  - `src/data_agent/cli.py`：`run` 的 `--config`（`cli.py:290`）改成可省、可重复的文件路径，过渡期也接受旧消融名；`mcp-server --config`（`:244`）同样接受路径或旧名；新增 `config show` 子命令；去掉 `run --data-sources`（`cli.py:93-98`，`file_task` `:212-236`）、`run --mcp-servers`（`:345-350`）、`web --data-sources`、`web --mcp-servers`、`web --im`（`:363-379`），`serve_web`（`:534-608`）改从生效配置拿数据源、外部 MCP、IM。
  - `src/data_agent/run_config.py`：`load_config`（`run_config.py:41-56`）改成从生效配置得出 `RunConfig`（或换掉 `RunConfig`，保持 `agent.py`、`mcp_server.py`、`verifier_v2/hooks.py` 的读法能用）。
  - `src/data_agent/web/sources.py`、`src/data_agent/external_mcp.py`、`src/data_agent/im/config.py`：解析函数改成吃配置里的那一节（行为和校验不变）。141 的 `test_im_config.py` 钉着 `im.config.load_config(path, environ)`、`product_config_support.run_config_of` 钉着 `run_config.load_config(<EffectiveConfig>)`：这两个签名保留（吃配置那一节的解析可以另加函数，旧签名转调它）。
  - `src/data_agent/web/app.py`、`web/launcher.py`、`web/conversations.py`：只改"子进程拿到哪份配置"（141 契约第 4 项）和去掉 `--data-sources`、`--mcp-servers` 的拼法（`launcher.py:162-183`、`conversations.py:138-143`）。
  - `tests/acceptance/` 里 141 标了 `TICKET_143` 的文件（`test_config_load.py`、`test_config_cli.py`、`test_config_wiring.py::test_a_run_without_any_config_has_every_layer_on`，以及 141 改写过的已有文件，清单见 141 的 Comments "逐文件改动"表）：**只删** `TICKET_143` 标记，以及因此没用的定义和 import。
  - `tests/`（除 `tests/acceptance/`）：新增或修改单元测试。
  - `docs/setup.md`：配置文件位置、分层、`config show`、三个旧参数改成哪一节。
- Must not edit：
  - `tests/acceptance/` 里除 `TICKET_143` 标记以外的一切；`tests/fixtures/product_config/`。
  - `src/data_agent/agent.py` 的提示词、模型设置（`ModelSettings`，145）、`build_options` 的 Skill/MCP/插件部分（147）；`gateway.py`、`sandbox.py`、`dbt_execute.py`、`context_tools.py`（144）；`classify.py`、`verifiers.py`（145）；`evals/`（146）；`skills/`；`web/src/`；`src/data_agent/configs/`（146）。
  - 其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、`Makefile`、`pyproject.toml`（加依赖要先停下问）。
- Server access：none。
- Spend cap：工单 agent $0（不能 source 模型的 env 文件）。

## Context

行号以 main `e494e49` 为准；140 合入后 `cli.py`、`web/`、`im/adapter.py` 的行号会移动。

- **spec**："配置文件与分层（Q1）"逐条是本工单的行为；"分节（Q2）"和 `decisions.md` Q2 是类型清单；ADR-0011 决定 1。
- **141 的契约**：以 141 最终回复和测试 docstring 为准（加载函数签名、`EffectiveConfig` 的方法、来源的写法、`config show` 的格式、子进程拿配置的方式）。
- **现有三份文件的解析**（并入后行为不变，只换来源）：
  - 数据源：`web/sources.py:50-66` `load_data_sources`，键见 `:33` `KEYS`；`dump_data_sources`（`:69`）现在是网页给每个对话一轮写 `--data-sources` 文件用的。
  - 外部 MCP：`external_mcp.load`（`cli.py:520-531` 调用），116 的放行清单与 `DATA_AGENT_MCP_` 前缀规则不变（`.scratch/mcp/issues/116-external-mcp-backend.md`）。
  - IM：`im/config.py:64-124` `load_config`，token 写的是变量名（`bot_token_env = "DATA_AGENT_SLACK_BOT_TOKEN"`，`:40-44`），前缀规则 `:104-109`。
- **已定的细节**（`decisions.md` Q8，和 spec 不一致时以它为准）：
  - `[layers]` 的键是 `skill`、`verifiers`、`context`（和 `RunConfig` 一致，`run_config.py:28-30`）；`[skills].tool`（默认 `true`）是 Q2 清单的一个键，类型在这里定，行为由 146、147 接。
  - 写变量名的键保留：IM 的 `bot_token_env`（`im/config.py:40-44`）、`[sources.*].env = "WAREHOUSE_URL"`（`web/sources.py:7-15` 的示例）。"只写 `${VAR}`"只管值本身是密钥的键。
  - 产品运行的 `version.config` 写配置文件名，没有文件时写 `default`（`version` 事件 `agent.py:2245-2259`，`config=` 在 `:2250`）；评测写层名由 146 做。
  - `[policy]` 与命令行参数冲突报错拒绝的行为在 144；本工单只保证加载结果里 `[policy]` 的键覆盖不了、来源记 policy。
- **明文密钥的判定**：spec 写"复用 IM 配置现有的判定与 pre-commit 的 gitleaks 规则之外，至少：`api_key`、`token`、`password`、`secret` 结尾的键不是 `${...}` 就拒绝"。gitleaks 规则在 `.gitleaks.toml`（`useDefault = true` 加 `deepseek-api-key` 一条）。
- **环境变量**：只放密钥、`DATA_AGENT_CONFIG`、部署模式；`DATA_AGENT_MODEL`、`DATA_AGENT_BASE_URL` 作为主模型的覆盖保留（现在由 `ModelSettings.from_env` 读，`agent.py:1237-1246`；145 把它接到 `[roles].main`，本工单只在加载结果里记来源）。
- **类型**：pydantic（仓库已用，`web/app.py:23`）或 dataclass 加校验，未知键报错。
- **用到 `--config <消融名>` 的地方**（本工单之后它们都要换）：`mcp-server --config`（`cli.py:244`，由 `build_options` 拼进 MCP server 参数，`agent.py:1621`）；评测入口（`harbor_agent.py:393`、`:421`，`jaffle.py:360`，`dab.py:814`，`ablate.py:511`，`board.py:178`；146 改）；网页（`launcher.py:166-167`、`conversations.py:138`）。过渡办法（Q8）：本工单让 `--config` 同时接受路径和旧消融名（名字照旧从 `src/data_agent/configs/` 读），评测入口不用改；146 改完评测入口后删掉旧名。
- **并行工单**：144、145、147、148 都改 `cli.py`、`agent.py`，后合入的一方 rebase。
- **`policy.disabled_models` 不在加载时拒绝**（141 契约复核第 6 条）：`test_config_wiring.py` 里这条 policy 测试标 `TICKET_144`；本工单的加载器在读文件时就拒绝会让它提前 XPASS。加载器只记下锁定和禁用集合，拒绝发生在 144 接线的地方。
- **`--dbt-target`、`--network` 不接进 `cli_overrides`**（141 合并前审查第 10 条）：这两个的接线归 144。本工单接了会让 `test_config_wiring.py` 里标 `TICKET_144` 的 policy 测试提前 XPASS，而本工单删不掉 144 的标记。

## Acceptance

- [ ] `TICKET_143` 标记全部删掉；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_config_load.py tests/acceptance/test_config_cli.py -v` 全部 PASSED，0 个 xfail；141 改写过、标 `TICKET_143` 的已有验收测试也全部 PASSED。
- [ ] `data-agent run --config full`（旧名）和 `--config <文件>` 都能跑；`mcp-server --config` 同样。
- [ ] 141 的守门组（`test_config_snapshot.py` 的守门、`test_config_eval_isolation.py`）和其他已合入的验收测试照旧通过。
- [ ] spec Q1 每条都有单元测试或验收测试；验收测试没钉的（例如 `${VAR}` 展开后的值不进 `repr` 和报错文字）有新单元测试。
- [ ] `docs/setup.md` 写了配置文件的位置、分层、`config show`、三个旧参数改成哪一节，示例只带 `${VAR}`。
- [ ] `make check` 通过。
- [ ] `create_app(config=None)` 等于代码默认、不读任何配置文件（包括用户级 `~/.data-agent/config.toml`）；单元测试用假 HOME 钉住（141 第 2 轮复核 N1：`test_mcp_web.py` 的 `make_web`、`plain` 没有假 HOME）。

## Evidence required

`make check` 输出；上面两个验收文件的 `-v` 输出；`git diff main -- tests/acceptance | grep '^[-+]' | grep -v '^[-+][-+]'`（只删标记）。不调模型的真实运行（$0）：在临时目录写一份用户级和一份项目级配置，跑 `uv run data-agent config show --workspace <目录>`，贴输出（每个键的来源）；一份带未知键的配置跑 `config show`，贴退出码和 stderr。

交给主会话：写出一条主会话跑的冒烟命令（用 `--config <示例配置>` 跑一次查数，例如 `data-agent run --type query --workspace <空目录> --config <示例> --events-dir runs/143-smoke --task "How many orders are there?"`，示例配置里写 `[sources.shop]` 指向 `tests/fixtures/shop.sql` 建出的 DuckDB），主会话 source 模型环境后跑，确认答案和事件文件。

## Stop conditions

- 旧消融名和路径同时接受时有歧义（例如一个文件恰好叫 `full`），又定不下来。
- 要改 Authority 以外的文件（例如 `agent.py` 的 Skill 部分、`evals/`）才能做到。
- 141 的契约和 spec、`decisions.md` Q8 对不上。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2 (return round 1)
- Review rounds: 4 in total (2 before the return, 2 this round)
- Model spend: $0
- Outcome: return round 1 fixed, `in-review`

## Comments

- 2026-11-27（主会话建票）：配置层的第一张实现工单，144–148 都建在它上面。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：`--config` 可重复、过渡期接受旧名（`run`、`mcp-server`）、层键 `skill`、保留变量名写法、产品 `version.config`；去掉"已有验收测试""评测入口还传消融名"两条停止条件（141 改写已有验收测试，过渡期接受旧名）。
- 2026-11-27（主会话）：按 141 合并前审查补 Context：`--dbt-target`、`--network` 不接进 `cli_overrides`（归 144）。
- 2026-11-27（主会话）：按 141 退回第 1 轮后的契约复核补 Context：`policy.disabled_models` 不在加载时拒绝（否则 144 的标记提前 XPASS）。
- 2026-11-27（主会话）：按 141 第 2 轮契约复核补验收标准：`create_app(config=None)` 不读用户级文件。
- 2026-11-27（工单 agent，opus-5.5；分支 `worktree-143-config-loader`，`c2308a0`…`322d38d`）：
  **What was built**
  - **Loader** (`src/data_agent/config.py`):
    - Merges code defaults, then the user-level file, then the project-level file (or the explicit files, or the one file `DATA_AGENT_CONFIG` names), then environment variables, then command-line values. Merging is key by key; lists are replaced, not joined. `[policy]` sits above all of them and records its source as `policy`.
    - It refuses, naming `<file>: <key>: <reason>` and never the value: unknown keys, wrong types, plaintext secrets, unset `${VAR}`, empty `[im.*]` / `[mcp.servers.*]` / `[sources.*]` tables, and recognisable keys or literal `Authorization` headers.
    - Text comes out canonical, with `${VAR}` references kept as written, plus its sha256. Relative paths resolve against the file that sets them. Every Q2 section has its keys, types and defaults defined here.
  - **CLI**:
    - New `config show` command.
    - `run --config` / `mcp-server --config` are optional and repeatable. During the transition they still accept an old ablation name, given alone; a file with the same name is written `./full`.
    - `--data-sources`, `--mcp-servers` and `--im` are removed; their contents come from `[sources.*]`, `[mcp]` (`file` or inline `servers`) and `[im.*]`.
  - **Web**:
    - `create_app(config=None)` means code defaults and reads no file (unit test uses a fake HOME).
    - Each web run gets `--config <run_dir>/config/<name>.toml`: the server's config with its own data sources, no `[im.*]`, references unexpanded.
    - Until 144, a form run adds a second `[layers]`-only file for the ablation it picked. Picking `full-old-skill` returns 400.

  **Evidence (from this session)**
  - `make check`: exit 0; 3573 passed, 3 skipped, 62 xfailed; basedpyright 0 errors; web 448 passed (`runs/143/check-3.txt`).
  - `pytest tests/acceptance/test_config_load.py tests/acceptance/test_config_cli.py -v`: 38 PASSED, 0 xfail.
  - Full non-integration acceptance suite: 417 passed, 62 xfailed (144/145/146/147/148), 0 XPASS, 0 failed.
  - `git diff main...HEAD -- tests/acceptance tests/fixtures` is 131 deleted lines and 2 added lines. Both added lines replace an inline mark: `pytest.param("turn", marks=TICKET_143)` became `"turn"`, and `else TICKET_143` became `else ()`. `tests/fixtures` is unchanged.
  - $0 real run, `HOME=<tmp>/home uv run data-agent config show --workspace <tmp>/ws` (exit 0). The keys not from defaults:
    ```
    models.my-model.api_key = "${MY_MODEL_API_KEY}"  # <tmp>/home/.data-agent/config.toml
    roles.main = "my-model"  # <tmp>/home/.data-agent/config.toml
    limits.max_budget_usd = 2.0  # <tmp>/home/.data-agent/config.toml
    limits.effort = "high"  # <tmp>/ws/.data-agent/config.toml
    sandbox.network = false  # policy        (the project file set it true)
    sources.shop.path = "/private/tmp/.../ws/.data-agent/shop.duckdb"  # <tmp>/ws/...
    policy.network = false  # policy
    ```
    With an unknown key: exit 2, stderr `data-agent config show: error: <tmp>/bad.toml: limits.max_budget: unknown key`.

  **Reviews**
  - **`da-review` (Codex):** 4 findings, all fixed in `4b7531e`, each with a test that failed first:
    - P1: only DeepSeek-shaped keys were caught. It now also catches GitHub, Slack, Anthropic, OpenAI and AWS keys, private keys, `*api-key` headers, and `Authorization`/`Cookie` values without a reference.
    - P2: IM `allowed_users` reached the bot unexpanded.
    - P2: an unset `${VAR}` inside `allowed_users` crashed with a traceback instead of a config error.
    - P2: empty entries were silently dropped.
  - **`eng:code-review`:**
    - Spec axis found nothing missing. It judged the `agent.py` change below to be within the ticket's intent (the Context names `agent.py:1621` as a place that has to change).
    - Standards axis, fixed in `2c44022`: removed the now-dead `load_data_sources`/`dump_data_sources` and the duplicate `toml_string`; dropped a return value nobody used; one helper for section ordering and one for writing run config files; renamed confusing names; took the hard-coded `im` case out of the generic walker; updated stale docstrings.
    - Not fixed, with reasons:
      - `im.config.load_config(path, environ)` stays because the ticket pins that signature.
      - Old ablation names on `--config` stay per Q8 until 146.
      - `Leaf.kind` as a string, the tuple returned by `locks()`, and file size are judgement calls, left alone.
  - **Found after the reviews, fixed in `322d38d`:**
    - A model or source name with a dot (e.g. `DATA_AGENT_MODEL=gpt-4.1`) produced text that failed to reload with `models.gpt-4.1: unknown key`. That would have broken the data server and every web run. Keys now keep their parts, and dotted names round-trip; the original dotted test input is back.
    - The form now refuses `full-old-skill`, which would otherwise have run the current Skills under that label.
    - Two web tests now use a fake HOME so they don't read a real `~/.data-agent/config.toml`.

  **Deviations and known limits**
  - **`agent.py:1621`, one line outside May-edit.** The data server's `--config` is now `config.server_arg()`, a file of the effective config written to the run's temp directory. It had to change because `version.config` is now the file's stem (e.g. `product`), and the data server can't load that by name. 144/145/147/148 need to rebase over it.
  - **README** still documents `--im`, `--data-sources` and `--mcp-servers`. It is outside Authority; `docs/setup.md` is updated.
  - **`[limits]`** is shown by `config show` but has no effect until 144. `RunConfig` keeps the code-default caps, otherwise `test_the_command_line_wins_over_the_limits` would pass early.
  - **For 144:** the loader already refuses a command-line value against `[policy]`, because 141's `test_a_command_line_value_against_the_policy_is_refused` requires it. Nothing feeds command-line values in yet, so 144 should not build this a second time.
  - A form run's `version.config` is the picked ablation name until 144 drops the form's pick.

  ## For the main session
  1. **Integration tests I removed marks from but could not run** (no model env here): `test_seam_a_run_cli.py`, `test_chat_e2e.py`, `test_mcp_e2e.py`. Run `make test-integration` before merging.
  2. **Smoke run.** Build `shop.duckdb` from `tests/fixtures/shop.sql`, then write `/tmp/143-smoke/product.toml`:
     ```toml
     [sources.shop]
     kind = "duckdb"
     path = "shop.duckdb"
     ```
     Run: `uv run data-agent run --type query --workspace /tmp/143-smoke/ws --config /tmp/143-smoke/product.toml --events-dir runs/143-smoke --task "How many orders are there?"`
     It passes if the answer is right, the `version` event has `config: "product"`, and there is a `sql` event. The `sql` event shows the data server started from the temp config file.
- 2026-11-27（主会话）：`agent.py:1621` 一行超出 May-edit，接受：Context 点名了这里要改（`version.config` 改成文件名后数据服务器按名字找不到）；Spec 审查也判在意图内。README 里旧参数的说明转给 144（下面）。
- 2026-11-28（主会话）：合并前真实运行（服务器，`322d38d`）：冒烟通过（答案 37 与 DuckDB 一致；`version.config = "product"`；`sql` 事件；`ps` 看到数据服务器 `--config /tmp/data-agent-config-*/product.toml`，文件里没有 `api_key`，运行后已删；$0.0032 ×2）。集成测试 31 个：30 passed，1 failed（`test_seam_a_run_cli.py::test_dbt_model_built_with_correct_values`，模型建在 `da_run_*` 临时 schema、测试查 `main`；140 改默认 `scratch` 时漏了这个测试的 `run_agent`，不是本工单的问题，开 164 修）。花费约 $0.16。
- 2026-11-28（主会话）：PR #150 CI 绿；Codex GitHub app 1 条 P2，核实属实。**退回（第 1 轮）：原因如下，在原分支 `worktree-143-config-loader` 上接着修**。
  1. **`subtree` 按扁平字符串前缀匹配**（`src/data_agent/config.py:604-618`）：数据源 `shop` 和 `shop.archive` 同时存在时，查 `sources.shop` 也会匹配 `sources.shop.archive.*`；它的键段是 `["sources", "shop.archive", ...]`，没有哪段前缀等于 `sources.shop`，`next()` 抛 `StopIteration`，`web` 和用配置文件的 `run` 都起不来。改成按键段（表边界）匹配，不靠扁平字符串前缀；配一个修复前会失败的单元测试（`shop` 与 `shop.archive` 两个数据源都能取到、互不混入），同类检查 `[mcp.servers.*]`、`[models.*]`、`[im.*]` 等用 `subtree` 的地方。
- 2026-11-28（工单 agent，退回第 1 轮；`96918d9`、`ff27757`、`1649a70`）：
  - 2026-11-28 (ticket agent, opus-5.5; branch `worktree-143-config-loader`, `96918d9`…`1649a70`; return round 1):

    **The returned bug**
    - **Fix** (`96918d9`): `subtree` now matches tables by key parts instead of a string prefix. A new helper, `table_depth` in `config.py`, does the matching:
      - A `Key` matches on its own parts.
      - A dotted string matches when its first parts joined with dots equal it.
    - **Callers:** `web/sources.py` `config_sources` now looks a source up by `Key(("sources", name))`.
    - **Same bug in the launcher:** `web/launcher.py` `config_args` also selected keys by the prefix `sources.<name>.`. When the run's data sources included only `shop`, it still copied in all of `sources."shop.archive"`. It now uses `table_depth` too.
    - **Tests, both failing before the fix:**
      - `test_a_name_that_starts_another_names_table_stays_apart` covers the three named sections: sources `shop`/`shop.archive`, mcp.servers `gh`/`gh.mirror`, models `gpt`/`gpt.4`. It failed with `StopIteration`.
      - `test_a_runs_config_file_keeps_another_sources_keys_out` failed with `['shop', 'shop.archive'] == ['shop']`.
    - **`[im.*]` needs no change:** its three names (slack, telegram, discord) are fixed in the schema, so one can't be the start of another.
    - **The other string-key callers** (`mcp.servers`, `mcp.file`, `im`, `layers.*`) all name fixed sections.

    **Evidence**
    - `make check`: exit 0; 3580 passed, 3 skipped, 62 xfailed; basedpyright 0 errors; web 448 passed (`runs/143/check-6.txt`).
    - `pytest tests/acceptance/test_config_load.py tests/acceptance/test_config_cli.py`: 38 passed.
    - This round changed no file in `tests/acceptance` or `tests/fixtures`. The diff against origin/main there is still only deleted mark lines, plus the 2 replacement lines recorded last round.
    - Real run ($0), a project-level file with sources `shop` and `"shop.archive"`, `HOME=<tmp>/home uv run data-agent config show --workspace <tmp>/ws`, exit 0:
      ```
      sources.shop.kind = "duckdb"  # <tmp>/ws/.data-agent/config.toml
      sources.shop.path = "<tmp>/ws/.data-agent/shop.duckdb"  # <tmp>/ws/.data-agent/config.toml
      sources."shop.archive".kind = "duckdb"  # <tmp>/ws/.data-agent/config.toml
      sources."shop.archive".path = "<tmp>/ws/.data-agent/old.duckdb"  # <tmp>/ws/.data-agent/config.toml
      ```
      `config_sources` on the same file returned two sources, `shop` → `shop.duckdb` and `shop.archive` → `old.duckdb`. Before the fix, this path raised `StopIteration`.

    **`da-review`: 4 × P2, all fixed in `ff27757`, each with a test that failed first**
    1. **Leaf and table with the same text.** With sources `shop` and `"shop.path"`, looking up the table `sources."shop.path"` returned `shop`'s `path` leaf, and `config_sources` crashed. A `Key` lookup in `value`/`raw_value` now matches leaves by their parts.
    2. **Credential in `DATA_AGENT_BASE_URL`.** The URL could carry a credential, and it was copied as-is into the TOML text, which `config show` prints and web runs write to disk. The text now holds `${DATA_AGENT_BASE_URL}`, while the value is still the expanded URL with source `env:DATA_AGENT_BASE_URL`. Child processes inherit the environment, so a reload still works:
       - Web runs are started with `os.environ`.
       - The data server runs in the SDK subprocess, which inherits the environment, and `sdk_env()` does not blank this variable.

       For that reason I changed the existing `test_a_name_with_a_dot_survives_the_text` so its reload passes the same environment. Its assertions are unchanged.
    3. **Base URL applied to the wrong model.** With `roles.main = "${CHOSEN_MODEL}"`, `DATA_AGENT_BASE_URL` was set on a model literally named `${CHOSEN_MODEL}`. It now goes to the model named after the reference is expanded.
    4. **Data server would not start.** It runs under `sdk_env()`, which blanks `ANTHROPIC_API_KEY`. A config with `api_key = "${ANTHROPIC_API_KEY}"` made it fail with "variable not set". Its config file (`cli.server_config`) now leaves out every `[models.*].api_key`; the server gets its model settings from `tool_env()`.

    **`eng:code-review`**
    - **Spec axis:** the return is fully fixed, nothing is missing, and there is no scope creep. Of its three notes:
      - Fixed in `1649a70`: `config show` printed dotted names unquoted, so `sources.shop.path.path` could not be told apart. It now writes each part as TOML does, e.g. `sources."shop.path".kind`; test `test_config_show_quotes_a_name_with_a_dot`, which failed first. Names without a dot print as before, so the format pinned in 141 is unchanged.
      - Not fixed: "a `${...}` in `DATA_AGENT_MODEL` crashes with KeyError." It does not reproduce: it already gives `ConfigFileError environment: roles.main: the variable NOPE it references is not set`.
      - For 145: inside the data server, `tool_env()`'s `DATA_AGENT_MODEL` overrides `roles.main`, so the base URL lands on a `[models.<model id>]` table. This is harmless today; 145 should check it when it connects `[roles]` to models.
    - **Standards axis:** no hard violations. Fixed in `1649a70`:
      - `is not None` instead of a truthiness test on the depth.
      - The api_key test is now a named `is_model_key`.
      - One module-level `from data_agent.config import ...` in `sources.py` replaces two local imports. There is no import cycle; I checked that `config.py` imports nothing from `data_agent`.
      - Review history removed from test docstrings.

      Not fixed, with reasons:
      - The string vs `Key` matching rules in `lookup`/`table_depth` stay split. Turning every string into `Key` parts would change what a plain dotted string means, and that would break the existing dotted-name lookups (`value("sources.a.b")`, which is `[sources."a.b"]`).
      - Not sharing one "filter leaves, then write the file" helper between `cli.server_config` and `launcher.config_file`: there are two call sites, each with a different filter. That is a judgement call.
      - `launcher` keeps `startswith(("sources.", "im."))` for top-level sections, whose names are fixed, so it is correct.
    - **Known limit:** a leaf could collide with another entry's leaf only in a contrived case, e.g. server `gh` with header `url` and server `"gh.headers"` with `url`. Both are the string `mcp.servers.gh.headers.url`, and the config's dicts are keyed by that string. Fixing it would mean keying every dict by a tuple of parts, which is a refactor outside this return.
- 2026-11-28（主会话）：退回第 1 轮核查：CI 绿、Codex 当前 head 无意见（`1649a70`）。服务器真实运行：两次冒烟通过（`shop`；`shop` + `"shop.archive"`），数据服务器临时配置文件没有 `api_key`，主模型的 `base_url` 是 `${DATA_AGENT_BASE_URL}` 引用；集成测试 28 passed、3 failed：164 的已知失败，加 `test_chat_e2e.py::test_b_an_ambiguous_question_is_clarified_and_answered_as_chosen` 第 1、3 次（模型没用 net/gross 的字眼、或没提问），单独重跑 3/3 通过，`322d38d` 时也 3/3，判为模型波动（deepseek-flash）。花费约 $0.34。PR #150 squash 合并（`c50e82b`）。Status done。
