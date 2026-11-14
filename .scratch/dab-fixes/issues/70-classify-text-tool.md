# 70 — 基础层的语义分类工具

Status: ready-for-agent
Blocked by: —
Spec: ../../../docs/adr/0005-dab-integration.md（Update 2026-11-14）
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
