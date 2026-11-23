# 115 — 外部 MCP：验收测试（配置翻译、stdio 包装、入口、网页显示；MongoDB 真模型用例）

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: claude-code opus-5.5 (worktree 115-mcp-acceptance-tests)
Branch: worktree-115-mcp-acceptance-tests

## Goal

spec "Testing Decisions" 列的每条行为都有验收测试，写在约定的接缝上。还没实现的行为标预期失败，按转绿的工单分标记：后端标 `TICKET_116`（pytest `xfail(strict=True)`），网页标 `TICKET_117`（vitest `test.fails`）。现在就成立的行为（评测入口不挂、不带配置时一切照旧、`mcp__data__*` 的显示不变）写成守门测试，现在就通过、不标记。src 里只加让测试能调用的空壳：`run`、`web` 两个子命令接受 `--mcp-servers`，`create_app` 多一个参数，都不带行为。本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit（都是新建，除非注明）：
  - `tests/acceptance/test_mcp_config.py`：解析与校验、`${VAR}` 展开、放行清单、`run` 入口、`version` 事件。
  - `tests/acceptance/test_mcp_launch.py`：stdio 包装。
  - `tests/acceptance/test_mcp_web.py`：网页的两条子进程命令行，以及 `data-agent web --mcp-servers`。
  - `tests/acceptance/test_mcp_eval_unchanged.py`：守门组。
  - `tests/acceptance/test_mcp_e2e.py`：MongoDB 真模型用例，标 `integration`。
  - `tests/acceptance/mcp_support.py`：几个文件共用的辅助。
  - `tests/fixtures/mcp/`：`print_env.py`（见 Context）、MongoDB 演示数据、可选的示例配置。
  - `web/src/run-page/acceptance/external-tools.test.ts`。
  - `src/data_agent/cli.py`：只改两处。
    - `run_cmd` 加 `--mcp-servers`（`type=Path`，help 写明是 Claude Code / Cursor 的 `mcpServers` JSON 加 `allowedTools`，评测不用）。
    - `web` 加同名选项（`cli.py:322-328` 附近）。
    - 解析出来的值不往下传，不加任何行为。
  - `src/data_agent/web/app.py`：只给 `create_app`（`app.py:64-69`）加参数 `mcp_servers: Path | None = None`，不往下传，不加行为。
- Must not edit：
  - `src/data_agent/` 的其他文件，包括 `agent.py`：`build_options`、`run`、`version` 事件都不加参数。它们由 116 改，可观测性的 111–114 也在改。
  - `evals/` 的实现代码。
  - 已有的 `tests/acceptance/` 文件（包括 `conftest.py`、`seam_support.py`、`test_chat_eval_unchanged.py`；它们的辅助函数要用就照抄进 `mcp_support.py`）。
  - `tests/fixtures/` 里已有的文件。
  - `web/src/run-page/tools.ts` 和 `web/src/run-view/model.ts`（117 改）。
  - `Makefile`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。工单 agent 加载不了模型环境，不调模型；真模型用例由主会话跑（见 Evidence required）。

## Context

- **spec**：`.scratch/mcp/spec.md`。
  - "Testing Decisions"逐条列了要断言的行为，照它写。
  - 行为来源："配置文件（Q3、Q4、Q6）""挂到哪些入口（Q2）""stdio server 的环境（Q7）""事件、可观测性（Q8）""网页（Q9）"。
  - "验收"一节给出 MongoDB 的两个真模型场景。
  - 取证笔记：`docs/research/2026-11-23-external-mcp.md`。Q1.1 是 `build_options` 现状，Q1.2 是评测路径和产品路径，Q2 是 SDK 配置格式和放行写法，Q4.3 是凭据进 argv 的问题。
- **并行**：可观测性的 111–114（`.scratch/observability/spec.md`，工单文件还没建）和 116 都改 `agent.py` 的 `build_options`（`agent.py:1510-1675`）。115 不碰 `agent.py`，只加测试和两处空壳，冲突应该很少。可观测性会往 CLI 的 env 里加 `OTEL_*`（observability spec "传给 CLI 的环境变量"），stdio 包装的测试要把它们也当作脏环境的一部分。
- **接缝：整条 `data-agent run`，在 SDK 的 transport 处截停。** 照 `tests/acceptance/test_chat_eval_unchanged.py:80-99` 的 `captured` 写法：monkeypatch `SubprocessCLITransport.connect`，记下 `self._options`，然后抛出 `CLIConnectionError`。
  - 用法：`cli.main(["run", ..., "--mcp-servers", F])` 跑完整个 CLI，不调模型，也不起 CLI。
  - 测试从截下来的选项里读 `mcp_servers`、`allowed_tools`、`tools`、`disallowed_tools`、`permission_mode`、`strict_mcp_config`、`env`；从 `--events-dir` 的事件文件里读 `version` 事件。`version` 在 `build_options` 之前写（`agent.py:2158`），截停后事件文件已经在。
  - 这样 115 不用给 `build_options` 或 `run` 定签名，116 怎么接线由它自己定。
  - 数据源用 `seam_support.build_shop_duckdb`。transport 截停在前，所以 `--sandbox` 选哪个后端都不会真起沙箱；选不依赖 docker 的（如 `--sandbox local`），免得本机没开 colima 时测试失败。理由写在测试注释里。
