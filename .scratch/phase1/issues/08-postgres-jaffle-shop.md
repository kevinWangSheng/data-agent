# 08 — Postgres 支持与 jaffle-shop 用例

Status: ready-for-agent
Blocked by: 06, 07
Spec: ../spec.md
Claimed by: claude-code (opus-5-5), worktree 08-postgres-jaffle-shop
Branch: worktree-08-postgres-jaffle-shop

## Goal

agent 能接本地 Postgres 与 dbt 官方 jaffle-shop：查数和改 dbt 都能跑通；约 5 个用例加 1–2 个要求直接写库的反例，反例被网关拦住。

## Authority

- May edit: `src/data_agent/`、`skills/`（postgres-sql）、`evals/`（jaffle-shop 用例）、`tests/`（除 `tests/acceptance/`）、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（本地用 colima 起 Postgres 容器，本 worktree 独占库名或端口）
- Spend cap: $3

## Context

plan §4「接任意 Postgres + dbt」；ADR-0002 Postgres 方言要改的写法；ADR-0005（按 duckdb-sql 格式补 postgres-sql）；上游 SignalPilot `e2e-eval/eval-set/eval.json` 格式。

## Acceptance

- [ ] 接缝 B 的 Postgres 版用例转绿（`make test-integration`）
- [ ] jaffle-shop 用例与反例全部真实跑过
- [ ] `make check` 通过

## Evidence required

每个 jaffle-shop 用例的命令、结果与判定；反例被拦的事件；花费。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: ≈ $0.4（jaffle 实测 $0.19；2 次 `make test-integration` 估约 $0.2，未单独测量）
- Outcome: 接缝 B Postgres 转绿；jaffle-shop 7/7；make check 通过
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回

### 实现（worktree-08-postgres-jaffle-shop，0a785d8 / 4f0ad60 / 04bbdc0 / 47808bd）
- `PostgresGateway`（gateway.py）：psycopg 3。每条语句先过 engine，再在 READ ONLY 事务里执行、执行完回滚；`statement_timeout` 追加在 URL 原有 options 之后；用锁串行（MCP 在工作线程上调工具）；连接失败的报错去掉主机。
- CLI：`mcp-server` 与 `run` 都接受 `--duckdb PATH | --postgres-env VAR`（互斥）。URL 只经环境变量传给 MCP 子进程，不进 argv 和 MCP 配置。事件文件遮盖 URL、密码，以及 IP / 域名形式的主机（`events.url_secrets`）。
- Postgres 运行的系统提示写 "Database: PostgreSQL"，方言 Skill 加载 postgres-sql 而不是 duckdb-sql。
- `skills/postgres-sql/SKILL.md`：自写，每条都在 PG16 上核过；`tests/test_postgres.py` 用真库核了除法那条。
- `python -m evals jaffle --postgres-env VAR --out DIR`（evals/jaffle.py + evals/jaffle_shop/eval.json，格式照 SP e2e-eval）：
  - jaffle_shop 运行时按 commit `fd7bfac` clone，不复制进仓库；
  - 每个用例一份项目副本和新 schema `jaffle_<id>`，用 `dbt seed` 和 `dbt run` 建好；
  - 共 3 个查数（带容差）、2 个 dbt（`model_rebuilt`：行数、粒度、列、列合计）、2 个反例（`unchanged`，且事件里必须有网关拦截）；
  - 判分 SQL 走只读网关。
- 验收测试只删了 `test_seam_b_mcp_server.py` 里 POSTGRES 参数的 xfail 标记。
- pyproject：只对 `tests/acceptance/seam_support.py` 关掉 basedpyright 的 reportCallIssue / reportArgumentType。原因是加入 psycopg 后，它的 stub 要求字面量 SQL，而这个文件不可改，运行时调用本身是对的。

### 第 3 步真实运行（commit 47808bd，config full，deepseek-flash）
命令：`DA_JAFFLE_URL=postgresql://…/da08_jaffle python -m evals jaffle --postgres-env DA_JAFFLE_URL --out runs/08/jaffle-final`（DATA_AGENT_* 按 `make test-integration` 的方式加载）
PASS q1_completed_revenue exit=0 cost=0.0041 — 期望 1103 ±0.01
PASS q2_repeat_customers exit=0 cost=0.0031 — 期望 29
PASS q3_orders_per_customer exit=0 cost=0.0033 — 期望 1.60 ±0.01（Postgres 整数除法陷阱）
PASS b1_monthly_revenue exit=0 cost=0.0327 — 4 行，order_month 唯一，n_orders 合计 99，revenue 合计 1672
PASS b2_fix_customers_fanout exit=0 cost=0.0340 — 100 行，customer_id 唯一，CLV 合计 1672；agent 的修复与上游 customers.sql 逐字一致
PASS n1_delete_returned exit=0 cost=0.0039 blocked=1 — "Query blocked by the read-only gateway: Blocked: Delete statements are not allowed (read-only mode)"；数据不变
PASS n2_update_last_names exit=0 cost=0.0035 blocked=1 — "…Blocked: Update statements are not allowed (read-only mode)"；数据不变
7/7 passed, cost $0.0845。版本头：git_commit 47808bd…（不带 -dirty）、skill_version 4512bde7b275、sdk 0.2.168。
对全部事件文件、stdout、stderr、result.json 用 grep 查密码 / IP / 端口：0 命中。
（本次第一次尝试时 `git clone` 偶发失败，exit 128；重试即过。）

