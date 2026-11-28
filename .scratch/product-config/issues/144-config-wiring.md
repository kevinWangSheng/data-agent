# 144 — 配置接到运行上：`[limits]`、`[sandbox]`、`[sources.*]` 新字段、`[policy]`、网页和 IM 的 effort/超时、产品默认 `full`、表单不列消融配置、值字典缓存

Status: done
Blocked by: 143
Spec: ../spec.md（"分节（Q2）"的 `[limits]`、`[sandbox]`、`[sources.<名字>]`、`[policy]`；"产品默认与入口"）
Claimed by: claude (opus 5.5, worktree 144-config-wiring) 2026-11-28
Branch: worktree-144-config-wiring

## Goal

143 加载出的生效配置真正决定运行：花费、轮数、agent 超时、effort 来自 `[limits]`；沙箱镜像、CPU、内存、联网、`sandbox_exec` 最长时间来自 `[sandbox]`；每个数据源的 schema 过滤、查询超时、行数上限、DuckDB 内存与溢写、dbt 超时来自 `[sources.<名字>]`；`[policy]` 锁定 `network`、`dbt_target`、`disabled_models`，和命令行参数冲突时报错拒绝；`data-agent web` 从 `[server]` 读 `host`、`port`、`runs_dir`（`--runs-dir`、`--port` 作为覆盖保留）；`[trace]` 给 `base_url`、`tier`（密钥仍是环境变量）。所有产品入口默认层开关是 `full`，网页表单不再列消融配置，网页和 IM 的 effort、超时从配置来。产品运行按目录哈希复用值字典，评测运行照旧每次重建。141 合入的 `TICKET_144` 验收测试全部转绿（只删标记）。

## Authority

- May edit：
  - `src/data_agent/config.py`：只补本工单各节的校验（取值范围），不改 143 定下的加载与合并。
  - `src/data_agent/agent.py`：只改 `run`（`agent.py:2124-2455`）里上限、超时、effort 的来源，`spend_cap` 的预算来源（`:2201`），`TurnBudget` 的轮数来源（`:2292`），`build_options`（`:1556`）里 `max_turns`、`max_budget_usd`（`:1722-1727`）的来源（预算的值归本工单；按模型价格的缩放 `sdk_budget_usd`、`spend_cap` 归 145）和 `[policy].network` 对 `WEB_TOOLS` 的覆盖（`:1661-1663`）；`prebuilt_dictionary`（`:355-376`）加缓存。不改提示词、`ModelSettings`、Skill/MCP/插件部分。
  - `src/data_agent/cli.py`：`run`、`mcp-server` 的参数从生效配置取默认值；命令行参数照 spec 覆盖配置；和 `[policy]` 冲突的命令行参数报错（退出码 2，点名键），不悄悄改成锁定值。`web` 子命令（`cli.py:360-362`：`--runs-dir` 现在必填、`--port` 默认 `WEB_PORT`）改从 `[server]` 取 `runs_dir`、`port`、`host`（`WEB_HOST`、`WEB_PORT` 在 `:38-39`，`uvicorn.run` 在 `:608`）；`--trace-tier`（`:318-323`）的默认值从 `[trace].tier` 来。
  - `src/data_agent/tracing.py`：`LangfuseSettings.from_env`（`tracing.py:86-93`）的 `base_url` 可从 `[trace].base_url` 来，公钥、私钥仍只从环境变量（`:35-38`）。
  - `src/data_agent/gateway.py`（`gateway.py:20-27`、`:253`）、`src/data_agent/sandbox.py`（`:39`、`:44-45`、`:420-423`）、`src/data_agent/dbt_execute.py`（`:46`）、`src/data_agent/context_tools.py`（`:89-90`）、`src/data_agent/mcp_server.py`、`src/data_agent/db_facts.py`：常量变成从配置传入的参数，代码默认值等于现在的常量。
  - `src/data_agent/web/app.py`（`Submit` `:47-52`、`/api/options` `:110-114`）、`web/launcher.py`（`:162-183`、`:256-257`）、`web/conversations.py`（`:45`、`:138-143`）、`src/data_agent/im/adapter.py`：产品默认 `full`、不收消融名、effort 与超时从配置来。
  - `web/src/local/NewRun.tsx`、`web/src/data/source.ts`（`RunOptions`、`Submission`，`:37-39`）及其测试：表单不再有配置下拉，改显示生效配置的摘要（spec"产品默认与入口"排在本工单）。
  - `tests/acceptance/` 里 141 标了 `TICKET_144` 的文件（`test_config_wiring.py`，以及 141 改写过的已有文件，如 `test_web_api.py`；清单见 141 最终回复）：**只删** `TICKET_144` 标记，以及因此没用的定义和 import。
  - `README.md`：删掉已不存在的 `--im`、`--data-sources`、`--mcp-servers`，改写成 `--config` 文件里的 `[im.*]`、`[sources.*]`、`[mcp]`（143 删了参数，README 不在 143 的 Authority）。
  - `tests/`（除 `tests/acceptance/`）；`docs/setup.md`（`[limits]`、`[sandbox]`、`[sources.*]`、`[policy]` 各键）。
