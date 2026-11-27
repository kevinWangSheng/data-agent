# 158 — 设置存储：sqlite、迁移、四层作用域、播种、生效设置解析、改动记录

Status: ready-for-agent
Blocked by: product-config 143, product-config 144, 157
Spec: ../spec.md（Implementation Decisions"设置的两类键（S7）""作用域与权限"的作用域部分"临时覆盖（S4）"的解析部分"存储与审计（S9）"）
Claimed by: —
Branch: —

## Goal

设置类的键存进 sqlite 单文件（`[server].db_path`，和 `runs_dir` 同卷），按 组织 → 工作区 → 频道 → 个人 四层各存一份覆盖：
- 库带版本号，启动时自动迁移。表：组织、工作区、设置覆盖（按作用域）、会话索引（线程 → 运行、所属频道）、改动记录（S9）。
- 首次启动用 `config.toml` 的设置类键播种组织层；之后改文件不覆盖库；文件和库不一致时能列出来；给"用文件的值覆盖"一个存储层的函数（接口和按钮是 160 的）。
- 每次运行开始解析一次生效设置：代码默认 < 文件 < 数据库（组织 < 工作区 < 频道，或 组织 < 工作区 < 个人）< 单次覆盖；`[policy]` 最后裁剪，越界报错。每个键带来源（代码默认 / 文件 / 数据库的哪层 / 单次覆盖 / 锁定）。
- 每次写入记改动：谁、何时、哪层、哪个键、改前改后；密钥类只记"改过"。
- 网页和 IM 起的运行拿到的是这份解析结果（经 141 契约 4 的"服务器写一份生效配置进运行目录、子进程带 `--config`"那条路）。
- 单独的 `data-agent run`：配了 `[server].db_path` 且文件存在时读组织层，否则只读文件；`data-agent config show` 默认显示组织层，`--workspace`、`--channel`、`--user` 选看某个作用域，标出数据库来源和不一致（定的细节第 11 条）。
- 评测入口不碰库（ADR-0011 第 3 条）。

157 里标 `TICKET_158` 的测试全部转绿。权限检查（谁能写哪层）是 159 的；本工单的写函数接受"写的人"作为参数、不做权限判断。

## Authority

- May edit：
  - 新建 `src/data_agent/settings/`（或 157 契约 1 定的模块路径）：存储、迁移、播种、解析、改动记录。
  - 143 建的配置模块（`src/data_agent/config.py` 或 143 实际建的文件）：加 `[server].db_path`、`[server].org_admins` 两个部署层键的类型；给设置类键加"属于设置类"的标记（S7 的划分），不改 143 的加载与合并。
  - `src/data_agent/cli.py`：`web` 启动时打开库、迁移、播种；`run` 在配了 `db_path` 且文件存在时读组织层；`config show` 加 `--workspace`、`--channel`、`--user`，标数据库来源和不一致。
  - `src/data_agent/web/app.py`、`web/launcher.py`、`web/conversations.py`：只改"每次运行拿到哪份生效配置"（运行开始时解析一次，写进运行目录）。`create_app` 只往后加带默认值的参数。
  - `src/data_agent/im/adapter.py`：只在把作用域（工作区、频道或个人）传给运行时改；新参数带默认值，`ConversationsLike.send` 的现有调用照旧能用。`!configure`、覆盖语法、公告是 161 的。
  - `tests/acceptance/`：唯一例外，删掉 157 文件里的 `TICKET_158` 标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩）。
  - `tests/`（除 `tests/acceptance/`）：单元测试。
  - `docs/setup.md`：`[server].db_path`、`org_admins`、设置类与部署层的划分、`config show` 的来源列。