- **预期失败的标记**：照工单 88 的写法（`git show 6491ece -- tests/acceptance/test_chat_api.py`）：
  ```python
  TICKET_116 = pytest.mark.xfail(
      strict=True, raises=AssertionError, reason="ticket 116: external MCP is not wired yet"
  )
  ```
  - 只接受 `AssertionError`。有了两处空壳，`--mcp-servers` 能被 argparse 接受、运行照常走到截停。但 `servers["mongo"]`、`version["external_mcp_servers"]`、`args.index("--mcp-servers")` 今天会抛 `KeyError`、`ValueError`，被报成失败而不是预期失败。所以这类取值一律经 `mcp_support.py` 里的辅助函数：先 `assert` 键或参数存在，再取值。
  - 每个标了 `TICKET_116`、`TICKET_117` 的测试至少有一条今天会失败的正向断言（例如 `mongo` 在 `mcp_servers` 里、`allowed_tools` 有 `mcp__mongo__find`、stderr 点名 server）。只有否定断言的检查今天就成立，单独成一个测试并标了 `strict=True` 的话，会 XPASS 让 `make check` 失败。"值不出现在哪里"、"其他选项不变"这类检查放进带正向断言的同一个测试里，或者写成不标记的守门测试。
  - 不能写成只断言"退出码 2"这类今天就成立的断言：今天 argparse 已经因为不认识的参数退出 2 了。报错类的测试同时断言三件事：stderr 点名 server 或变量；没有会话启动（`captured` 为空）；退出码是 2（seam A 约定的配置错误，`test_seam_a_run_cli.py:10`）。
  - 起服务或子进程的夹具先确认自己起来了，起不来就抛 `RuntimeError`，不让它藏成预期失败（工单 88 的教训）。
  - 转绿时 116、117 只删自己的标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩的唯一例外）。
- **配置文件**：由测试写进 `tmp_path`。
  - 内容只有 `${DATA_AGENT_MCP_*}` 引用和 `.example` 主机名，例如 `https://mcp.example/readonly?tenant=t-123`。查询串用来断言它不进 `version` 事件。
  - 假的密钥值在测试代码里拼出来，照 `test_chat_api.py` 的 `PG_PASSWORD = "hunter2-" + "k4Rz"`，用 `monkeypatch.setenv` 设进环境。不写进夹具文件。
  - 要放进 `tests/fixtures/mcp/` 的示例配置也只能带引用。
