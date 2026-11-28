# 141 — 产品配置：验收测试（配置加载、运行参数快照、产品默认、评测隔离、`config` 事件）

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md（"Testing Decisions"的"验收测试接缝"）
Claimed by: claude (opus-5.5, worktree 141-config-acceptance-tests)
Branch: worktree-141-config-acceptance-tests

## Goal

spec "验收测试接缝"的五组行为都有验收测试，写在约定的接缝上。还没实现的行为标预期失败，按转绿的工单分标记 `TICKET_143` … `TICKET_148`（pytest `xfail(strict=True)`）。现在就成立的行为（评测入口的运行参数和现在逐项相同、使用者的配置文件和目录不影响评测、对话与 IM 的层开关今天已是 `full`）写成守门测试，现在就通过、不标记。另外把 24 个已合入的验收文件里用旧接口的调用改成新接口（`decisions.md` Q8 人定第 1 条），只改调用方式、断言意思不变，逐条列出供人审。src 里只加一个空壳模块 `src/data_agent/config.py`（见 Authority），不带行为。本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit（新建）：
  - `tests/acceptance/product_config_support.py`：几个文件共用的辅助（截停、取值辅助、快照规范化、假 HOME 与假工作区）。
  - `tests/acceptance/test_config_load.py`：加载函数接缝（`TICKET_143`）。
  - `tests/acceptance/test_config_cli.py`：`config show`、`run --config <路径>`、三个旧参数并入（`TICKET_143`）。
  - `tests/acceptance/test_config_wiring.py`：`[limits]`、`[sandbox]`、`[policy]`、产品默认 `full`、网页表单、网页与 IM 的 effort/超时（`TICKET_144`）。
  - `tests/acceptance/test_config_models.py`：`[models.*]`、`[roles]`（`TICKET_145`）。
  - `tests/acceptance/test_config_snapshot.py`：评测入口的运行参数快照（守门，不标记）和评测入口改读配置文件（`TICKET_146`）。
  - `tests/acceptance/test_config_eval_isolation.py`：评测隔离（守门，不标记）。
  - `tests/acceptance/test_config_event.py`：`config` 事件与 `check_request`（`TICKET_146`）。
  - `tests/acceptance/test_config_skills.py`：Skill 工具、`preload`、插件、`[mcp]`（`TICKET_147`）。
  - `tests/acceptance/test_config_prompts.py`：`[prompts]`、`[instructions]`、`[sources.*].docs`/`description`、`DB_TYPE` 与任务文件提示（`TICKET_148`）。
  - `tests/fixtures/product_config/`：从当前 main 录下的快照、示例配置文件（只带 `${VAR}` 引用）、假 Skill 目录、假插件目录、假说明文件。
  - 已合入的 24 个验收文件（Context"已有验收测试"的清单）：只改调用方式（`--config <消融名>`、`--data-sources`、`--mcp-servers`、`--im`、`load_config`、`CONFIG_NAMES`），断言意思不变；和已定决定直接冲突的断言按新决定改（例如 `test_web_api.py:354-357` 表单能选 `baseline`）。每处改动列进最终回复。
  - `src/data_agent/config.py`（新建，空壳）：只有 `EffectiveConfig`（dataclass，字段和方法照 Context 契约 1，方法返回空值）、一个异常类、`load_effective_config(...)`（返回空的 `EffectiveConfig`，不读文件、不抛异常）。没有别的模块 import 它。
- Must not edit：
  - `src/data_agent/` 的其他文件（`cli.py`、`agent.py`、`run_config.py`、`configs/`、`web/`、`im/` 都不加参数、不加子命令）；`evals/`；`skills/`；`web/src/`。
  - 已有 `tests/acceptance/` 文件里上面那类调用以外的内容（断言意思、夹具、输入）；`conftest.py`、`seam_support.py`（不在 24 个文件里；辅助函数要用就照抄进 `product_config_support.py`）；`tests/fixtures/` 里已有的文件（新接口要的配置文件放 `tests/fixtures/product_config/`）。
  - `Makefile`、`.github/workflows/`、`pyproject.toml`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none。
- Spend cap：$0。不调模型，没有 `integration` 用例。

## Context

行号以 main `e494e49` 为准。140（`worktree-140-product-safety-defaults`，进行中）改 `cli.py`、`web/app.py`、`web/launcher.py`、`web/conversations.py`、`im/adapter.py`、`evals/harbor_agent.py`、`evals/jaffle.py`，合入后这些行号会移动；本工单在 140 合入前开工的话，合入后 rebase 重录快照（见"快照"）。

- **spec 与决定**：`.scratch/product-config/spec.md`（"Implementation Decisions"各节是行为来源，"验收测试接缝"是接缝）；`decisions.md` Q1–Q6，以及 Q8（建票时发现的问题，人定与主会话定的实现细节；和 spec 不一致时以 Q8 为准）；ADR-0011；审计 `docs/research/2026-11-27-product-config-audit.md`（"审计 N.N"）。
- **截停接缝**：照 `tests/acceptance/test_chat_eval_unchanged.py:80-99` 的 `captured`：monkeypatch `SubprocessCLITransport.connect`，记下 `self._options`，抛 `CLIConnectionError`。`cli.main([...])` 跑完整个 CLI，不调模型、不起 CLI。`version` 事件在 `build_options` 之前写（`agent.py:2245`），截停后事件文件已经在。
  - **任务提示词不在截停的选项里。** `run` 在 `agent.py:2292-2302` 拼任务提示词（`QUERY_PROMPT` 里的 `ANSWER_RULE`（`:263`）、`DBT_PROMPT` 里的 `DB_TYPE` 那句（`:294`）、`TASK_FILE_NOTE`（`:2301`）），经 `ClaudeSDKClient` 在连上之后才发（`agent.py:750-775`），transport 的 `connect` 看不到它。Q8 定快照比较覆盖任务提示词，148 的行为也要它，所以接缝要能同时拿到提示词和选项，例如 monkeypatch `data_agent.agent.query`（`agent.py:750`）记下 `prompt`、`options` 再抛错。用哪种由本工单定，测试注释写明理由。
  - 数据源用 `seam_support.build_shop_duckdb`。`--sandbox local`，不依赖 docker（照 115）。
