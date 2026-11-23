# 116 — 外部 MCP：后端（配置解析与校验、`${VAR}` 展开、放行清单、`--mcp-servers` 接线、stdio 包装、`version` 事件、文档）

Status: in-review
Blocked by: 115（done）
Spec: ../spec.md
Claimed by: claude (opus 5.5, worktree 116-external-mcp-backend)
Branch: worktree-116-external-mcp-backend

## Goal

`data-agent run --mcp-servers F` 和 `data-agent web --mcp-servers F` 把用户的 `mcpServers` JSON（Claude Code / Cursor 格式加每个 server 的 `allowedTools`）里的 server 挂给 agent：只放行清单列出的工具，凭据由 `DATA_AGENT_MCP_*` 环境变量展开，stdio server 经包装命令在干净环境里启动，`version` 事件记下挂了哪些 server 和放行清单。不带 `--mcp-servers` 时一切照旧，评测入口不挂。115 合入的 `TICKET_116` 验收测试全部转绿（只删标记），README 和 `docs/setup.md` 写明用法、MongoDB 与 GitHub 示例配置、风险和已知限制。

## Authority

- May edit：
  - 新增 `src/data_agent/external_mcp.py`（或同名包）：读文件、校验、`${VAR}` 展开、放行清单、给 `version` 事件的摘要、stdio 包装的实现。
  - `src/data_agent/cli.py`：`run` 和 `web` 的 `--mcp-servers` 往下传（115 的空壳在 `cli.py:321-326`、`cli.py:336-341`，`serve_web` 在 `cli.py:454`，`create_app` 调用在 `cli.py:483`）；新增包装子命令（名字自定，spec 的 `mcp-launch` 前面有"如"字）。
  - `src/data_agent/agent.py`：只改三处。`build_options`（`agent.py:1511`，`mcp_servers=` 在 `agent.py:1651`）加一个默认不挂任何外部 server 的参数；`run`（`agent.py:2062`）接收并往下传；`version` 事件（`agent.py:2162-2172`）加 `external_mcp_servers`。另外可以把展开后的值加进 `EventLog(secrets=...)`（`agent.py:2159`）做兜底脱敏。
  - `src/data_agent/web/app.py`、`web/launcher.py`、`web/conversations.py`：把 `create_app` 的 `mcp_servers`（115 的空壳，`app.py:69`）拼进两条子进程命令行（照 `--network` 的传法，`launcher.py:168`、`conversations.py:143`），只带文件路径。
  - `tests/acceptance/test_mcp_*.py`：**只删** `TICKET_116` 标记，以及因此没用的定义和 import（AGENTS.md 硬规矩的唯一例外）。
  - `tests/`：新增单元测试（覆盖下面"测试没钉、本工单要做"的行为）。不改已有测试文件和 `tests/fixtures/` 里已有的文件。
  - `README.md`、`docs/setup.md`。
  - `NOTICE`（复制或改写了上游内容时）。
- Must not edit：
  - 115 合入的验收测试的断言、夹具、输入（`tests/acceptance/test_mcp_*.py`、`mcp_support.py`、`tests/fixtures/mcp/`），包括其中过时的"xfail until 116"类 docstring；`conftest.py`、`seam_support.py`。
  - `web/src/`（117 改 `tools.ts`、`model.ts` 的显示）。
  - `evals/`、`run_config.py`、`configs/`、`skills/`、提示词文本、`Makefile`、`.github/workflows/`、`.env.example`。
  - 其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: 工单 agent $0（加载不了模型环境，不调模型）。MongoDB 真模型用例由主会话跑，小规模、已预先授权（见 Evidence required）。

## Context