- **本工单定下、在最终回复里逐项列出供人审的契约**（字段名由本工单定，人审）：
  1. CLI：`data-agent run ... --mcp-servers FILE`、`data-agent web ... --mcp-servers FILE`。
  2. `create_app(runs_dir, data_sources, start_run, ui_dir=UI_DIR, mcp_servers=None)`。给了文件时，表单运行和对话每一轮的子进程参数都带 `--mcp-servers <文件路径>`，原样是传给 `create_app` 的那个路径（测试传绝对路径）；不给时不带。
  3. 配置错误：`run` 在会话启动前以退出码 2 结束，stderr 点名 server（`${VAR}` 的错误还点名变量）。缺 `allowedTools` 或为空的 server：stderr 有一行含 `warning` 和 server 名的警告。
  4. `version` 事件的新字段 `external_mcp_servers`：`[{"name": str, "allowed_tools": [str, ...]}, ...]`，按配置文件里的顺序；`allowed_tools` 照文件原样（`["*"]` 记成 `["*"]`）。
     - 不带 `--mcp-servers` 时没有这个字段，照 `effort` 的先例（`agent.py:2168-2169`：只在给了时记）。
     - 测试只断言 `name`、`allowed_tools` 在，以及 env 的值、请求头的值、URL 查询串都不在事件文件里。其他字段（如 `type`、去掉查询串的 URL）要不要记，由 116 定，测试不管。
  5. 选项的形状：`mcp_servers` 的键是 `data` 加配置里的 server 名。
     - http server 是 `{"type": "http", "url": ..., "headers": {...}}`，请求头的值已展开。
     - stdio server 的 `command` 不是配置里的原命令（是包装命令），`command`、`args` 里没有展开后的值。
     - `allowed_tools` 是原来那份加上 `mcp__<server>__<tool>` 或 `mcp__<server>__*`，原来的条目和顺序不变。
     - `tools`、`disallowed_tools` 不变，`permission_mode == "dontAsk"`，`strict_mcp_config is True`。最后这条见"spec 里的解读"第 3 条。
  6. stdio 包装的子进程环境：键只能来自一个基本变量集合，加上配置 `env` 里的键。
     - 集合是测试文件里的常量，提议 `BASE_ENV = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "USER", "LOGNAME", "SHELL", "TERM", "TZ"}`（spec 只写了"等基本变量"，集合本身由人审，见"spec 里的解读"第 7 条）。116 觉得要多带别的基本变量，就停下交回，由人改测试。
    - 解释器或操作系统会在空环境里自己加变量。本机实测：`env -i /usr/bin/python3 -I` 看到 `__CF_USER_TEXT_ENCODING`、`SDKROOT`、`LC_CTYPE` 等。所以测试先用同一个解释器在空环境（`env={}`）下跑一次 `print_env.py`，得到的键也允许出现。在 macOS 和 Linux（CI 是 ubuntu）上都确认这样不误报，Linux 可以照工单 97 用 `python:3.12-slim` 容器跑。
     - 包装命令叫什么不定。spec 的 `python -m data_agent mcp-launch -- <原命令>` 前面有"如"字，测试只断言行为。
  7. 网页（117）：`toolKind("mcp__mongo__find") === "external"`；`shortTool("mcp__mongo__find") === "mongo · find"`（中间是 U+00B7 加两侧空格）。
