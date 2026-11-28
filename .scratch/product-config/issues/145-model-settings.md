# 145 — 模型：`[models.*]`、`[roles]`、价格可省、上下文窗口、effort 取值、小模型/分类/验证器模型

Status: in-review
Blocked by: 143
Spec: ../spec.md（"分节（Q2）"的 `[models.<名字>]`、`[roles]`）
Claimed by: claude (opus-5-5) 2026-11-28
Branch: worktree-145-model-settings

## Goal

模型设置从配置来，不再只适配 DeepSeek：每个 `[models.<名字>]` 有端点、`${VAR}` 密钥、可省的单价、上下文窗口与压缩窗口、可用的 effort 取值；`[roles]` 的 `main`（默认 `deepseek-flash`）、`small`、`classify`、`verifier` 各自选一个模型，省了等于 `main`。现有价目表变成代码默认的 `[models.deepseek-flash]`、`[models.deepseek-v4-pro]`。没填价格的模型花费记"未知"、不传 SDK 的 `max_budget_usd`（没有任何花费上限），运行开头给一条警告，`classify_text` 照常可用。`DATA_AGENT_MODEL`、`DATA_AGENT_BASE_URL` 仍覆盖主模型。141 合入的 `TICKET_145` 验收测试全部转绿（只删标记）。

## Authority

- May edit：
  - `src/data_agent/config.py`：只补 `[models.*]`、`[roles]` 的校验与代码默认值（两个 DeepSeek 模型），不改 143 的加载与合并。
  - `src/data_agent/agent.py`：只改 `Price`、`PRICES`、`SDK_UNKNOWN_MODEL_PRICE`、`priced_usd`、`sdk_budget_usd`（`agent.py:393-430`）、`spend_cap`（`:744-747`）、`ModelSettings`（`:1232-1286`）、`Effort`/`EFFORTS`（`:140-141`）、`build_options` 里 `model=`、`env=` 的模型部分和 `max_budget_usd`（`:1694-1696`、`:1723-1727`）、`run` 里 `ModelSettings.from_env`（`:2162`）和 `result` 的花费字段。
  - `src/data_agent/classify.py`（`setup_from_env` `:177-193`）：分类模型取 `[roles].classify`；未定价模型也能用。
  - `src/data_agent/verifiers.py`（`:103` `model="inherit"`）：验证器模型取 `[roles].verifier`。
  - `src/data_agent/cli.py`：`--effort` 的取值校验改成按模型的 `efforts`（`cli.py:311-315`）。
  - `tests/acceptance/` 里 141 标了 `TICKET_145` 的文件（`test_config_models.py` 等，清单见 141 最终回复）：**只删** `TICKET_145` 标记，以及因此没用的定义和 import。
  - `tests/`（除 `tests/acceptance/`）；`docs/setup.md`（`[models.*]`、`[roles]`，示例只带 `${VAR}`）。
- Must not edit：`tests/acceptance/` 里除 `TICKET_145` 标记以外的一切；`tests/fixtures/product_config/`；`agent.py` 的提示词、Skill/MCP/插件部分、上限与沙箱部分（144、147、148）；`evals/`（146；`evals/lsb_single_shot_agent.py:59-67` import `PRICES`、`priced_usd`、`DEFAULT_MODEL`、`DEFAULT_BASE_URL`，改名或删掉它们要保持这些名字能用，或停下）；`skills/`；`web/`；其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none。
- Spend cap：工单 agent $0（不能 source 模型的 env 文件）。

## Context

行号以 main `e494e49` 为准。

- **审计条目**（`docs/research/2026-11-27-product-config-audit.md` 第 3 节）：
  - 3.1 上下文窗口 1M、压缩窗口 400k 写死在 `sdk_env`（`agent.py:1280-1281`，注释 `:1275-1279` 记了 CLI 版本差异）。spec：省了用代码默认 1M/400k。
  - 3.2 价目表只有两个模型（`agent.py:404-407`）；没价格的模型 `spend_cap` 返回 `None`（`:744-747`），SDK 按它自己的兜底价算（`SDK_UNKNOWN_MODEL_PRICE` `:413`），$1 约 1/30 就停（`docs/plan.md:192`）。
  - 3.3 `classify_text` 没有账本就拒绝（`classify.py:181-187`）；账本只在有 `spend` 时经 `server_env` 给 MCP server（`agent.py:1680-1683`）。
  - 3.4 effort 只收 `low/high/max`（`agent.py:140`）。spec：`efforts` 省了不校验。
  - 3.5 所有模型槽位都是主模型（`sdk_env` 的 `ANTHROPIC_SMALL_FAST_MODEL` … `CLAUDE_CODE_SUBAGENT_MODEL`，`agent.py:1265-1270`；验证器 `model="inherit"`，`verifiers.py:103`）。
