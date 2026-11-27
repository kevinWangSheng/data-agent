# 148 — 提示词与业务上下文：`[prompts]`（产品版答题规则）、`[instructions]`、`[sources.*].docs`/`description`、`DB_TYPE` 与任务文件提示挪进评测配置

Status: ready-for-agent
Blocked by: 143、146
Spec: ../spec.md（"分节（Q2）"的 `[prompts]` 一条、`[instructions]`、`[sources.<名字>]` 的 `description`/`docs`）
Claimed by: —
Branch: —

## Goal

产品提示词不再带评测的东西，使用者能放自己的业务上下文：`[prompts]` 可覆盖提示词片段，至少 `answer_rule`；产品默认用产品版答题规则（允许表格、可以写假设）；所有评测配置文件（DAB、LiveSQLBench、jaffle 用的）保留现在的答题规则（`decisions.md` Q8）。`DB_TYPE` 那句只写进 `evals/profiles/data-eng-bench.toml`。任务文件提示产品运行总是加，评测配置文件照现在的条件（只在验证器开时）。`[instructions].files` 的内容进系统提示词；`[sources.<名字>].description` 进提示词，`docs` 目录复制或只读挂进工作区。141 的快照守门组（评测运行参数）照旧逐项相同；`TICKET_148` 验收测试全部转绿（只删标记）。

## Authority

- May edit：
  - `src/data_agent/config.py`：只补 `[prompts]`、`[instructions]`、`[sources.*].description`/`docs` 的校验，不改 143 的加载与合并。
  - `src/data_agent/agent.py`：只改提示词部分：`SYSTEM_PROMPT`（`agent.py:147-153`）后的追加段、`ANSWER_RULE`（`:212-218`）和用它的 `QUERY_PROMPT`（`:249-264`，`CHARTS_QUERY_PROMPT` `:266`）、`DBT_PROMPT` 的 `DB_TYPE` 那句（`:294-295`）、`TASK_FILE_NOTE`（`:307-314`）与 `run` 里加它的条件（`:2301`）、`system_prompt`（`:1360-1383`）里说明文件与数据源描述的段；`run`、`saved_task`（`:335-344`）里任务文件在不开验证器时也要存（产品运行）。不改 Skill 段（147）。
  - `evals/dab.py`：`TASK_PROMPT`（`dab.py:103-126`，用 `ANSWER_RULE` `:125`）改从配置文件的 `[prompts].answer_rule` 取。
  - `evals/profiles/*.toml`：加 `[prompts]`（`dab.toml`、`livesqlbench.toml` 和 jaffle 用的配置写现在的 `ANSWER_RULE`；`data-eng-bench.toml` 写 `DB_TYPE` 那句；以及保持评测行为不变所需的其他片段）。
  - `src/data_agent/web/launcher.py`（`prepare_workspace`，查数运行的空工作区 `:411-413`）、`src/data_agent/web/sources.py`（`DataSource` `:37-47`）：`docs` 进工作区、`description` 随数据源走。
  - `tests/acceptance/` 里 141 标了 `TICKET_148` 的文件（`test_config_prompts.py` 等，清单见 141 最终回复）：**只删** `TICKET_148` 标记，以及因此没用的定义和 import。
  - `tests/`（除 `tests/acceptance/`）；`docs/setup.md`（`[prompts]`、`[instructions]`、`[sources.*].description`/`docs`）。
- Must not edit：`tests/acceptance/` 里除 `TICKET_148` 标记以外的一切；`tests/fixtures/product_config/`；`skills/`（ADR-0005：榜单的答题格式不进 Skill）；`agent.py` 的 Skill/MCP/插件（147）、模型设置（145）、上限（144）；`evals/` 里除 `dab.py` 的提示词和 `evals/profiles/` 以外的文件；`web/src/`；其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none。
- Spend cap：工单 agent $0（不能 source 模型的 env 文件）。

## Context

行号以 main `e494e49` 为准；140 合入后 `web/launcher.py` 的行号会移动。

