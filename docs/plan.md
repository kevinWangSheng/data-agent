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

## 6. coding agent 开发环境与开发模式（已讨论，2026-10-14；替换 2026-10-12 版）

这一节讲给 coding agent（Claude Code 写代码、Codex 审查）干活用的环境和流程，不是给人用的工具准备。依据：`docs/research/2026-10-13-coding-agent-env.md`（上游仓库与 Claude Code / Codex 官方文档）、`~/dev/AI/report/agentic-software-delivery-research/report.md`（交付流程研究）。全部"先写下，再统一配置"，配置时逐项实测。

### 开发模式

照报告里"以工单和依赖图为控制面"的一类做（round-4 机制比较 §4），借三个案例的具体做法：Stripe Minions 的固定流水线（确定性步骤与 agent 步骤交替）、Simon Willison 的"先跑测试 → 红绿 TDD → 真实运行并留证据"、StrongDM 的外部行为验证（agent 改不到验收标准）。

- **控制面**：工单放 `.scratch/<feature>/issues/`，带"Blocked by"依赖；状态 `ready-for-agent → in-progress → in-review → done`，一个工单一个 agent，开工前先认领。同时跑几个 agent 不设上限，只要工单之间不互相依赖；审查堆积时再调。
- **交接契约**：工单模板补齐报告列的 7 项（`report.md:63`）：目标、权限（可改哪些目录、能否连服务器）、上下文链接、证据要求、当前状态、停止条件、失败处理。
- **隔离**：一个工单一个 worktree，加上沙箱（见下）。worktree 只隔离代码，每个 worktree 还要有自己的 DuckDB 文件、Postgres 库或端口、`runs/` 目录（Armin Ronacher：共享状态要另外分开）。
- **每个工单的流水线**：
  1. 确定性：建 worktree、装依赖，先跑测试拿基线。
  2. agent：在 spec 约定的测试接缝上 TDD 实现（`/implement`）。
  3. 确定性：`make check` 与 pre-commit。
  4. agent：真实运行一次（跑 CLI、单题、截图），命令与真实输出贴进工单 Comments。
  5. agent：两道审查。先 Codex（`da-review`，换模型找 bug，只读）；再由 Claude 开新上下文跑 `eng:code-review`（Standards、Spec 两个子 agent 并行，Spec 对照本工单验收标准；照 mattpocock `/code-review`）。第二道补的是 `codex review --base` 不能带提示词、不对照 spec 的缺口；它和写代码的同为 Claude，独立性靠 Codex 那一道补，`/retro` 发现漏审再改（2026-10-15 定）。每条结果修或在工单写明不修的理由；修复最多 2 轮，不过就转给人。
  6. 人：看 diff 和证据，决定合并。
- **强制程度**：先靠 skill（`/implement` + 新工单模板）约束步骤，只有提交前检查是硬卡；`/retro` 发现哪一步常被跳过，再把那一步改成脚本。
- **外部行为验证**：验收的测试接缝在 `/to-spec` 时由人确认，实现时 agent 不改；留出集只经 `da-remote` 返回汇总分数，agent 拿不到失败细节。
- **人的边界**：spec 的测试接缝、工单拆分、合并到 main、push、上榜提交、超出花费上限。agent 不合并、不 push。
- **反馈**：每批工单后 `/retro`；机械性规则改成检查，需要判断的写进 `CODING_STANDARDS.md`。
- **每个工单记小账**：工单末尾记尝试次数、审查轮数、花费、接受/退回、人审查用时，最后汇总成"用 agent 开发"的数据。

### 环境

- **说明文件**：`AGENTS.md` 是短主文件（约 100–150 行）：检查命令、目录地图、硬规矩（照 ktx 的 MUST / MUST NOT 与 git 安全）、指针；细节放 `docs/` 和子目录 `AGENTS.md`。`CLAUDE.md` 保持 `@AGENTS.md` 导入（官方做法）。
- **自检回路**：
  - 快：`make check` = ruff check + ruff format --check + basedpyright + 单元测试，不联网不调模型，约 1 分钟内，每次改完都跑；按改动面挑最小检查集，慢命令输出存文件再反复看（ktx `AGENTS.md:146-168`）。
  - 小样本：一个很小的 DuckDB 文件测只读网关和 MCP 工具，进 git。
  - 主循环不做测试替身，用真模型的集成测试（pytest 标记，默认跳过，手动跑），密钥在 coding agent 会话里是遮罩过的占位符（见下面的权限一条），只在请求发往模型域名时由沙箱代理换成真值。变量名用 `DATA_AGENT_*`，不用 `ANTHROPIC_*`，免得把本机 Claude Code 也指到 DeepSeek。
  - 我们的 agent 启动时 `env` 只传给它自己的子进程，`setting_sources=["user"]` 不读本仓库的 `.claude/` 设置（照 SP `claude_agent_options.py:185-209`）；不影响本机正在用的 Claude Code。
  - 评测在服务器上跑（第 4、7 节）。