- **spec 的写法**：单价是 `input_price`/`cache_read_price`/`output_price`（每百万 token），今天的 `Price` 字段是 `input`/`cached_input`/`output`（`agent.py:393-399`）；`api_key = "${VAR}"`（143 展开）；`[roles].main` 必填、默认 `deepseek-flash`。
- **没填价格**（`decisions.md` Q8）：不传 SDK 的 `max_budget_usd`，运行开头给一条警告（去处和文字照 141 契约第 7 项）。今天没价格的模型仍把 `max_budget_usd` 交给 SDK（`agent.py:1723-1727`），SDK 按兜底价约 1/30 就停（`docs/plan.md:192`）。
- **141 的契约**：花费"未知"在 `result` 事件里怎么写、警告的去处、验证器模型走哪个设置，以 141 最终回复为准。
- **和 144 的分工**：`build_options` 的 `max_budget_usd=`（`agent.py:1723-1727`）两张都碰：144 管预算的值从哪来（`[limits].max_budget_usd`），本工单管按模型价格的缩放和没价格时的处理（`sdk_budget_usd`、`spend_cap`）。
- **评测不变**：评测配置文件（146）写死模型；代码默认值必须让 141 的快照守门组仍相等（`sdk_env` 的每个键和值不变）。
- **并行工单**：143（基础）、144、147、148 都改 `agent.py`，后合入的一方 rebase。
- **数据服务器里的主模型**（143 交回）：数据服务器里 `tool_env()` 给的 `DATA_AGENT_MODEL` 会覆盖 `roles.main`，`DATA_AGENT_BASE_URL` 于是落到 `[models.<模型 id>]` 表上；今天无害，本工单把 `[roles]` 接到模型时核对这一点。数据服务器的临时配置文件已经不带 `[models.*].api_key`（模型设置从 `tool_env()` 来）。

## Acceptance