- Must not edit：
  - `tests/acceptance/` 里除 `TICKET_158` 标记以外的一切；`tests/fixtures/`（含 141 的快照）。
  - `evals/`：评测入口不读库，也不需要改。
  - `src/data_agent/agent.py`：读库只在 `cli.py` 的 `run` 入口，生效配置照旧经 143 的加载结果交给 agent。
  - `web/src/`（160）；159 的登录与权限代码；161 的 IM 命令。
  - `pyproject.toml`：sqlite 用标准库 `sqlite3`，不加依赖（要加就停下）。
  - `Makefile`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。工单 agent 不能 source 模型的 env 文件，不调模型；真实运行交给主会话（见 Evidence required）。

## Context

行号取自 main `30a18e9`。140（in-review）、143、144 会移动这些行，认领时按符号名在当前 main 上核对。

- **决定**：spec"设置的两类键（S7）"（哪些键是部署层、哪些是设置类）、"存储与审计（S9）"；ADR-0012 决定 1、2、7、8；`decisions.md` S2、S7、S9 原文，Q8"`[policy]` 和命令行参数冲突：报错"；`decisions.md`"建 settings 工单时主会话定的细节"第 3、5、6、11 条。
- **验收测试**：157 的 `test_settings_resolve.py`、`test_settings_seed.py`、`test_settings_eval_isolation.py` 里标 `TICKET_158` 的。契约 1–3（模块名、`db_path` 默认、作用域标识、单次覆盖和线程快照的形状）见 157 的测试 docstring 和人审结论。
- **配置层**：143 的加载器与 schema、144 的"配置接到运行上"。product-config 144 已按 ADR-0012 调整：144 只做文件层，"文件 + 数据库"的生效设置归本工单；本工单在 144 留下的那一处解析入口上叠数据库和单次覆盖，不另写一套合并。
- **子进程怎么拿到设置**：141 契约 4（建议：网页服务器把生效配置写进运行目录，子进程参数带 `--config <那个文件>`），以 141/143/144 合入的为准。现在的拼法：表单 `launcher.py:159-183`（`--config` 在 `:166`），对话 `conversations.py:132-149`（`--config full` 在 `:138`、`CONFIG = "full"` 在 `:45`）；IM 的回合经 `conversations.send`（`im/adapter.py:302`），不传作用域。
- **作用域**：
  - 频道里 = 工作区 + 频道；私聊 = 工作区 + 个人（S2）。数据结构从一开始就按多工作区（S5），每条覆盖带工作区 id。
  - IM 今天的 `Incoming`（`im/platform.py:35-52`）没有工作区 id；Slack 的 `to_incoming`（`im/slack.py:176-196`）没读事件的 `team`。把工作区 id 带进 `Incoming` 是 161 的（作用域映射）；本工单的解析函数接受作用域参数，161 合入前 IM 一侧用一个默认工作区（定的细节第 6 条；多工作区的配置形状 `[im.slack.workspaces.<名字>]` 由 161 实现），最终回复写明默认工作区的 id。
  - 网页里发起的运行（表单、网页对话）：设置用 组织 + 本人所在工作区 + 个人（同私聊，定的细节第 5 条）。本工单的解析函数接受这个作用域；"本人"是谁由 159 的身份给出（159 Blocked by 本工单），159 合入前网页运行按组织层解析（local 本机用户即组织管理员）。
