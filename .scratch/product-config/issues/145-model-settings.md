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

- Attempts: 3（前两次被内存不足中断）
- Review rounds: 2（da-review 1 轮，eng:code-review 1 轮外加 1 次后续修复）
- Model spend: $0
- Outcome: 验收测试转绿、`make check` 通过；两次真实运行待主会话代跑
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