- [ ] `TICKET_145` 标记全部删掉；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_config_models.py -v` 全部 PASSED，0 个 xfail。
- [ ] 141 的守门组和其他已合入的验收测试照旧通过。
- [ ] 141 留下的缺口（141 Comments"需要主会话处理"第 3 条）：`[limits].effort` 取模型 `efforts` 里的值（如 `medium`）时，这个值进到 SDK options。144 已合入就加单元测试；没合入就写进 Comments，主会话转给后合入的一方。
- [ ] 验收测试没钉的行为有新单元测试：未定价模型的 `classify_text` 能跑（替身模型）且花费记"未知"；`[roles].classify` 的模型真用在分类调用上；`efforts` 校验的报错点名模型和取值；`DATA_AGENT_MODEL` 覆盖 `[roles].main`。
- [ ] `docs/setup.md` 写了 `[models.*]`、`[roles]` 的每个键、默认值，以及没填价格时的后果。
- [ ] `make check` 通过。
- [ ] `[limits].effort` 按 `EFFORTS`（各模型的 `efforts`）校验（144 交回：144 没校验 effort 的取值）。`check_policy` 已拒绝 `policy.disabled_models` 里的 `[roles]` 模型，别重复做。

## Evidence required

`make check` 输出；`test_config_models.py` 的 `-v` 输出；`git diff main -- tests/acceptance | grep '^[-+]' | grep -v '^[-+][-+]'`。不调模型的真实运行（$0）：`data-agent config show` 显示两个代码默认模型和 `[roles]`。

交给主会话（写出命令）：一份 `[roles].small = "deepseek-flash"`、`[roles].main = "deepseek-v4-pro"` 的配置跑一次查数，读事件文件确认 `version.model` 和花费；再用一份没填价格的模型配置（同一端点、别名）跑一次，确认 `result` 花费记"未知"、开头有警告、运行没有被 SDK 兜底价提前停。

## Stop conditions

- 改模型设置会让 141 的快照守门组失败（代码默认值和现在的 `sdk_env` 不一致）。
- 要改 `evals/` 才能保持评测入口能用。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 4（前两次被内存不足中断；退回第 1 轮修复 1 次）
- Review rounds: 4（首轮：da-review 1 轮，eng:code-review 1 轮加 1 次后续修复；退回第 1 轮：da-review 1 轮，eng:code-review 1 轮）
- Model spend: $0
- Outcome: 两条退回项已修，da-review 的 2 条已修；`make check` 通过；(c)(d) 真实运行待主会话代跑
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：审计第 3 节（只适配 DeepSeek 的按模型设置）。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：没填价格时不传 SDK 的 `max_budget_usd`、开头警告；去掉对应的停止条件。
- 2026-11-28（主会话）：按 143 交回补 Context：数据服务器里 `DATA_AGENT_MODEL` 覆盖 `roles.main`。
- 2026-11-28（主会话）：按 144 交回补验收标准：effort 取值校验归本工单。
- 2026-11-28（工单 agent，opus-5-5）：
  1. **继续上次工作**：前两次因本机内存不足中断。上次的提交 `a17f58a` 和未提交的改动都是本工单的，接着做完并提交，没有丢弃。
  2. **第一次 `make check` 失败**：28 个用例失败（`tests/test_config.py`、`test_conversation_turn.py`、`test_multi_source_run.py` 等）。原因是 CLI 提前算模型设置时就要求密钥，而这些测试替换掉了 `agent.run`、不设密钥。修法：`from_config`/`from_env` 加 `require_key`，`cli.model_settings` 传 False，缺密钥照旧由 `agent.run` 的 `check_key()` 拒绝。修完后最后一次 `make check`：`3605 passed, 3 skipped, 81 deselected, 56 xfailed`，web `448 passed`，exit 0。
  3. **证据**：
     - `uv run pytest tests/acceptance/test_config_models.py -v`：6 个全部 PASSED，0 xfail（main_role、context_and_compaction_windows、small_and_verifier_roles、priced_model_scales_the_sdk_cap、unpriced_model_has_no_cap_a_warning_and_an_unknown_cost、models_efforts_bound_the_effort）。
     - `git diff origin/main...HEAD -- tests/acceptance tests/fixtures | grep '^[-+]' | grep -v '^[-+][-+]'`：只有 `TICKET_145 = pytest.mark.xfail(...)` 这 5 行、`pytestmark = TICKET_145` 和一个空行被删。这里用三个点，因为两个点对比 main 会混进工单 164 改的 `--dbt-target` 那几行。
     - $0 真实运行 `HOME=$(mktemp -d) uv run data-agent config show`：显示 `models.deepseek-flash.*`（0.3 / 0.006 / 1.2，efforts `["low","high","max"]`）、`models.deepseek-v4-pro.*`（1.32 / 1.32 / 3.96）和 `roles.main = "deepseek-flash"  # default`。
  4. **交给主会话的两次真实运行**（`--workspace` 和数据源按惯常的 query 任务）：
     - (a) 配置 `[roles]\nmain = "deepseek-v4-pro"\nsmall = "deepseek-flash"\n`，跑 `uv run data-agent run --type query --config <该文件> --workspace <ws> --events-dir runs/145-a --task "<查数问题>"`。读事件文件：`version.model` 应是 `deepseek-v4-pro`，`result` 里 `cost_basis` 是 `price_table`，花费按两模型每项取高价算，验证器的实际模型也看一下。
     - (b) 配置 `[models.ds-alias]\nbase_url = "https://api.deepseek.com/anthropic"\n\n[roles]\nmain = "ds-alias"\n`（同一端点、换个没价格的名字；如果端点不认这个名字，就换一个 DeepSeek 接受的别名）。跑同样的命令，`--events-dir runs/145-b`。确认：stderr 第一行有 `warning: the model ds-alias has no price ...`；事件文件有 `warning` 事件；`result` 里 `cost_basis: "unknown"`、`cost_usd: null`；运行没有被 SDK 兜底价在约 $0.03 处提前停下。
  5. **审查**：
     - **`da-review main`** 给了 3 条，都属实，都已修（`959abd7`）：
       - [P1] 写在 `[models.*]` 里的端点和主机名没打码：现在 `ModelSettings.secrets()` 把 main 和 classify 的非默认端点及主机名加进去，代码默认的 DeepSeek 端点不打码，和以前一样。
       - [P2] 有一项单价是 0 时，SDK 的备用上限会提前把运行停掉：现在有 0 就不给 SDK 备用上限，只剩价格表的上限。
       - [P2] 负数、`nan`、`inf` 的价格被接受：现在运行前退出 2，报错点名哪个键。
     - **`eng:code-review main`** 的 Standards 和 Spec 两路，已修（`55117d5`、`0d36bd4`）：
       - `sdk_budget_usd` 没人调用了，还沿用旧的"没价格就给 budget"规则，和 Q8 相反：删掉，`tests/test_run_config.py`、`tests/test_dab.py` 改断言 `backstop_usd`；`build_options` 文档串里的旧名字一并改了。
       - `PRICES` 的回退写了两遍：抽成 `ModelSettings.model_prices()`。
       - `small`/`verifier` 的表写了和 main 不同的端点或密钥时被悄悄忽略，不符合"拒绝、不悄悄改"：现在运行前退出 2。只比较用户写的值。advisor 发现第一版会误拒代码默认表：比如设了 `DATA_AGENT_BASE_URL`、`main = deepseek-v4-pro`、`small = deepseek-flash`，第二个提交修了，修复前会失败的测试已加。
     - **不修的**：
       - `spend_cap` 还收模型名字符串：`evals/dab.py` 在用，按 Authority 不能改 `evals/`。
       - `classify.py` 里重复的 `ledger is None` 判断：四处都短，换成空对象要改 `tool_spend`，超出范围。
       - effort 现在是普通字符串（`build_options` 里 `cast(EffortLevel, ...)`）：SDK 的类型只列 Claude 的取值，按模型的 `efforts` 校验本来就要求这样，代码注释写了。
       - 改动超出 Authority 列的行号（`server_env` 总是带 `tool_env()`、`run(models=)` 参数、`cli.model_settings` 和 `run_task` 的传递）：这些是把功能接上必需的，在允许的文件里，没碰提示词、Skill、MCP、上限和沙箱部分。
       - `evals/dab.py` 的副作用：模型没价格时 `build_options` 现在给 `max_budget_usd=None`，但 `unknown_cost` 只在 `run` 里用，所以 `result` 仍记 `cost_basis: "sdk"`。评测的模型都有价格，今天没影响；记成已知限制，归 146。
  6. **三条新规则，需要主会话确认**（spec 没写）：
     - 几个角色的模型价格不同时，花费每一项取其中最高价，只会高估。
     - 有一项单价是 0 时不给 SDK 备用上限。
     - `small`/`verifier` 写了和 main 不同的端点或密钥时拒绝，而不是忽略。
  7. **核对 Context**：
     - **数据服务器**：`tool_env()` 现在给的是 classify 角色的模型、端点和密钥，所以数据服务器里 `DATA_AGENT_MODEL` 覆盖 `roles.main`、`DATA_AGENT_BASE_URL` 落到 classify 模型的表上。分类调用用的正是 classify 模型，结果对。数据服务器只经 `classify.setup_from_env` 里的 `ModelSettings.from_env` 读模型，它的 MCP 分支在 `cli.model_settings` 之前就 `return` 了，不受 effort 和端点校验影响。
     - **`[limits].effort`**（144 交回后新加的验收项）：`cli.model_settings` 按主模型的 `efforts` 校验，新测试 `test_a_limits_effort_outside_the_models_efforts_names_the_file_and_the_value`。`policy.disabled_models` 没重复做。
     - **`[limits].effort` 的值进到 SDK options**（141 留下的缺口）：144 还没合入（`origin/main` 上 `5285275` 是 144 in-review），这个单元测试交给后合入的一方。
     - **和 144 的冲突**：两边都改 `agent.py` 的 `build_options` 里 `max_budget_usd=` 那段，本工单已删掉 `sdk_budget_usd`，后合入的一方 rebase。