- **权限、沙箱与防泄密**：
  - 提交进 git 的 `.claude/settings.json`：deny 读写 `.env*`、`~/.ssh`、`~/.aws` 等；deny `git reset --hard`、`git clean`、`git push --force`，以及直接执行的 `ssh` / `scp` / `rsync`。
  - 开 Claude Code 沙箱：禁读上述路径；网络白名单（PyPI、GitHub、npm、DeepSeek），新域名第一次弹窗放行；docker 用 `excludedCommands` 排除。
  - 密钥文件放仓库外 `~/.config/data-agent/env`，由 `scripts/da-claude` 加载（2026-10-15：放在仓库里时沙箱禁读让 pytest 收集失败，且 Codex 审查能读到）。
  - 凭证遮罩：DeepSeek key 只以占位符出现在 agent 眼前，由沙箱代理在发往允许的域名时替换（只能配在用户级设置里）。
  - 连服务器：仓库外固定脚本 `~/.local/bin/da-remote`，只接受几个子命令（开始评测、查状态、拉结果），在沙箱外运行，agent 不能改它，输出里的 IP 换成 `<server>`。服务器 IP 只在 `~/.ssh/config`。不装 Tailscale；以后公开部署网页时再定是否用 Cloudflare 代理隐藏源站。
  - Codex：审查一律 `-s read-only`（覆盖全局的 `danger-full-access`）；按路径禁读是 beta，先实测再启用。
  - 转公开前再扫一遍历史里的密钥与 IP。
- **hooks**：
  - pre-commit（照 ktx）：行尾空格、yaml/json/toml、拦大文件、ruff、gitleaks；Claude 和 Codex 都绕不过。
  - Claude PostToolUse：改完 `.py` 自动 `ruff format` + `ruff check --fix`（只处理那个文件）。
  - 先不加 Stop hook；agent 常不跑检查就收工时再加。
- **审查模型**：Codex 用 `gpt-6-astra`（官方最强档，已核实是官方 id），高推理；本机默认 `gpt-6.1-sol` 接近 Astra、成本更低，可作复核或备选。
- **开发期 MCP**：先不配；需要查外部文档时照 ktx 用 Context7。
- **目录**：`src/data_agent/`、`skills/`、`evals/`、`tests/`，以后加 `web/`；`runs/`、`.claude/worktrees/` 不进 git；只提交汇总报告。
- **远程仓库与 CI**：私有仓库 `github.com/kevinWangSheng/data-agent`（2026-10-12），以后转公开；GitHub Actions 在 push 时跑 `make check`。
- **编码规范**：`CODING_STANDARDS.md` 只放需要判断的规则（类型注解写全、新逻辑配测试、Skill 不含针对榜单的内容、不绕过只读网关、上游复制的文件保留版权头）。

### 环境配置剩余项（2026-10-15）

上面已配好并实测的不再列。做完一项删一项。

- [ ] 在 `~/.config/data-agent/env` 照 `.env.example` 填好 key 并 `chmod 600`（人做；agent 读不到，无法核实）。仓库内 `.env` 已移除、settings 禁读已加、沙箱内 `make check` 已通过（2026-10-15，`4c43d33`）。
- [ ] `da-remote`：源码放 `scripts/`，装到 `~/.local/bin/`，子命令只有开始评测、查状态、拉结果，输出把 IP 换成 `<server>`。等服务器就绪（`docs/setup.md` #3）再写、再实测。
- [ ] Codex 按路径禁读（beta）实测，能用再在 `da-review` 里启用。
- [ ] 本机工具（Docker 守护进程、Harbor、git-lfs、psql）照 `docs/setup.md` 在工单 01 开始时装。
- [ ] 流水线端到端试跑一次（等 key 填好）：`da-claude` 启动后遮罩生效、建 worktree + `uv sync` + pre-commit 能跑、`da-review` 审一个真实分支、工单 Ledger 能填。用工单 01 来跑，不另造假工单；各段此前只单独配置过，没有连起来跑过。

## 7. 运行与评测环境（已讨论，2026-10-12）

机器与账号见 `docs/setup.md`。这里只记花费。