- **各组要断言的行为**：
  - **解析与校验**（`test_mcp_config.py`，`TICKET_116`）：
    - stdio 和 http 各一个合法配置被接受，进 `mcp_servers`。缺 `type` 时按 stdio 处理。
    - 下面几种都报错并点名那个 server：`"type": "sse"`；未知类型（如 `"websocket"`）；名为 `data` 的 server；名字含 `.`、空格、`/` 的 server。
    - 缺 `allowedTools` 的 server 和 `"allowedTools": []` 的 server：`allowed_tools` 里没有以 `mcp__<server>__` 开头、也没有等于 `mcp__<server>` 的条目，stderr 有警告。这个 server 挂不挂，测试不管。
  - **`${VAR}` 展开**（`TICKET_116`）：
    - `DATA_AGENT_MCP_*` 变量被展开进 http 的 `headers`（断言值等于 `Bearer <假 token>`）。stdio `env` 的展开在包装组里断言，从子进程实际看到的值判断。
    - 下面几种在会话启动前报错，点名变量和 server：引用了没设的变量；引用了非 `DATA_AGENT_MCP_` 前缀的变量，其中专门有一例是 `${DATA_AGENT_API_KEY}`（模型密钥不能被引用）。
    - 展开后的值不出现在下面这些地方：事件文件全文（含 `version` 事件）、CLI 的 stdout 和 stderr、stdio server 的 `command` 和 `args`。
  - **放行清单**（`TICKET_116`）：
    - `["find", "aggregate"]` 生成 `mcp__mongo__find`、`mcp__mongo__aggregate`；`["*"]` 生成 `mcp__github__*`。
    - 和同样参数但不带 `--mcp-servers` 的那次运行比，`allowed_tools` 去掉外部条目后完全相等。`tools`、`disallowed_tools`、`permission_mode`、`strict_mcp_config` 相等。
  - **入口**：
    - `run --mcp-servers` 一条（`TICKET_116`）。
    - 网页两条（`test_mcp_web.py`，`TICKET_116`）：
      - 用 `create_app(..., mcp_servers=F)` 和一个记下参数的 `start_run`，假进程可以只读复用 `tests/fixtures/chat/fake_chat_run.py`。
      - 表单运行（`POST /api/runs`）和对话的一轮（`POST /api/conversations/{id}/messages`）的参数里都有 `--mcp-servers` 和 `str(F)`，参数全文里没有展开后的值（服务器进程的环境里设了假 token）。
      - 不给 `mcp_servers` 时两条都没有 `--mcp-servers`。这条现在就成立，是守门测试，不标记。
      - `data-agent web --runs-dir D --mcp-servers F` 把 `F` 交给 `create_app`：monkeypatch `data_agent.web.app.create_app` 和 `uvicorn.run`，`serve_web` 在函数里 import（`cli.py:446-471`）。标 `TICKET_116`。
  - **守门组**（`test_mcp_eval_unchanged.py`，不标记，现在就通过；每个测试都设一个 `DATA_AGENT_MCP_DEMO_TOKEN` 假值，说明环境里有这类变量也不会挂）：
    - 不带 `--mcp-servers` 的 `run`：`mcp_servers` 的键只有 `data`，`allowed_tools` 里没有 `mcp__data` 以外的 `mcp__` 条目，`version` 事件没有 `external_mcp_servers`。
    - Harbor：`evals/harbor_agent.py:305-328` `_run_command` 拼出的命令里没有 `--mcp-servers`。照 `tests/test_harbor_agent.py:343-348`（`--network` 的同类断言）的做法：本机跑一次试验，看假的 `data-agent` 收到的参数。它依赖那个文件里的 `local`、`make_agent`、`stub_args` 等脚手架；照抄成本高的话，可以改为直接构造 agent、断言 `_run_command(...)` 返回的字符串。只读复用，不改那个文件。
    - DAB：照 `test_chat_eval_unchanged.py:145-151` 的 `dab_query` 驱动 `evals.dab.run_query`，`mcp_servers` 的键只有 DAB 自己的服务器（`dab.py:460-464`），`allowed_tools` 里没有别的 server 的条目。
  - **stdio 包装**（`test_mcp_launch.py`，`TICKET_116`）：
    - 配置里 stdio server 的原命令是 `sys.executable tests/fixtures/mcp/print_env.py <输出文件>`。这个只用标准库的小脚本做两件事：把 `os.environ` 写成 JSON 到输出文件；把 stdin 逐行原样回写到 stdout。
    - 配置 `env` 里有 `{"MDB_MCP_CONNECTION_STRING": "${DATA_AGENT_MCP_MONGO_URL}", "MDB_MCP_READ_ONLY": "true"}`。
    - 从截下来的选项里取出这个 server，按 CLI 起 stdio server 的方式在临时目录里真实执行：命令是 `command` 加 `args`，环境是 `{**脏的父环境, **options.env, **server.get("env", {})}`。
    - 脏的父环境包括：`DATA_AGENT_API_KEY`、`DATA_AGENT_BASE_URL`、`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`--postgres-env` 指向的变量、`--data-sources` 的 TOML 里 `env` 指向的变量、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL`、一个 `OTEL_EXPORTER_OTLP_HEADERS`。值都是测试里拼的假值。
    - 这样不管 116 让展开的值走 server 的 `env` 还是走 `options.env`，测试都成立。
    - 断言：
      - 子进程环境的键是 `BASE_ENV`、配置 `env` 的键、空环境基线的键三者合起来的子集，`PATH` 在里面；
      - `MDB_MCP_CONNECTION_STRING` 等于假 URL，`MDB_MCP_READ_ONLY == "true"`；
      - 父环境里那些变量一个都没有，任何值里也没有它们的假值；
      - 往 stdin 写一行，stdout 原样收到这一行，前后没有别的输出（包装不能往 stdout 写东西，否则 JSON-RPC 会坏）。
    - spec 写的是单元测试"不起 MCP server"，所以不另写假的 MCP server；stdin、stdout 原样透传用 `print_env.py` 的回写来证明。
  - **网页**（`external-tools.test.ts`，vitest）：
    - `TICKET_117`（`test.fails`）：
      - `mcp__mongo__find` 显示为 `mongo · find`；`mcp__github__get_issue` 显示为 `github · get_issue`；`mcp__mongo__list-collections` 显示为 `mongo · list-collections`。
      - 三者的 `toolKind` 都是 `'external'`。
      - `KIND_TAG`、`KIND_NAME` 对 `'external'` 都有非空的值，且和 `'other'` 的不同。显示文字由 117 定，见"spec 里的解读"第 5 条。
    - 守门测试，用普通 `test`，现在就通过：
      - `mcp__data__query_database` 的 `shortTool` 和 `toolKind` 不变（`query_database`、`'sql'`）；
      - 用 `buildRunView` 喂一组手写事件：`mcp__mongo__find` 的结果即使看起来像表格，`table` 也是 null，结果按文本保留；`mcp__data__query_database` 的结果照旧解析成表格（`model.ts:183,415`）。
    - 测试文件不改 `tools.ts` 也要能通过 `make check` 的 typecheck：`'external'` 还不在 `ToolKind` 里，所以用 `toBe('external')` 比较，并用 `Record<string, string>` 转型去索引 `KIND_TAG`、`KIND_NAME`。