- **spec**：`.scratch/mcp/spec.md`。行为来源是"配置文件（Q3、Q4、Q6）""挂到哪些入口（Q2）""stdio server 的环境（Q7）""事件、可观测性（Q8）"；"Testing Decisions"和"验收"。spec 里的行号（如 `agent.py:1650`）已过时，以本工单写的当前行号为准。
- **取证笔记**：`docs/research/2026-11-23-external-mcp.md`。Q1.1 是 `build_options` 现状；Q1.2 是评测路径（Harbor `evals/harbor_agent.py:305-328`，DAB 在进程内调 `build_options` 再换 `mcp_servers`，`evals/dab.py:460-464`）；Q2 是 SDK 配置格式和放行写法；Q4.3 和未确认项 8 是凭据进 argv 的问题。
- **115 的契约**（`.scratch/mcp/issues/115-mcp-acceptance-tests.md` Context 契约 1–7、Comments 的最终回复）。要点：
  - 配置错误：`run` 在会话启动前以退出码 2 结束，stderr 点名 server（`${VAR}` 的错误还点名变量）。缺 `allowedTools` 或为空：stderr 有一行含 `warning` 和 server 名，这个 server 的工具一个都不放行。
  - 选项形状：`mcp_servers` 的键是 `data` 加配置里的名字；http server 是 `{"type": "http", "url", "headers"}`，**请求头的值已展开**（`test_mcp_config.py:208-215`、`:262`）；stdio server 的 `command` 是包装命令，`command`、`args` 里没有展开后的值（`:264-270`）；`allowed_tools` 是原来那份加上 `mcp__<s>__<t>` 或 `mcp__<s>__*`，原条目和顺序不变；`tools`、`disallowed_tools` 不变，`permission_mode == "dontAsk"`，`strict_mcp_config is True`（`:273-293`）。
  - `version.external_mcp_servers`：`[{"name", "allowed_tools"}]`，按文件顺序，`["*"]` 原样；不带 `--mcp-servers` 时没有这个字段（照 `effort` 的先例，`agent.py:2172`）；事件文件里没有 env 值、请求头值、URL 查询串（`test_mcp_config.py:297-311`）。
  - stdio 包装：子进程环境的键只能来自 `BASE_ENV`（`test_mcp_launch.py:66-80`，测试常量，是上限）、配置 `env` 的键、空环境基线；没有 `DATA_AGENT_*`、`ANTHROPIC_*`、`LANGFUSE_*`、数据库连接串变量、`OTEL_*`；stdout 原样回写 stdin，包装不往 stdout 写任何东西（`test_mcp_launch.py:168-214`）。测试按 `{**脏父环境, **options.env, **server.get("env", {})}` 起进程，所以展开的值走 server 的 `env` 还是走 `options.env`，测试都成立。
  - 网页：表单运行和对话每一轮的参数都带 `--mcp-servers <传给 create_app 的路径>`，参数全文没有展开后的值；`data-agent web --mcp-servers F` 把 `F` 交给 `create_app`（`test_mcp_web.py:157-219`）。
- **mongodb-mcp-server 的已核实事实**（115 Comments，不调模型）：镜像 `mongodb/mongodb-mcp-server:3.3.0`；只读开关 `MDB_MCP_READ_ONLY=true`（18 个工具），`false` 时多出 `insert-many` 等写入工具（27 个）；查询工具要 `connectionId`，所以放行清单要带 `list-connections`；默认发遥测，配置设 `MDB_MCP_TELEMETRY=disabled`。已核实的配置写法见 `test_mcp_e2e.py:168-189`。文档里的 MongoDB 示例照这些写，不照 spec 示例的 `:latest`。
- **GitHub 示例**：spec 写的只读端点 `https://api.githubcopilot.com/mcp/readonly` 和请求头写法要照 GitHub 官方文档（`github/github-mcp-server` 的 README 或 docs）核实，文档里附链接和访问日期；没核实到的标 `未确认`，不凭记忆写。
- **SDK**：`claude-agent-sdk==0.2.168`（`uv.lock:537-538`）。`subprocess_cli.py:667-692`：`mcp_servers` 是 dict 时序列化成 JSON 作为 `--mcp-config` 的 argv 值；是字符串时原样当路径或 JSON 传给 `--mcp-config`。
- **包装起 `docker`**：本机 colima 靠 `~/.docker` 下的 context 找 daemon，`HOME` 在 `BASE_ENV` 里，够用；本机没设 `DOCKER_HOST`（115 Comments）。用 `DOCKER_HOST` 的用户要在配置 `env` 里写它，README 写明。
- **并行工单**（后合入的一方 rebase）：
  - 可观测性 112（`.scratch/observability/spec.md:117`，工单文件还没建）：改 `build_options`、CLI 的环境变量（加 `OTEL_*`）、`README.md`、`docs/setup.md`，还会**升级 SDK**。升级后 `--mcp-config` 的序列化方式可能变，rebase 后重核"待人定"第 1 条。113 是本地中转，主要是新模块。
  - 图表 119（`.scratch/charts/spec.md:75`）：改 `cli.py`（`--charts`）、`build_options`，以及网页的两条子进程命令行（`launcher.py`、`conversations.py`）。
- **spec "验收"里网页那条**（对话里问一次、运行页显示"mongo · find"）的显示部分属于 117，本工单不做；本工单只保证网页拼的命令行带 `--mcp-servers`。
- 只用 uv。每个 worktree 用自己的 `runs/` 目录。

