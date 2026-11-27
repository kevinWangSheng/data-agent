# 140 — 产品路径的三处安全默认：CLI dbt 写临时 schema、服务器级联网开关、隐藏别的运行的临时 schema

Status: ready-for-agent
Blocked by: —
Spec: 无（执行已有规矩的修正，不是新设计；人 2026-11-27 定先单独派，不等 product-config spec）
Claimed by: —
Branch: —

## Goal

产品入口默认不写生产库、操作者能在服务器上统一关掉联网（IM 跟随）、agent 看不到别的运行留下的 `da_run_*` 临时 schema。评测入口的行为不变。

## Authority

- May edit：
  - `src/data_agent/cli.py`：`run` 的 `--dbt-target` 默认值；`web` 子命令加服务器级联网开关；
  - `evals/harbor_agent.py`：dbt 任务显式传 `--dbt-target task`（只这一处）；
  - `src/data_agent/web/app.py`、`web/launcher.py`、`web/conversations.py`、`src/data_agent/im/adapter.py`：联网默认值从服务器设置来；
  - `src/data_agent/mcp_server.py`、`src/data_agent/db_facts.py`、`src/data_agent/context_tools.py`：目录查询排除别的运行的 `da_run_*`；
  - `tests/`（除 `tests/acceptance/`）；`docs/setup.md`、`README.md`：写明新的默认值和开关。
- Must not edit：`tests/acceptance/`、`skills/`、`src/data_agent/agent.py`、`src/data_agent/configs/`、`evals/` 里除 `harbor_agent.py` 那一处以外的文件、`web/src/`（前端不动）、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
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

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：审计（`docs/research/2026-11-27-product-config-audit.md`）查出的三处评测行为漏进产品。人定先单独派，不等 product-config spec。