- **预期失败的标记**：照工单 115（`.scratch/mcp/issues/115-mcp-acceptance-tests.md` Context"预期失败的标记"）：
  ```python
  TICKET_143 = pytest.mark.xfail(
      strict=True, raises=AssertionError, reason="ticket 143: the config loader is not built yet"
  )
  ```
  - 只接受 `AssertionError`。取字典键、找参数位置、读事件字段一律经 `product_config_support.py` 的辅助函数：先 `assert` 在，再取值。今天会抛 `KeyError`、`AttributeError` 的写法会被报成失败，不是预期失败。
  - 报错类的断言不用 `pytest.raises`：没抛时它报 `Failed: DID NOT RAISE`，不是 `AssertionError`。写成 `try/except` 记下异常，再 `assert`。CLI 报错类同时断言三件事：stderr 点名文件和键（或变量）；没有会话启动（截停列表为空）；退出码 2（seam A 约定，`test_seam_a_run_cli.py:10-11`）。只断言"退出码 2"不行：今天 argparse 对不认识的参数、缺 `--config` 已经退出 2。
  - 每个标记的测试至少有一条今天会失败的正向断言。只有否定断言的检查（"某值不出现""其他选项不变"）放进带正向断言的同一个测试，或写成不标记的守门测试，否则 strict 会 XPASS。
  - 转绿时各工单只删自己的标记，以及因此没用的 import 或定义（AGENTS.md 硬规矩的唯一例外）。
- **空壳 `src/data_agent/config.py`**：`test_config_load.py` 要 import 它，import 失败不算预期失败。空壳的 `load_effective_config` 返回空的 `EffectiveConfig`，`value()`/`source()` 返回 `None`，`files` 为空，不抛 `NotImplementedError`（那会被报成失败）。CLI 那几组不需要空壳：`config show` 和不带 `--config` 的 `run` 今天由 argparse 以 `SystemExit(2)` 结束，测试接住后做正向断言即可。
- **配置文件**：测试写进 `tmp_path`；用户级用 monkeypatch `HOME` 指向 `tmp_path` 下的假家目录（`~/.data-agent/config.toml`），项目级写在假工作区的 `.data-agent/config.toml`。密钥只写 `${VAR}`，假值在测试代码里拼（照 `test_chat_api.py` 的 `PG_PASSWORD = "hunter2-" + "k4Rz"`）、用 `monkeypatch.setenv` 设。`tests/fixtures/product_config/` 里的文件也只带引用。
- **已定的接口**（`decisions.md` Q8，测试照这些写）：
  - `--config` 可写多次，后面覆盖前面；显式 `--config` 优先于 `DATA_AGENT_CONFIG`。评测入口传"基础配置 + 层文件"两次 `--config`。
  - 过渡：143 期间 `data-agent run` 和 `mcp-server` 的 `--config` 同时接受路径和旧消融名；146 把评测入口改完后删掉旧名。
  - `config` 事件是第二条（在 `version` 之后）。`version.config` 照旧写配置名：评测写层名，产品写配置文件名，没有文件时写 `default`。
  - `[layers]` 的键是 `skill`、`verifiers`、`context`（和 `RunConfig`、`version.layers` 一致，`run_config.py:28-30,38`）。`[skills].tool` 默认 `true`。
  - 已有的变量名写法（IM 的 `bot_token_env`、`[sources.*].env = "WAREHOUSE_URL"`）保留；"只写 `${VAR}`"只管值本身是密钥的键。
  - `[policy]` 和命令行参数冲突：报错拒绝（退出码 2，点名键），不悄悄改成锁定值。
  - 模型没填价格：不传 SDK 的 `max_budget_usd`，运行开头给一条警告。
  - `[server]` 的 `host`、`port`、`runs_dir` 和 `[trace]` 的 `base_url`、`tier`（密钥仍是环境变量）归 144；`data-agent web` 读它们，`--runs-dir`、`--port` 作为覆盖保留。`[server].mode` 归 cloud-deploy 153。
  - 迁移第一步所有评测配置文件（含 LiveSQLBench、jaffle）保留现在的答题规则，只有产品用新版。
  - 插件清单文件不计入 Skill 版本哈希（147）；Skill 文本改动（含 dbt-workflow 第 2 步）推到迁移第二步，不在任何工单里。
- **本工单定下、在最终回复里逐项列出供人审的契约**（名字和格式由本工单定，人审；下面是建议）：
  1. 加载函数：`data_agent.config.load_effective_config(paths, environ, cli_overrides) -> EffectiveConfig`。`paths` 的形状（显式文件列表 / 用户级加项目级）、`cli_overrides` 的键写法（如 `"limits.max_budget_usd"`）由本工单定。`EffectiveConfig` 至少有：按键取值、按键取来源（`"default"`、文件路径、`"env:<变量>"`、`"cli"`、`"policy"` 之类）、参与合并的文件列表、生效配置的 TOML 全文（`${VAR}` 原文）和它的 sha256。异常类名和"报错指出文件和键"的格式。
  2. CLI：`data-agent config show`（接受哪些参数：`--config`、`--workspace` 找项目级），每行一个键、值、来源的输出格式，出错退出码 2。`data-agent run --config <路径>`，可省（省了读用户级与项目级）、可重复。
  3. 三个旧参数去掉以后：`run --data-sources`、`run --mcp-servers`、`web --data-sources`、`web --mcp-servers`、`web --im` 都由 argparse 拒绝；`[sources.*]`、`[mcp]`、`[im.*]` 从配置文件读到的内容和以前从这三个文件读到的相同（数据源出现在 MCP server 参数里，外部 MCP 出现在 `mcp_servers`、`allowed_tools`，IM 平台交给 `service.start`）。
  4. 网页和对话的每次运行拿到哪份配置：建议网页服务器把自己的生效配置写进运行目录，子进程参数带 `--config <那个文件>`（显式文件不会再叠一次用户级）。测试从 `create_app` 的 `start_run` 记下参数，再把参数交给进程内的 `cli.main` 截停，读出层开关和上限。
  5. 网页接口：`GET /api/options` 不再有 `configs`（或只剩生效配置的摘要，字段名由本工单定）；`POST /api/runs` 不带 `config` 时成功、子进程拿到的层开关是生效配置的。
  6. `config` 事件的字段（建议）：`config`（生效配置 TOML 全文，`${VAR}` 原文）、`sha256`、`files`（参与合并的文件路径，按合并顺序）、`file_hashes`（Skill、说明文件、插件目录里每个文件的 sha256）。
  7. 没价格时那条警告的去处（stderr、事件，或两者）和文字。