## 测试没钉、本工单要做的

能写单元测试的都写（新文件，不改验收测试）：

- `sse`、OAuth 相关字段、其他未知类型：报错并点名 server（验收测试覆盖了 `sse` 和未知类型；OAuth 字段由本工单判断怎么识别）。
- 展开后的值不进日志：除了 stdout、stderr（已测），任何 `logging` 输出也不带。
- 核实并在 Comments 里报告：CLI 会不会把自己的环境传给 stdio server（取证笔记未确认项 8）。不论结果都走包装（spec Q7）。
- README：用法、两个示例配置的指引、写入类工具的风险（列进清单就能用，不做调用前确认）、已知限制（stdio server 跑在宿主机、网络不受 `--network` 控制；外部工具的入参和输出会进事件文件和打开导出时用户自己的 Langfuse；`DOCKER_HOST`；"待人定"第 1 条的现状）。
- `docs/setup.md`：MongoDB（本机 docker、`colima start`、演示库、`DATA_AGENT_MCP_MONGO_URL`）和 GitHub（只读 token 写进 `~/.config/data-agent/env`，变量名 `DATA_AGENT_MCP_GITHUB_TOKEN`）的准备步骤和完整示例配置。示例只带 `${DATA_AGENT_MCP_*}` 引用，不带任何真实值。

## 待人定（人 2026-11-23 定为"以后再定"，运行中不问）

规则：合入的测试钉住的，照测试做；测试没钉的，按下面的默认做（spec 字面或最保守的选项），实现成容易改的形状（一处常量或一个函数），最终回复里在"待人定"下逐条列出：做了什么、核实到的事实、改成别的选项要动哪里。主会话合并前拿去问人。这几条都**不是**停下的理由。

1. **凭据进 CLI 的 argv**。spec 写"展开后的值不写进……命令行参数"，但 SDK 把 `mcp_servers` 整个序列化成 `--mcp-config` 的 argv。
   - http：测试钉住了截下来的选项里请求头已展开（`test_mcp_config.py:215`、`:262`），所以照测试做，不换路径。
   - stdio：测试两种走法都接受。默认取决于下面的核实：CLI 把自己的环境传给 stdio server 的话，展开的值走 `options.env`，server 配置里只带变量名的映射（包装从继承的环境里按映射取值），argv 里就没有 stdio 的值；不传的话，值走 server 的 `env`（进 argv），照实报告。
   - 只核实、报告事实，不改实现路径：CLI 自己展开 `--mcp-config` 里的 `${VAR}` 吗（Claude Code 对 `.mcp.json` 有这个功能，对 `--mcp-config` 的 JSON 字符串或文件是否适用，查官方文档并实测）；`mcp_servers` 传文件路径（`subprocess_cli.py:692`）能否避开 argv，代价是什么（测试按 dict 读选项）。报告里写明 SDK 和 CLI 的版本号（112 会升级 SDK）。
2. **`${VAR}` 写在 `args`、`url`、`command` 里**。默认：拒绝，报错点名 server 和字段，退出码 2。spec 只说展开进 `env` 和 `headers`。原样透传只有在 CLI 不会自己展开 `${VAR}` 时才安全，否则会绕过 `DATA_AGENT_MCP_` 前缀规则，所以连同第 1 条一起核实 CLI 的行为并报告。
3. **`data-agent web` 启动时要不要校验配置**。默认：校验。`serve_web` 用同一个解析函数先校验一遍，配置错误时不起服务、退出码 2、stderr 点名 server；每次运行的子进程照常再校验。只有一处调用点，删掉就回到"不校验"。`test_mcp_web.py` 的 web CLI 测试设了 token，两种做法都能过。
4. **`BASE_ENV` 的内容**。测试常量（`test_mcp_launch.py:66-80`）是上限，本工单不能放宽。包装只带这个集合里存在的变量（或更少）。觉得还缺别的变量（例如 `DOCKER_HOST`、代理变量），不改测试，在"待人定"里列出理由。
5. **`version` 事件要不要记 `type` 和去掉查询串的 URL**。默认：只记 `name` 和 `allowed_tools`（测试钉住的那两个字段，最保守）。加字段只改摘要函数一处。

另外照 115 Comments 一并列出、请人确认：`strict_mcp_config` 带外部配置时仍是 `True`（测试已钉）；MongoDB 集成测试是两个用例（人可以删第二个）。

## Acceptance

