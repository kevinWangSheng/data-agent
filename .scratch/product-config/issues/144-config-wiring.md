# 144 — 配置接到运行上：`[limits]`、`[sandbox]`、`[sources.*]` 新字段、`[policy]`、网页和 IM 的 effort/超时、产品默认 `full`、表单不列消融配置、值字典缓存

Status: ready-for-agent
Blocked by: 143
Spec: ../spec.md（"分节（Q2）"的 `[limits]`、`[sandbox]`、`[sources.<名字>]`、`[policy]`；"产品默认与入口"）
Claimed by: —
Branch: —

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

## Acceptance

- [ ] `TICKET_144` 标记全部删掉；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_config_wiring.py -v` 全部 PASSED，0 个 xfail。
- [ ] 141 的守门组和其他已合入的验收测试照旧通过（评测运行参数逐项不变：代码默认值等于现在的常量）。
- [ ] 验收测试没钉的每个键（例如 `duckdb.memory_limit` 真进了 `SET memory_limit`、`dbt_timeout_seconds` 真限住了 dbt、`exec_max_seconds` 真限住了 `sandbox_exec`、值字典第二次运行复用了缓存、评测运行没复用）有新单元测试。
- [ ] `docs/setup.md` 写了这几节的每个键和默认值。
- [ ] `make check` 通过（含 web 的 typecheck 与 vitest）。

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：把审计第 1.3（通用版）、2.1、2.2、4、5.3 节接到配置上。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：加 `[server]`（host、port、runs_dir）和 `[trace]`（base_url、tier）；`[policy]` 冲突报错；去掉"`[server]`/`[trace]` 没排给本工单""已有验收测试钉住旧行为"两条停止条件（141 改写已有验收测试）。
- 2026-11-27（主会话）：按 ADR-0012 调整："文件 + 数据库"的生效设置交给 settings 158，本工单只做文件层；Context 加"只做文件层"一条，要求运行的生效配置经一处解析入口，供 158 叠数据库。本工单原本就没有数据库的范围，Authority、Acceptance 不变。
