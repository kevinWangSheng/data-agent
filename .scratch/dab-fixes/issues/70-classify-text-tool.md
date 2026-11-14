# 70 — 基础层的语义分类工具

Status: done
Blocked by: —
Spec: ../../../docs/adr/0005-dab-integration.md（Update 2026-11-14）
Claimed by: claude (opus-5-5) worktree 70-classify-text-tool
Branch: worktree-70-classify-text-tool

## Goal

agent 有一个工具，能把一条只读查询取出的文本按给定的类别逐行分类：工具自己分批调模型，把逐行标签存下来供后续查询或 Python 使用，并返回每个类别的行数。产品和 DAB 两个工具服务器都有它。

## 已和用户定下的（2026-11-14，grilling Q6、Q7、Q12–Q14）

- 形态：入参是一条取文本的只读查询、一个类别列表、一段可选的说明（告诉模型按什么标准分）。工具分批调模型，每行得到类别列表里的一个标签。工具名和参数名由实现定，名字要通用（例如 `classify_text`）。
- 结果：逐行标签存成一张表（行的键、标签），后续的 SQL 或 Python 能用；工具的返回值里有每个类别的行数、表存在哪里、处理了多少行。
- 位置：基础层。所有配置（含 baseline）都有，不受 `skill`、`verifiers`、`context` 开关影响。在 `src/data_agent/mcp_server.py` 和 `evals/dab_server.py` 都注册；分类逻辑只写一份，两边共用。
- 模型：用本次运行自己的模型和端点（`ModelSettings`，`src/data_agent/agent.py:570-586`），不另配模型。
- 花费：工具调模型的 token 计入本次运行的花费（`cost_usd`、`evals/spend.py` 读到的实付数），并受 `max_budget_usd` 约束。
- 两道上限：
  - 单次调用的行数上限，做成一个常量，暂定 20,000；最终值由主会话实测每千行成本后定。超过时不截断，直接报错并说明上限。
  - 运行预算：分类途中预算用完就停，报告已完成多少行、已存下的标签在哪里。
- 取数走只读网关：产品侧用 `gateway.query`；DAB 侧和 `query_database` 走同一条受检查的路径（SQL 库和 Mongo 都要能取）。
- 工具返回事实，不返回猜测：模型返回的标签不在类别列表里、或某批调用失败时，如实报告行数，不默默归到某一类。

## Authority

- May edit: `src/data_agent/`（新模块、`mcp_server.py`、`agent.py` 里和花费计入有关的部分）、`evals/dab_server.py`、`evals/dab.py`（只限把模型设置和花费接通）、`evals/spend.py`（只限读到工具的花费）、`tests/`（新测试和 `tests/fixtures/` 里的新样本）、`pyproject.toml` 与 `uv.lock`（只限需要一个调模型的 HTTP 客户端依赖时）
- Must not edit: `skills/`、查询与 dbt 任务的提示、`src/data_agent/configs/*.toml`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- 诊断（ADR-0005 Update 2026-11-14）：有的题要按文章内容分类再统计。agent 现在只能逐篇读（耗尽轮数或上下文）或用关键词近似（答错）。
- 工具服务器是独立的 stdio 子进程：产品侧 `agent.py:710-722` 启动 `python -m data_agent mcp-server`，DAB 侧 `evals/dab.py:388-393` 启动 `evals/dab_server.py`，环境变量由 `server_env()` 给。子进程现在拿不到模型的端点和密钥；怎么传进去由实现定，但密钥不能出现在事件、日志、工具输出和 `dab_tool_calls.jsonl` 里（`EventLog` 的 `secrets=` 是现有的遮蔽手段）。
- 端点是 Anthropic 兼容的（`sdk_env`，`agent.py:589-617`：`ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`）。仓库现在没有直接调模型的代码，也没有 `anthropic` 依赖（`pyproject.toml:7-14`）。
- 花费现在怎么算：`Spend`（`agent.py:341-501`）只看 SDK 的消息流和 CLI 写的 transcript；工具进程里的调用它看不到。要设计一条通道把工具的 token 用量带回运行（例如工具结果里带用量、或写进事件文件），让 `Spend.check` 能据此停下，`result` 事件的 `cost_usd` 和 `evals/spend.py` 都包含它。价目表在 `PRICES`（`agent.py:228` 起）。
- 结果怎么存：
  - DAB 侧已有 `Results`（`evals/dab_server.py:228-260`），查询结果按变量名存下来给 `execute_python` 用；标签表照这个办法存。
  - 产品侧的网关只读，查询任务没有 Bash（`QUERY_BUILTIN_TOOLS`，`agent.py:84`）。标签表放工作区文件；后续 SQL 怎么用到它（例如工具提供按标签过滤或聚合的后续调用，或网关允许读这一个文件）由实现定，要在最终回复里写明并有测试。做不到"后续 SQL 或 Python 能用"就停下交回。
