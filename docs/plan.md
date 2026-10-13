# 大致规划

写于 2026-10-11。这是当前的工作方向，不是规则：每一条都可以随讨论或实测结果修改，改了就直接更新本文件。已讨论过的部分标"已讨论"，其余是待讨论的默认想法。

## 1. 产品与业务（已讨论，2026-10-11）

依据：HANDOFF §3，上游取证 `docs/research/2026-10-11-upstream-product-shape.md`。

- **定位**：开源、可自托管的通用 data agent，对标 SignalPilot，项目主体是 agent 的行为。
- **用户与场景**：分析师或数据工程师给出业务问题或数据任务，agent 探索数据源、必要时澄清、执行、自检、交付。
- **任务类型**：目前两类——dbt 分析工程、跨库查数。原来的"SQL 与 CRUD 管理操作"先不做：上游五个项目都默认只读、写操作都被隔离，和我们的只读网关 + dbt 写入一致。
- **形态**：agent 本体是命令行工具（ADR-0001），外面加网页界面（见第 2 节）。Slack、对外 MCP 暂不做。
- **数据源**：Postgres、DuckDB、dbt 项目；MongoDB 只在 DAB 评测里用（ADR-0005）。"文档"暂时指项目里已有的文件（dbt yml、README、榜单自带的库说明），用文件工具读；Notion/Confluence 之类的连接器以后再说。上游多数也是把这些先同步成文件再让 agent 搜。
- **一期大致算完成**：沿用 HANDOFF §3 的验收线——DAB 与 data-eng-bench 超过基线，消融能拆出各层贡献，能接任意 Postgres + dbt 跑。具体怎么测放到第 4 节。

## 2. 求职呈现（已讨论，2026-10-11）

- **做一个网页界面**，模仿上游，不追求轻量，合适就行。大致两块，照 SignalPilot 的"对话 + 评测"划分：
  - 运行页：提问后实时看到 agent 的过程（工具调用、SQL、验证结果、澄清提问），最后给答案、表格或图。
  - 评测页：基线与各层消融的分数、成本；点进单题可回放轨迹和失败归因。
- **暂定模仿 SignalPilot**（`signalpilot/web/app/chats/`、`app/evals/`，聊天组件用 `@assistant-ui/react`）：它是总对标，同时有对话和评测两块，它的对话 agent 也跑在 Claude Agent SDK 上（`notebook-server/signalpilot/_server/ai/standalone_chat_tools.py`）。澄清交互和图表可以参考 nao（`apps/frontend/src/components/chat-messages/`）。整体不 fork：两者都绑着各自的后端。真正动手做界面时再核一次、定下来。
- **时机**：agent 和评测跑通后再做界面。但 agent 一开始就把运行过程输出成结构化事件，让界面、评测回放、trace 共用（带到第 3 节）。
- **界面以外**：README（一句话说清楚 + 运行页动图）、榜单提交链接、消融与失败归因报告；博客可选。trace 以自己的事件文件为准，在界面里回放；另接 Langfuse 作为可选的 trace 看板（见第 5 节）。

## 3. agent 设计（已讨论，2026-10-11）

依据：`docs/research/2026-10-11-upstream-agent-design.md`；之前已定的 ADR-0002（验证工具）、ADR-0003（上下文工具契约）、ADR-0005（FM1–FM4）和 CONTEXT 词条不变。大体照 SignalPilot 做：

