# 45 — 快照与 4 个确定性验证工具

Status: in-review
Blocked by: 44（done）, 39（phase1，done）
Spec: ../spec.md
Claimed by: claude (worktree 45-snapshot-and-tools)
Branch: worktree-45-snapshot-and-tools

## Goal

实现 spec 里的快照、`model_diff`、`contract_check`、`grain_facts`、`compare_by_key`，并在启用了验证器的配置里注册成 MCP 工具；`verify_model_values` 不再注册。让 44 里对应的验收测试转绿。

## 已和用户定下的（2026-11-04）

全部按 spec「快照」和「工具」两节实现，下面几条不要改：
- 快照范围：`dbt_project.yml`；`models/macros/snapshots/tests` 下的 `.sql` 和 `.yml`；`seeds/` 单个文件 ≤5 MB。不含 `dbt_packages/target/logs`。放在 workspace 外的运行临时目录。
- `model_diff` 的解析方法：替换 `ref`/`source`，把 `config`/`var`/宏调用换成占位符，再用 sqlglot 解析；解析不了就退回文本 diff 并标"未解析"。
- 列契约只认题面和快照里原有的 YML；窗口函数只查改动过的；粒度检查的豁免只认题面和原有 YML；`compare_by_key` 拒绝引用被测模型或其他改动过的模型，用 full outer join 覆盖全部键。
- `compare_by_key` 的相等规则写进代码，照 spec：DECIMAL 按这一列的小数位数四舍五入后相等；浮点相对误差 ≤1e-6；其他类型完全相等；两边都是 NULL 算相等。现有工具里没有可以沿用的实现，规则原来写在 `agents/value-verifier.md:124-129` 的提示词里。
- 所有 SQL 走只读网关；表目录用工单 39 的缓存。
- 按 `verifiers` 开关注册，不按配置名：full、no-skill、no-context、full-old-skill 都注册新工具。

## Authority

- May edit: `src/data_agent/verifier_v2/`、`src/data_agent/verifier_tools.py`（只改注册：加新工具、去掉 `verify_model_values`）、`src/data_agent/agent.py`（只加开跑前的快照调用）、`NOTICE`、`tests/`（`tests/acceptance/` 除外；按 AGENTS.md 只许删掉转绿测试的 `xfail`）
- May also edit: `tests/acceptance/seam_support.py` 的 `VERIFIER_TOOLS`，只做一件事：去掉 `verify_model_values`、加上 4 个新工具名，与注册改动同一提交（主会话 2026-11-05 决定，见工单 44 Comments 第 6 条）
- Must not edit: `tests/acceptance/` 的其余内容、`src/data_agent/agents/`、`skills/`、`src/data_agent/configs/`、钩子相关代码（工单 46）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0.3

## Context

- spec：`.scratch/verifier-v2/spec.md`；ADR-0009。
- 上游参考：`SignalPilot/benchmark/signalpilot-plugin/agents/verifier.md:34-74`（粒度、NULL 子检查，以及 MODIFY 时 population 检查记 N/A）。
- 现有工具：`src/data_agent/verifier_tools.py`（只改注册）。`compare_by_key` 的相等规则以 spec「工具」一节 `compare_by_key` 下的四条为准，现有代码里没有可沿用的实现。

## Acceptance

- [ ] 44 里快照、`model_diff`、`contract_check`、`grain_facts`、`compare_by_key`、配置相关的验收测试转绿（删掉 `xfail`，其余一字不改）。
- [ ] 单元测试：sqlglot 在 duckdb 和 postgres 两种方言下都能解析夹具。
- [ ] `make check` 通过。
- [ ] （主会话）服务器真实运行：对 dbt-fix-division-by-zero 的 base image 项目做一次快照，手工改掉一个过滤条件后调 `model_diff`，贴出输出，并记下 2356 个模型下的快照耗时和大小。

## Evidence required

验收测试和单元测试的输出；`make check` 输出；真实运行的输出。

## Stop conditions

- 有验收测试只有改测试本身才能通过。
- 快照在 2356 个模型的项目上超过 10 秒或 50 MB：停下，贴出实测数据。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 2（第 1 轮 eng:code-review 4 条 spec 已修 + Fable 退回 7 条；第 2 轮 eng:code-review Standards 1 硬 + 6 smell、Spec 1 P2 + 1 P3，全部已修或写明理由；Codex 5 次 at capacity）
- Model spend: $0（无模型调用）
- Outcome: in-review，branch `worktree-45-snapshot-and-tools`（9d7bf06、7fc40d2、e7ef157、16738ce 合 main、退回轮 19f68db、7ba87fc），PR #46；t45-real 1/1
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（worktree 45-snapshot-and-tools）