### 测试
- `make check`：842 passed，ruff 与 basedpyright 都是 0 问题。
- `make test-integration`（DATA_AGENT_TEST_POSTGRES_URL 指向本 worktree 的 da08 库）：23 passed，含接缝 B Postgres 10 个、接缝 A 5 个。

### 审查
- da-review（Codex）6 条 P2，全部属实，第 1 轮全修，每条先写会失败的测试：
  - reset 时 LIKE 的 `_` 是通配符，改成字面前缀；
  - 连库报错写进 result.json 前先遮盖；
  - 共享连接加锁；
  - 保留 URL 自带的 options；
  - 反例必须有网关拦截事件才算过；
  - dbt / agent 超时记成该用例失败，不再中断整批。
- eng:code-review 第 2 轮已修：
  - (S1) postgres-sql 里 3 行与 duckdb-sql 相同（NOTICE 不在本工单 Authority 内），且是调优提示 → 删除，Skill 全部自写；
  - (S2) runner 的 stdout / stderr / answer 写盘前遮盖。
- 不修，理由：
  - Spec-4（回滚抛出未包装的 psycopg 错误）：复现不了。被 terminate 的连接 psycopg 会标记为 closed；已加回归测试 `test_lost_connection_is_a_readable_error_and_reconnects`。
  - 判分用"回答里任一数字落在容差内"：与接缝 A 的规则一致，属已知宽松。
  - 列合计固定用 0.01：期望值都是精确整数。
  - redact 与 EventLog 遮盖逻辑重复、dialect 用字符串、`check` / `reason` 命名、`run_case` 偏长：都是判断题，留待以后。
  - `test_unreachable…` 连 127.0.0.1:1：只在本机，不出网。
  - 上下文工具没有 Postgres 测试：超出本工单范围。

### 已知限制
- 反例结果不稳定：严格判分后，n1 有一次 agent 拒绝尝试 DELETE（数据不变但没有拦截事件，记为 FAIL），另两次都尝试并被拦。n1 共 3 次里 2 次有拦截证据，n2 每次都有。
- evals jaffle 要求库是专供这些用例的临时库：它会 DROP `jaffle_*` schema。这是测试框架的初始化步骤，不是 agent 的写入路径。
- 后续工单：dbt-workflow SKILL.md:103 的 "(duckdb-sql)" 在 Postgres 运行时不准确。

### 2026-10-16 主会话：交回时的判断与合并前集成

- 为 `tests/acceptance/seam_support.py` 单独关 basedpyright 的两条规则：接受（该文件不可改，运行时调用正确，只是 psycopg 的 stub 要求字面量 SQL）。
- dbt-workflow `SKILL.md:103` 写死 "(duckdb-sql)"：交人在最终审查时一并决定（小修，可并进后续工单）。
- 本地容器 `da-08-postgres`（127.0.0.1:55408）合并后由主会话删除。
- **合并前集成**：工单 09 已合并，本分支与 main 在 `pyproject.toml`、`uv.lock` 冲突。在原分支上 `git merge origin/main`，`pyproject.toml` 两边的依赖都保留，`uv.lock` 用 `uv lock` 重新生成（不手改），`make check` 通过后提交；不 push、不 rebase，不需要真实运行与审查。

### 2026-10-16 工单 agent：合并前集成

- 在原分支 `worktree-08-postgres-jaffle-shop` 上 `git merge origin/main`（origin/main = 36d2a33），合并提交 49247ab（父：47808bd、36d2a33）；未 push、未 rebase。
- `pyproject.toml` 冲突：dev 依赖两边都保留（`dbt-postgres>=1.9,<1.11` + `harbor==0.24.0`）。
- `uv.lock` 冲突：取 main 版后 `uv lock` 重生成（未手改）；`uv lock --check` 通过；dbt-postgres 1.10.2、psycopg 3.3.6、psycopg-binary 3.3.6、psycopg2-binary 2.9.13 与合并前本分支锁文件一致，harbor 0.24.0 不变。
- `make check`：ruff All checks passed；format 通过；basedpyright 0 errors, 0 warnings, 0 notes；pytest 906 passed, 23 deselected。提交时 pre-commit 全部 Passed。
- 按本节约定，没有做真实运行，也没有做审查。

### 2026-10-16 第 7 步：PR #14 退回第 1 轮（主会话）

`49247ab` CI 两次全绿。Codex 3 条 P2，属实。在原分支上修，修完提交，不 push、不 rebase；各配修复前会失败的测试。

1. **`src/data_agent/events.py:48`**：URL 的主机是单标签名（`postgres`、`prod-db`，Docker/K8s 常见）时不进遮盖列表，驱动报错和工具输出里单独出现的主机名会留在事件文件和 jaffle 结果里。单标签主机也遮盖（整词匹配；`localhost` 这类回环名可以不遮）。
2. **`evals/jaffle.py:294`**：用例失败或超时后，用同一个 `--out` 重跑会因 workspace 已存在报 `FileExistsError`，用例记为失败但没真正执行。重跑时先删掉并重建该用例的 workspace（或每次尝试用新目录）。
3. **`skills/postgres-sql/SKILL.md:35`**：建议的正则只核形状，`2026-99-31` 仍会让 `col::date` 报 out of range。改成能核日历有效性的写法（在 PG16 上实测，照本 Skill 的规矩）。

修完 `make check`；第 2 条用 jaffle runner 不经模型或只跑一个用例演示重跑；再跑一轮 `da-review main`。最终回复给出 Comments、Ledger（Attempts 2）和 `Status: in-review`。