- [ ] `TICKET_116` 标记全部删掉：`tests/acceptance/` 下 13 个 `@TICKET_116` 装饰器和 4 个 `TICKET_116 = pytest.mark.xfail(...)` 定义（`test_mcp_config.py:67`、`test_mcp_e2e.py:56`、`test_mcp_launch.py:62`、`test_mcp_web.py:44`），以及因此没用的 import。`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_mcp_*.py -v`：29 个非集成测试全部 PASSED，0 个 xfail、0 个 skip；`--collect-only -m integration` 列出 e2e 的两个用例。
- [ ] 守门组（`test_mcp_eval_unchanged.py`）和已有的 `test_chat_eval_unchanged.py` 照旧通过：不带 `--mcp-servers` 时 `build_options` 的 `mcp_servers` 只有 `data`，Harbor 命令行没有 `--mcp-servers`，DAB 的选项只有它自己的服务器。
- [ ] "测试没钉、本工单要做的"每条都做了，能单测的有新测试（bug 修复配一个修复前会失败的测试）。
- [ ] "待人定"5 条按默认实现，每条都在最终回复里列出事实和改法。
- [ ] README、`docs/setup.md` 写了用法、MongoDB 和 GitHub 示例配置、风险、已知限制；GitHub 端点有官方文档链接和访问日期或标 `未确认`。
- [ ] 文档和测试里没有密钥、主机地址：`uv run python -m evals scrub tests/fixtures/mcp` 0 处改动，pre-commit 的密钥与公网 IP 扫描通过。
- [ ] `make check` 通过。
- [ ] 主会话跑的 MongoDB e2e 两个用例 PASSED（见 Evidence required）。

## Evidence required

工单 agent 贴到最终回复的 Comments：

- `make check` 的输出（`tee` 存文件）。
- `uv run pytest tests/acceptance/test_mcp_*.py -v` 的输出，以及 `git diff main --stat -- tests/` 和 `git diff main -- tests/acceptance | grep '^[-+]' | grep -v '^[-+][-+]'`，证明测试只删了标记。
- 不调模型的真实运行（$0）：
  - 用 MCP Python 客户端（照 115 的做法）经包装命令起 `mongodb/mongodb-mcp-server:3.3.0`，连本机 mongo 容器，列出工具、调一次 `find`。证明包装在干净环境里能起 docker 镜像、stdout 透传不坏 JSON-RPC。
  - 一次配置错误的 `data-agent run --mcp-servers <坏配置>`：退出码 2，stderr 点名 server。
  - 一次缺 `allowedTools` 的运行的警告行。
  - 如做了"待人定"第 3 条：`data-agent web --mcp-servers <坏配置>` 的退出码和 stderr。
- "待人定"第 1、2 条的核实：查的文档链接、实测命令和输出、SDK 与 CLI 版本。

交给主会话真跑（工单 agent 不跑；写好下面的命令，确认路径和 worktree 名）：

- **MongoDB e2e**：在本工单的 worktree 里，先 `colima start`，确认沙箱镜像在（`docker image inspect` 沙箱镜像，没有就 `make sandbox-image`；e2e 的 `data-agent run` 不带 `--sandbox`，默认 docker），然后：
  ```bash
  mkdir -p runs/116-e2e && set -a && . ~/.config/data-agent/env && set +a && \
    uv run pytest tests/acceptance/test_mcp_e2e.py -m integration -v 2>&1 | tee runs/116-e2e/pytest.log
  ```
  只跑这一个文件，不用 `make test-integration`（它跑全部集成测试，超出预先授权的范围）。两个用例 PASSED 就是本工单的真实运行证据；主会话把输出和花费（读事件文件）抄进 Comments。失败就按"退回：原因"交回。
- **GitHub（手动，主会话做）**：人先在 `~/.config/data-agent/env` 里准备只读 token（`DATA_AGENT_MCP_GITHUB_TOKEN`）。工单 agent 写好示例配置的路径和一条命令，例如 `data-agent run --type query --duckdb <shop.duckdb> --workspace runs/116-github/work --events-dir runs/116-github/events --mcp-servers <github 配置> --task "<问一个公开仓库的 issue>"`，问题自己写，不用榜单题目。主会话跑，确认事件里有 `mcp__github__` 的 `tool_start`，答案对得上那个 issue。

## 审查（工作流程第 5 步）

`da-review` 现在用不了（Codex 不可用）。第 5 步改成：