- 2026-11-28（主会话）：合并前核查：CI 绿；Codex GitHub app 无意见。服务器真实运行（`0d36bd4`，env 加载后 `unset DATA_AGENT_MODEL`）：(a) `main = deepseek-v4-pro` → `version.model` 和转录里全是 `deepseek-v4-pro`，`cost_basis: price_table`（$0.037），答案 37；(b) 无价格模型（用 `DeepSeek-Flash`，端点不认 `ds-alias`）→ stderr 首行和 `warning` 事件都有"has no price"，`cost_basis: unknown`、`cost_usd: null`，正常完成没被 SDK 兜底停下；(c) dbt 任务、`main = deepseek-v4-pro`、`verifier = deepseek-flash` → 两个验证器子 agent 的转录 assistant 消息全是 `deepseek-v4-pro`，代码设了 `CLAUDE_CODE_SUBAGENT_MODEL=deepseek-flash`，CLI 2.1.330 不让它盖过定义里的 `inherit`。花费约 $0.64（(c) 的 `[limits].max_budget_usd = 0.20` 没生效，因为 `[limits]` 接线在 144，未合入）。第 6 条三条新规则人已确认（`decisions.md`"145 实测后定"1–3）。**退回（第 1 轮）：原因如下，在原分支 `worktree-145-model-settings` 上接着修**。
  1. **阻塞：`[roles].verifier` 不生效**：验证器定义写 `model="inherit"`（`src/data_agent/verifiers.py:103`），CLI 不让 `CLAUDE_CODE_SUBAGENT_MODEL` 盖过它，验证器总跑主模型。改成把验证器角色的模型 ID 直接写进 `AgentDefinition.model`（SDK 收完整模型 ID，`claude_agent_sdk/types.py:116`）；没设 `[roles].verifier` 时等于主模型。改正 `agent.py:1464` 附近说"变量盖过 `inherit`"的注释；`CLAUDE_CODE_SUBAGENT_MODEL` 留不留按是否还有用处定、写明理由。单元测试钉住 `options.agents` 里每个验证器的 `model` 等于 verifier 角色的模型。`verifiers.py` 若不在 Authority，照"把功能接上必需"处理并在回复里写明。
  2. **`DATA_AGENT_MODEL` 盖过文件的 `[roles].main` 时打警告**（`decisions.md`"145 实测后定"第 4 条）：stderr 一行 `warning:`（点名变量和被盖掉的值与文件）加一个 `warning` 事件；`config show` 在 `roles.main` 那行标出被盖过；优先级不变。没有文件设 `[roles].main` 时不警告。配修复前会失败的测试。
  - 合并 main 时注意：144（PR #153）在退回第 1 轮修复中，两边都改 `build_options` 的 `max_budget_usd=`，后合入的一方 rebase。