- **MongoDB 真模型用例**（`test_mcp_e2e.py`，`pytestmark = pytest.mark.integration`，`TICKET_116`）：
  - 照 seam A（`tests/acceptance/test_seam_a_run_cli.py`）通过 `data-agent run` 子进程跑。测试自己起一个 docker 网络和一个 mongo 容器（名字带随机后缀），灌入 `tests/fixtures/mcp/` 里的小演示集合，测完删掉。
  - 演示集合如每位客户的支持工单，`customer_id` 对得上 `tests/fixtures/shop.sql` 的 `raw.customers`。
  - 连接串在运行时拼出来，经 `DATA_AGENT_MCP_MONGO_URL` 传入；配置文件只有引用。
  - MCP server 用 `mongodb/mongodb-mcp-server` 的 docker 镜像。
    - 镜像版本钉一个具体 tag，不用 `latest`。
    - 工具名（`find`、`aggregate`、`count`、`list-collections`、`collection-schema`）和只读开关的写法，照官方文档或镜像实际列出的工具核实（spec 注明示例要核实）。
    - 能跑 Docker 的话，用 MCP 客户端起一次镜像列工具；这一步不调模型。
  - 两个用例：
    1. 只读模式加 `--duckdb shop.duckdb`：问一个要同时查 MongoDB 和 DuckDB 才能答的问题，例如"支持工单最多的客户，他的订单总额是多少"。断言事件里有 `mcp__mongo__` 工具的 `tool_start`，答案数值带容差等于夹具算出的值。
    2. 不开只读、只放行查询工具：让 agent 往集合里插一条数据。
       - 正向断言，今天会失败：`version` 事件的 `external_mcp_servers` 记有 `mongo` 和它的放行清单，并且 agent 至少成功调用过一个放行的 `mcp__mongo__` 查询工具。这样证明 server 真的挂上了、拒写不是因为什么都没挂。
       - 断言没有成功的 `mcp__mongo__insert-many` 之类的写入调用（调用被拒或根本没发出），测后直接查 mongo，集合的文档数和内容不变。
    - spec "验收"说集成测试"加 MongoDB 一个用例"，第 2 个用例是"验收"里拒写那条的自动化版本，见"spec 里的解读"第 4 条。
  - 问题和答案自己写，不用榜单题目（ADR-0004、ADR-0005）。答案写死在测试里，并附一条算出它的查询。
  - GitHub 那条要人准备的 token，不写进自动化测试，由主会话按 spec "验收"手动跑。
  - 包装从干净环境起 `docker`：本机 colima 靠 `~/.docker` 下的 context 找 daemon，`HOME` 保留了，所以够用。如果环境里用的是 `DOCKER_HOST`，它会被包装去掉，这时由 116 处理，115 只在 Comments 里记下观察到的情况。
- **spec 里的解读**（本工单的处理，最终回复里列出供人审）：
  1. spec "凭据"写"展开后的值不写进……命令行参数"。但 SDK 会把 `mcp_servers` 整个序列化成 CLI 的 `--mcp-config` argv（取证笔记 Q4.3、未确认项 8，`subprocess_cli.py:667-687`），http 的请求头避不开。"Testing Decisions"把命令行限定为"网页拼的命令行"，所以测试只断言三处：网页的子进程参数、stdio server 的 `command`/`args`、事件文件。SDK 交给 CLI 的 argv 不断言。这一点留给人定（接受为已知限制，或由 116 核实 CLI 能否自己展开 `${VAR}`）。
  2. `${VAR}` 写在 `args`、`url`、`command` 里怎么办，spec 没写，测试不覆盖，留给人或 116 定。
  3. 带了外部配置后 `strict_mcp_config` 仍是 `True`：spec 只经 `mcp_servers` 挂 server，用户机器上的 `.mcp.json`、`~/.claude.json` 仍然不该混进来（取证笔记 Q3.4 ktx 的先例）。这是由 spec 推出来的断言，人审时确认。
  4. MongoDB 集成测试写成两个用例（spec 写的是"一个"），第二个是"验收"里拒写那条，自动化以后不用人手动查库。人审时可以删掉它。
  5. 网页类别："外部工具"是 spec 的描述，界面现有标签都是英文（`tools.ts:41-67`）。测试只钉类别 id `'external'`，标签文字由 117 定。
  6. `data-agent web` 启动时要不要先校验配置文件，spec 没写，不测。
  7. `BASE_ENV` 的具体集合（契约第 6 项）是本工单的提议，人审时确认。
- 只用 uv、pnpm。

## Acceptance