- **主循环**：一个主 agent，一次 `query()`，先加载工作流 skill——dbt 任务用 dbt-workflow（8 步），查数任务用 sql-workflow + 方言 skill。最大轮数暂按 SP：dbt 200、查数 50（大库 75）。验证者用 SDK 的 `agents=` 定义（内容照抄 SP 的 md），这样每次运行按配置开关各层，方便消融。
- **先抄的 skills**：dbt-workflow、dbt-write、dbt-debugging、sql-workflow、duckdb-sql、sqlite-sql、verifier、value-verifier，外加自补的 postgres-sql。其余（snapshots、versioning、testing、domain-*、write-report）等失败归因说明需要时再加。
- **验证**：照 dbt-workflow 第 8 步——结构、数值两个只读验证者并行，逐项给"通过 / 失败 / 需更多信息 / 不适用"；主 agent 先复现失败再做最小修改，改过的模型重审；全部通过或不适用为止，受最大轮数约束。只用于 dbt 任务；查数任务用 sql-workflow 自带的验证步骤。
- **澄清**：产品里用 nao 的工具形式（一个问题 + 2–5 个可点选项，问完本轮结束等回答），规则照 SP"先探索、只在歧义会改变答案时问；不问就在答案里写明假设"。跑榜时移除该工具。
- **Memory**：照 SP 的知识库——agent 提议、人在界面里批准后才生效，按连接分开存。评测里沿用 CONTEXT 的口径：每题从空开始、题后丢弃，避免题与题、次与次之间串信息。
- **事件流**：照 SP 聊天，每次运行一份只追加、带序号的事件日志；事件类型取与 agent 过程有关的一部分（状态、文本增量、工具开始/完成、SQL、澄清请求、错误、结果含花费与 token），验证者的开始结束用 hooks 记。界面按序号续读，评测回放和 Harbor 轨迹都从这份日志转出。
- **失败 → 改 Skill**：照 SP 赛后自诊断，只看开发集失败题；agent 写报告（FM1–FM4 分类、引用出问题的 skill 原文、给具体修改），建议必须通用、不针对单题；人审后再提交。不做全自动改写。

## 4. 评测设计（已讨论，2026-10-12）

依据：`docs/research/2026-10-12-upstream-eval-design.md`；ADR-0004（开发集/留出集、6 个消融配置、主指标）、ADR-0005（DAB 接入）、CONTEXT 的 Baseline 定义不变。

- **算分**：照 data-eng-bench 榜单代码（`leaderboard/src/leaderboard/core/metrics.py`）——准确率 ± 按题标准误，外加 pass@k；出错的运行记 0。DAB 用它自带的统计脚本。本地数字和榜上同一算法。
- **超过基线**：留出集上按题配对做 bootstrap，差值 95% 区间不含 0 才算超过；每一层的消融贡献也这样报，未显著或负贡献照实写。上游没人做显著性检验，这是按学术惯例补的。
- **防看答案**：data-eng-bench 由 Harbor 在 agent 跑完后才拷入测试；我们照官方配置关掉网页搜索与抓取（题和答案在 GitHub 公开）。
- **成本**：每次运行记未缓存输入、缓存输入、输出 token 和美元花费（取自 SDK `ResultMessage`），经 Harbor `populate_context_post_run` 回填；报告给逐题明细和总量。
- **接任意 Postgres + dbt**：dbt 官方 `jaffle-shop` + 本地 Postgres，约 5 个用例，格式照 SP `e2e-eval/eval-set/eval.json`（查数核数值带容差；改 dbt 核模型重建、行数、粒度、列），再加 1–2 个越权反例确认只读网关能拦住。
- **DAB 基线**：和最终配置一样每题 5 次；预算不够怎么削减放到第 7 节。DAB 自带 ReAct 的榜上分数只作榜单对照。
- LiveSQLBench（二期）不做 CRUD，只跑 180 道 SELECT 题。

## 5. 技术栈（已讨论，2026-10-12）

依据：上游各仓库的 `pyproject.toml` / `package.json` / Dockerfile 与 PyPI、npm 当前版本（2026-10-12）。模型与配置方式见 ADR-0001 Update，不变。

- **agent 本体**：Python 3.12（SP 网关、Harbor 要求 ≥3.12；data-eng-bench 镜像是 `python:3.12-slim`）；uv 管依赖，锁文件进 git；`claude-agent-sdk` 锁精确版本（SP 锁 `==0.2.157`，当前 0.2.165）；官方 `mcp` Python SDK 2.x，stdio 方式运行（SP 用 `mcp>=2.2,<3`）；只读网关照搬 SP `engine/`（sqlglot）；数据库驱动 `duckdb`、`psycopg` 3。
- **检查与测试**：ruff、pytest + pytest-asyncio、basedpyright（SP notebook 在用）。
- **界面**：后端 FastAPI + uvicorn，用 SSE 推事件（SP、nao、Harbor 都用 FastAPI）；前端照 SP `web/package.json`——Next.js 16、React 19、`@assistant-ui/react`（MIT）、Tailwind 4、zustand、swr、recharts、vitest，pnpm 管依赖。
- **数据存储**：
  - 运行事件：每次运行一个 JSONL 文件，是唯一的原始记录（上榜轨迹、失败归因、回放、成本都从它来；Harbor 容器里只能靠文件带出记录）。
  - 应用状态：SQLite，存运行索引和 Memory 的待批准/已批准条目（nao 的默认做法）。
  - Postgres 只当被查询的数据源（jaffle-shop、DAB），不存应用状态。
  - trace 看板：Langfuse 作为可选导出，同一份事件多写一份，开关控制、不开不影响功能。不用 LangSmith：它只有企业版能自托管，和"开源可自托管"不符。Langfuse 自托管要 6 个容器，放 Linux 服务器上，和界面一起做。SDK 能否直接导出 OpenTelemetry 未确认，做的时候再核。