- **各组要断言的行为**：
  - **配置加载**（`test_config_load.py`，`TICKET_143`）：
    - 优先级：代码默认值 < 用户级 < 项目级 < 环境变量 < `cli_overrides`；每个键的来源对。
    - 合并按节按键，同一个键后面的层整个替换，列表不拼接（例如用户级 `[skills].dirs = ["a"]`、项目级 `["b"]` 得 `["b"]`）。
    - `[policy]` 锁定的键（`network`、`dbt_target`、`disabled_models`）任何层覆盖不了，来源记 policy。
    - 显式文件（参数或 `DATA_AGENT_CONFIG`）时不读用户级、项目级；多个显式文件后面覆盖前面；参数和 `DATA_AGENT_CONFIG` 都有时只用参数。
    - 未知节、未知键报错，报错点名文件和键。
    - 明文密钥拒绝：`api_key`、`token`、`password`、`secret` 结尾的键不是 `${...}` 时报错，报错点名文件和键、不含那个值；再加一例 gitleaks 默认规则或 `.gitleaks.toml` 的 `deepseek-api-key` 规则（`\bsk-[0-9a-f]{32}\b`）会认出的值。写变量名的键（`[im.slack].bot_token_env = "DATA_AGENT_SLACK_BOT_TOKEN"`、`[sources.x].env = "WAREHOUSE_URL"`）照常接受。
    - `${VAR}` 引用的变量没设时报错，点名变量和键。
    - `DATA_AGENT_MODEL`、`DATA_AGENT_BASE_URL` 覆盖主模型（来源记 env）；这条的生效在 145 的选项里断言，这里只断言加载结果。
  - **CLI**（`test_config_cli.py`，`TICKET_143`）：
    - `config show` 打印每个键的值和来源，密钥只显示 `${VAR}`。
    - `run --config <文件>` 的会话里能看到文件里的值（例如 `[sources.shop]` 出现在 MCP server 参数里）；`--config a --config b` 时 `b` 的值赢。
    - 产品运行（不给 `--config`、或给一个文件）的 `version.config` 是配置文件名，没有文件时是 `default`。
    - 配置文件错误（未知键、明文密钥、缺变量）：退出码 2、stderr 点名、没有会话。
    - 契约第 3 项：三个旧参数被拒；`[sources.*]`、`[mcp]`、`[im.*]` 的内容到了原来的去处。外部 MCP 沿用 116 的规矩（放行清单、`DATA_AGENT_MCP_` 前缀，`test_mcp_config.py` 已钉的行为），这里只断言"从配置文件读到了"。
  - **配置接到运行上**（`test_config_wiring.py`，`TICKET_144`）：
    - `[limits]`：`max_budget_usd`、`max_turns.dbt/query` 进选项的 `max_budget_usd`（未定价模型时）或 spend 上限、`max_turns`；`effort` 进选项；`agent_timeout_sec` 进 deadline（事件或选项里可观测的那一处，本工单定）。
    - `[sandbox]`：`backend`、`network`、`exec_max_seconds` 等在 MCP server 参数或沙箱设置里可观测的部分。
    - `[policy]`：`dbt_target = "scratch"` 时 `--dbt-target task` 被拒绝（退出码 2，stderr 点名 `policy.dbt_target`，没有会话）；`network = false` 时 `--network` 同样被拒绝。`disabled_models` 里的模型被 `[roles]` 选中时报错（spec 把 `[policy]` 整节排给 144）。
    - 产品默认：不给任何配置文件、不带 `--config` 时 `data-agent run` 能跑到截停，`version.layers` 全开（今天 argparse 要求 `--config`，会失败）。网页表单的运行（不带 `config` 字段）层开关全开（契约第 4、5 项）。
    - 守门（同一文件，不标记，现在就通过）：对话一轮和 IM 一轮的层开关是 `full`。对话用 `create_app` 加记参数的 `start_run`，`POST /api/conversations/{id}/messages`（照 `test_mcp_web.py` 的做法）；IM 走同一条路（`im/adapter.py:302` 调 `conversations.send`，不传 `network` 和配置），用 `Conversations.send` 直接驱动。记下的参数交给进程内的 `cli.main` 截停，从 `version.layers` 读层开关，不直接比 `--config` 的值（今天是 `--config full`，`conversations.py:45`、`:138`；143 之后换成文件，这条守门照样成立）。
    - 网页和 IM 的 effort、agent 超时从配置来：配置写了 `[limits].effort` 时，表单运行和对话一轮的子进程参数带上它。
    - `[server]`：`data-agent web` 不带 `--runs-dir`、`--port` 时用 `[server].runs_dir`、`port`，带了时参数赢（照 `test_mcp_web.py` 的做法 monkeypatch `create_app` 和 `uvicorn.run`，读交给 `uvicorn.run` 的 `host`、`port`）。`host` 只断言默认是 `127.0.0.1`；非本机地址要令牌是 cloud-deploy 153 的，这里不测。
    - `[trace]`：`tier` 是 `--trace-tier` 的默认值（`cli.py:318-323`），`base_url` 代替 `DATA_AGENT_LANGFUSE_BASE_URL`（`tracing.py:35-38`）；公钥、私钥仍从环境变量读。
    - `[sources.*]` 的 `query_timeout_seconds`、`max_rows`、`duckdb.memory_limit` 等在接缝上能观测的（例如 MCP server 参数或环境）就断言；观测不到的写明交给 144 的单元测试。
  - **模型**（`test_config_models.py`，`TICKET_145`）：
    - `[roles].main` 指一个 `[models.x]`：选项的 `model`、`sdk_env` 的 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`（`agent.py:1256-1261`）来自它；`api_key = "${VAR}"` 展开后只进选项的 `env`，不进事件文件。
    - `context_window`、`compact_window` 进 `CLAUDE_CODE_MAX_CONTEXT_TOKENS`、`CLAUDE_CODE_AUTO_COMPACT_WINDOW`（今天写死在 `agent.py:1280-1281`）；省了是 1000000、400000。
    - `[roles].small` 进 `ANTHROPIC_SMALL_FAST_MODEL`、`ANTHROPIC_DEFAULT_HAIKU_MODEL`；`verifier` 进验证器的模型（`verifiers.py:103` 现在是 `"inherit"`）或 `CLAUDE_CODE_SUBAGENT_MODEL`（`agent.py:1270`），由本工单按 145 能做到的写法定；省了都等于 `main`。
    - 价格：`[models.x]` 写了价格时 spend 上限按它算（`sdk_budget_usd` 的缩放，`agent.py:420-430`）；没写价格时选项的 `max_budget_usd` 是 `None`（不传 SDK）、运行开头有那条警告（契约第 7 项）、`result` 的花费记"未知"（字段写法由本工单定）。
    - `efforts`：写了取值时，`[limits].effort` 不在里面就报错；省了不校验（今天 `--effort` 只收 `low/high/max`，`agent.py:140`）。
  - **快照**（`test_config_snapshot.py`）：
    - 守门（不标记，现在就通过，之后每张工单都要保持通过）：每个榜单一道样本题，照评测入口自己的方式驱动，截下选项和任务提示词，规范化后和 `tests/fixtures/product_config/` 里从当前 main 录下的快照逐项相等。榜单与驱动方式：
      - data-eng-bench：`evals/harbor_agent.py:379-406` `_run_command` 拼出的命令（照 `test_mcp_eval_unchanged.py:66` 直接构造 agent 读字符串），按 shell 规则拆成参数，路径换成测试的工作区和数据库，交给进程内的 `cli.main`。
      - LiveSQLBench：`_query_command`（`harbor_agent.py:408-433`），同上。它要 Postgres：`make check` 里没有 Postgres（`seam_support.POSTGRES_URL_ENV` 的用例都是集成测试），本工单定怎么在不联网的单元测试里拿到它的快照（例如值字典的构建在截停前要连库，见 `agent.py:2305-2316`），做不到就停下交回。
      - DAB：`evals.dab.run_query`，照 `test_chat_eval_unchanged.py:144-152` 驱动（DAB 自己的系统提示词、MCP server，`dab.py:515-530`）。
      - jaffle：`evals/jaffle.py:355-363` 拼的命令（要 Postgres，处理同 LiveSQLBench）。
      - 层：每个榜单至少 `full`；另外录一份 `baseline` 和 `full-old-skill`（Skill 层关、Skill 目录换版本的两个极端）。录几个由本工单定，写明理由。
    - 快照的内容（Q5）：系统提示词全文、任务提示词全文、`tools`、`allowed_tools`、`disallowed_tools`、`permission_mode`、`mcp_servers`（命令和参数）、`skills`、`plugins`、`setting_sources`、`strict_mcp_config`、`max_turns`、`max_budget_usd`、传给 CLI 的 `env`（键和值），以及 `version` 事件的 `skill_version`、`skills`、`config`、`layers`。
    - 规范化只换掉每次运行都不同的部分（`tmp_path`、仓库路径、`sys.executable`、运行 id、时间戳、测试用的假密钥），规则写在 `product_config_support.py` 一处，注释写明每条为什么。不许把内容规范化掉：提示词文字、工具清单、环境变量的键、上限的数值都原样比。
    - MCP server 的参数今天带 `--config <消融名>`（`agent.py:1621`），迁移后会变成配置文件路径；快照怎么比这一项由本工单定，人审。
    - `TICKET_146`：同样的驱动方式下，评测入口给 `data-agent run` 的参数里有两个 `--config`，依次是 `evals/profiles/<榜单>.toml` 和 `evals/profiles/layers/<层名>.toml`，`version.config` 是层名，且快照仍和录下的相等。另一个 `TICKET_146` 测试：`run --config full`（旧消融名）被拒绝，退出码 2（143 期间仍接受，146 删掉）。
    - lsb-single-shot（不经 `data-agent run`）只从 `livesqlbench.toml` 读 `[models]`/`[roles]`，不套层文件（Q8）：不在本组，交给 146 的单元测试。
  - **评测隔离**（`test_config_eval_isolation.py`，不标记，现在就通过）：使用者有 `~/.data-agent/config.toml`（改了 `[layers]`、`[limits]`、`[prompts]`，挂了 `[mcp]` 服务器、`[skills].dirs`、`[plugins].dirs`、`[instructions].files`）、项目级 `.data-agent/config.toml`、`DATA_AGENT_CONFIG` 指向使用者的文件时，照快照组的方式驱动每个评测入口，规范化后的快照和录下的相等。今天没有代码读这些文件和变量，所以现在就通过；它防的是 143–148 之后评测入口读到使用者的配置。显式 `--config` 优先于 `DATA_AGENT_CONFIG`（Q8）。
  - **记录**（`test_config_event.py`，`TICKET_146`）：
    - 一次 `run --config <文件>`（文件里有 `api_key = "${DATA_AGENT_TEST_KEY}"` 一类引用，变量设了假值）：事件文件的第二条是 `config` 事件（第一条仍是 `version`），含生效配置全文（引用是 `${VAR}` 原文）、sha256（等于对全文重算的值）、文件列表、Skill 目录每个文件的哈希；事件文件全文没有假密钥值。
    - `evals.ablate.check_request`（`evals/ablate.py:413-440`）：同一个 `--out`，生效配置哈希不同时拒绝续跑；相同时照旧。参数怎么加由本工单定（今天的签名在 `:413-420`）。
  - **Skill、MCP、插件**（`test_config_skills.py`，`TICKET_147`）：
    - 产品默认（不给配置文件）的查数运行：选项的 `skills` 不是 `[]`（今天 `agent.py:1721`），`plugins` 有内置 `skills/` 的本地插件；系统提示词有 `sql-workflow` 的全文、没有 `duckdb-sql` 的全文。dbt 运行同理：只有 `dbt-workflow` 的全文。对话一轮同样。
    - `[skills].disabled` 里的 Skill 不在 `skills` 里；`[skills].preload` 的全文进系统提示词。
    - `[skills].dirs` 的目录作为本地插件出现在 `plugins`；`[plugins].dirs` 同样，`enabled` 为空是全部。
    - `[mcp].servers` 的服务器进 `mcp_servers`、放行清单进 `allowed_tools`；`claude_code_config = true` 时读假家目录里使用者的 Claude Code MCP 配置，服务器默认放行全部工具、可用 `[mcp]` 按服务器名补 `allowedTools` 收窄（spec"已定"1）；产品运行 `strict_mcp_config` 不再是 `True`，但 `mcp_servers` 只有这两处来源（假工作区里放一个 `.mcp.json`，它的服务器不出现）。
    - 不变：`Bash` 在 `disallowed_tools`、`setting_sources` 不含 `project`。
    - 插件名、Skill 名的写法（`data-agent:sql-workflow`）、插件清单的格式以 142 的结论为准。142 没合入时本组照 spec 写，名字写成一处常量，最终回复里注明待 142 核对。
    - 插件清单不计入 Skill 版本哈希：同一个 Skill 目录，加上插件清单文件（如 `.claude-plugin/plugin.json`）前后 `skill_version`（`agent.py:1289-1296`）相同（今天哈希目录下的每个文件，会失败）。
    - 不断言 Skill 文本（例如 dbt-workflow 第 2 步）有任何改动：Q8 把它推到迁移第二步。
    - Skill 工具每次调用写一条事件：截停接缝上没有模型调用，测不了；写明交给 147 的单元测试和主会话的真实运行。
  - **提示词与业务上下文**（`test_config_prompts.py`，`TICKET_148`）：
    - 产品默认的查数任务提示词不含现在的 `ANSWER_RULE` 全文（`agent.py:212-218`），含产品版（由 148 写；测试只断言旧文不在、`[prompts].answer_rule` 写了时用写的那段）。
    - `[instructions].files` 的内容进系统提示词；`[sources.x].description` 进提示词；`[sources.x].docs` 的目录出现在工作区（复制或只读挂进，148 定，测试只断言工作区里能读到那份文件）。
    - 产品 dbt 任务提示词没有 `DB_TYPE` 那句（`agent.py:294-295`）；任务文件提示（`TASK_FILE_NOTE`，`agent.py:309-313`）在产品 dbt 运行里总是有，层开关关了验证器也有（今天只在验证器开时加，`agent.py:2301`）。
    - 评测那一侧（`DB_TYPE` 只在 data-eng-bench；DAB、LiveSQLBench、jaffle 的答题规则都照现在，Q8）由快照守门组保证，这里不重复。
- **spec 里的解读**（本工单的处理，最终回复里列出供人审）：
  1. 快照里 MCP server 参数的 `--config` 一项怎么比（今天是消融名，迁移后是路径）。
  2. LiveSQLBench、jaffle 的快照离不开 Postgres 时的做法。
- **已有验收测试**（Q8 人定第 1 条授权本工单改）：下面 24 个已合入的验收文件用到本 spec 要去掉的接口（`--config <消融名>`、`--data-sources`、`--mcp-servers`、`--im`、`load_config`、`CONFIG_NAMES`）：`test_seam_a_run_cli.py`、`test_seam_b_mcp_server.py`、`test_seam_c_eval_cli.py`、`test_chat_api.py`（契约 `:60-63`）、`test_chat_e2e.py`、`test_chat_eval_unchanged.py`、`test_web_api.py`（`:330`、`:354-357`）、`test_im_config.py`、`test_im_steer_cancel.py`、`test_mcp_config.py`、`test_mcp_e2e.py`、`test_mcp_eval_unchanged.py`、`test_mcp_launch.py`、`test_mcp_web.py`、`mcp_support.py`、`test_charts_server.py`、`test_charts_switch.py`、`test_observability_evals.py`、`test_observability_relay.py`、`test_observability_switch.py`、`test_livesqlbench_entry.py`、`test_skill_improve.py`、`skill_improve_support.py`、`test_verifier_v2.py`（`grep -rln '"--config"\|load_config\|--data-sources\|--mcp-servers\|"--im"\|CONFIG_NAMES' tests/acceptance` 得这 24 个）。
  - 改法：产品入口的调用改成配置文件（放 `tests/fixtures/product_config/`，或测试里写进 `tmp_path`）：`--data-sources F` 改成 `[sources.*]`、`--mcp-servers F` 改成 `[mcp]`、`--im F` 改成 `[im.*]`，`--config <消融名>` 改成一份带 `[layers]` 的配置文件；评测入口（`test_chat_eval_unchanged.py`、`test_mcp_eval_unchanged.py`、`test_observability_evals.py`、`test_livesqlbench_entry.py` 等驱动 Harbor、DAB 的）照评测入口自己的方式，`load_config(<名>)` 改成新的读法。断言的意思不变：比的是同一件事、同一个值。
  - 冲突的断言按 Q8 和 spec 改：`test_web_api.py:330`（`/api/options` 有 `full`）、`:354-357`（表单能选 `baseline` 并带 `--config baseline`）按契约第 5 项改；`test_chat_api.py:60-63` 契约里的 `--config full`、`--data-sources FILE` 按契约第 4 项改；`test_seam_a_run_cli.py:8-9` 的接口说明同步改。其余发现的冲突同样处理，逐条列出。
  - 改过的调用今天跑不通（新接口还没有）：这些测试标实现那条接口的工单的标记（多半是 `TICKET_143`；只有评测入口两次 `--config` 才成立的标 `TICKET_146`），预期失败的规矩同上（经辅助函数先断言再取值，只接受 `AssertionError`）。这期间它们原来守的行为暂时没有守门，最终回复按文件列出哪些测试从 PASSED 变成 XFAIL、各由哪张工单转回。
  - 最终回复逐文件列出：改了哪几行、改前改后的调用、断言是否原样、标了哪个标记。
- 只用 uv、pnpm。每个 worktree 用自己的 `runs/` 目录。

## Acceptance

- [ ] spec "验收测试接缝"五组每一条都有断言，落在 Context"各组要断言的行为"写的文件里；"已定的接口"和契约第 1–7 项写在相应测试文件的 docstring 里。
- [ ] 24 个已有验收文件按 Context"已有验收测试"改完：`git diff main -- tests/acceptance` 里，已有文件的改动只涉及调用方式、冲突断言和标记；最终回复有逐文件清单。
- [ ] `TICKET_143` … `TICKET_148` 标记的测试当前都以预期失败通过检查，没有被跳过的；守门测试（快照组的守门、评测隔离组、对话与 IM 层开关今天已是 `full` 的那条）当前都通过、不带标记。
- [ ] 每个预期失败的测试，失败原因是功能还没做，不是夹具没起来、import 失败或 argparse 不认识参数。最终回复给每个标记贴一个 `--runxfail` 跑出的真实失败原因。
- [ ] 临时写一个参考实现（不提交，结束后 `git status src/ evals/` 只剩空壳 `config.py`），证明去掉标记后至少 `TICKET_143` 一组能通过；每组至少一个反向检查会失败，例如：列表拼接了；`[policy]` 被项目级覆盖；显式文件时还读了用户级；`config` 事件里出现了假密钥值；评测入口读了假家目录里的配置（会让守门组失败）。
- [ ] 快照守门组：录快照用的命令写进 `product_config_support.py` 的注释，换一台机器（或 CI 的 ubuntu）重跑仍然相等；本机和 `python:3.12-slim` 容器（照工单 97）各跑一次。
- [ ] 夹具和测试里没有密钥、主机地址：`uv run python -m evals scrub tests/fixtures/product_config` 0 处改动，pre-commit 的密钥与公网 IP 扫描通过。
- [ ] `git diff main --stat -- src/` 只有新文件 `src/data_agent/config.py`。
- [ ] `make check` 通过。

## Evidence required

贴进最终回复的 Comments：`make check` 输出（`tee` 存文件）；`uv run pytest tests/acceptance/test_config_*.py -v` 的输出（每个测试是 XFAIL 还是 PASSED）；每个标记的 `--runxfail` 失败原因；参考实现与反向检查的结果；脱敏命令输出；契约第 1–7 项、"spec 里的解读"第 1–2 条、"已有验收测试"的逐文件清单（改了哪几行、改前改后、标记、哪些测试从 PASSED 变成 XFAIL）。

真实运行：本工单不调模型，没有要主会话跑的。

## Stop conditions

- 某条行为在约定接缝上测不了，或要改 Authority 以外的实现代码才能测（例如快照组拿不到任务提示词、LiveSQLBench 或 jaffle 的快照离不开 Postgres）。
- 快照守门组或评测隔离组写出来现在就不通过。
- 断言需要一个人还没定的选择，且不属于契约里由本工单提议的细节，也不在 `decisions.md` Q8 里。
- 某个已有验收测试改成新接口后，断言的意思保不住（不是调用方式的问题）。
- spec 自相矛盾到写不出一致的测试。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2（第 1 个会话被 print 模式的后台等待上限截停，第 2 个接续）
- Review rounds: 2
- Model spend: $0
- Outcome: in-review；`python:3.12-slim` 容器那次由主会话在服务器上补
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：product-config spec 的验收测试工单，人审后合入；143–148 照它转绿。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：加改写 24 个已有验收文件；`--config` 可重复、过渡期接受旧名、`config` 事件第二条、层键 `skill`、`[policy]` 冲突报错、没价格不传 SDK 上限、`[server]`/`[trace]` 归 144、插件清单不计入哈希、Skill 文本改动推到迁移第二步。
- 2026-11-27（工单 agent，opus-5.5，接续会话；分支 `worktree-141-config-acceptance-tests` HEAD `e7d9233`）：
  **1. 核对和补完已有验收文件**
  - 18 个未提交的改动逐个核对过：都只改了调用方式、加了标记，断言意思没变。
  - 另外发现并修了几处：
    - `run_config.load_config(<名>)` 和 `CONFIG_NAMES` 还留在 `test_verifier_v2.py`、`test_observability_switch.py`、`test_observability_relay.py` 里。146 会删掉这两个名字，而这些文件之后没人能改，所以本工单改成 `product_config_support.run_config_of(<名>)`：把层文件读成生效配置，再交给 `load_config`。这是照 143 的 Authority："load_config 改成从生效配置得出 RunConfig"。
    - `test_mcp_config.py` 有两处断言 `strict_mcp_config is True`，和 spec"产品运行不再设 strict_mcp_config"冲突，按新决定改了（见文件表）。
  - 24 个文件里，剩下 5 个不用改，理由见文件表末尾。

  **2. 第 3 步：检查和真实运行**
  - `make check`（合并 main 之后）：通过，exit 0。
    - pytest：3399 passed，3 skipped，190 xfailed。3 个 skip 原来就有，main 上也是 3 个。
    - basedpyright：0 errors。web：448 passed。
  - `pytest tests/acceptance/test_config_*.py`：14 个 PASSED（守门测试），83 个 XFAIL。
  - 全部非集成验收测试：279 passed，190 xfailed，0 个 XPASS。
  - 脱敏：`python -m evals scrub tests/fixtures/product_config` 报 `changed 0 file(s) of 16`。pre-commit 的密钥扫描通过。
  - `git diff main...HEAD --stat -- src/ evals/` 只有新文件 `src/data_agent/config.py`。参考实现放在 `/tmp/141-ref/repo`，没提交。
  - 真实运行：本工单不调模型，没有要跑的。

  **3. 参考实现与反向检查**
  - 新测试：上一会话做了，结果在 `runs/141/reverse.txt`、`reasons-after-143.txt`。
    - 列表拼接、`[policy]` 被项目级覆盖、显式文件时还读用户级、事件里出现假密钥、评测入口读了用户配置、评测答题规则变了、对话轮次跑成 `no-verifiers`：每一种都让对应测试失败。
  - 改过的已有文件：本会话把它们放进 143 的参考实现，用 `--runxfail` 跑。
    - 结果 177 passed，7 failed。
    - 失败的 7 个全是 `TICKET_146`，原因都是 `no evaluation layer file …/evals/profiles/layers/full-old-skill.toml yet (ticket 146)`。
    - 所以标 `TICKET_143` 的改动在 143 做完后都能转绿。

  **4. 每个标记今天的 `--runxfail` 失败原因（改过的已有文件，107 个，全是 AssertionError）**

  | 标记 | 今天的失败原因 |
  |---|---|
  | `TICKET_143` | `mcp-server` 进程类：`data-agent does not take ['mcp-server', '--config', '…/layers/<名>.toml', …]` |
  | `TICKET_143` | 进程内 `run` 类：`expected one session, got 0`，或 `stderr does not name '<server>'`（argparse 拒绝 `--config <文件>`） |
  | `TICKET_143` | 进程内 RunConfig 类：`the config loader did not merge …/layers/full.toml` |
  | `TICKET_143` | `web` 类：`data-agent web exited 2`；`create_app takes no config`；`the turn's --config is not a file: full` |
  | `TICKET_144` | `'configs' not in {… 'configs': ['baseline', …]}` |
  | `TICKET_144` | `{"detail":[{"type":"missing","loc":["body","config"]…` |
  | `TICKET_146` | `no evaluation layer file …/full-old-skill.toml yet (ticket 146)` |

  - 新测试各组的失败原因见 `runs/141/runxfail-today.txt`，全是功能缺失。
  - 本会话新加的测试 `test_a_form_body_that_still_names_an_ablation_runs_the_servers_config`：今天因为表单照用了 `baseline`，层开关全关，`{'skill': False…} == {'skill': True…}` 失败。

  **5. 两道审查**

  `da-review main`（Codex），3 条：
  - **修了**：`served()` 的 monkeypatch 漏到第二次调用（第二次包住的是第一次的替身）。改成每次调用用 `monkeypatch.context()`。修前用探针复现了失败（`created` 只剩 `args`/`kwargs`），修后通过。
  - **修了**：`test_a_run_without_any_config_has_every_layer_on` 原标 `TICKET_144`，但 143 一做完它就会 XPASS。改标 `TICKET_143`，143 参考实现上已转绿。
  - **不修**：快照里的 Skill 哈希比的是"被哈希的是哪个目录"，看不出脚本内容的改动。理由：守门比的是同一份 checkout 上新旧两套机制；如果录成固定的内容摘要，之后任何正当的 Skill 修改（比如 skill-improve）都会让守门永久失败，而之后的工单不许重录。

  `eng:code-review main`（新上下文，Opus）：
  - **修了**：上面两条它也报了。
  - **修了**：两个 `TICKET_145` 测试依赖 144 的 `[limits]` 接线，但 145 只被 143 挡着。改成：
    - 价格测试用默认 $1 预算，断言 `1.0 * 3.125`；
    - effort 在取值范围内时只断言运行能启动。
  - **修了**：旧测试的表单请求仍带 `"config": "full"`。契约第 5 项写明"多余的 `config` 字段收下但忽略"，并新加了上面那个用 `baseline` 的 `TICKET_144` 测试来钉住它。
  - **列给人审、不改代码**：
    - `run_config_of` 依赖的接口（第 5 节契约表最后一行）；
    - `profile_path` 要求容器里的路径带 `profiles/`，`harbor_agent` 用 `run_config=` 参数（这两条约束交给 146）；
    - DAB 只录了 `no-context`（`run_query` 拒绝 `full`）；
    - 测试辅助函数有重复，`product_config_support.py` 长达 1163 行。

  第二轮审查（只看 `65b858c`），3 条：
  - **修了**：effort 的说明写得比测试实际断言的多。已改说明，并写明这是已知缺口。
  - **修了**：多余 `config` 字段的契约没有测试。已加测试。
  - **交主会话**：143 工单的 Authority 没列 `test_config_wiring.py`（见"需要主会话处理"第 1 条）。

  修复用了 2 轮，到上限。

  ### 契约第 1–7 项（全文在各测试文件的 docstring 里，供人审）

  | 项 | 内容 | 写在哪 |
  |---|---|---|
  | 1 加载函数 | `load_effective_config(paths, environ, cli_overrides, workspace=None) -> EffectiveConfig`；`.value(key)`、`.source(key)`（`default` / 文件路径 / `env:<VAR>` / `cli` / `policy`）、`.files`、`.text`、`.sha256`；异常类 `ConfigFileError(path, key)` | `test_config_load.py` |
  | 2 CLI | `config show [--config F]... [--workspace D]`，每行 `<键> = <TOML 值>  # <来源>`；`run --config` 可省、可重复 | `test_config_cli.py` |
  | 2 补充 | `version.config` 是最后合并的那个文件去掉 `.toml` 的名字，没有文件时是 `default` | `test_config_cli.py` |
  | 3 旧参数 | 五个旧参数由 argparse 拒绝；`[mcp].file` 指向 Claude Code 格式的 JSON；`[im.<平台>]` 的键照旧 | `test_config_cli.py` |
  | 4 网页拿哪份配置 | `create_app(..., config=EffectiveConfig\|None)`；每次运行带 `--config <服务器写的文件>` | `test_config_wiring.py` |
  | 5 网页接口 | `/api/options` 没有 `configs`；`POST /api/runs` 不收 `config`，收到也忽略 | `test_config_wiring.py` |
  | 6 `config` 事件 | 字段 `config`、`sha256`、`files`、`file_hashes`，是第二条事件 | `test_config_event.py` |
  | 7 没价格时的警告 | 写进 stderr，同时记一条 `warning` 事件；`result` 事件记 `cost_basis: unknown` | `test_config_models.py` |
  | 本会话新增（接 1） | `run_config.load_config(<EffectiveConfig>)` 返回 RunConfig；143、146 要保留这个签名 | `product_config_support.run_config_of` |

  ### spec 里的解读（供人审）
  1. **快照里 MCP server 参数的 `--config`**：照 `config_arg_rule` 的规则比，规则写在 `product_config_support.py` 里（cb655f7）。
  2. **LiveSQLBench、jaffle 离不开 Postgres**：截停前用 DuckDB 的值字典代替（`dictionary_from_duckdb`），快照照录（cb655f7）。
  3. **本会话新增，argparse 拒绝**：改过的已有测试，今天的失败原因就是"argparse 不收 `--config <文件>`"，因为缺的功能本身就是这个。验收里"不是 argparse 不认识参数"这条按新测试来理解；这些改过的测试由参考实现证明以后能转绿。

  ### 逐文件改动（24 个）

  | 文件 | 改前 → 改后 | 断言 | 标记（PASSED→XFAIL 个数） |
  |---|---|---|---|
  | `test_seam_a_run_cli.py` | `--config full` → `--config layer_config(<名>)`；接口说明同步改 | 原样 | 143（集成测试，5 个；今天跑过，都是 XFAIL） |
  | `test_seam_b_mcp_server.py` | `mcp-server --config <名>` → 层文件；先 `assert_cli_accepts` | 原样 | 143；`full-old-skill` 的 3 个是 146（30） |
  | `test_charts_server.py` | `--config no-context` → 层文件 | 原样 | 143（21） |
  | `test_charts_switch.py` | 同上；加了 `assert len(seen) == 1` | 原样；DAB 改成同步调用 | 143（3） |
  | `test_chat_api.py` | `--data-sources FILE` → `--config FILE`，读里面的 `[sources.*]`；契约说明同步改 | 意思不变（名字、`env`、路径） | 143（1） |
  | `test_chat_e2e.py` | `web --data-sources` → `--config`；场景 f 换成层文件 | 原样 | 143（集成测试，24 个，都是 XFAIL） |
  | `test_chat_e2e.py` 夹具 | 服务器起不来时记下 `start_error`，在 `call()` 里断言 | 原样 | 夹具改动：没有它，预期失败会报成夹具错误 |
  | `test_chat_eval_unchanged.py` | Harbor 的参数改从 `_run_command` 取；DAB 改走 `python -m evals dab --config no-context` | 原样 | 不标（仍通过） |
  | `test_web_api.py` | `:330`、`:354-357` 是冲突断言，按契约第 5 项改 | 冲突断言按新决定改 | 144（2） |
  | `test_im_config.py` | `web --im F` → `--config` 带 `[im.*]` 的文件 | 原样 | 143（2） |
  | `test_im_config.py` `serve()` | 加假 HOME；`SystemExit` 转成 AssertionError | 原样 | 夹具改动：隔离用户配置，并让预期失败落在断言上 |
  | `test_mcp_config.py` | `--mcp-servers` → `[mcp].file` | 原样，除下一行 | 143（17） |
  | `test_mcp_config.py` `:156`、`:292` | 和 spec 冲突，改成"和不挂服务器的运行相同" | 冲突断言按新决定改 | — |
  | `test_mcp_e2e.py` | `--mcp-servers` 和 `--config full` → 两个配置文件 | 原样 | 143（集成测试，2 个；今天 mongo 夹具超时报 ERROR，夹具没改过） |
  | `test_mcp_eval_unchanged.py` | 层文件；DAB 改走命令行 | 原样 | 143（1） |
  | `test_mcp_launch.py` | 层文件；`--data-sources` → `--config` | 原样 | 143（2） |
  | `test_mcp_web.py` | `create_app(mcp_servers=)` → `config=`；`web --mcp-servers` → `--config`；`mounted` 夹具改成在测试里建 | 意思不变 | 143（4） |
  | `mcp_support.py` | `run_args` 改用层文件和 `[mcp]` 文件；新增 `config_file`、`assert_cli_accepts` | — | — |
  | `test_observability_evals.py` | DAB 改走命令行（`--runs 2 [--trace]`），环境变量改用 monkeypatch | 原样 | 不标（仍通过） |
  | `test_observability_switch.py` | `run --config no-context` → 层文件；`load_config(…)` → `run_config_of(…)` | 原样 | 143（17，含原来"导出关闭"的守门） |
  | `test_observability_relay.py` | `load_config("full")` → `run_config_of("full")` | 原样 | 143（2） |
  | `test_verifier_v2.py` | `CONFIG_NAMES`、`load_config` → `RUN_CONFIG_NAMES`、`run_config_of` | 原样 | 143 ×2；含 `full-old-skill` 的 4 个是 146（5） |
  | `test_seam_c_eval_cli.py`、`test_skill_improve.py`、`skill_improve_support.py` | 不改：那里的 `--config <名>` 是评测入口（`board`、`ablate`），spec 定消融名只在评测入口认 | — | — |
  | `test_livesqlbench_entry.py` | 不改：`--mcp-servers` 只出现在"命令里没有它"的否定守门里 | — | — |
  | `test_im_steer_cancel.py` | 不改：`--data-sources` 只出现在给假对象的拒绝文字里，是输入 | — | — |

  - PASSED→XFAIL 一共 107 个：143 转回 98 个，144 转回 2 个，146 转回 7 个。完整清单在 `runs/141/passed-to-xfail.txt`，不含集成测试。

  ### 需要主会话处理
  1. 143 工单的 Authority 加上 `test_config_wiring.py::test_a_run_without_any_config_has_every_layer_on` 的 `TICKET_143` 标记，否则 143 删不掉它。
  2. `test_im_config.py` 钉着 `im.config.load_config(path, environ)`。143 的 Authority 写着要把解析函数改成吃配置里的那一节，所以 143 要保留这个签名（或者人另定）。
  3. 已知缺口：`[limits].effort = "medium"`（今天的 `--effort` 不收这个值）最终会不会出现在 options 里，没有测试，要 144 和 145 都合入才测得到。写进后续工单的验收标准。
  4. `python:3.12-slim` 容器那次运行：Docker 磁盘清出空间后跑 `runs/141/linux-pytest.sh <导出目录>`。