**结果**：44 里 24 个工单 45 的验收测试去掉 `xfail` 后全绿，另加 28 个单元测试（`tests/test_verifier_v2_tools.py`）。`make check` 干净（ruff、format、basedpyright，1615 passed）。一条测试工单会话无权修：`tests/acceptance/test_seam_b_mcp_server.py::test_verify_model_values` 调用已被拿掉的工具。

**交主会话的两个决定**
1. 删掉 seam B 的 `test_verify_model_values`（spec 说该工具已去掉；`test_new_tools_follow_the_verifiers_switch` 正是断言这一点）。
2. 快照路径通过环境变量 `DATA_AGENT_SNAPSHOT_DIR` 传给 MCP server（在 `build_options` 里设，和 Postgres URL 同一方式），因为 `cli.py` 和 `mcp_server.py` 在 Authority 之外。要更干净可以加 `--snapshot` 参数，需扩范围。

**第 3 步本地真实运行**（无服务器权限；stdio 驱动 MCP server 对 verifier_v2 夹具，快照取自 `project_before`，workspace 换成 `project_after`）：

```
tools: audit_model_sources, check_model_schema, compare_by_key, contract_check, describe_table, dictionary_search, discover, explain_query, grain_facts, hydrate, list_tables, map_columns, model_diff, query_database

$ model_diff({})
### all_events (changed, project project)
- [rows] changed: UNION  ->  UNION ALL
### conditional (changed, project project)
未解析 (not parsed: Jinja statements or SQL sqlglot could not read); line diff against the snapshot:
-WHERE status = 'pending'
+WHERE status IN ('pending', 'review')
### customer_ltv (new, project project)
- [source] added: stg_orders
- [grain] added: GROUP BY customer_id
- [config] added: materialized: table
### daily_sales (changed, project project)
- [config] changed: materialized: view  ->  materialized: table
### ranked_orders (changed, project project)
- [window] changed: ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date, order_id)  ->  ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date)
### stg_customers (changed, project project)
- [source] changed: raw.customers  ->  raw.customers_v2
### stg_orders (changed, project project)
- [filter] removed: WHERE status <> 'cancelled'
### top_orders (changed, project project)
- [rows] added: LIMIT 100

$ contract_check(models="stg_orders, ranked_orders, customer_ltv", task_columns="customer_ltv: customer_id, lifetime_value")
### stg_orders
exists: PASS — main.stg_orders exists (table)
columns: FAIL — contract from snapshot_yml: order_id, customer_id, status, amount, order_date; missing: status; extra: none
window ordering: N/A — no window function was added or changed in this model
### ranked_orders
window ordering: FAIL — ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date): 1 rows share their ORDER BY value (order_date) within PARTITION BY customer_id; the ordering is not unique
### customer_ltv
columns: PASS — contract from task: customer_id, lifetime_value; missing: none; extra: bonus_col

$ grain_facts(model="daily_sales", key_columns="store_id")
rows with a NULL key: 0
duplicate rows (beyond the first per key): 1
exemption candidate: the snapshot YML at project/models/schema.yml declares store_id, sale_date unique

$ compare_by_key(model="cmp_model", key_columns="k", reference_sql="SELECT customer_id AS k, sum(amount) AS dec_v FROM stg_orders GROUP BY 1")
Rejected: the reference SQL reads stg_orders: a reference must not read the model under test or any model created or changed in this run
```

**快照开销，合成 2356 模型项目**（真实镜像上的数字待服务器运行）：快照 2405 个文件、0.85 MB、0.69 s；`model_diff` 无改动 0.43 s，10 个改动 0.46 s。

**审查**：Codex `da-review main` 三次 "Selected model is at capacity"，未跑。eng:code-review Standards：无硬问题；smell 已处理（共享计数帮助函数、只遍历一次表、kind 帮助函数、`workspace_alias` 改名、删未用的 `diffs` 参数）。按 Authority 不修：`verifier_tools.py` 里的 `verify_values` 及其帮助函数成了死代码，走 MCP 的测试已删，等 47 一起拿掉 value-verifier 提示词时清理。eng:code-review Spec：4 条都修了并配回归测试：粒度豁免只在 YML 点名键以外的列时成立；项目 `vars` 只作用于 SQL 里调了 `var()` 的模型；`compare_by_key` 拒绝带限定名的被测模型和别名下的改动模型；计数按不同键而不是 join 后的行。

