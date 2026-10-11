# 上游产品形态取证：用户、写操作、形态、资产接入、部署、人工介入

日期：2026-10-11。方法：`git clone --depth 1` 后读 README、docs 与代码。路径相对各仓库根目录；未核实处标 `未确认`。

| 简称 | 仓库 | HEAD |
|---|---|---|
| SP | SignalPilot-Labs/SignalPilot | f5eee22, 2026-09-28 |
| SPP | SignalPilot-Labs/signalpilot-plugin | f7b0c09, 2026-06-19（比 SP 仓库内副本旧；副本另有 tableau/dashboard/github/notebook skills） |
| nao | getnao/nao | 5bde830, 2026-10-09 |
| dash | agno-agi/dash | 6d86fca, 2026-07-10 |
| ktx | Kaelio/ktx | 49a4ae6, 2026-07-07 |

ktx 是供其他 agent 调用的上下文层，本身不是 agent（`README.md:32`, `:76`）。

## 1. 目标用户与场景

- SP："Agentic Data and Platform Teams"（`README.md:21`），"analytics-engineering tasks"（`:54`）；例子是建 dbt 模型（`:72`）与分析师式提问、做看板（`docs/docs/product/chat.mdx:27-32`）。
- SPP：Claude Code 用户（`README.md:106`）；"build a dbt project or write SQL"（`:71`）。
- nao："data teams" 与 "business users"（`README.md:38,45`）；数据团队搭上下文并给 agent 写单测（`:40-42`）。
- dash：未写画像；"Ask a question in English, get a correct, meaningful answer"（`README.md:102`）。
- ktx：想让 Claude Code/Codex/Cursor/OpenCode 按核准口径查数仓的人（`README.md:95-99`）。

共识：两类人——维护上下文/建模型的数据工程师，与在聊天里提问的业务方。只有 SP 以建 dbt 模型为主打，其余以回答问题为主。

## 2. 写操作

- SP：交互查询用 sqlglot 语句类型黑名单拦 Create/Drop/Alter/Insert/Update/Delete/Merge/Truncate/Grant/Command 等（`signalpilot/gateway/gateway/engine/denylists.py:9-25`，`engine/validation.py:143-146`，CTE 内嵌 DML `:191-203`）。dbt 流量走单独代理，只放行 dbt-postgres 实际发出的 DDL/DML 并拦破坏性语句（`engine/dbt_validation.py:1-12,270`，`dbt_proxy/forwarder.py:57`）。验证子 agent 只读（`README.md:98`）。
- nao：默认拦截，管理员开关 `dangerouslyWritePermEnabled` 才放开（`apps/backend/src/agents/tools/execute-sql.ts:45-50`）；上下文修改走 PR（`services/context-pr.service.ts:158-164`）。
- dash：分析角色靠 Postgres `default_transaction_read_only=on` 只读（`db/session.py:94-106`）；工程角色只能写 `dash` schema（`dash/tools/build.py:23-50`）。
- ktx：设计上只读，每个连接器都调 `assertReadOnlySql`（`packages/cli/src/context/connections/read-only-sql.ts:91-98`）；上下文以 YAML/Markdown 写进项目、按 git diff 审。

共识：生产数据默认只读，由代码（AST 或数据库设置）强制；允许写的地方都被隔离（dbt 代理、专属 schema、默认关的开关）；dbt 与上下文的改动是文件改动，走 git/PR。

## 3. 产品形态

- SP：Claude Code/Codex 插件、MCP、Web 聊天与看板、Slack（`README.md:36-40`）；主入口是插件（`:36`），其他 MCP 客户端为实验性（`:39`）。
- SPP：Claude Code 插件 + MCP。
- nao：`nao chat` Web UI 为主（`README.md:105-111`），另有 Slack/Teams 等路由与给编码 agent 的 skills。
- dash：AgentOS Web UI、Slack、终端、REST API；主次 `未确认`。
- ktx：CLI + 本地 MCP 守护进程，供编码 agent 调用（`README.md:76,164`）。

共识：两派——编码 agent 里的能力（SP、ktx）与自有聊天应用（nao、dash）；SP 两者都做。

## 4. 接企业已有资产

- SP：11 个连接器（`README.md:237`）；扫描 dbt 项目（`:79`）、Notion（`:261`）、知识库（`:260`）。
- nao：多种数仓（`cli/README.md:92`）；仓库/dbt、MetricFlow、Notion、Confluence、Metabase、Tableau 等经 `nao sync` 同步成文件（`cli/nao_core/config/base.py:22-31`）。
- dash：只有 Postgres（`README.md:342`）；手写 JSON/SQL 知识文件。
- ktx：10 种数据库（`README.md:106`）；dbt、MetricFlow、LookML、Looker、Metabase、Notion、Google Drive、历史 SQL（`packages/cli/src/context/ingest/adapters/`）。

共识：dbt、语义层、BI、wiki 作为上下文，多数是先同步成文件再让 agent 搜，而不是实时 MCP。

## 5. 自托管

SP 用 docker compose，必需 Postgres、MinIO、gateway、web、sandbox 等（`docker-compose.yml:20-407`）；nao 可 `pip install` 或单个 `docker run`，默认 SQLite；dash 用 compose + pgvector；ktx 用 `npm install -g`，无托管服务。共识：Docker Compose 为常态，最轻的是 pip/npm + 本地文件。

## 6. 人工介入

- SP：聊天里 `CLARIFICATION_REQUESTED:` 暂停运行（`standalone_chat/worker.py:77,451-461`）；查询审批只按成本、默认关（`governance/query_planner.py:213-231`）；知识条目 `pending` 待人批准（`docs/docs/product/knowledge-base.mdx:118-124`）。
- nao：`clarification` 工具给 2–5 个选项后停下等待（`agents/tools/clarification.ts:5-27`）；上下文修正走 PR。
- dash：只有提示词约束（`dash/instructions.py:215`）。
- ktx：矛盾标给人审（`README.md:67-68`），上下文按 git diff 审。

共识：人工关卡放在上下文/知识的变更上（审批队列或 PR），而不是逐条查询；聊天产品有显式的澄清并等待工具。