- **上限**：一期模型花费暂定 $300，只用 DeepSeek（默认 `deepseek-flash`）。工单 01 用 5 题实测单次成本后重算，再决定上限和模型。
- **Claude**：不跑全量。103×3 次推算约 $400，承受不起。最多用 Sonnet 5.5 跑工单 01 的 5 题对照（约 $7）；实测后如果确实便宜，再单独讨论。
- **推算**（未实测）：一期约 1,700 次运行（留出集 540、全量 309、DAB 最终 270、DAB 基线 270、开发集迭代约 300）；按每次约 200 万输入 token（85% 命中缓存）+ 4 万输出估，flash 约 $0.15/次，合计约 $250。单价取自 ADR-0001 Update（2026-10-05）。
- **护栏**：每次运行设 SDK `max_budget_usd`（flash 先设 $1）；累计花到上限的 80% 就停下来找人。
- **超支时的降级顺序**：DAB 基线每题 5→3 次 → 留出集每题 3→2 次 → 去掉"全开旧版 Skill"配置 → 留出集 30→20 题。上榜要的 data-eng-bench 103×3 与 DAB 54×5 不降。
- 批量评测尽量放在 DeepSeek 闲时半价时段跑（具体时段配置时核实）。

运行时还要注意的（依据 data-eng-bench@a3278ad、harbor@dc933be）：

- **上榜轨迹是公开的**（`leaderboard/SUBMIT.md:45` "Trials must be publicly readable"）。上传 Harbor Hub 前，从轨迹和事件文件里清掉 API key、私有端点地址、服务器 IP 与主机名；写一个清理脚本，上传前强制跑，并在单元测试里覆盖。
- **报错按 0 分算**（`SUBMIT.md:41`），而 Harbor 默认不重试（`src/harbor/models/job/config.py:293-295`）。只对模型接口的临时错误（限流、5xx、断连）开重试；超时、拒答不重试，照 Harbor 默认的排除列表（`:303-315`）。本地消融把"基础设施报错"和"agent 失败"分开统计，前者重跑，不进分数。
- **服务器与并发**：每题容器 2 核 / 8 GB、agent 超时 4000 秒（`tasks/*/task.toml`），官方配置并发 4（`configs/data-eng-bench-duckdb.claude-code.yaml`），所以 8 核 / 32 GB 够用。上榜不许改超时和资源（`SUBMIT.md:36-37`），只能靠并发提速；DeepSeek 的并发与限流额度未确认，工单 01 实测。
- **每次运行记版本**：事件文件开头写 git commit、Skill 版本、配置名（基线/全开/关某层）、模型名、SDK 版本。"新旧 Skill 对比"和复现全靠它。
- **结果怎么拿回来**：`runs/` 只留在服务器，用 `~/.ssh/config` 的主机别名 rsync 回 Mac 做失败归因；只把汇总报告提交进 git。
- **trace 看板**：Langfuse/LangSmith 的选择见第 5 节，不变。补一点：评测容器里不往 Langfuse 发（容器只靠文件带出记录），只在本地运行和网页界面里接。

## 8. 实施计划（最后做）

前面几节定了以后，用 `/to-spec`、`/to-tickets` 写 spec 与工单。2026-10-05 的旧草稿 `.scratch/phase1/` 已于 2026-10-15 删除（git 历史 `f0ccd77` 可查）；`docs/setup.md` 与第 7 节里的"工单 01/08/09/10"是旧编号，写新工单时一并改。

- 没有截止时间，每天推进；开发主要由多个 agent 并行完成。拆工单时尽量让工单之间少依赖、能并行，每个工单在独立分支（或 worktree）上做。

## 9. 初始化推进顺序（2026-10-15）

第 6 节剩余项与第 8 节按下面顺序一项一项做，做完一项再开下一项。

每一项的规矩：

1. 先看证据（仓库文件、上游、命令输出），给出决定和依据；只有不可逆或对外的事才停下来问。
2. 改文件，只改这一项需要的。
3. 验证：贴命令和真实输出；没验证的标 `未确认`。
4. 收尾：在本节勾掉这一项、删掉第 6 节对应的剩余项，单独一个本地提交，不 push。

顺序：

- [x] 1. 审查第二道复核：定做法并配好（第 6 节流水线第 5 步）。
- [ ] 2. Codex 按路径禁读（beta）实测，能用就在 `da-review` 里启用。
- [ ] 3. 本机工具：Harbor、git-lfs、psql 安装命令备好由人执行（要写沙箱外目录）；Docker Desktop 由人启动；`docs/setup.md` 同步（参考仓库已于 2026-10-14 clone 到 `~/dev/AI/data-agent-refs/`）。
- [ ] 4. 人：`~/.config/data-agent/env` 填 key、`chmod 600`。与 3 一样可随时做，不挡 5、6。
- [ ] 5. 第 8 节：`/to-spec` 写一期 spec，人确认验收的测试接缝；分几次讨论。
- [ ] 6. 第 8 节：`/to-tickets` 拆工单与依赖图，改掉 `docs/setup.md` 与第 7 节的旧工单编号。
- [ ] 7. 用工单 01 端到端试跑流水线（依赖 1–4、6）。
- [ ] 8. `da-remote`（等服务器就绪）。
