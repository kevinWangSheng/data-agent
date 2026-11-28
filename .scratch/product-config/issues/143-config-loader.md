# 143 — 配置加载：分层合并、`${VAR}`、未知键、明文密钥、`config show`、`--config` 路径、三个旧参数并入

Status: ready-for-agent
Blocked by: 141
Spec: ../spec.md（"配置文件与分层（Q1）""分节（Q2）"）
Claimed by: —
Branch: —

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
- **`--dbt-target`、`--network` 不接进 `cli_overrides`**（141 合并前审查第 10 条）：这两个的接线归 144。本工单接了会让 `test_config_wiring.py` 里标 `TICKET_144` 的 policy 测试提前 XPASS，而本工单删不掉 144 的标记。

## Acceptance

- [ ] `TICKET_143` 标记全部删掉；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_config_load.py tests/acceptance/test_config_cli.py -v` 全部 PASSED，0 个 xfail；141 改写过、标 `TICKET_143` 的已有验收测试也全部 PASSED。
- [ ] `data-agent run --config full`（旧名）和 `--config <文件>` 都能跑；`mcp-server --config` 同样。
- [ ] 141 的守门组（`test_config_snapshot.py` 的守门、`test_config_eval_isolation.py`）和其他已合入的验收测试照旧通过。
- [ ] spec Q1 每条都有单元测试或验收测试；验收测试没钉的（例如 `${VAR}` 展开后的值不进 `repr` 和报错文字）有新单元测试。
- [ ] `docs/setup.md` 写了配置文件的位置、分层、`config show`、三个旧参数改成哪一节，示例只带 `${VAR}`。
- [ ] `make check` 通过。

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：配置层的第一张实现工单，144–148 都建在它上面。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：`--config` 可重复、过渡期接受旧名（`run`、`mcp-server`）、层键 `skill`、保留变量名写法、产品 `version.config`；去掉"已有验收测试""评测入口还传消融名"两条停止条件（141 改写已有验收测试，过渡期接受旧名）。
- 2026-11-27（主会话）：按 141 合并前审查补 Context：`--dbt-target`、`--network` 不接进 `cli_overrides`（归 144）。