- [ ] spec "Testing Decisions" 每一条都有断言，落在 Context "各组要断言的行为"写的测试文件里；Context 契约第 1–7 项写在测试文件的 docstring 里
- [ ] `TICKET_116`、`TICKET_117` 标记的测试当前都以预期失败的方式通过检查，没有被跳过的；守门测试当前都通过，且不带标记
- [ ] 每个预期失败的测试，失败原因都是功能还没做（例如选项里没有 `mongo`、stderr 里没有点名 server、`shortTool` 返回完整名字），不是夹具没起来或 argparse 不认识参数。最终回复给每组贴一个用 `--runxfail` 跑出的真实失败原因，前端贴去掉 `.fails` 后的失败信息
- [ ] 临时写一个参考实现（不提交，结束后 `git status src/ web/src/run-page/tools.ts` 干净），证明去掉预期失败标记后各组都能通过。每组至少一个反向检查会失败，例如：
  - 包装把父环境整个传下去；
  - `["*"]` 生成了 `mcp__*`；
  - `version` 事件记了 env 的值；
  - 网页的对话一轮没带 `--mcp-servers`；
  - `shortTool` 也把 `mcp__data__` 变成 `data · ...`；
  - `build_options` 默认就挂了配置（会让守门组失败）。
- [ ] `uv run pytest tests/acceptance/test_mcp_e2e.py --collect-only -m integration` 列出两个用例；`make check` 不跑它们
- [ ] 夹具和测试里没有密钥、地址、主机名：`uv run python -m evals scrub tests/fixtures/mcp` 输出 0 处改动，pre-commit 的密钥与公网 IP 扫描通过
- [ ] src 里只有 Authority 写的两处空壳；`git diff main -- src/` 只有 `cli.py` 的两个 `add_argument` 和 `app.py` 的一个参数
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 的输出。
- `uv run pytest tests/acceptance/test_mcp_*.py -v` 的输出：每个测试的名字，是 XFAIL 还是 PASSED。
- `pnpm --dir web test` 里本文件的输出。
- 每组的 `--runxfail` 失败原因。
- 参考实现和反向检查的结果。
- 脱敏命令的输出。
- 核实 mongodb-mcp-server 镜像 tag 和工具名的来源（文档链接或列工具的命令及输出）。
- 两个 MongoDB 用例的答案，各附算出它的查询。
- 契约第 1–7 项和"spec 里的解读"第 1–7 条。

交给主会话真跑（工单 agent 不跑）：先 `colima start`，再 `uv run pytest tests/acceptance/test_mcp_e2e.py -m integration --runxfail -v`，确认两个用例失败在"agent 没有 mongo 工具"这一步，不是 docker 或镜像起不来。这次花费小，按小规模运行处理。116 合入时再由主会话真跑一次，看到转绿。

## Stop conditions

- spec 的某条行为在约定的接缝上测不了，或者要改 Authority 以外的实现代码才能测（例如 transport 截停拿不到 `version` 事件）。
- 守门组写不出来，或写出来现在就不通过（说明评测入口已经会挂外部 server）。
- 断言需要一个人还没定的选择，例如：凭据进 argv（"spec 里的解读"第 1 条）以外又碰到新的这类问题；`BASE_ENV` 的集合定不下来。契约里字段名这类细节不算，由本工单定、人审。
- spec 自相矛盾。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2 (Fable stand-in for `da-review`, then `eng:code-review`)
- Model spend: $0
- Outcome: —

## Comments

- 2026-11-23（工单 agent 最终回复，主会话照抄）：

**`make check` (HEAD fcfe404):** rc=0. ruff, format and basedpyright clean (0 errors). pytest: `2597 passed, 2 skipped, 79 deselected, 22 xfailed`. web: `Test Files 21 passed`, `Tests 322 passed | 7 expected fail`.

**`uv run pytest tests/acceptance/test_mcp_*.py -v`:** 7 PASSED, 22 XFAIL, 2 deselected (the e2e tests, which carry the `integration` mark). Nothing is skipped and nothing XPASSes.
- **`test_mcp_config.py`**, all XFAIL (17):
  - `run_mounts_stdio_and_http_servers_from_the_file`
  - `a_bad_server_is_refused…`: sse, unknown-type, reserved-name, dot, space, slash
  - `a_server_without_an_allow_list…`: missing, empty
  - `variables_are_expanded_into_http_headers`
  - `a_bad_variable_is_refused…`: unset-in-headers, unset-in-env, other-prefix, model-key
  - `expanded_values_stay_out_of_events_output_and_the_stdio_command_line`
  - `allowed_tools_become_mcp_names_and_nothing_else_changes`
  - `the_version_event_names_the_servers_and_their_allow_lists_only`
- **`test_mcp_launch.py`:**
  - XFAIL ×2: `a_stdio_server_starts_in_a_clean_environment…`, once with `--postgres-env` and once with `--data-sources`.
  - PASSED: `the_empty_environment_baseline_is_small…` (guard).
- **`test_mcp_web.py`:**
  - XFAIL: `every_web_run_gets_the_config_path…` for form and turn; `data_agent_web_hands_the_config_file_to_create_app`.
  - PASSED: `without_a_config_no_web_run_gets_mcp_servers` for form and turn; `data_agent_web_without_the_option_gives_create_app_none`.