- **评测**：Harbor（`uv tool install harbor`，当前 0.24.0）跑 data-eng-bench；DAB 用自写的小 runner，从本地 clone 按路径 import 它的脚手架（DAB 无许可证，不复制进仓库）；统计只用 numpy，算分复用 data-eng-bench 的 `metrics.py`。
- **dbt 版本**：data-eng-bench 镜像固定 `dbt-core>=1.8,<1.11`、`dbt-duckdb==1.10.0`、`duckdb==1.2.2`（`base-image/Dockerfile:19-21`）。Harbor 里把 agent 装进独立虚拟环境，dbt 用镜像自带的；本地开发和 jaffle-shop 也锁 dbt 1.10.x，与评测一致。
- **形态与许可**：一期是命令行 + 网页，不做 Slack；仓库 Apache-2.0，从 SP 复制的文件保留原版权头并在 `NOTICE` 注明。
- **运行环境**：Mac 做开发与单元测试，批量评测和 DAB 在 x86_64 Linux 服务器上跑（见 `docs/setup.md`）。

## 6. 开发环境（已讨论，2026-10-12）

依据：SP `CONTRIBUTING.md`、nao `AGENTS.md` / `CLAUDE.md` / `.husky/pre-commit` / `cli/Makefile`、ktx `AGENTS.md` / `.pre-commit-config.yaml`、Harbor `AGENTS.md`。以下大多"先写下，后续统一配置"。

- **流程与分工**：`/to-spec` → `/to-tickets` → 每个工单 `/implement`（在约定的测试接缝上 TDD）→ 代码审查 → 提交。一个工单一个分支（如 `t/01-tracer-bare-agent`，照 SP `CONTRIBUTING.md:209-216`），你看过 diff 后合进 main。你把关：spec 的测试接缝、工单拆分、每个分支的 diff、push 与榜单提交。
- **代码审查用不同的模型**：写代码的 agent 和审查的 agent 用不同模型。审查用 Codex，模型按你指定的 GPT-6 astra、high 推理（本机 Codex 当前默认是 `gpt-6.1-sol`，配置时核实确切模型名）。审查结论同样交给另一个模型复核。
- **检查命令**：`make check` = `ruff check` + `ruff format --check` + `basedpyright` + `pytest`（只跑单元测试）；做界面后加前端 lint、类型检查、vitest。每次改完都跑，提交前必须通过。
- **提交前检查**：照 ktx 用 pre-commit——行尾空格、yaml/json/toml 校验、拦大文件（防轨迹误提交）、ruff，外加密钥扫描（如 gitleaks；SP 在 CI 里用 trufflehog）。
- **测试三层**：单元测试（只读网关、MCP 工具接 DuckDB 小样本、事件转换、算分；不联网不调模型，`make check` 跑）；集成测试（真调模型：单题 data-eng-bench、jaffle-shop 冒烟；用 pytest 标记默认跳过、手动跑，照 SP `e2e_cloud` 标记）；评测（服务器上跑 Harbor，见第 4 节）。
- **编码规范**：`CODING_STANDARDS.md` 给审查者读。照 SP：Python 3.12+、类型注解写全、ruff、新逻辑配测试；本项目专属：Skill 不含针对榜单的内容（ADR-0005）、不绕过只读网关、上游复制的文件保留版权头。只写审查者能逐条核对的规则。
- **AGENTS.md** 只放导航指针（检查命令、编码规范、`plan.md`），不堆规则。
- **目录**：`src/data_agent/`（照 Harbor `src/harbor/`）、`skills/`、`evals/`、`tests/`，以后加 `web/`。运行产出（如 `runs/`）不进 git，只提交汇总报告。
- **密钥与敏感信息**：
  - API key 只放 `.env`（不进 git）或系统环境变量；服务器 IP 不写进仓库，用 `~/.ssh/config` 的主机别名（如 `data-agent-server`）。
  - 配置 coding agent 不读这些：Claude Code 在 `.claude/settings.json` 的 `permissions.deny` 里禁止读 `.env*`、密钥目录、`~/.ssh`；Codex 做对应设置。注意只禁 Read 工具挡不住 shell 里 `cat`，需要连同 Bash 规则或沙箱一起配，配置时核实各自支持到什么程度。
  - 服务器用 SSH 密钥登录（私钥只在本机）。
  - "全部跳过权限检查"只在 Harbor 容器里用，本机不用。常用安全命令（`uv run`、`make check`、只读 git）预先放行。