- **Skill 和说明在线程开始时固定**（S4）：解析函数接受"线程开始时的快照"；快照存在会话索引里。本工单只建会话索引的表和读写函数（线程 → 运行 id、所属频道或本人），由 161 写（定的细节第 3 条）；`im/threads.json` 照旧是 IM 自己的线程映射。
- **播种**：首次启动（库不存在或组织层为空，以契约为准）用文件的设置类键播种；之后改文件不覆盖。不一致的判定按键逐个比；"用文件的值覆盖"在存储层是一个函数，写一条改动记录。
- **改动记录里的密钥类**：设置类键里值本身是密钥的（若有，例如 `[mcp]` 内联服务器的 `${VAR}` 引用）只记"改过"；判定照 143 的明文密钥规则。
- **`config show`**：143 的输出加数据库来源；默认组织层，`--workspace`、`--channel`、`--user` 选作用域（定的细节第 11 条）。
- **评测**：评测入口（`evals/harbor_agent.py`、`evals/dab.py`、`evals/jaffle.py`、`evals/ablate.py`）经 `data-agent run --config <评测配置文件>`。显式 `--config` 只读给的文件（Q1），评测配置文件不写 `[server]`（没有 `db_path`，也没有推出默认库路径的 `runs_dir`），所以按第 11 条不读库。`db_path` 的默认值若由 `runs_dir` 推出，单独 `run` 只在文件给了 `[server]` 时才算"配了"，最终回复写明实际规则；157 的评测隔离守门钉住这一点。
- **网页运行的生效配置文件**：写进运行目录、交给子进程的那份不带 `[server].db_path`，否则子进程按第 11 条会在已解析好的频道或个人值上再叠一次组织层。
- **迁移**：照常见做法用 `PRAGMA user_version` 或一张版本表，迁移按版本号顺序执行，启动时自动升级；备份即复制文件（S9）。
- 只用 uv。每个 worktree 用自己的 `runs/` 目录和库文件。

## Acceptance

- [ ] 157 里标 `TICKET_158` 的测试去掉标记后通过；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行
- [ ] 141 的快照守门组、评测隔离组，157 的评测隔离守门，以及其他已合入的验收测试照旧通过
- [ ] 单元测试：迁移（空库、旧版本库）、播种只发生一次、改动记录的密钥类只记"改过"、并发写入不丢（sqlite 的锁）
- [ ] 单元测试：单独的 `data-agent run` 配了 `db_path` 且文件存在时用组织层的值、文件不存在时只用文件；交给网页运行子进程的生效配置文件不带 `db_path`
- [ ] 单元测试：库里组织层改了一个键（如 `[limits].max_turns.query`），网页表单一次运行、对话一轮、IM 一轮经替身 `start_run` 拿到的生效配置文件里是库里的值
- [ ] `docs/setup.md` 写了 `db_path`、`org_admins`、两类键的划分、`config show` 的来源列
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 输出。
- `uv run pytest tests/acceptance/test_settings_resolve.py tests/acceptance/test_settings_seed.py tests/acceptance/test_settings_eval_isolation.py -v`。
- `git diff main -- tests/acceptance | grep '^[-+]' | grep -v '^[-+][-+]'`（只删标记）。
- 不调模型的真实运行（$0）：临时目录起一次 `data-agent web --config <示例>`（再停掉），用 `sqlite3 <db> .schema` 贴出表；改配置文件后 `data-agent config show` 贴出不一致的标注。

交给主会话（最终回复写出命令）：库里组织层改 `[limits].max_turns.query = 5`，网页对话问一题，确认运行目录里的生效配置和事件里的轮数上限是库里的值。花费小，按小规模运行处理。

## Stop conditions

- 157 的某个验收测试看起来不对，或要改它才能转绿。
- 144 没留下单一的解析入口，叠数据库要改 144 的契约。
- 要让评测入口读库，或改 `evals/`，才能做到。
- 要加运行时依赖。
- 第二轮审查修复后仍不过。
- 不停止、不重启本机的 Docker/colima 守护进程。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-27（主会话建票）：settings 的存储与解析。等 product-config 143、144 和 157 合入。改动在后端，照常跑 `da-review` 和 `eng:code-review`。从 144 接过"文件 + 数据库"的生效设置（ADR-0012）。
- 2026-11-27（主会话）：按"建 settings 工单时主会话定的细节"第 3、5、6、11 条改票：会话索引只建表、由 161 写；网页运行用 组织 + 本人工作区 + 个人；161 前用默认工作区；单独 `run` 读组织层、`config show` 选作用域。去掉"`run` 读库""作用域没定"两条停止条件。