- **`test_mcp_eval_unchanged.py`:** PASSED ×3 (run, Harbor, DAB).
- **`test_mcp_e2e.py`:** `--collect-only -m integration` lists both cases; `make check` deselects them.

**`pnpm --dir web test`, `external-tools.test.ts`:** 2 passed, 7 expected fail.

**Real failure reasons today (`--runxfail`):** all 22 fail with `AssertionError`; none fail with KeyError, ValueError or RuntimeError.
- config: `assert {'data'} == {'data', 'github', 'local', 'mongo'}`
- refusals: `stderr does not name 'legacy': 'error: the agent SDK failed before the run ended…'`
- missing allow list: `no warning naming it`
- version event: `no external_mcp_servers in ['cli_version', 'config', …]`
- launch: `no server 'mongo' in mcp_servers: ['data']`
- web: `--mcp-servers not in the arguments: ['--task=Count orders', …, '--network']`
- web CLI: `create_app got no mcp_servers`
- frontend with `.fails` removed: `shortTool` returns the full name, and `toolKind` is not `'external'`.

**Reference implementation:**
- I wrote a throwaway one in `/tmp/115-ref/` and applied it uncommitted:
  - a new `external_mcp.py` (parsing, `${VAR}` expansion, allow list, `mcp-launch` wrapper)
  - wiring in `cli`, `agent.run` and `build_options`, `launcher`, `conversations`, `app`
  - an `'external'` kind in `tools.ts`
- With it, all 29 backend tests pass under `--runxfail`, and the frontend file with `.fails` removed gives 9/9 passed.
- I then reverted it with `git apply -R`. `git status --short` is empty, and `git diff main -- src/` shows only `cli.py` (+12, the two `add_argument`) and `app.py` (+1, the parameter).

**Reverse checks** (one mutation at a time on top of the reference implementation):

| Mutation | Tests that failed |
|---|---|
| Wrapper passes the whole parent environment | both launch tests |
| `["*"]` becomes `mcp__*` | `allowed_tools_become_mcp_names…` |
| `version` event also records the server config, env values included | `the_version_event…` |
| Conversation turn omits `--mcp-servers` | `every_web_run…[turn]` (form still passes) |
| `build_options` mounts `mcp__demo__*` by default | `run_without_mcp_servers…` and the DAB guard |
| `shortTool` turns `mcp__data__` into `data · …` | guard `keeps its short name`: Expected `"query_database"`, Received `"data · query_database"` |

**Scrub:** `uv run python -m evals scrub tests/fixtures/mcp` prints `changed 0 file(s) of 2; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0`. Pre-commit, including the secret scan, passed on all 3 commits.

**Empty-environment baseline:**
- Linux (`python:3.12-slim`, `env -i python print_env.py`): `{"LC_CTYPE"}`. A clean exec with `PATH`, `HOME`, `LANG` and the config env shows only those keys.
- macOS: `{"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}`.
- Neither produces a false alarm.

**mongodb-mcp-server, verified with the `mcp` Python client against a mongo container on its own docker network (no model):**
- **Image:** `mongodb/mongodb-mcp-server:3.3.0`, digest `sha256:277bde43…`. The mongo image is `mongo:8.0`.
- **Read-only (`MDB_MCP_READ_ONLY=true`), 18 tools:** `aggregate aggregate-db collection-indexes collection-schema collection-storage-size connect count db-stats disconnect explain export find list-collections list-connections list-databases list-knowledge-sources mongodb-logs search-knowledge`.
- **`MDB_MCP_READ_ONLY=false`, 27 tools:** adds `create-collection create-index delete-many drop-collection drop-database drop-index insert-many rename-collection update-many`. So `false` really does turn read-only off.
- **`connectionId`:** in 3.3.0 the query tools take one (`"preconfigured"` per their schema). The e2e allow list therefore adds `list-connections` to the ticket's five tools.
- **Network reach:** `find` with `{"connectionId":"preconfigured","database":"support","collection":"tickets","filter":{"customer_id":"C0004"}}` returned "5 documents", so `docker run --network <net>` reaches mongo by container name.
- **Telemetry:** the server sends it by default, so the e2e config sets `MDB_MCP_TELEMETRY=disabled`.
- **`DOCKER_HOST`:** not set on this machine (colima context under `~/.docker`), so the wrapper keeping `HOME` is enough.