- **远程仓库与 CI**：已建私有仓库 `github.com/kevinWangSheng/data-agent`（2026-10-12），以后转公开——转之前再扫一遍历史里的密钥与 IP。CI 用 GitHub Actions，push 时跑 `make check`。

## 7. 运行与评测环境（已讨论，2026-10-12）

机器与账号见 `docs/setup.md`。这里只记花费。

- **上限**：一期模型花费暂定 $300，只用 DeepSeek（默认 `deepseek-flash`）。工单 01 用 5 题实测单次成本后重算，再决定上限和模型。
- **Claude**：不跑全量。103×3 次推算约 $400，承受不起。最多用 Sonnet 5.5 跑工单 01 的 5 题对照（约 $7）；实测后如果确实便宜，再单独讨论。
- **推算**（未实测）：一期约 1,700 次运行（留出集 540、全量 309、DAB 最终 270、DAB 基线 270、开发集迭代约 300）；按每次约 200 万输入 token（85% 命中缓存）+ 4 万输出估，flash 约 $0.15/次，合计约 $250。单价取自 ADR-0001 Update（2026-10-05）。
- **护栏**：每次运行设 SDK `max_budget_usd`（flash 先设 $1）；累计花到上限的 80% 就停下来找人。
- **超支时的降级顺序**：DAB 基线每题 5→3 次 → 留出集每题 3→2 次 → 去掉"全开旧版 Skill"配置 → 留出集 30→20 题。上榜要的 data-eng-bench 103×3 与 DAB 54×5 不降。
- 批量评测尽量放在 DeepSeek 闲时半价时段跑（具体时段配置时核实）。

运行时还要注意的（依据 data-eng-bench@a3278ad、harbor@dc933be）：

- **上榜轨迹是公开的**（`leaderboard/SUBMIT.md:45` "Trials must be publicly readable"）。上传 Harbor Hub 前，从轨迹和事件文件里清掉 API key、`ANTHROPIC_BASE_URL` 之外的私有地址、服务器 IP 与主机名；写一个清理脚本，上传前强制跑，并在单元测试里覆盖。
- **报错按 0 分算**（`SUBMIT.md:41`），而 Harbor 默认不重试（`src/harbor/models/job/config.py:293-295`）。只对模型接口的临时错误（限流、5xx、断连）开重试；超时、拒答不重试，照 Harbor 默认的排除列表（`:303-315`）。本地消融把"基础设施报错"和"agent 失败"分开统计，前者重跑，不进分数。
- **服务器与并发**：每题容器 2 核 / 8 GB、agent 超时 4000 秒（`tasks/*/task.toml`），官方配置并发 4（`configs/data-eng-bench-duckdb.claude-code.yaml`），所以 8 核 / 32 GB 够用。上榜不许改超时和资源（`SUBMIT.md:36-37`），只能靠并发提速；DeepSeek 的并发与限流额度未确认，工单 01 实测。
- **每次运行记版本**：事件文件开头写 git commit、Skill 版本、配置名（基线/全开/关某层）、模型名、SDK 版本。"新旧 Skill 对比"和复现全靠它。
- **结果怎么拿回来**：`runs/` 只留在服务器，用 `~/.ssh/config` 的主机别名 rsync 回 Mac 做失败归因；只把汇总报告提交进 git。
- **trace 看板**：Langfuse/LangSmith 的选择见第 5 节，不变。补一点：评测容器里不往 Langfuse 发（容器只靠文件带出记录），只在本地运行和网页界面里接。

## 8. 实施计划（最后做）

前面几节定了以后，再用 `/to-spec`、`/to-tickets` 重写或修改 `.scratch/phase1/` 的草稿。

- 没有截止时间，每天推进；开发主要由多个 agent 并行完成。拆工单时尽量让工单之间少依赖、能并行，每个工单在独立分支（或 worktree）上做。