- 基础层工具的注册方式看 `mcp_server.py:165-200` 和工单 37 加的事实类工具；`tool_annotations.py` 有 `READ_ONLY`。
- 调用会很久：两万行要很多批。看清工具调用的超时（MCP 客户端超时、工单 54 的 trial 截止时间 `deadline`、DAB 侧 150 秒查询超时只管查询本身），批次可以并发，但要有上限。
- 结果要可复现：温度设 0；同一批文本和类别，提示固定。

## Acceptance

- [ ] 新样本：`tests/fixtures/` 里一份非基准来源的小文本集（自己写的短文本，带期望类别），不含任何 DAB 数据。
- [ ] 新测试（假模型，不联网）：对样本分类后，返回的各类行数正确，标签表里每行有键和标签，后续查询或 Python 能读到它。
- [ ] 新测试：超过单次行数上限时报错，不调模型。
- [ ] 新测试：假模型返回类别列表之外的标签、或某批失败时，返回值如实报告这些行，不归入任何类别。
- [ ] 新测试：预算在途中用完时停下，返回已完成行数，已完成的标签留着。
- [ ] 新测试：工具的 token 用量进了运行的 `cost_usd`，并能让预算检查停下运行。
- [ ] 新测试：三个配置开关任意组合下，产品和 DAB 两个服务器都注册了这个工具；取文本的查询是写操作时被网关拦下。
- [ ] 新测试：模型密钥不出现在事件、工具输出和工具调用日志里。
- [ ] `make check` 通过；`make test-integration` 里加一条真调模型的测试（对样本分类，主会话跑）。

## Evidence required

- 测试输出。
- 主会话在合入前做真实运行：跑新加的集成测试；再本地跑一道要用到分类的查询题（用样本库），贴出工具调用、返回值和 `result` 事件里的花费；量出每千行的实付成本，据此定行数上限的最终值。工单会话在最终回复里写明命令。

## Stop conditions

- 工具的花费没法可靠地计入运行花费和预算上限。
- 产品侧做不到让后续 SQL 或 Python 用上标签表。
- 需要改 Skill 或提示才能让工具可用（那是另一张工单的事，交回讨论）。
- 需要多于一个新依赖。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0.14, main session's real runs (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted
- Human review minutes: — (filled by the human at merge, optional)

## Comments

### 实现（分支 worktree-70-classify-text-tool；e1ce29a、6e397da、d7871f9）
- 工具 `classify_text(sql, key_column, text_column, categories, instructions="")`，DAB 侧多一个 `db_name`。分类逻辑只有一份，在 `src/data_agent/classify.py`；`mcp_server.py` 和 `evals/dab_server.py` 都在基础层注册，所有配置都有。
- 取数：先跑 `SELECT count(*) FROM (<sql>)`，过上限就报错，不调模型。网关的行数上限是 10,000，超过时按 key 排序分页（LIMIT/OFFSET）。产品侧走 `gateway.query`；DAB 侧 SQL 走 `governed` 加 `query_tool.exec`，Mongo 走 DAB 自己的 JSON 请求，都和 `query_database` 同一条路径。
- 调模型：用 `anthropic` SDK（唯一的新依赖），端点和密钥取本次运行的 `ModelSettings`，密钥以 Bearer 方式发送，和 CLI 一致。temperature 0 放在 `extra_body` 里发（这个 SDK 的 create() 没有 temperature 参数），提示固定。每批最多 50 行、30,000 字符，最多 8 批并发。
- 结果如实：模型给的标签不在类别里（`outside_categories`）、回复里漏了这一行（`no_label`）、调用失败（`failed`）、文本为空（`empty_text`）的行都不归入任何类别，单独计数，错误信息和模型给的标签都做了遮蔽。
- 标签表：
  - 产品侧存成进程内存里 DuckDB 的 `labels_<n>` 表，另写一份 `<workspace>/data_agent_labels/labels_<n>.csv`。后续 SQL 用新工具 `query_labels`：走同样的只读检查和行数上限，这个库禁止访问外部文件。
  - 它连不到原库的表，所以要做联合统计，就在分类查询里把需要的列一并取出，这些列会留在标签表里。测试：`test_rows_are_counted_per_category_and_kept_for_later_sql`。
  - DAB 侧和查询结果一样，存成 execute_python 的变量（`Results.keep`）。
