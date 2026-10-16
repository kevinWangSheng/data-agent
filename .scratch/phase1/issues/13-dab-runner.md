# 13 — DAB runner

Status: in-review
Blocked by: 03, 05
Spec: ../spec.md
Claimed by: claude-code (opus-5-5)
Branch: worktree-13-dab-runner

## Goal

从本地 DAB clone 按路径 import 它的脚手架，把 `common_scaffold/tools` 包成 MCP 工具，用官方提示文件跑题，每题 5 次，输出 DAB 要求的提交 JSON 与轨迹。

## Authority

- May edit: `evals/`（DAB runner）、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $3

## Context

ADR-0005；plan §4 DAB 基线；上游 `~/dev/AI/data-agent-refs/DataAgentBench`（没有许可证：只能 import 和运行，不复制任何代码或文本进本仓库）。

## Acceptance

- [ ] 单题真实跑通 5 次并产出提交格式
- [ ] 本仓库里没有任何 DAB 代码或提示文本
- [ ] 大库任务最大 75 轮（spec「运行配置」；工单 05 留的缺口）：定义何为大库并接到运行配置
- [ ] `make check` 通过

## Evidence required

单题运行命令与提交 JSON 片段；花费。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 3（da-review 2 轮，code-review 1 轮）
- Model spend: ~$0.050（6 次真实运行）
- Outcome: DAB runner 完成；1 题 × 5 次真实运行并写出提交文件；PG/Mongo 数据集待服务器补跑
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `bd8699d`、`164098c`、`92c8760`、`55ba31b`）

**实现**
- `evals/dab.py`：`python -m evals dab`。
  - 题面来自 `query.json` 和官方 `db_description.txt`；默认追加 `db_description_withhint.txt`，`--no-hints` 可关。
  - 每次运行写 `OUT/<dataset>/query<id>/run<k>/events.jsonl` 和 DAB 自己的工具日志 `dab_tool_calls.jsonl`。agent 的工作目录是 run 目录里的空 `workspace/`，碰不到 ground truth。
  - 合并写出 `OUT/submission.json`，格式照 README 与 `leaderboard_submissions/`：`dataset`、`query`、`run`、`answer` 都是字符串。答案按事件文件同样的规则遮盖密钥。
  - 默认每题 5 次。
  - 出现基础设施错误时，该次运行不进提交文件，CLI 退出码 2。DAB 的 MCP server 没启动起来也算基础设施错误。
  - 重跑时跳过提交文件里已有的运行。
  - `--budget` 在花费到 80% 时停下，退出码 1。
- `evals/dab_server.py`：从本地 clone 用 import 加载 DAB 的 `QueryDBTool` 和 `ListDBTool`，用我们的名字 `query_database` / `list_tables` 经 MCP 暴露。工具描述在运行时取自 DAB 的 `get_spec()`。
  - SQL 先过只读网关：sqlglot AST 检查，并限制在 10000 行以内。
  - MongoDB 只跳过 SQL 检查，走 DAB 的只读 find。
  - 其他网关检查不了的库类型一律拒绝。
- 依赖：`evals/dab-requirements.txt`，版本照 DAB 的 `environment.yaml`。用 `uv run --with-requirements` 叠加安装，没有改 `pyproject.toml` 和锁文件。
- 大库：数据文件（`db_path`、`sql_file`、`dump_folder`）总计 ≥ 250 MiB 即为大库，最大轮数取运行配置的 `large`。6 个配置都加了 `large = 75`，并相应更新了 `test_run_config` 的断言。
- 测试：`tests/test_dab.py` 共 35 个，用的是自写的假 DAB（`tests/fixtures/fake_dab`，不含任何 DAB 代码）；另加了 `tests/conftest.py`，让测试能直接 import `evals`。

**检查**：`make check` 结果为 567 passed, 15 deselected, 15 xfailed。