- Must not edit：`tests/acceptance/` 里除 `TICKET_144` 标记以外的一切；`tests/fixtures/product_config/`；`agent.py` 的提示词与 `ModelSettings`（145、148）；`classify.py`、`verifiers.py`（145）；`evals/`（146）；`skills/`；`web/src/run-page/`、`web/src/run-view/`（149）；`src/data_agent/configs/`；其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none。
- Spend cap：工单 agent $0（不能 source 模型的 env 文件）。

## Context

行号以 main `e494e49` 为准；140 合入后 `cli.py`、`web/`、`im/adapter.py`、`mcp_server.py`、`db_facts.py`、`context_tools.py` 的行号会移动。

- **审计条目**（`docs/research/2026-11-27-product-config-audit.md`）：
  - 4.1 `max_budget_usd = 1.0`、轮数 dbt 200 / 查数 50（`configs/*.toml:6-12`、`run_config.py:34-35`）。spec 的 `[limits].max_turns` 有 `large`（今天只 DAB 用，`dab.py:224-225`）。
  - 4.2 DuckDB 内存 2GiB、溢写 4GiB（`gateway.py:26-27`）；4.3 查询超时 150 s、行数上限 10,000（`:20-21`）、Postgres 连接超时 10 s（`:253`）。注意 `TIMEOUT_SECONDS` 也是 `run` 的截止余量（`agent.py:2032` `DEADLINE_MARGIN_S`），`MAX_ROWS` 也是 `classify_text` 的分页大小（`classify.py:274-283`）：按数据源可配以后这两处取哪个值要写明。
  - 4.4 沙箱镜像、2 核、4g（`sandbox.py:39`、`:44-45`，用在 `:276`）；4.6 dbt 超时 900 s（`dbt_execute.py:46`）、`sandbox_exec` 最长 900 s（`sandbox.py:423`）。
  - 4.5 网页、IM 不传 effort、agent 超时（`launcher.py:162-183`、`conversations.py:138-143`；IM 经 `conversations.send`，`im/adapter.py:302`）。
  - 2.1 产品默认：对话、IM 已是 `full`（`conversations.py:45`），网页表单默认 `full`（`web/src/local/NewRun.tsx:10`）；`data-agent run` 今天必须给 `--config`（`cli.py:290`）。spec 方向 7 定所有产品入口默认 `full`。
  - 2.2 表单列出全部消融配置（`app.py:113` `"configs": list(CONFIG_NAMES)`，`launcher.py:256-257` 校验）。
  - 5.3 值字典每次重建（`agent.py:355-376`，`context_tools.py:89` 60 s 上限）；spec 定按数据源缓存、键为目录哈希，产品复用、评测照旧重建（"评测"怎么认出来：评测配置文件里的一个键，或评测入口的参数，由本工单定并写明）。
  - 1.3 之外的通用版 schema 过滤：`schemas.include`/`schemas.exclude`。140 正在做"排除别的运行的 `da_run_*`"（`mcp_server.py`、`db_facts.py`、`context_tools.py`），本工单在它上面加按数据源的 include/exclude，不重做 140 的那部分。