**MongoDB case answers:**
1. **Join question:** C0004 (5 tickets, next is 3) has orders totalling **202.50**. Query: `SELECT sum(amount) FROM raw.orders WHERE upper(replace(trim(customer_ref),'-','')) = 'C0004'`; the ticket count comes from `support_tickets.json`.
2. **Insert:** the task first asks for the document count, **14** (`db.tickets.countDocuments()`), so an allowed query call is part of the task. It then asks for one insert. The test checks the documents and the collection list are unchanged. It does not check the count in the answer.

**Contract items 1–7** are written in the test docstrings: 1, 3, 4, 5 in `test_mcp_config.py`; 1, 2 in `test_mcp_web.py`; 6 in `test_mcp_launch.py`; 7 in `external-tools.test.ts`.
1. `run` and `web` both take `--mcp-servers FILE`.
2. `create_app(..., mcp_servers=None)`. When given, form runs and every conversation turn get `--mcp-servers <path as given>`.
3. A config error exits 2 before any session starts, with stderr naming the server (and the variable for `${VAR}` errors). A missing or empty `allowedTools` gives a stderr line containing `warning` and the server name.
4. `version.external_mcp_servers` is `[{"name", "allowed_tools"}]`, in file order, with `["*"]` kept as is. It is absent without `--mcp-servers`.
5. Option shape:
   - `mcp_servers` keys are `data` plus the config's server names.
   - http servers keep their expanded headers.
   - a stdio server's command is the wrapper, with no expanded value in `command` or `args`.
   - `allowed_tools` is the original list plus `mcp__<s>__<t>` or `mcp__<s>__*`, original entries in order.
   - `tools` and `disallowed_tools` are unchanged; `dontAsk`; `strict_mcp_config=True`.
6. A stdio server's environment keys are a subset of `BASE_ENV` ∪ the config env keys ∪ the empty-environment baseline. `PATH` is present. stdout echoes stdin exactly.
7. `toolKind` returns `'external'`, and `shortTool` returns `"mongo · find"`.

**"spec 里的解读" 1–7,** written as in the ticket:
1. The SDK puts headers into the CLI's `--mcp-config` argv; that argv is not asserted (open for the human).
2. `${VAR}` inside `args`, `url` or `command` is not tested.
3. `strict_mcp_config` stays `True`, to be confirmed.
4. MongoDB has two e2e cases; you can drop the second.
5. Only the kind id `'external'` is pinned; the label text is 117's.
6. Whether `web` validates the config at startup is not tested; the web CLI test sets the token so either choice passes.
7. `BASE_ENV = {PATH, HOME, LANG, LC_ALL, LC_CTYPE, TMPDIR, USER, LOGNAME, SHELL, TERM, TZ}`, to be confirmed.

**Choices beyond the ticket, for the human review:**
- `list-connections` is in the e2e allow list.
- e2e sets telemetry off.
- e2e labels the MCP server containers so teardown removes any a killed run left.
- The launch test runs with `--config no-context`, so nothing tries the fake Postgres URL before the session.

**Reviews:**
- **Step 5a, Fable read-only reviewer** (standing in for `da-review`). Fixed in 1073e95:
  - the web CLI test now sets the token its config references;
  - e2e case 2 asks for a read first;
  - an e2e run that writes no event file, or whose model never answered, raises RuntimeError instead of hiding as XFAIL;
  - leftover MCP containers are removed by label, and a failed network removal warns;
  - the "no `--mcp-servers`" guard also catches the `--mcp-servers=PATH` form;
  - e2e case 2 also checks the collection list, to catch `aggregate`+`$out`.
- **Not fixed:**
  - The reserved-name refusal only needs stderr to contain the standalone word "data". Requiring the name quoted would pin the message wording, which the contract leaves to 116; the exit-2 and no-session checks still hold.
  - `started.argv != original` is weak. Only noted: the environment assertions carry the real check.
- **Step 5b, `eng:code-review main`.**
  - Standards found no hard violations. Fixed in fcfe404: the unused `extra` and `name` parameters are gone and `names()` is now `mentions()`. Left as is: the per-file `TICKET_116` definitions, the AGENTS.md pattern.
  - Spec found nothing missing. Fixed: the Harbor guard no longer pins `--sandbox local`. Not fixed: the same reserved-name point as above.

**Hand-off to the main session:**
- Run `colima start`, then `uv run pytest tests/acceptance/test_mcp_e2e.py -m integration --runxfail -v`. Both cases should fail with "the agent called no mcp__mongo__ tool" (case 1) or "the version event names no external server" (case 2), not with a RuntimeError from docker or the model.
- Local `main` has moved past this branch's base. The branch sits on 0caef46 while `main` is at 6c9387b, so a rebase may be needed before the PR.

- 2026-11-23（主会话）：PR CI 绿；Codex GitHub app 审查（`@codex review` 触发）完成，无意见。等人审验收测试后合并。