- 花费通道（`src/data_agent/tool_spend.py`）：每次运行在工作区外有一个 ledger 目录（`DATA_AGENT_SPEND_DIR`），和模型设置一起通过 SDK 子进程的环境传给工具服务器，不进 argv，也不进 MCP config。
  - run 一侧把预算、价格和自身花费写进 `run.json`；工具一侧把每次模型调用的 token 追加到 `tools.jsonl`。
  - `Spend.cost_usd()`、`Spend.check` 和所有 result 事件都计入工具花费，result 事件多出 `tool_*_tokens` 和 `tool_cost_usd` 字段。`evals/spend.py` 读的是 `cost_usd`，所以不用改。
  - 工具每开一批之前，先检查"运行花费 + 已花的工具花费 + 每个在途批次预留一次最贵调用的花费"是否已到预算。到了就停，返回已完成多少行，已完成的标签留在表里。
- 已知限制：
  1. 运行的模型不在 `PRICES` 里时，没有 ledger，工具直接报错，不调模型（没有价格就无法守住预算）。这需要人来定。
  2. 工具在 trial 截止时被杀掉的话，这次调用的标签没有写出来，但已花的 token 已经记进 ledger，会计入花费。
  3. 分页时 count 查询和每一页是分开的查询，中间数据变动的话可能漏行或重复（重复会被拒）；只读数据集上没有影响。
  4. Skill 里没有提到这个工具，agent 只能靠工具描述发现它；按工单要求，Skill 不在本工单改。

### 越出 Authority 字面的改动（都很小，且必需）
- `tests/fixtures/fake_dab/.../fake_pymongo.py`：加了 `documents` 字段，用来测 Mongo 取数。
- `tests/test_dab.py`：工具集合里加上 `classify_text`。
- `tests/test_postgres.py`：SDK env 里多出工具用的变量；增加两个参数，测 `--postgres-env` 不能用工具的变量名。
- `agent.py`：新增 `ModelSettings.tool_env`，并在 `postgres_env` 冲突检查里加上这几个变量名，属于工单 Context 要求的"把模型设置传给子进程"。

### 第 3 步：真实运行（未做，交给主会话）
工单会话不能加载模型的 env 文件，所以没有真实调过模型。本地的端到端行为由 stdio 测试覆盖：用假模型端点，产品和 DAB 两个服务器都跑过。请主会话运行：
1. 集成测试，输出里有 token 数和花费：
   set -a && . ~/.config/data-agent/env && set +a && uv run pytest -q -s -m integration tests/test_classify.py
   用打印出的 20 行花费 × 50，得到每千行的实付成本，据此定 `ROW_LIMIT`。
2. 本地查询题（样本库，去掉类别列）：
   mkdir -p runs/70/ws
   uv run python -c "import duckdb; c=duckdb.connect('runs/70/articles.duckdb'); c.execute(\"CREATE TABLE articles AS SELECT id, text FROM read_csv('tests/fixtures/classify_texts.csv')\")"
   set -a && . ~/.config/data-agent/env && set +a && uv run data-agent run --type query --config baseline --duckdb runs/70/articles.duckdb --workspace runs/70/ws --events-dir runs/70/events --task "How many of the texts in the articles table are about sport, weather, cooking and music respectively? Classify each text by its meaning."
   贴出 classify_text 的 tool_start/tool_end 和 result 事件里的 cost_usd 与 tool_cost_usd。