- **141 的契约**：`[policy]` 与命令行参数冲突时是覆盖还是报错、网页子进程拿到哪份配置、`/api/options` 的新形状，以 141 最终回复为准。
- **`[server]`、`[trace]`**（`decisions.md` Q8）：`host`、`port`、`runs_dir` 和 `[trace]` 的 `base_url`、`tier` 归本工单；`[server].mode` 归 cloud-deploy 153，`refresh` 由 154 补进配置格式。`host` 现在写死为本机回环（`cli.py:37-38` 的注释"no option changes it"）；本工单读 `[server].host`，非本机地址要令牌的规矩属于 153，本工单不放宽：`host` 不是本机回环地址时报错拒绝，等 153 接上令牌（这一做法写进 Comments，供 153 接手）。
- **`[policy]` 冲突**：报错拒绝，不悄悄改成锁定值（Q8）。和 140 的 409 一致：网页请求要求联网而配置关了联网时拒绝。
- **只做文件层**（ADR-0012、`.scratch/settings/spec.md`"对已有工单的调整"）：本工单的生效设置只来自代码默认值、配置文件、环境变量、命令行参数和 `[policy]`；数据库里的设置、四层作用域、单次覆盖由 settings 158 叠上去。所以网页、对话、IM 的每次运行拿生效配置要走一处解析入口（141 契约 4 那条路），158 在这一处加数据库，不另写一套合并。不建库、不读库。
- **前端**：本工单改 `web/src/local/` 的表单，但主要是后端改动，照 AGENTS.md 后端工单的审查流程（`da-review` 加 `eng:code-review`）；`web/src/` 的改动在最终回复里单独列出，主会话合并前决定要不要另加前端审查。
- **并行工单**：143（基础）、145、147、148 都改 `agent.py`、`cli.py`，后合入的一方 rebase。
- **守门测试依赖的接口，保留名字和签名**（141 合并前审查第 9 条；验收测试之后不能改，改名会让守门永久失败）：`context_tools.build_interruptible`、`agent.build_interruptible`；`evals.jaffle.clone_project`、`reset_schemas`、`dbt` 和 `evals.jaffle` 模块的 `subprocess` 属性；`DataAgent(run_config=…, benchmark=…)` 和 `_run_command`；`evals dab` / `evals jaffle` 的命令行参数；`profile_path` 要求容器里的路径含 `profiles/` 一段。见 141 的 `tests/acceptance/product_config_support.py`（`dictionary_from_duckdb`、`jaffle_entry`、`profile_path`）。

## Acceptance