- 2026-11-28（工单 agent，opus-5-5，退回第 1 轮修复）：
  1. **先 merge 了 origin/main**：`3cf6bf1`，修完又合了一次，是 `9a6e71a`。两次都没有冲突。
  2. **退回第 1 条（阻塞）：`[roles].verifier` 不生效**，修在 `ac3affe`。
     - 改法：`verifiers.load_definition` 和 `verifier_agents` 加了 `model` 参数，`build_options` 传 `settings.verifier or settings.model`。每个验证器的 `AgentDefinition.model` 现在是完整模型 ID，没设 `[roles].verifier` 时等于主模型。
     - `agent.py` 里说"变量盖过 `inherit`"的注释已改正。
     - `CLAUDE_CODE_SUBAGENT_MODEL` 留着。理由一：141 的快照守门组（`tests/fixtures/product_config/snapshots/*.json`）钉住了它，去掉就触发停止条件。理由二：它仍给没写模型的子 agent 用。`docs/setup.md` 也写了。
     - 修复前会失败的测试：`test_every_verifier_runs_on_the_verifier_roles_model`，修复前实际值是 `{'inherit'}`。
     - `verifiers.py:103` 本来就在 Authority 里。
  3. **退回第 2 条：`DATA_AGENT_MODEL` 盖过文件的 `[roles].main` 时警告**，修在 `ac3affe`。
     - 打警告：stderr 一行 `warning: DATA_AGENT_MODEL=<值> overrides [roles].main = '<原值>' in <文件>: ...`，再加一个 `warning` 事件，字段是 `variable`、`model`、`overridden`、`file`。
     - `config show` 那行显示成 `roles.main = "x"  # env:DATA_AGENT_MODEL (overrides "<原值>" from <文件>)`。
     - 优先级不变。下面三种情况不警告：没有文件设 `[roles].main`；值相同；命令行又覆盖了它。
     - 测试：`test_data_agent_model_over_the_files_main_role_warns` 和 `test_config_show_marks_the_main_role_the_environment_overrides`，两个修复前都失败。另有 `..._without_a_files_main_role_does_not_warn` 守不警告的情况。
     - **超出 Authority 的改动**：`config.py` 在 `load_effective_config` 里给 `EffectiveConfig` 加了 `overridden` 字段，只记录、不改合并结果。环境变量盖过以后文件里的值就没了，不记录的话警告点不出原值。
  4. **证据**：
     - `make check`：`3620 passed, 3 skipped, 81 deselected, 90 xfailed`；web `448 passed`；`exit=0`。输出存在 `/tmp/145-check.txt`。
     - `uv run pytest tests/acceptance/test_config_models.py -v`：6 个全部 PASSED，0 xfail。
     - `git diff origin/main...HEAD -- tests/acceptance tests/fixtures | grep '^[-+]' | grep -v '^[-+][-+]'`：只有 `TICKET_145 = pytest.mark.xfail(...)` 那 5 行、`pytestmark = TICKET_145` 和一个空行被删。
     - $0 真实运行，配置是 `[roles]\nmain = "deepseek-v4-pro"\n`：
       - `HOME=$(mktemp -d) DATA_AGENT_MODEL=deepseek-flash uv run data-agent config show --config <该文件>` 输出 `roles.main = "deepseek-flash"  # env:DATA_AGENT_MODEL (overrides "deepseek-v4-pro" from <文件>)`。
       - 不设这个变量时输出 `roles.main = "deepseek-v4-pro"  # <文件>`。
  5. **审查**：
     - **`da-review main`** 给了 2 条 P2，都属实，都已修（`40d1a15`），修复前会失败的测试走真实的 `Launcher.config_args` 往返：
       - 网页、对话、IM 运行收到的配置文件写全了所有键，代码默认值也写进去。读回来时 `deepseek-flash` 的默认端点被当成用户写的，于是 `DATA_AGENT_BASE_URL=代理`、`main = v4-pro`、`small = flash` 被拒，退出 2。这是本工单上一轮 `55117d5` 引入的回归。改法：等于代码默认值（`DEFAULT_MODELS`）的 `base_url`/`api_key` 不算用户写的。
       - 网页启动的运行丢了覆盖警告。改法：`web/launcher.py` 的 `config_args` 对被盖过的键写回文件原值。运行子进程继承服务器的环境（`cli.py:778-795`，`im/config.py:139` 只去掉 IM 令牌），所以会重新应用覆盖并警告，模型不变。**`web/launcher.py` 不在 Authority 里**，这是把功能接上必需的改动。
     - **`eng:code-review main`**：
       - Spec 一路：两条退回项都齐了，三处超范围改动判定为必需。另有三条低优先级意见，都不修（见第 6 条）。
       - Standards 一路：没有违反硬规则。两处打警告代码重复，已修（`d431c3d`，抽成 `warn` 函数）。
       - 其余 Standards 意见不修：
         - `from_config` 太长、元组该换成命名类型、`spend_cap` 收字符串、`unknown_cost` 调了两次、`price or ...`：都是前几轮已合进分支的代码，不是本轮改动。其中 `spend_cap` 收字符串是因为 `evals/dab.py` 在用，`evals/` 不在 Authority 里。
         - `DEFAULT_BASE_URL` 和 effort 取值在 `agent.py`、`config.py` 各有一份：`evals/` 从 `agent` 导入这些名字，要合并得同时保证这些名字照旧可用，超出本轮范围。
  6. **已知限制**（不修，写明理由）：
     - 用户在默认模型的表里显式写了默认的 DeepSeek 端点、而 main 用别的端点时，不再拒绝。读回后的配置分不出"写了默认值"和"代码默认"，而且效果和省略一样（setup.md："省了或写得一样就行"）。
     - 网页、对话、IM 运行里，覆盖警告点名的是运行目录里的配置快照（`runs/<id>/config/<名字>.toml`），不是服务器原来的文件。值是对的。
  7. **交给主会话的真实运行**（`[roles].verifier` 只在单元测试层面钉住了 `options.agents`，CLI 2.1.330 认不认定义里的非 Claude 模型 ID 要实测）：
     - (c) 重跑：dbt 任务，配置 `[roles]\nmain = "deepseek-v4-pro"\nverifier = "deepseek-flash"\n`。命令 `uv run data-agent run --type dbt --config <该文件> --workspace <ws> --events-dir runs/145-c2 --task "<dbt 任务>"`。确认验证器子 agent 转录里 assistant 消息的 `model` 是 `deepseek-flash`，主会话是 `deepseek-v4-pro`。
     - (d) 同一任务，配置里不写 `[roles].verifier`（评测现在给定义传显式 ID，以前传 `inherit`）。`--events-dir runs/145-d`，确认验证器跑主模型、能正常完成。
     - (e) 可选：env 里留着 `DATA_AGENT_MODEL`，配置写不同的 `[roles].main`，跑一次查数。确认 stderr 和事件文件里有覆盖警告。