1. 先派一个只读的 `model: fable` 子 agent 审本分支已提交的 diff（对照本工单和 spec，找 bug，只报问题、带 `file:line`，不改文件），结果当 `da-review` 的结果处理。
2. 再开新上下文跑 `eng:code-review main`（Standards、Spec 两个子 agent）。

每条结果要么修（修完提交），要么写明不修的理由；修复最多 2 轮。

## Stop conditions

- 要改验收测试的断言、夹具、输入才能转绿，或者觉得某个验收测试错了（AGENTS.md 硬规矩，优先于"运行中不问"）。
- 不改 Authority 以外的文件就做不到（例如评测入口必须改代码才能保证不挂）。
- spec 自相矛盾，且不是"待人定"里已列的那几条。
- 第二轮审查修复后仍不过。

"待人定"的 5 条不是停下的理由，照默认做完再列出。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0
- Outcome: — (pending the e2e run and merge)

## Comments

- 2026-11-23（工单 agent 最终回复，主会话照抄）：

**Real runs (no model, $0)**

- **`make check`** (`runs/116/check-4.txt`): exit=0; `2684 passed, 2 skipped, 79 deselected, 75 xfailed`; basedpyright `0 errors`; web `322 passed | 52 expected fail`.
- **Acceptance** (`runs/116/acceptance-v.txt`): `29 passed, 2 deselected`, 0 xfail, 0 skip. `--collect-only -m integration` lists the two e2e cases.
- **Test diff**: `git diff main -- tests/acceptance tests/fixtures` is deletions only.
  - 13 `-@TICKET_116`
  - 4 × the 3-line `TICKET_116 = pytest.mark.xfail(...)` definition
  - 3 blank lines, so ruff's import-order check passes; still deletions only
  - `pytest` is still imported in all four files; the old docstrings are untouched.
- **Scrub**: `evals scrub tests/fixtures/mcp` reports `changed 0 file(s) of 2`. Pre-commit "Detect hardcoded secrets" passed.
- **MongoDB through the wrapper** (`runs/116/mongo-probe/`): MCP Python client, `mongodb/mongodb-mcp-server:3.3.0`, local `mongo:8.0`.
  - Command used: `python3 -P -m data_agent mcp-launch --mcp-servers <abs>/mcp.json --server mongo`
  - Result: `tools (18): aggregate … find … list-connections …`, `find is_error: False`, `Query on collection "tickets" resulted in 5 documents`.
- **Bad configs:**
  - `run`, sse server: `data-agent run: error: MCP server 'legacy': type 'sse' is not supported (only stdio and http)`, exit=2.
  - `run`, wrong prefix: `MCP server 'github': headers 'Authorization': variable GITHUB_TOKEN does not start with DATA_AGENT_MCP_`, exit=2.
  - Missing `allowedTools`: `warning: MCP server 'quiet' has no allowedTools; it is not mounted and none of its tools is allowed`.
  - `web`: `data-agent web: error: MCP server 'github': headers 'Authorization': variable GITHUB_TOKEN does not start with DATA_AGENT_MCP_`, exit=2.

**Does the CLI pass its own environment to stdio servers?** (取证笔记未确认项 8)

Yes, all of it. I probed with SDK 0.2.168 / CLI 2.1.301, a dead model endpoint and a fake key (`runs/116/probe/`). A variable that existed only in the CLI's environment reached the server, and so did `ANTHROPIC_API_KEY` and `CLAUDE_CONFIG_DIR`. So the wrapper is required, not just defence in depth.

**Reviews**

Fable stood in for `da-review`, then `eng:code-review main` ran.

Fixed:
1. **Allow-list bypass** (Fable, high): server names containing `__`, or starting or ending with `_` (e.g. `data_`), are now refused. The CLI splits `mcp__<server>__<tool>` on `__`, so `mcp__data___x` would have counted as one of our own `data` tools.
2. **Masking**: a URL-valued variable's password and host are now masked in events too.
3. **Logging test**: it now runs the whole `run` path under `caplog` at DEBUG and checks that log records exist.
4. **Crashes instead of exit 2**: a non-string `type`, or an env name that is empty, has `=`, or holds NUL, used to crash with a traceback. Now exit 2.
5. **Missing tests** added: exit 127, `${` in the file path, a server with `url` and no `type` (clearer error).
6. The wrapper now runs with `python -P`, so a module planted in the workspace is not imported.
7. Cleanups: one host helper instead of two copies; dropped a needless `Sequence` round-trip; README now covers the stdio half of 待人定 1.