### 测试输出
- make check：ruff、格式、basedpyright（0 errors）全部通过；pytest 2121 passed，1 skipped，39 deselected（321 秒）。
- tests/test_classify.py：35 passed，覆盖全部验收项（计数和标签表、行数上限、类别外标签/失败/漏标/空文本、预算中途停、并发批次不超预算、花费进 cost_usd 并能停下运行、8 种开关组合都注册、写操作被拦、密钥遮蔽、DAB 的 SQL/Mongo/空结果）。并发测试连跑 8 次稳定。

### 审查
- da-review（Codex）第 1 轮，三条都修了（6e397da）：
  - P1 并发批次可能超预算：首个调用返回前只跑一批，之后每个在途批次预留一次最贵调用的花费；
  - P1 类别外的标签没有遮蔽：已遮蔽；
  - P2 DAB 空结果没有列名、报错：改为 0 行。
- eng:code-review 第 2 轮（d7871f9）：
  - 已修：Spec 3，docstring 里"最多超一次调用"不准确，改成如实描述；Standards 2，模型 env 变量名定义了两份，改用 `ModelSettings.from_env`。
  - 不修及理由：
    - Spec 1（无价格的模型不能用工具）：交给人定，见已知限制 1。
    - Spec 2（dbt 任务的 Bash 可能 echo 出密钥）：同一个值本来就在 CLI 环境里（`ANTHROPIC_AUTH_TOKEN`），没有新增暴露；而且命令守卫只允许 dbt、两个脚本和打印 DB_TYPE。
    - Spec 4：见已知限制 3。
    - Spec 5–7：见上面"越出 Authority"一节；query_labels 是工单允许的"后续调用"。
    - Spec 8：产品侧工具输出的遮蔽已由 `test_labels_outside_...` 直接断言（`API_KEY not in text`）。
    - Standards 1（Tokens 和计价重复）：合并要动 agent.py 里很多现有用法，tool_spend 也会反过来依赖 agent，超出本工单范围。
    - Standards 3–8：重复检查是为了在取数前就失败；`records` 和 `as_result` 用途不同；`tool` 字段留作日志；名字、模块拆分属于判断题，不改。

### 主会话 2026-11-14
- 接受越出 Authority 字面的改动（两个现有测试、一个假库、`query_labels`）。
- 合并 main（含 68）进分支：04e4da6，无冲突。
- 真实运行（deepseek-flash；临时 worktree；首个提交 e1ce29a 和合并后的 04e4da6 各跑一遍，结果一致）：
  - 集成测试 `uv run pytest -q -s -m integration tests/test_classify.py`：1 passed，20 条全部分对；1 次模型调用，586 input、322 output token，$0.0006。
  - 查询题（04e4da6，baseline，20 行样本库）：agent 调了 `classify_text`（sql `SELECT id, text FROM articles ORDER BY id`，四个类别，带 instructions），返回 "20 rows fetched; 20 labeled"，四类各 5；最终答案 "sport: 5, weather: 5, cooking: 5, music: 5"。`result` 事件：`cost_usd` 0.003447，其中 `tool_cost_usd` 0.000548（639 input、297 output）。
  - 每千行成本（直接调工具，1,000 行 = 20 条样本 × 50；无缓存的首次调用）：短文本（约 25 token/行）20 次调用、25,460 input、5,258 output，$0.0139，墙钟 4.9 秒；长文本（约 290 token/行）45 次调用、290,654 input、6,775 output，$0.0953，墙钟 8.2 秒。重复同样的文本时端点会命中缓存，成本更低（$0.0076、$0.0124），不作为依据。
  - 行数上限：维持 20,000。短文本 20,000 行约 $0.28，在单次运行 $1 预算内；长文本约 $1.9，由运行预算拦停，不靠行数上限。
- PR #78：CI 绿；Codex PR 审查（04e4da6）没有意见；squash 合入 main（2486759）。待人定：模型不在 `PRICES` 里时工具拒绝调用（已知限制 1），人不同意再开后续工单。
