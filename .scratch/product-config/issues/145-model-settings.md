# 145 — 模型：`[models.*]`、`[roles]`、价格可省、上下文窗口、effort 取值、小模型/分类/验证器模型

Status: ready-for-agent
Blocked by: 143
Spec: ../spec.md（"分节（Q2）"的 `[models.<名字>]`、`[roles]`）
Claimed by: —
Branch: —

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

## Acceptance

- [ ] `TICKET_145` 标记全部删掉；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_config_models.py -v` 全部 PASSED，0 个 xfail。
- [ ] 141 的守门组和其他已合入的验收测试照旧通过。
- [ ] 验收测试没钉的行为有新单元测试：未定价模型的 `classify_text` 能跑（替身模型）且花费记"未知"；`[roles].classify` 的模型真用在分类调用上；`efforts` 校验的报错点名模型和取值；`DATA_AGENT_MODEL` 覆盖 `[roles].main`。
- [ ] `docs/setup.md` 写了 `[models.*]`、`[roles]` 的每个键、默认值，以及没填价格时的后果。
- [ ] `make check` 通过。

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：审计第 3 节（只适配 DeepSeek 的按模型设置）。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：没填价格时不传 SDK 的 `max_budget_usd`、开头警告；去掉对应的停止条件。