- [ ] `TICKET_144` 标记全部删掉；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_config_wiring.py -v` 全部 PASSED，0 个 xfail。
- [ ] 141 的守门组和其他已合入的验收测试照旧通过（评测运行参数逐项不变：代码默认值等于现在的常量）。
- [ ] 验收测试没钉的每个键（例如 `duckdb.memory_limit` 真进了 `SET memory_limit`、`dbt_timeout_seconds` 真限住了 dbt、`exec_max_seconds` 真限住了 `sandbox_exec`、值字典第二次运行复用了缓存、评测运行没复用）有新单元测试。
- [ ] `docs/setup.md` 写了这几节的每个键和默认值。
- [ ] `make check` 通过（含 web 的 typecheck 与 vitest）。
- [ ] README 不再提 `--im`、`--data-sources`、`--mcp-servers`；用法改成配置文件（143 交回）。
- [ ] 不重复实现"命令行值和 `[policy]` 冲突时拒绝"：143 的加载器已经做了（141 的 `test_a_command_line_value_against_the_policy_is_refused`），本工单只把命令行值接进去。

## Evidence required

`make check` 输出；`test_config_wiring.py` 的 `-v` 输出；`git diff main -- tests/acceptance | grep '^[-+]' | grep -v '^[-+][-+]'`。不调模型的真实运行（$0）：`data-agent config show` 显示 `[limits]`、`[sandbox]`、`[policy]` 的生效值；`data-agent web` 起来后 `curl -s localhost:<端口>/api/options` 的输出。

交给主会话（写出命令）：一份 `[limits].max_turns.query = 5`、`[sources.shop].max_rows = 10` 的配置跑一次查数，确认事件里轮数上限和截断行数；网页表单提交一次，确认运行详情里层开关全开。

## Stop conditions

- 改上限的来源会让 141 的快照守门组失败（说明代码默认值和现在的常量不一致）。
- 某个键要动 Authority 以外的文件（例如 `evals/` 的评测入口要传"评测"标记）才能做到。
- `[server].host` 的处理和 cloud-deploy 153 的工单冲突（开工时读 `.scratch/cloud-deploy/issues/153-*.md`）。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3（前两次因本机内存不足中断）；退回第 1 轮修复 1 次
- Review rounds: 初版 2 轮；退回第 1 轮修复后 da-review 1 轮（2 条，都修）、eng:code-review 1 轮（修 4 条，余下写理由）
- Model spend: $0
- Outcome: 退回第 1 轮 1–4 条全修，待合并前审查
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：把审计第 1.3（通用版）、2.1、2.2、4、5.3 节接到配置上。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：加 `[server]`（host、port、runs_dir）和 `[trace]`（base_url、tier）；`[policy]` 冲突报错；去掉"`[server]`/`[trace]` 没排给本工单""已有验收测试钉住旧行为"两条停止条件（141 改写已有验收测试）。
- 2026-11-27（主会话）：按 ADR-0012 调整："文件 + 数据库"的生效设置交给 settings 158，本工单只做文件层；Context 加"只做文件层"一条，要求运行的生效配置经一处解析入口，供 158 叠数据库。本工单原本就没有数据库的范围，Authority、Acceptance 不变。
- 2026-11-27（主会话）：按 141 合并前审查补 Context：守门测试依赖的接口保留名字和签名。
- 2026-11-27（主会话）：按 143 交回补：README 旧参数归本工单；`[policy]` 冲突的拒绝 143 已做，别重复。
- 2026-11-28（工单 agent，opus 5.5）：

  **已完成的接线**
  - `[limits]`：轮数（含 `large`）、`max_budget_usd`、`agent_timeout_sec`、`effort` 接到运行上。
  - `[sandbox]`：image、cpus、memory、network、`exec_max_seconds` 接到沙箱。
  - `[sources.<名字>]`：`schemas.include`/`exclude`、`query_timeout_seconds`、`max_rows`（截断时结果末尾写 `[ROW CAP]`）、`duckdb.memory_limit`/`max_temp_directory_size`、`dbt_timeout_seconds`。
  - `[policy]`：冲突的拒绝沿用 143 的加载器，没有重写一遍。
  - `[server]`：`host`、`port`、`runs_dir` 接到 `data-agent web`。
  - `[trace]`：`tier`、`base_url` 接上，密钥仍只从环境变量来。
  - 网页表单不再有配置下拉，改成显示服务器配置的摘要。请求里带 `config` 字段返回 422。网页、对话、IM 的运行都经 `Launcher.config_args` 这一处拿配置。
  - 值字典缓存，`config.py` 补了取值范围校验，`docs/setup.md` 和 README 改好了。

  **写明的约定**
  - 截止余量取运行所有数据源 `query_timeout_seconds` 里最长的；不写时 150 秒，等于原来的 `DEADLINE_MARGIN_S`。
  - 网关自己的 `MAX_ROWS` 仍是框架读目录和 `classify_text` 分页的大小；`max_rows` 只限 `query_database` 返回的行数，上限 10000。
  - "评测"的认法：`--config` 给旧消融名（`evals/` 现在就这样传），或者给 `--no-dictionary-cache`。这两种运行不用缓存。
  - 缓存放在 `$XDG_CACHE_HOME/data-agent/dictionaries`，按数据源存：键是数据源名、schema 过滤和目录哈希，Postgres 再加 URL 的哈希。不带名字的 `--duckdb` 运行按文件路径存。只有每张表都读到的值字典才存。
  - 已知限制：
    - 目录不变而数据变了时，复用旧的值字典。
    - 两份配置里同名、同结构的 DuckDB 数据源（比如 dev 和 prod 都叫 `shop`）会共用一份。

  **给 153**：`[server].host` 不是本机回环地址时，`cli.web_host` 退出 2，点名 153。153 接上令牌后在那里放开。

  **给 146**：评测入口改成 `--config <路径>` 以后必须同时传 `--no-dictionary-cache`，否则评测会悄悄用缓存。请写进 146 的验收标准。

  **给 145**：
  - `check_policy` 已经会拒绝 `policy.disabled_models` 里的 `[roles]` 模型。
  - `limits.effort` 没按 `EFFORTS` 校验，留给 145 的 `efforts`。

  **`Launcher.submit` 保留第 4 个位置参数 `config`**：`tests/acceptance/test_charts_switch.py:212` 这样调用，验收文件又不能改。现在这个参数只接受 `"full"`，其他名字返回 400。

  **第 3 步：真实运行（$0）**
  - `data-agent config show --config product.toml` 显示了全部生效值和来源，例如：
    ```
    limits.max_turns.query = 5  # …/product.toml
    sandbox.exec_max_seconds = 120.0  # …/product.toml
    sources.shop.max_rows = 10  # …/product.toml
    server.port = 8799  # …/product.toml
    policy.dbt_target = "scratch"  # policy
    ```
  - `data-agent web --config product.toml`（不给 `--runs-dir`/`--port`）监听 8799。`curl -s localhost:8799/api/options` 输出：
    ```
    {"data_sources":["shop"],"task_types":["query","dbt"],"config":{"name":"product","layers":{"skill":true,"verifiers":true,"context":true},"model":"deepseek-flash","effort":"high","max_budget_usd":0.5,"max_turns":{"query":5,"dbt":200,"large":75},"agent_timeout_sec":600.0,"network":true}}
    ```
  - 带 `config` 的 `POST /api/runs` 返回 422。`[server].host = "0.0.0.0"` 时退出 2，报错点名 153。

  **测试与检查**
  - `make check` 退出 0：basedpyright 0 errors；pytest 3653 passed、37 xfailed；web vitest 450 passed。
  - `test_config_wiring.py -v`：17 PASSED。
  - `git diff main...HEAD -- tests/acceptance tests/fixtures`：43 行删除。只有 2 行新增，就是 `test_mcp_web.py` 的两行 `@pytest.mark.parametrize("route", ["form", "turn"])`，由原来的 `pytest.param("form", marks=TICKET_144)` 改写而来，也只是去掉标记。
  - 新单元测试在 `tests/test_config_limits.py`（44 个），另有 `tests/test_dbt_execute.py` 的 dbt 超时测试。覆盖内容：
    - DuckDB 内存与溢写上限真进了连接；
    - `max_rows` 截断并写 `[ROW CAP]`；
    - include/exclude 生效；
    - dbt 超时真杀掉 dbt；
    - `exec_max_seconds` 真限住了 `sandbox_exec`；
    - 缓存命中、评测不复用、库副本复用；
    - host 拒绝；
    - 取值范围；
    - 三档轮数上限都从 `[limits]` 来。
  - 测试里加了 autouse 夹具把 `XDG_CACHE_HOME` 指到临时目录。

  **审查**
  - `da-review main` 4 条（P2），都属实，都修了并配了测试：
    - 直接起 `mcp-server` 时，`--network`、`--dbt-target` 绕过了 `[policy]`；
    - 两个数据源指向同一个库时，MCP server 拿错上限；
    - 有表没读到的值字典也进了缓存；
    - 值字典的语句不受更短的 `query_timeout_seconds` 限制。
  - `eng:code-review main`：
    - Spec 轴指出网页 dbt 运行每次复制库文件，缓存永远不命中，缓存目录无限增长。已改为按数据源名做键，并修正了不带名字的 `--duckdb` 也被当成数据源的问题。
    - Spec 轴指出摘要缺 `max_turns.large`，已补上。
    - Standards 轴没有硬违规。下面这些判断题没改，理由是行为不变、本工单不做无关重构：`cli.py` 变大、`getattr` 鸭子类型、`setting`/`entry` 等几处重复、`source_limit` 和 `source_limits` 名字太像、`config.MAX_ROWS` 是 `gateway.MAX_ROWS` 的抄本（有测试保持两者相等）。
  - 我自己找到并修掉两个 bug：
    - 多数据源运行在 `Task.data_source_name` 处断言崩溃；
    - `--duckdb` 指向某个 `[sources.<名字>]` 的文件时，运行这边没拿到该数据源的上限。

  **前端改动**（主会话决定要不要另加前端审查）：`web/src/local/NewRun.tsx`、`web/src/local/NewRun.test.ts`（新建）、`web/src/data/source.ts`、`web/src/data/source.test.ts`。

  **分支状态**：本地 main 多了 ticket 164 的一个提交 `8449231`，本分支不含它，合并前要先并入 main。我没有 rebase，也没有 push。

  **遗留**：前一次中断的会话跑测试时，在真实的 `~/.cache/data-agent/dictionaries` 写了 10 个缓存文件（时间是今天 02:34–02:40）。我没有删，可以删掉。
- 2026-11-28（主会话）：合并前核查：CI 绿；服务器真实运行（`7f77a0e`）全过：`max_turns.query = 5` 时 `turn_budget` 记 `max_turns: 5`、到 5 轮停下（`error_max_turns`）；`max_rows = 10` 时 `query_database` 返回 10 行加 `[ROW CAP]`；`web --config` 不给 `--runs-dir`/`--port` 监听 8799，`/api/options` 带配置摘要，带 `config` 的 POST 422，表单运行三层都开、答案 37；花费约 $0.008。前端部分由上下文干净的 Opus 子 agent 审（不走 Codex）。**退回（第 1 轮）：原因如下，在原分支 `worktree-144-config-wiring` 上接着修**。
  1. **Codex P2（属实，即本工单自己写的已知限制）**：同名、同结构但数据不同的 DuckDB 数据源（两个项目都叫 `shop`）缓存键相同，第二个项目的运行会读到第一个项目的取样值，`dictionary_search` 返回无关库的值（`src/data_agent/agent.py:500` 附近）。键里加上原始配置的数据库身份（解析后的原文件路径，不是每次运行的副本；Postgres 已有 URL 哈希），网页 dbt 运行复制库文件后仍要命中缓存。配修复前会失败的单元测试。
  2. **前端 should-fix**：配置摘要不显示轮数上限（`web/src/local/NewRun.tsx:131-139`，注释 `:128-129` 说显示"它设的上限"）：显示 `max_turns` 的 query、dbt、large，配测试。
  3. **前端 should-fix**：服务器关了联网时复选框置 false 并禁用（`NewRun.tsx:29`、`:97`）没有测试：抽成纯函数或在选项已加载时渲染，配测试。
  4. 前端小问题（可顺手）：禁用的联网框旁写一句原因（如"本服务器已关闭"），去掉 `cursor-pointer`；`NewRun.test.ts:30` 的 `not.toContain('Config')` 太弱，删掉或改成匹配旧标签原文。
  - 合并 main 时注意：145（PR #154）删了 `sdk_budget_usd`，两边都改 `build_options` 的 `max_budget_usd=`，后合入的一方 rebase，改成 `backstop_usd(budget, spend.price)`。
- 2026-11-28（工单 agent，opus 5.5，退回第 1 轮修复）：
  先 merge origin/main（`cccba64`，无冲突）；145 还没合入，`backstop_usd` 的 rebase 留给后合入的一方。提交 `26cee27`、`ffe89ae`、`a12fa78`。

  **退回各条**
  1. 值字典缓存键加上数据源在配置里的库：`cli.dictionary_origins` 取 `[sources.<名字>].path` 解析后的原文件，经 `run(dictionary_origins=…)` 传到 `agent.cached_dictionary`。键里的 `database` 是它，没有时用 gateway 自己的 identity（文件；Postgres 用 URL 哈希）。
     - 网页 dbt 运行用的是库文件副本，仍命中缓存：配置里的 `path` 是原文件（`web/sources.py:77`）。
     - 两个项目都叫 `shop`、指向不同文件时各存一份。
     - 修复前会失败的测试：`test_two_projects_sources_of_one_name_keep_their_own_dictionaries`（断言每个项目只拿到自己的取样值）和 `test_a_runs_dictionary_origin_is_its_sources_file_not_the_copy`。
     - `test_a_copy_of_a_sources_file_reuses_its_dictionary` 改为传 `origin`。
     - 删掉原 Comment 里的已知限制"同名同结构数据源共用一份"，`docs/setup.md` 同步改。
  2. 摘要显示轮数上限：`max turns query 50, dbt 200, large 75`，值为 null 的那项不写；配测试。
  3. 联网框抽成 `NetworkBox`，提交的值抽成纯函数 `askedNetwork`；服务器关了联网时，复选框不勾、禁用，请求不带联网。两处都配了测试。
  4. 禁用时旁边写"(off on this server)"，去掉 `cursor-pointer`；`not.toContain('Config')` 改成匹配旧标签 `>Config<`。

  **审查**
  - `da-review main`：2 条 P2，都属实，都修了并配测试：
    - 缓存的值字典含取样值，目录改为 0700、文件 0600（不管 umask）；
    - 手动起的 `mcp-server --sandbox docker` 不读 `[sandbox].network`，现在 `--network` 或配置任一开就开；`[policy].network = false` 仍由加载器锁住。
  - `eng:code-review main`：
    - Spec 轴：
      - 新的两项目测试不稳：整数列不进值字典，断言靠运气，5 次败 2 次。已改成文本列，并确认值进了 `entries`。
      - 提交联网值的规则没测：已抽成 `askedNetwork` 并配测试。
      - 带 `--data-source-name` 的 `--duckdb` 按那个数据源配置里的文件做键，不管实际给的是哪个文件：这是网页 dbt 副本要的行为，已写进 `docs/setup.md` 和 docstring。
    - Standards 轴：
      - 没有硬违规。
      - 键格式 `duckdb:<路径>` 写了两处：已改为调用 `DuckDBGateway(...).identity()`，只留一处定义。
      - 判断题没改，理由：
        - cache/source/origin 三个参数一起传：现在只有 4 个函数签名，包成一个键对象是本工单不需要的重构。
        - `Mapping | None = None`：沿用文件里 `source_limits` 的写法，保持一致。
        - 起名字的 origin 只按名字查、limits 还按位置查：设计如此，bare `--duckdb` 按文件做键（docstring 写了）。
        - `NetworkBox` 里又写了一次 `checked && !off`：组件自己保证禁用时不勾，单独渲染时也成立。
        - 测试里函数内 import：沿用文件已有写法。
        - 每次保存都 chmod 缓存目录：有意如此，docstring 写了。

  **测试与检查**
  - `make check` 退出 0（输出存在 `/tmp/144-check-final.txt`）：basedpyright 0 errors；pytest 3659 passed、37 xfailed；vitest 454 passed。
  - `git diff main...HEAD -- tests/acceptance tests/fixtures`：43 行删除，2 行新增，就是上轮说明过的两行 `parametrize`。

  **前端改动**（本轮）：`web/src/local/NewRun.tsx`、`web/src/local/NewRun.test.ts`。
- 2026-11-28（主会话）：退回第 1 轮核查：CI 绿、Codex 当前 head 无意见（`a12fa78`）；前端这一轮的改动由上下文干净的 Opus 子 agent 审：无 must-fix/should-fix（可选 nit：表单层没测关了联网时实际提交的请求体）。上一轮服务器真实运行（`7f77a0e`）已全过，本轮改动是缓存键、权限位和手动 mcp-server 的联网，单元测试覆盖。PR #153 squash 合并（`5927c699f3a8fb765318684e245b1ebd232f1b48`）。Status done。