**设计说明**：YAML 用小型子集解析器 `verifier_v2/yaml_lite.py`（pyyaml 只是传递的 dev 依赖，`pyproject.toml` 在 Authority 外）。`model_diff` 也报告删除的模型（`status: "removed"`）和 SQL 内 `config()` 的改动。

### 2026-11-05 主会话

- 决定 1：照工单会话建议，主会话在 45 分支上删掉 seam B 的 `test_verify_model_values`（含未用的 `re` 导入），该文件 30 passed，`make check` 1615 passed、16 xfailed。决定 2：接受环境变量 `DATA_AGENT_SNAPSHOT_DIR`，与 Postgres URL 同一方式，不扩范围。
- 主会话再跑 `da-review main` 第 4 次仍 "at capacity"；改看 PR #46 的 Codex app 审查。分支已 push，PR #46 已开。
- 与 48（PR #45）的关系：后合入的那张给 4 个新工具加 `annotations=READ_ONLY`，48 的测试会把漏的抓出来。
- 服务器真实运行排在 `t48-real` 之后。
- 2026-11-05 主会话：服务器真实运行 `t45-real`（commit e7ef157；`ablate --split dev --config full --task dbt-cart-abandonment-recovery --trials 1 --budget 2`）：reward 1（1/1），实际价 $0.2460（2 个事件文件在高峰）。`status` 事件列出的工具含 `model_diff`、`contract_check`、`grain_facts`、`compare_by_key`，不含 `verify_model_values`。验证者调用了 `contract_check`（3 个模型 exists/columns 全 PASS、window N/A）和 `grain_facts`（`mart_ecommerce__recovery_scorecard` 按 cart_id：NULL 0、重复 0，"exemption source in the snapshot YML: none"，说明快照存在且被读到）；`model_diff`、`compare_by_key` 这一局没被调用（验证者提示词还是 v1，47 才改）。value-verifier 子 agent 报告 `verify_model_values` 不可用、只能用 `query_database` 自己算，这是预期内的过渡状态，47 拿掉该验证者。真实镜像上 2335 模型项目的快照耗时没有单独记录（事件流里没有快照事件），未确认。PR #46 CI 待看，Codex app 三次都 "unknown error"。
- 2026-11-05 主会话：PR #46 CI 绿；主会话已把 main（含 48）并进分支（16738ce），4 个新工具带 `annotations=READ_ONLY`，`make check` 1624 passed。Codex app 3 次 unknown error，按人 2026-11-05 的决定改用 Fable 5.1 只读审查，7 条意见逐条核对后 **退回**（第 1 轮）：
  - 退回 1（P1）：`verifier_v2/compare.py:198-219` 的 FULL OUTER JOIN 用 `IS NOT DISTINCT FROM` 做连接条件，Postgres 拒绝（审查者在 postgres:17 实测：`FULL JOIN is only supported with merge-joinable or hash-joinable join conditions`），所以 `compare_by_key` 在 Postgres 上每次都是工具错误，且没有 Postgres 测试覆盖。修法：每个键用 `m.k = r.k OR (m.k IS NULL AND r.k IS NULL)`，或两边 COALESCE 到哨兵值；补一个 Postgres 下的测试（现有 `tests/test_postgres.py` 的模式）。
  - 退回 2（P2）：`verifier_v2/yaml_lite.py:29-40,141` 遇到块标量（`description: |`、`>-`）、多行普通标量、流式映射（`{combination_of_columns: [a, b]}`）、锚点、以 `[` 开头的值时整份文件静默解析成 `{}`，`contract_check` 于是报"snapshot YML has no entry"、`grain_facts` 报无豁免，把假事实当真事实。最低要求：解析不了时工具明说"snapshot YML could not be read"；块标量和流式映射在真实 dbt 项目里常见，应支持。
  - 退回 3（P2）：`compare.py:66-74` 与 `sql.py:144-151` 的拒绝检查只比裸表名，丢掉 schema：模型 `orders` 读 `source('raw','orders')` 时，参考 SQL `FROM raw.orders` 会被当成"reads orders"拒绝。引用带限定名且目录知道模型 schema 时按 (schema, name) 比较。
  - 退回 4（P2）：`contract.py:33-41`、`model_diff.py:74-79`：模型标为未解析时 windows 为空，窗口检查却断言"no window function was added or changed"记 N/A，应改成"not checked: model SQL not parsed"。
  - 退回 6（P3，顺手修）：`contract.py:35,53` 窗口映射按文件名 stem 做键，`models` 传 `main.orders` 或别名时查不到，误报 N/A；统一走 `find_model` 的结果或 stem。
  - 退回 7（P3，顺手修）：`grain.py:49-50` 快照 YML 里 unique+not_null 的列在建出来的模型里不存在时整次调用抛 ToolError，应作为事实报出（missing key column），不吞掉 NULL/重复计数。
  - 不退回 5（P3）：DuckDB `query_table('my_model')` 以字符串读表，绕过 sqlglot 的拒绝检查。记为已知限制；若改动只是在参考 SQL 里拒绝 `query_table(` 文本，可以顺手加。
  - 审查者核过没问题的：键列与模型名经目录解析并加引号、SCALE_SQL 字面量转义、所有语句走 Queryable/网关、相等规则与 spec 一致、CTE 遮蔽处理、快照范围与 5 MB seed 规则、MCP server 包住工具异常。