Not fixed, with reasons:
- **Our own `data` server's `python -m` in the workspace** (Fable #9): same shape as #6, but `agent.py:1569` is outside the three spots this ticket may change. Needs a follow-up ticket.
- **Short variable values** are masked across the whole event file. Documented in the README instead: reference only secrets with `${}`.
- **`${}` refused in `url`** is the 待人定 2 default.
- **Unknown fields and `url`-without-`type` are refused.** Accepting them would mean silently dropping fields or overriding the spec's default of stdio. Changing this is one constant, `FIELDS`.
- **The wrapper re-reads the file when the CLI starts the server.** Editing the file mid-run changes the stdio command. Documented in the README.
- **Smells** (one record type for both transports, the `RESERVED_NAME` copy needed to avoid a circular import, `mcp_args` read across classes, the SDK-private import in a test): judgement calls, left as they are.
- **Scope additions** the spec didn't ask for: the tool-name character check, the URL scheme check, the `${`-in-path refusal, URL-part masking, `-P`. All are small hardening steps, commented in the code.

### 待人定

1. **Credentials in the CLI's argv**
   - http: as the tests require, expanded headers are in the `--mcp-config` argv. This is documented as a known limit.
   - stdio: values reach the wrapper through the CLI's environment. The wrapper's arguments are only the absolute file path and the server name.
   - The CLI does expand `${VAR}`, with any variable name, in `args` and `env`, for both the JSON-string and the file form of `--mcp-config`. I confirmed this by probe.
   - Claude Code's docs (https://code.claude.com/docs/en/mcp, read 2026-11-23) cover expansion only for `.mcp.json`: `${VAR}` and `${VAR:-default}` in command, args, env, url and headers. They are silent on `--mcp-config`.
   - Passing a file path instead of a dict doesn't help: the CLI expands the same way, and the acceptance tests read the options as a dict.
   - Where to change it: `ExternalServers.sdk_servers` / `env`.
2. **`${VAR}` in args, url or command**: refused, naming the server and field, exit 2. Because the CLI expands any variable there, passing them through would bypass the `DATA_AGENT_MCP_` rule. Values that themselves hold `${` are refused too. Where to change it: `no_reference`.
3. **`data-agent web` checks the file at startup** (see the run above). To drop the check, remove the `external_servers(parser, mcp_servers)` call in `serve_web`.
4. **`BASE_ENV`**: exactly the test's upper bound.
   - `DOCKER_HOST` and proxy variables are left out; users add them to the server's `env`, as the README says.
   - Possibly missing: `HTTP(S)_PROXY`/`NO_PROXY`, for stdio servers behind a proxy. That would need the test constant changed.
5. **The `version` event** records only `name` and `allowed_tools`. Adding fields changes `summary()` only.
- Also for you to confirm:
  - `strict_mcp_config` stays `True` with an external config.
  - The MongoDB integration test is two cases.
  - A server without `allowedTools` is not mounted at all.
  - A file in which no server has an allow list records `external_mcp_servers: []`.

### For the main session to run

**MongoDB e2e**, in worktree `116-external-mcp-backend`:
1. `colima start`
2. Check the sandbox image with `docker image inspect`, or run `make sandbox-image`.
3. Run:
```bash
mkdir -p runs/116-e2e && set -a && . ~/.config/data-agent/env && set +a && \
  uv run pytest tests/acceptance/test_mcp_e2e.py -m integration -v 2>&1 | tee runs/116-e2e/pytest.log
```

**GitHub**: first put `DATA_AGENT_MCP_GITHUB_TOKEN` in the env file. The config is already written at `runs/116-github/mcp-github.json`, using the official `/readonly` endpoint and the `Authorization: Bearer` header.
```bash
mkdir -p runs/116-github/work && uv run python -c "import duckdb; duckdb.connect('runs/116-github/shop.duckdb').execute(open('tests/fixtures/shop.sql').read())" && \
set -a && . ~/.config/data-agent/env && set +a && \
uv run data-agent run --type query --config full --duckdb runs/116-github/shop.duckdb \
  --workspace runs/116-github/work --events-dir runs/116-github/events \
  --mcp-servers runs/116-github/mcp-github.json \
  --task "In the GitHub repository github/github-mcp-server, what is the title of issue #1, and is it open or closed?"
```
Check that the events contain a `tool_start` for an `mcp__github__` tool and that the answer matches issue #1.

- 2026-11-23（主会话）：PR CI 绿；Codex GitHub app 审查完成，无意见。合并前：真实运行（`runs/batch2/live-checks.sh`）和"待人定"交人定。