- **审计条目**（`docs/research/2026-11-27-product-config-audit.md`）：
  - 2.3 DAB 判分驱动的 `ANSWER_RULE`（不要表格、不列备选，`agent.py:212-218`；来由见注释 `:207-211`，ADR-0005 Update 2026-11-14）在每个查询提示里（`QUERY_PROMPT` `:263`），对话也有（对话走 `CHARTS_QUERY_PROMPT`，`:266`）；DAB 自己的任务提示词也用它（`dab.py:53`、`:125`）。
  - 2.4 产品 dbt 提示里有 data-eng-bench 的 "`echo $DB_TYPE`"（`agent.py:294-295`，工单 100）。Harbor 命令自己也查 `DB_TYPE`（`harbor_agent.py:385-387`）。
  - 2.5 任务文件提示只在开验证器时加（`agent.py:2301`，`saved_task` 只在 `verified` 时跑，`:2230`），"保持和旧运行可比"（注释 `:307-308`）。
  - 5.1 系统提示是常量，没有说明文件或追加入口（`agent.py:147-153`、`:1360-1383`）。
  - 5.2 查数运行的工作区是空目录（`launcher.py:411-413`）；数据源条目没有文档目录或描述字段（`web/sources.py:33` `KEYS`）。
- **ADR-0005 与 `[prompts]`**：`decisions.md` Q2 定评测配置文件的 `[prompts]` 写榜单官方答题格式不算违反 ADR-0005（复现官方提示、不在 Skill 里、产品默认不受影响）。
- **评测不变**：141 的快照守门组含任务提示词全文。LiveSQLBench 和 jaffle 的查数题今天也经 `QUERY_PROMPT` 拿到 `ANSWER_RULE`；Q8 定所有评测配置文件都保留现在的答题规则，只有产品用新版。
- **`docs` 的做法**：spec 写"复制或只读挂进工作区"，两种由本工单选、写明理由。挂进 Docker 沙箱时注意 `check_databases_outside`/`check_no_credentials`（`agent.py:2100-2121`）和工作区守卫（`workspace_guard`）对新文件的处理。
- **并行工单**：147 改 `system_prompt` 的 Skill 段、145 改模型设置、144 改上限，都在 `agent.py`；后合入的一方 rebase。

## Acceptance

- [ ] `TICKET_148` 标记全部删掉；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_config_prompts.py -v` 全部 PASSED，0 个 xfail。
- [ ] 141 的快照守门组和评测隔离组照旧通过；其他已合入的验收测试照旧通过。
- [ ] 验收测试没钉的行为有新单元测试：`[prompts]` 的未知片段名报错；`[instructions].files` 缺文件报错点名文件；`docs` 目录里的凭据文件（`workspace_guard.credential_files` 认得的）不进工作区。
- [ ] 产品版答题规则的文字写在一处常量；每份评测配置文件里的答题规则与现在的 `ANSWER_RULE` 逐字相同。
- [ ] `docs/setup.md` 写了 `[prompts]`、`[instructions]`、`[sources.*].description`/`docs`。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出；`test_config_prompts.py` 的 `-v` 输出；`git diff main -- tests/acceptance | grep '^[-+]' | grep -v '^[-+][-+]'`。不调模型的真实运行（$0）：一次产品查数运行截停前的任务提示词和系统提示词（带一个说明文件、一个数据源描述）。

交给主会话（写出命令）：一份带 `[instructions].files`（一条业务规则，例如"订单金额不含运费"）和 `[sources.shop].description` 的配置跑一次查数，确认答案照了那条规则；一次 dbt 运行确认任务提示里没有 `DB_TYPE`、有任务文件提示。

## Stop conditions

- 写了评测配置文件的 `[prompts]` 后快照守门组仍失败（评测的任务提示词变了）。
- 要改 Skill 文本或 Authority 以外的文件才能做到。
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

- 2026-11-27（主会话建票）：审计第 2.3–2.5、5.1、5.2 节。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：所有评测配置文件（含 LiveSQLBench、jaffle）保留现在的答题规则；去掉对应的停止条件。