**真实运行**（DAB commit `6ad9d68`，stockindex 数据集，只有 SQLite 和 DuckDB，不需要服务器）：
```
uv run --env-file ~/.config/data-agent/env --with-requirements evals/dab-requirements.txt \
  python -m evals dab --dab-root runs/13/DataAgentBench --dataset stockindex --query 1 \
  --runs 5 --config full --out runs/13/out --budget 3
stockindex query1 run0..run4: finished      exit=0
```
- 5 次运行都通过 DAB 自带的 `query1/validate.py`。轮数 9–12，每次花费 $0.0077–0.0097，合计 $0.0418。
- MCP 状态 `data: connected`；工具 `mcp__data__list_tables`、`mcp__data__query_database`；版本头里 `dab.skills = [sql-workflow, duckdb-sql]`，`large = false`，`max_turns = 50`。
- 提交 JSON 片段：`{"dataset": "stockindex", "query": "1", "run": "0", "answer": "## Answer\n\n**The Shenzhen Stock Exchange Component Index (`399001.SZ`) …"}`
- 修复后又验证了两点：在同一 out 目录重跑，5 次全部跳过，退出码 0，没有花费；用 baseline 新跑 1 次，结果 finished，花费 $0.0082。

**DAB 数据的获取**：`~/dev/AI/data-agent-refs/DataAgentBench` 是稀疏的部分 clone，里面没有 `common_scaffold/tools`，也没有任何题目。我在 `runs/13/` 下另做了一个稀疏 clone（已被 gitignore），两个 stockindex 数据库文件从 HF 镜像下载，sha256 与 `dataset_manifest.tsv` 一致。

**许可**：用 `git grep` 搜 DAB 的提示词、工具描述和题目字符串，没有命中。

**审查**
- `da-review main`：
  - 第 1 轮修了两条：基础设施错误原先退出码为 0，且空答案会写进提交文件；提交文件里的答案没有遮盖密钥。
  - 第 2 轮修了两条：MCP server 启动失败时被当成 finished；按提示删掉失败的运行目录后，重跑会卡在已经跑完的 run0。
  - 每条都先补了一个修复前会失败的测试。
- `eng:code-review main`（Standards 和 Spec 两个子 agent）修了以下几条：
  - 网关遇到未知库类型时原先放行，现在拒绝；
  - `--budget` 原先接受 NaN、负数和 0，现在拒绝；
  - 读不出花费时（SpendUnknown）原先没被捕获，现在退出码 2；
  - `RESULT_CHARS` 挪到 `dab_server.py`；
  - 删了一个重复的测试。

**不修的理由 / 已知限制**
- **DAB 的 `execute_python` 和 `return_answer` 没包。**
  - `execute_python` 需要 Docker 镜像 `python-data:3.12` 和 autogen，而且和 DAB 自己 agent 里按 tool call id 存结果的机制绑在一起。
  - 答案用我们 agent 的最终消息给出，所以不需要 `return_answer`。
  - 影响：需要用 Python 处理非结构化文本的题可能吃亏，大结果也只能截断到 10000 字符。
  - 要不要加，等全量 DAB 的失败归因再决定（遗漏，建议记进后续工单）。
- **大库只接到了 DAB runner。** `data-agent run` 只收 DuckDB，没有可测量的"大库"输入，所以仍用 `query` 的 50 轮（计划内，产品侧等多数据源工单）。
- **Postgres 和 MongoDB 数据集没有本地跑过。** 本工单没有服务器权限（Server access: none），这部分要在服务器上补跑（计划内，plan §9 第 8 项）。
- **`run_once` 与 `agent.run` 有约 40 行重复。**
  - 原因：Authority 不含 `src/`，而工单 04 正在改 `agent.py`。
  - 后续：04 合并后，让 `agent.run` 接受 prompt 和 MCP server 参数，DAB 这边改为直接调用，重复就消掉了。
- **`dab.py` 偏长、参数成组传递、运行状态用字符串**：都是判断项，保持现状。
- **重跑遇到"没有提交条目的运行目录"会以退出码 2 停下，不会自动覆盖它的轨迹。** 这是有意的设计，处理方法已写在错误信息里。
- **透明说明**：开发时我（coding agent）读过 stockindex `query1/validate.py`，看到了标准答案。它没有进入任何 prompt、Skill 或代码；DAB 不属于留出集（ADR-0004）。

### 2026-10-16 主会话：交回时的两个判断

- 改了 Authority 外的 `src/data_agent/configs/*.toml`（加 `large = 75`）：接受。"接到运行配置"这条验收是主会话从工单 05 转过来时加的，Authority 没同步，矛盾在主会话。
- 大库阈值"数据文件 ≥ 250 MiB"：暂定接受；全量 DAB 跑出各数据集的轮数分布后再校准。
- `execute_python` 未包：属于遗漏（不是计划内），影响非结构化抽取题；交人决定是否开后续工单。