### 2026-11-05 工单会话（退回第 1 轮修复，commits 19f68db、7ba87fc）

`make check` 干净（1638 passed、16 xfailed）。Postgres 集成测试在本机 Postgres 17 scratch 库 `da_t45_scratch` 通过（`DATA_AGENT_TEST_POSTGRES_URL`，`uv run pytest tests/test_verifier_v2_tools.py -m integration -k postgres` 1 passed）。

**逐项修法**
- 退回 1：join 改成两个普通等式 `(m.k IS NULL) = (r.k IS NULL) AND COALESCE(m.k, 哨兵) = COALESCE(r.k, 哨兵)`，哨兵是该键任一非 NULL 值，算在单行 CTE 里 CROSS JOIN 到两边，Postgres 走 Hash Full Join（EXPLAIN 实测）。审查者建议的 `a = b OR (a IS NULL AND b IS NULL)` 实测 Postgres 17 同样拒绝。DuckDB 对 ON 里的标量子查询报 INTERNAL Error，所以哨兵放 CTE。某键两边全 NULL 时先探测，该键只按 NULL 标志连接（第 2 轮 Spec P2）。
- 退回 2：`yaml_lite.py` 支持块标量（`|`、`>`、`-`/`+`）、带内容的流式映射、跨行流式列表、续行普通标量；锚点/别名/标签/`|2` 仍不支持但抛 `YamlLiteError`（带行号）。`contract_check` 的 columns 和 `grain_facts` 输出写明 "snapshot YML could not be read: 路径 (原因)"。`dbt_project.yml` 读不了仍回退 `{}`。
- 退回 3：`sql.table_references` 返回 (schema, name)；带限定名的引用只在已知 schema 内拒绝，未建出的改动模型 schema 未知则任意限定名都拒绝。
- 退回 4：未解析模型的窗口检查报 N/A，证据 "not checked: the model's SQL was not parsed …"。
- 退回 6：`project.model_name` 把 `main.orders` 或别名解析回模型名。
- 退回 7：`grain_facts` 新增 `missing_key_columns`，YML 里 unique+not_null 但模型没有的列作为事实报出并排除出计数。
- 顺手 5：参考 SQL 文本里出现 `query_table(` 直接拒绝。

**真实运行**（stdio，DuckDB 夹具 + 一个 YML 带锚点的第二项目）：`contract_check` 对未解析模型报 "not checked"、对读不了的 YML 写明路径和行号；`grain_facts` 报出 `return_id` 为 YML 有而模型缺的键列；`compare_by_key` 接受 `FROM raw.orders`、拒绝 `FROM orders` 和 `query_table(`。Postgres 前后对照：旧代码 `FULL JOIN is only supported with merge-joinable or hash-joinable join conditions`，新代码 `only_in_model 0 / only_in_reference 1 / mismatched 0`。

**审查**：`da-review` 第 5 次 at capacity，跳过。eng:code-review Standards：`sql.table_names` 死代码已删，6 处 smell 已处理。Spec：P2（两边全 NULL 的键）已修配 DuckDB + Postgres 测试；P3（Postgres 测试覆盖复合键）已补。

**不修**：(a) `dbt_project.yml` 读不了仍回退 `{}`，否则四个工具全部不可用；要显式报告可在后续加 `model_diff` 的 warnings。(b) `yaml_lite.py` 371 行、`project.py` 约 400 行，接近上限未越界，不拆。(c) 参考 SQL 每次调用多跑 1 次哨兵探测；无 ORDER BY 的 LIMIT 参考哨兵可能不同但结果仍正确。