- 2026-11-27（主会话）：PR #149 CI 绿（ubuntu）。服务器上 `python:3.12-slim` 容器（导出挂在 `/work`）：4 failed / 10 passed；服务器普通 checkout 14 passed；挂在 `/t141src` 时 14 passed。日志在主会话 scratchpad，结论见下面第 1 条。合并前审查（上下文干净的 Opus 子 agent）结论"修完再合"，无阻塞项；Codex GitHub app 1 条 P2（Skill 快照没有内容指纹）。
- 2026-11-27（主会话）：**退回（第 1 轮）：原因如下，在原分支 `worktree-141-config-acceptance-tests` 上接着修**。决定依据 `decisions.md`"调研后定"。
  1. **路径替换不认边界**：`product_config_support.py` 的 `normalize_text`（约 :445-452）用 `str.replace` 把仓库路径换成 `<repo>`，checkout 在 `/work` 时把 `/workspace` 换成 `<repo>space`（容器里 dab-no-context、jaffle-full 的快照与隔离守门共 4 个失败）。改成认路径边界的替换（`/tmp` 同理）；修完后快照守门在 `/work` 挂载的容器里要通过（主会话在服务器上复跑）。
  2. **Skill 指纹**（Codex P2；`decisions.md`"调研后定"第 4 条）：快照记过滤后的真实内容哈希（排除 `__pycache__`、`*.pyc`、gitignore 的文件，照 Harbor），不再退成目录标签。之前"之后的工单不许重录"这个不修理由已不成立：重录归主会话。
  3. **`file_hashes` 的期望集合**（`test_config_event.py:92`）：排除 `__pycache__`、`*.pyc`，同上。
  4. **重录规则写进注释**（`product_config_support.py:25`、`test_config_snapshot.py:4-5`）：守门只证明迁移第一步新旧一致；146 合入后由主会话在迁移第二步或有意的 Skill 改动时重录，审 diff、记进工单。
  5. **契约第 5 项**：不钉"多余的 `config` 字段静默忽略"。删掉 `test_config_wiring.py:276` 那条测试；旧表单（`test_chat_api.py:107`、`test_mcp_web.py:64`、`test_web_api.py` 的辅助）里删掉 `config` 字段；这个字段的处理不钉。
  6. **每次运行的配置文件里没有展开的密钥**：在已标 `TICKET_143` 的测试里加 `GITHUB_TOKEN` 不在运行的 `--config` 文件文本里（`test_mcp_web.py:196` 附近；`test_chat_api.py` 同理查连接串）。
  7. **143 之后会变空的守门**（`test_mcp_web.py:200`）：加"`--config` 是文件时，它的 `mcp.file`、`mcp.servers` 没设"，今天通过、143 之后仍有意义。
  8. **数据服务器 `--config` 的旁路断言**（`config_arg_rule`，`product_config_support.py:401`）：不重录快照，另断言每个值要么是等于 `version.config` 的消融名，要么是 `evals/profiles/` 或运行临时目录下的路径，不在假家目录或工作区下。
  9. **`docs` 的测试**（`test_config_prompts.py:110-128`；`decisions.md`"调研后定"第 3 条）：改成钉"`docs` 留在工作区外、agent 只读"：不出现在 `--workspace` 下；系统提示词写出 `docs` 路径；`workspace_guard` 放行读、拒绝写。标 `TICKET_148`。
  - 契约逐项清单、spec 解读、逐文件改动表在交回时照上次的格式更新。修复轮次另算：这是 PR 的第 1 轮退回。
