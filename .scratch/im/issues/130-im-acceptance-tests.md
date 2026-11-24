# 130 — IM 入口：验收测试（会话映射、触发、澄清、进度、插话与取消、排版、配置、出错；全部不连平台、不调模型）

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 130-im-acceptance-tests)
Branch: worktree-130-im-acceptance-tests

## Goal

spec "Testing Decisions"（`.scratch/im/spec.md:102-114`，含 2026-11-24 的更新）列的公共部分八组行为都有验收测试，落在 spec 约定的接缝上：一个内存里的假平台（实现平台接口）加对话模块的替身，不连真实平台、不调模型。还没实现的行为标预期失败（`xfail(strict=True)`），由 131 转绿。适配器的平台接口和公共部分的入口写成只有签名、函数体抛出 `NotImplementedError("ticket 131")` 的空壳（Context "本工单定下的契约"）。本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit（都是新建）：
  - `src/data_agent/im/`：只放平台接口（Protocol 或抽象类）、消息与按钮的数据类型、常量，以及公共部分入口的空壳（含契约第 7、8 项），函数体 `raise NotImplementedError("ticket 131")`。不放任何行为，不 import 平台库。
  - `src/data_agent/cli.py`：只给 `web` 子命令（`cli.py:360-373`）加一个 `--im`（`type=Path`，help 写明是 IM 配置的 TOML），解析出来的值不往下传，不加任何行为（照工单 115 给 `--mcp-servers` 加空壳的做法）。
  - `tests/acceptance/test_im_*.py`：按下面"各组要写的测试"分文件，文件名由本工单定。
  - `tests/acceptance/im_support.py`：假平台、对话模块的替身、往临时 runs 目录写事件文件的辅助、可控时钟。照 `tests/acceptance/mcp_support.py`、`observability_support.py` 的做法单独成文件。
  - `tests/fixtures/im/`（需要时）：手写的事件文件、示例配置 TOML（只写变量名，不写 token 值）。
- Must not edit：
  - `pyproject.toml`、`uv.lock`：本工单不加 slack_bolt、aiogram、discord.py。假平台不需要它们；它们是可选依赖 `data-agent[im]`（spec `:89`），由 131–133 加。
  - `src/data_agent/cli.py` 里上面那个 `add_argument` 以外的任何改动（`serve_web`、`start_run` 由 131 改）、`src/data_agent/web/`（`app.py`、`conversations.py`、`launcher.py`、`runs.py`）、`src/data_agent/events.py`，以及 `src/data_agent/` 里 `im/` 以外的任何文件。
  - 已有的 `tests/acceptance/` 文件（包括 `conftest.py`、`seam_support.py`、`test_chat_api.py`；要用的辅助只读 import 或照抄进 `im_support.py`）和 `tests/fixtures/` 里已有的文件（可以只读使用）。
  - `web/`、`evals/`、`Makefile`、`.github/workflows/`、README、`docs/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0（不调模型；spec 的单元测试不连平台、不调模型，`.scratch/im/spec.md:104`）

## Context

代码现状按 HEAD `6b7ada6` 核对；行号以本节为准，`docs/research/2026-11-23-im-entry.md` 题 4 里的行号已过时（例如它写的 `cli.py:35` 现在是 `cli.py:38`）。

- **spec**：`.scratch/im/spec.md`。行为来源是"进程与接口"`:20`、"会话映射"`:29`、"澄清、进度、取消"`:38`、"答案排版"`:45`、"权限"`:52`、"配置"`:58`、"出错"`:81`、"库"`:87`，"Testing Decisions"`:102-114` 逐条列了要断言的行为，照它写。spec 里标"Update 2026-11-24"的几处（`:25`、`:77`、`:79`、`:83`、`:89`）是这次更新。取证笔记：`docs/research/2026-11-23-im-entry.md`（题 3 三个平台的限制，题 4 本仓库对话后端，"未确认项"）、`docs/research/2026-11-23-im-gateway-designs.md`（10 个设计点的上游做法）。
- **本工单的范围**：spec `:114`"每个平台实现（131–133）再有一组测试：平台消息格式 ↔ 接口的转换（用平台库的事件样例）"，本工单读作由各平台工单在自己的工单里写（它们的接缝是平台库，平台库也由它们加），本工单不写，也不加平台库。spec 的"发送失败：重试一次"（`:85`）、"遇到平台限流就跳过"（`:41`）、Markdown 转各平台格式（`:47`）、缺库时 `--im` 的报错（`:89`）、`--im` 读配置后真的起平台，都不在"Testing Decisions"里，本工单不测。`--im` 只用于"启动运行时的环境"一条（见"token 与环境"）。

- **对话模块（替身要照它的真实形状）**：`src/data_agent/web/conversations.py`。
  - `Conversations.create() -> str`（`:57-67`）：新建对话目录和 `conversation.json`，返回对话 id。
  - `Conversations.send(conversation, text, network=True) -> dict`（`:97-110`）：空白消息抛 `Refused(400, ...)`（`:103-104`）；这个对话有一轮在跑时变成插话，返回 `{"kind": "steer", "run_id", "id"}`（`:108-109`、`:168`）；否则开新一轮，返回 `{"kind": "turn", "turn", "run_id"}`（`:156`）。
  - 一轮的参数里总有 `--charts`、`--sandbox docker`，`network` 为真时有 `--network`，网页带了 `--mcp-servers` 时也有（`:136-146`）。所以 IM 只要调 `send()`，运行选项就和网页对话一样（spec `:27`）。
  - `Conversations.cancel(conversation) -> dict`（`:112-121`）：这个对话没有一轮在跑时抛 `Refused(409, ...)`（`:118-119`）。
  - 插话的已知限制：`result` 已写、进程还没退出时发的插话送不到，事件文件里没有它的 `steer_delivered`（`:158-161`）。
  - "同时只跑一条"跨表单和所有对话：另一条运行在跑时，新一轮被拒，`Refused(409, "run ... is still going; one run at a time")`（`src/data_agent/web/launcher.py:214`）。`Refused(status, detail)` 定义在 `launcher.py:67-73`。
  - 这些方法是同步的、会持有 launcher 的锁；网页路由用 `to_thread.run_sync` 调它们（`src/data_agent/web/app.py:160`、`:166`、`:177`）。
  - 替身：一个记录调用的对象，有同名方法，返回上面的真实形状，按测试设定抛 `Refused`。可以继承或照抄 `Conversations` 的签名，不起子进程，不用 `tests/fixtures/chat/fake_chat_run.py`。

- **事件读取（和网页同一套）**：`src/data_agent/web/runs.py`。
  - 布局：每条运行是 `<runs-dir>/<run_id>/`，里面一个 `*.jsonl` 事件文件（`:53-59`）；对话在 `<runs-dir>/conversations/`（`:27`）。
  - `RunsDir.read(run_id, offset=0) -> Chunk | None`（`:103-106`）：只返回以换行结尾的完整行，`Chunk.offset` 是下次读的起点（`:140-147`）。
  - `RunsDir.has_result(run_id)`（`:79-83`）：`result` 是一条运行写的最后一个事件（`:161`）。
  - `RunsDir.ended(run_id)`（`:74-77`）：本服务起的运行退出时没写 `result`，`web-run.json` 的状态变成 `cancelled` 或 `failure`（`launcher.py:307-319`）。所以被取消、崩掉的运行没有 `result` 事件可读。
  - 测试把事件一行一行追加进临时 runs 目录里的真实事件文件，适配器经 `RunsDir` 读，不另造读取接口。没有 `result` 的运行（取消、崩溃，spec `:83`）用 `RunsDir.write_record`（`runs.py:67-72`）写一份 `web-run.json`，状态 `cancelled` 或 `failure`（`launcher.py:315` 的取值）。
  - `<runs-dir>/im/`：`RunsDir.run_dir` 把 `conversations` 以外的任何普通名字都当作运行（`:44-51`）。`im/` 里没有 `*.jsonl` 也没有 `web-run.json` 时 `summary` 返回 `None`（`:122-128`），不进运行列表；放了 `*.jsonl` 就会被当成一条运行。映射文件不能用 `.jsonl` 后缀。

- **事件（测试要喂的类型和字段）**：
  - `clarification`：`tool_use_id`、`question`、`options`（`src/data_agent/agent.py:1525-1530`）；选项 2–5 个，互不相同、非空白（`src/data_agent/mcp_server.py:96-110`）。
    - 澄清的一轮写完 `clarification` 就结束：`result` 是 `success`、没有 `answer` 事件（`agent.py:1502-1509` 的说明）。
    - 现成样例 `tests/fixtures/chat/conversation/20261117T100000Z-a1a1a1/events.jsonl` 的顺序是 `version task status thinking tool_start tool_end tool_start clarification tool_end result`。它的 `result.terminal_reason` 是 `completed`，真实 CLI 是 `hook_stopped`（`agent.py:1505-1507`），所以测试不要靠 `terminal_reason` 认澄清。
  - `tool_start`：`tool`、`tool_use_id`、`parent`（子 agent 的调用非空）、`input`（`agent.py:1841-1847`）；查数时紧跟一个 `sql` 事件（`:1848-1855`）；`tool_end`：`tool`、`is_error`、`content`（`:1861-1868`）。
  - 验证子 agent 开始：`verifier_start`（`src/data_agent/verifiers.py:120`）。
  - `answer`：`text`（`agent.py:2404`），只在有最终答案、没有被停下时写。
  - `result`（`agent.py:2424`，字段来自 `result_fields`，`:1881-1942`）：`subtype`、`is_error`、`terminal_reason`、`errors`、`cost_usd` 等。
  - 用时：spec `:83` 定为按 `result.ended_ts`（工单 122 加的字段），没有时用 `result.ts`，起点是第一个事件的 `ts`（和网页一样，`runs.py:167`、`:173`）。122 还没合入，现在 `src/` 里没有 `ended_ts`；测试的事件文件手工带上它，或者只断言不依赖它的结构（例如用时出现、花费出现）。
  - 结束原因的现有取值：
    - 预算用完：`subtype: "error_max_budget_usd"`、`terminal_reason: "budget_exhausted"`（`agent.py:704-705`）；
    - 轮数用完：`error_max_turns` / `max_turns`（`agent.py:2060`）；
    - 到截止时间：`error_deadline` / `deadline`（`agent.py:2052`），只在 `data-agent run --agent-timeout-sec` 给了时出现（参数没有默认值，`cli.py:297-301`；`cli.py:460`）；对话的一轮不带这个参数（`conversations.py:136-146`），所以网页对话里不会出现，spec 已删掉"超时"（`:83`），不测；
    - 模型调用失败：`terminal_reason: "api_error"`，`api_error` 字段是 CLI 的错误文本（如 "API Error: Request timed out"，`agent.py:1932-1934`）。
  - 图表：工具名 `mcp__data__display_chart`（`mcp_server.py:374`，server 名 `data`）。网页的答案区只算主 agent 成功、结果能解析成图的调用（`web/src/chart/model.ts:52-73` 的 `answerCharts`：跳过子 agent 的、出错的、没有结果的）。Python 端没有现成的计数函数（`src/data_agent/charts.py` 只有 `display_chart` 自己的校验和返回值）。现成样例 `tests/fixtures/charts/run/events.jsonl`：6 次调用，5 次成功、1 次失败，都在主 agent。

- **token 与环境**（spec `:77`、`:79`、`:112`）：
  - 运行写事件文件时要遮的值由 `agent.py:2201-2210` 拼出：`known_secrets`（`events.SECRET_ENV` 的三个变量和模型端点的主机，`src/data_agent/events.py:27-37`）、Postgres 连接串的 `url_secrets`、Langfuse 的密钥、外部 MCP 配置的值，都从运行子进程自己的环境读。里面没有 IM 的 token 变量。
  - 起运行的调用链：`serve_web` 定义 `start_run`（`cli.py:556-565`），`subprocess.Popen([sys.executable, "-m", "data_agent", "run", *args], cwd=..., start_new_session=True, stdin=subprocess.PIPE)`，不传 `env=`，所以子进程继承网页进程的全部环境；`serve_web` 把它交给 `create_app(runs_dir.resolve(), sources, start_run, mcp_servers=...)`（`cli.py:568`）；`create_app` 交给 `Launcher`（`app.py:77`）；表单运行和对话的每一轮都经 `Launcher.launch` 里的 `self.start_run(args, cwd)`（`launcher.py:232`）起进程。沙箱命令拿不到服务器的环境变量（`src/data_agent/sandbox.py:248` 的说明）。
  - **启动运行时的环境，今天就能测的接缝**：照 `tests/acceptance/test_mcp_web.py:168-193` 的 `serve`，monkeypatch `data_agent.web.app.create_app`（记下参数）和 `uvicorn.run`，跑 `cli.main(["web", "--runs-dir", D, "--im", F])`；再 monkeypatch `data_agent.cli` 里的 `subprocess.Popen`（`cli.py:10` 是 `import subprocess`）记下关键字参数，调用截下来的 `start_run(["--task=x"], tmp_path)`。
    - 正向断言放前面：`Popen` 收到了 `env=`（今天没有，`AssertionError`）。
    - 然后：`env` 里没有配置点名的 token 变量名，任何值里都没有 token 的值；`PATH` 和一个无关的 `DATA_AGENT_` 变量（如 `DATA_AGENT_API_KEY` 的假值）还在，说明只去掉了 IM token。
    - 平台不能在测试里真的连：契约第 7 项的"启动适配器"入口由 `serve_web` 调用，测试把它换成记录调用的替身。
    - 可以另加契约第 8 项纯函数的测试，但它证明不了 `start_run` 用了它，所以上面这条经 `start_run` 的测试必须有。
  - 不另加遮罩（spec `:79`，主会话 2026-11-24 更正）：运行的遮罩从子进程自己的环境读（`agent.py:2201`），变量去掉后运行拿不到这些值，事件文件里也就不会有。
  - 适配器自己不写事件。"token 不出现在日志和事件里"在本接缝上能断言的是：适配器的日志（`caplog`）、stderr 和 stdout（`capsys`）、映射文件、交给对话替身的参数、假平台收到的每一条消息、配置报错的文本里都没有 token 的值。
  - 假 token 值在测试代码里拼出来，照 `tests/acceptance/test_chat_api.py:104` 的 `PG_PASSWORD = "hunter2-" + "k4Rz"`，用 `monkeypatch.setenv` 设进环境，不写进夹具文件。不要在源码里写出形如真实 Slack token（`xoxb-`、`xapp-` 加长串数字）的字面量，pre-commit 的密钥扫描会拦。

- **测试工具**：`pytest-asyncio` 已在 dev 组（`pyproject.toml:39`），`asyncio_mode = "auto"`（`:84`）。节流测试用可控时钟（由契约注入），不用真实 sleep。

### 本工单定下的契约（写进 `src/data_agent/im/` 的空壳和测试 docstring，最终回复逐项列出供人审）

模块、类、方法、字段的名字和签名由本工单定、人审；131–133 照它实现。至少要覆盖：

1. **平台接口**（spec `:24-26`，照 Chat SDK、Hermes 的窄接口）：
   - 必需：收消息、发消息。收到的消息带平台、聊天 id、线程 id（可为空）、用户 id、是私聊还是群、是否 @ 了机器人（spec `:25`）。"私聊还是群"是必需字段，字段名和类型由本工单定、人审。
   - 可选能力：编辑消息、按钮及其回调、typing 指示。平台要能声明自己有没有这些能力，适配器按声明降级（spec `:26`）。
   - 平台还要声明自己的单条长度上限和编辑节流间隔，测试里的假平台用自己的小数值，不写死 4000、4096、2000、1 秒、2 秒。
2. **公共部分的入口**：适配器接收一个平台、对话模块（或替身）、`RunsDir`、允许名单和时钟，处理收到的消息和按钮回调。同步还是异步由本工单定。
3. **会话映射**：映射文件放在 runs 目录下适配器自己的路径（spec 举例 `<runs-dir>/im/threads.json`，`:36`），不改 `conversation.json`。会话键是平台 + 聊天 id + 线程 id（`:31`）。
4. **配置读取**：一个函数，输入 `--im` 的 TOML 路径和环境变量映射，返回各平台的配置或抛出点名平台的错误（spec `:60-78`）。变量名必须是 `DATA_AGENT_<平台>_` 前缀（`:77`）。
5. **排版**：按平台上限拆分答案、把表格转成代码块、附图表说明，可以是纯函数。
6. **进度文本**：由事件序列生成状态文本，可以是纯函数。
7. **启动适配器的入口**：`serve_web` 在 `--im` 给了时调用它（131 接线）；验收测试把它换成替身，所以它的名字和所在模块由本工单定死，131 不能绕开它直接起平台。
8. **运行子进程的环境**：由网页进程的环境和 IM 配置得出交给 `Popen` 的 `env`（去掉配置点名的 token 变量），可以是纯函数。
9. **spec 没写死、由本工单定的行为细节**：下面每一项都由本工单定下、写进测试和 docstring，在最终回复里逐项列出，合并时人审。它们都不是停下的理由，照 111、118 的做法处理：
   - 忙：另一条运行在跑，`send()` 抛 `Refused(409)` 时机器人回什么；已经 `create()` 的对话怎么处理（不留下空对话，或者下次复用）。其他 `Refused`（如没配数据源的 400）同样处理。
   - 结束状态：点了停止之后、澄清的那一轮（没有 `answer`）、没有 `result` 的运行（取消、崩溃），状态消息各显示什么。
   - 机器人文字的语言和步骤标签（网页界面是英文，`web/src/run-page/tools.ts:24-30` 的 `shortTool`、`toolKind`）。
   - 文字 `stop` 的匹配规则（大小写、去掉 @ 之后），以及没有一轮在跑时（`cancel()` 抛 `Refused(409)`）回什么。
   - 名单对按钮点击、停止、插话是否同样生效。
   - 配置边界：未知的平台段落、`--im` 文件里一个平台段落都没有、`--im` 没带 `--data-sources`。
   - 拆分边界：单个段落就超过上限时怎么拆、代码块不拆开、表格截断的具体行数。
   - 图表张数 N 的算法：照网页的 `answerCharts`（`web/src/chart/model.ts:52-73`），只算主 agent 成功、结果能解析成图的调用。

### 预期失败的标记

- 照工单 88、115 的写法，每个文件定义：
  ```python
  TICKET_131 = pytest.mark.xfail(
      strict=True,
      raises=(NotImplementedError, AssertionError),
      reason="ticket 131: the IM adapter is not implemented yet",
  )
  ```
  只接受这两种异常：空壳抛 `NotImplementedError`，没做的行为让正向断言失败。夹具、假平台、事件文件写错引起的 `KeyError`、`TypeError`、`RuntimeError` 会报成失败，不藏成预期失败。夹具起不来就抛 `RuntimeError`（工单 88、115 的教训）。
- **strict 预期失败不能 XPASS**：很多"没有 X"的断言今天就成立（没回应、没调用 `send()`、日志里没有 token）。每个标了 `TICKET_131` 的测试都要先调用空壳（今天抛出 `NotImplementedError`），或者带一条今天会失败的正向断言，放在否定断言前面。例如"群里没 @ 不回应"先在同一个测试里断言"@ 了就回应"。只有否定断言的检查写成不标记的守门测试，或者并进有正向断言的测试。
- **不锁定措辞**：spec 写的文字（"未授权""查询数据库…""完成 · 用时 · 花费""这次答案有 N 张图"）由 131 定。测试只断言结构和数据：回复里有对方的用户 id、运行 id、图的张数 N、表的总行数；状态文本随事件变化；最终状态和前面的进度状态不同；按钮只写编号。
- **"约"字的数值**：spec 的"约 20 行""约 1 秒"用明显在两边的输入测（例如 5 行的表不截断、100 行的表截断），节流用平台声明的间隔加可控时钟测。
- 转绿时 131 只删 `TICKET_131` 标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩的唯一例外）。

### 各组要写的测试（spec "Testing Decisions" 逐条）

- **会话映射**（`:106`）：
  - 新消息（私聊一条；群里 @ 机器人一条）调用 `create()` 一次、`send()` 一次，`send()` 的对话 id 等于 `create()` 返回的；
  - 同一线程的回复只调用 `send()`，对话 id 不变，不再 `create()`；另一线程的新消息开另一个对话；
  - Telegram 的 `/new`（标 `TICKET_131`：映射逻辑属于公共部分，132 只把命令解析成"开新对话"，spec 工单表 `:96-97`）：没有线程的聊天里，"开新对话"之后的消息进一个新对话，之前的消息进旧对话；
  - 重启后映射还在：用同一个 runs 目录新建一个适配器，同一线程的回复进原来的对话；映射文件不是 `*.jsonl`，`RunsDir.summaries()` 里没有它。
- **触发**（`:107`）：
  - 群里没 @ 不回应、不调用对话模块；@ 了就回应；
  - 机器人已参与的线程里不 @ 也回应；
  - 名单外的用户：私聊收到一条回复，里面有他自己的用户 id；群里假平台没有收到任何消息；两种情况对话替身的 `create()`、`send()` 都没有被调用。
- **澄清**（`:108`）：
  - 喂一个 `clarification` 事件（可只读复用上面的对话样例），假平台收到一条正文里按编号列出问题和每个选项的消息；有按钮能力时按钮上只有编号，选项文字不在按钮上；
  - 点第 k 个按钮：`send()` 被调用一次，内容恰好是第 k 个选项的文字；
  - 打字回复：`send()` 被调用，内容是打的字；
  - 没有按钮能力的假平台：只有编号正文，没有按钮。
- **进度**（`:109`）：
  - 一轮开始发一条状态消息；按事件序列（如查数的 `tool_start`、`verifier_start`、`answer`、`result`）编辑它，状态文本随步骤变化；
  - 结束时状态文本和进度中的不同，里面有用时和花费（用时按上面"用时"一条）；答案另发一条，正文是 `answer.text`；
  - 节流：可控时钟下，一个节流间隔内来几个事件只编辑一次；时钟走过间隔后再编辑；
  - 没有编辑能力的假平台：没有任何编辑调用，只发最终答案。
- **插话与取消**（`:110`）：
  - 这个对话有一轮在跑时收到消息：调用 `send()`（替身返回 `{"kind": "steer", ...}`），不 `create()`；
  - 状态消息上有"停止"按钮（有按钮能力时）；点它调用 `cancel()`，对话 id 正确；
  - 文字 `stop` 调用 `cancel()`，不调用 `send()`。
- **排版**（`:111`）：
  - 长答案按段落拆成多条，每条都不超过假平台的上限，拼回去内容不丢；
  - Markdown 表格转成代码块；行数明显超过时截断，并写出总行数；
  - 有成功的 `display_chart` 调用时附一句说明，里面是图的张数。用 `tests/fixtures/charts/run/events.jsonl`（5 次成功、1 次失败），断言 N 是 5；没有图的运行不附。
- **配置**（`:112`）：
  - 合法配置（一个平台、多个平台）被接受，只返回写了的平台；
  - 下面几种都抛错，错误文本点名平台：引用的变量没设；名单为空；变量名不是这个平台的 `DATA_AGENT_<平台>_` 前缀，其中专门有一例是 `DATA_AGENT_API_KEY`（模型密钥），一例是别的平台的前缀（如 `[slack]` 里写 `DATA_AGENT_TELEGRAM_...`）；
  - 启动运行时的环境：经 `start_run` 的那条测试（Context"token 与环境"）；
  - 在每一种报错、合法配置的读取，以及适配器处理一条消息的过程中，token 的值都不出现在 Context"token 与环境"列的地方。
- **出错**（`:113`）：
  - `result` 为失败（如 `api_error`）、预算用完、轮数用完时，线程里收到一条回复，里面有这次的运行 id；三种回复互不相同。结束原因用 Context 列的真实取值造事件。
  - 没有 `result` 的运行：`web-run.json` 状态为 `cancelled`、`failure` 各一例，线程里收到一条回复，里面有运行 id，两种不同。

## Acceptance

- [ ] spec "Testing Decisions" `:106-113` 每一条都有断言，落在上面"各组要写的测试"列的用例里；Context"本工单定下的契约"1–9 写在 `src/data_agent/im/` 的空壳和测试 docstring 里
- [ ] 标了 `TICKET_131` 的测试当前都以预期失败通过，没有被跳过的，也没有 XPASS；不标记的守门测试当前通过
- [ ] 每个预期失败的测试，失败原因都是功能还没做（`NotImplementedError: ticket 131`，或正向断言不成立），不是夹具、事件文件、替身出错。最终回复给每组贴一个用 `--runxfail` 跑出的真实失败原因
- [ ] 临时写一个参考实现（不提交，结束后 `git status src/` 只剩 `src/data_agent/im/` 的空壳），证明去掉标记后各组都能通过；每组至少一个反向检查会失败，例如：
  - 名单外的用户在私聊里也调用了 `send()`；
  - 群里没 @ 也回应；
  - 重启后映射丢了，同一线程开了新对话；
  - 按钮上写了选项全文；
  - 节流失效，每个事件都编辑；
  - 拆分后有一条超过上限；
  - 失败的 `display_chart` 也算进 N；
  - token 的值进了日志；
  - 文字 `stop` 被当成普通消息发给 `send()`；
  - `start_run` 把整个环境原样传下去，或者把 `PATH` 也去掉了；
  - `DATA_AGENT_API_KEY` 被当作 Slack 的 token 变量接受
- [ ] `git diff main --stat` 只有 Authority 列的文件；`pyproject.toml`、`uv.lock`、`src/data_agent/web/` 没动，`git diff main -- src/data_agent/cli.py` 只有 `--im` 的一个 `add_argument`
- [ ] 夹具和测试里没有密钥、地址、主机名：有 `tests/fixtures/im/` 时，`uv run python -m evals scrub tests/fixtures/im` 输出 0 处改动；pre-commit 的密钥与公网 IP 扫描通过
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 的输出。
- `uv run pytest tests/acceptance/test_im_*.py -v` 的输出：每个测试的名字，XFAIL 还是 PASSED。
- 每组一个 `--runxfail` 的失败原因。
- 参考实现和反向检查的结果。
- 脱敏命令的输出（有夹具目录时）。
- 契约 1–9 的清单（名字、签名、字段，第 9 项每一条定成了什么），供人审。

本工单没有真实运行：spec "验收"`:116-124` 的每平台真跑在 131–133 做。

## Stop conditions

- spec 的某条行为在"假平台 + 对话替身 + 临时 runs 目录"这个接缝上测不了，或者要改 Authority 以外的文件才能测。
- 断言需要一个 spec 没定、也不在契约里的决定。契约的名字、签名、字段和契约第 9 项列的行为细节由本工单定、人审，不算。
- spec 自相矛盾，或者 spec 和 Context 列的代码现状对不上。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2 (Codex 1 finding fixed; eng:code-review judgement calls fixed or answered above)
- Model spend: $0
- Outcome: accepted

## Comments

- 2026-11-24（工单 agent 最终回复，主会话照抄）：

**Scope.** `git diff main...HEAD --stat` shows 17 files, 2194 insertions:
- `src/data_agent/im/` (7 stub files);
- `src/data_agent/cli.py`, where the only change is the `--im` `add_argument` (`type=Path`, help says TOML);
- `tests/acceptance/im_support.py` and 8 `test_im_*.py` files.

`pyproject.toml`, `uv.lock` and `src/data_agent/web/` are untouched. No platform library is imported. There is no `tests/fixtures/im/` directory: the configs are written into `tmp_path`, so the scrub command does not apply. Pre-commit, including the secret scan, passed on all 4 commits.

**`make check`** on `05a212e`: ruff "All checks passed!"; basedpyright "0 errors, 0 warnings, 0 notes"; pytest "2851 passed, 2 skipped, 79 deselected, 59 xfailed in 772.42s"; web "Test Files 30 passed (30), Tests 442 passed (442)"; exit=0.

**`pytest tests/acceptance/test_im_*.py -v`**: "1 passed, 59 xfailed", 0 XPASS, 0 skipped. The one PASSED test is the guard `test_data_agent_web_without_im_starts_no_adapter`. Every other test, across all 8 files, is XFAIL.

**`--runxfail` failure reasons per group:**

| Group | Tests | Reason |
|---|---|---|
| sessions | 5 | `NotImplementedError: ticket 131` |
| triggers | 6 | `NotImplementedError: ticket 131` |
| clarification | 5 | `NotImplementedError: ticket 131` |
| progress | 5 | `NotImplementedError: ticket 131` |
| steer/cancel | 9 | `NotImplementedError: ticket 131` |
| format | 9 | `NotImplementedError: ticket 131` |
| config | 11 | `NotImplementedError: ticket 131` |
| config | 1 | `assert 0 == 1` (`service.start` was never called) |
| config | 1 | `AssertionError: the run inherits the server's whole environment` (`Popen` got no `env=`) |
| errors | 7 | `NotImplementedError: ticket 131` |

None of the failures is a fixture or stand-in error.

**Reference implementation and reverse checks.** The reference implementation is not committed; it lives in git-ignored `runs/im130/ref/` and is swapped in by `runs/im130/swap.sh`. `git status --short src/` is clean afterwards. With it in place, `--runxfail` gives "60 passed". Each injected bug makes these tests fail:

| Injected bug | Tests that fail |
|---|---|
| outsider's message is sent | both outsider trigger tests |
| group message without @ gets an answer | `only_a_mention…`, `group_without_threads…` |
| thread map lost on restart | `the_thread_map_survives_a_restart…` |
| full option text on the buttons | `numbered_options_with_number_buttons` |
| no throttle | `edits_wait_out_the_platforms_interval` |
| a split part over the limit | `paragraph_over_the_limit…` |
| failed `display_chart` counted in N | `charts_are_counted…`, `answer_with_charts_says_how_many` |
| table not cut to fit one message | `table_too_long_for_one_message…` |
| token written to the log | `no_token_value_reaches…` |
| text `stop` sent as a message | the 3 `word_stop` cases, `stop_with_no_turn_running` |
| whole environment passed to the run | `a_run_starts_without_the_im_tokens` |
| `PATH` dropped from the run's environment | `a_run_starts_without_the_im_tokens` |
| any `DATA_AGENT_` prefix accepted (incl. `DATA_AGENT_API_KEY`) | `model-key-as-token`, `other-platforms-prefix`, the no-token test |
| one generic failure reply, no run id | all errors tests except "stopped and crashed read differently" |

**Reviews (2 fix rounds):**
- **`da-review main` (Codex).** One finding, fixed: spec `:49` also cuts a table that doesn't fit in one message. `tables_to_code` gained a `limit`, and a `format_answer` test was added.
- **`eng:code-review main`.** No hard violations and no blocking spec gaps. Fixed:
  - the support-module docstring now says that only the `expect_*` helpers assert;
  - one comprehension became `next()`;
  - a test was renamed from "too wide" to "too long";
  - the `start` stand-in is bound to its real signature;
  - the status after a clarification turn and after Stop is now tested;
  - the "nothing running" reply is checked to land in its thread.
- **Fixed after a last self-review:**
  - the retry after a refused send used the same text as the refused message, so that assertion was always true;
  - the steer test no longer forbids acknowledging a steer (the spec says nothing about it);
  - `service.py` now says the `data-agent[im]` libraries are checked inside `start`, so ticket 131 cannot break the two `--im` CLI tests.
- **Not fixed, with reasons:**
  - the repeated clock-advance-and-poll lines: a shared helper is left out to keep each test explicit;
  - `Incoming` and `Press` repeat the same fields, and `platform` is a plain string: that is how the human-reviewed contract shapes them;
  - `typing` has no test: the spec lists it and the "Testing Decisions" section doesn't test it;
  - `FakePlatform.press` always presses as a group message: no test needs a press in a direct chat;
  - `"0.25" in text` fixes the cost to at least two decimals: that is data, not wording.

**Contract 1–9, for human review:**

1. **Platform interface** (`im/platform.py`).
   - `Incoming(platform, chat_id, thread_id: str | None, user_id: str, direct: bool, mentioned: bool, text, new_conversation=False)`. The private-or-group field is `direct: bool`.
   - Buttons and messages: `Button(label, value ≤64 B ASCII)`, `Outgoing(text, buttons)`, and `Press(platform, chat_id, thread_id, user_id, direct, message_id, value)` for a button press.
   - The platform declares `Capabilities(edit, buttons, typing)` and `Limits(max_chars, edit_interval)`.
   - `Platform` protocol: `name`, `capabilities`, `limits`, and the async methods `start(on_message, on_press)`, `stop`, `send(chat_id, thread_id, Outgoing) -> id`, `edit(chat_id, message_id, Outgoing)` and `typing`.
   - The platform implementation, not the adapter, does these: resolves `thread_id` (Slack: the message `ts`; Discord: the id of the thread it makes; Telegram: None), takes the mention out of the text, maps `/new` to `new_conversation`, converts Markdown, and gives user ids as strings.
2. **Common entry point** (`im/adapter.py`).
   - `Adapter(platform, conversations: ConversationsLike, runs: RunsDir, allowed_users, clock=time.monotonic)`, async, with `start()`, `on_message()`, `on_press()`, `poll()` (one pass) and `run()` (the loop, `POLL_SECONDS = 0.5`).
   - The adapter never sleeps; handlers finish their platform calls before returning; conversation calls go to a worker thread.
3. **Thread map.** The key is (platform, chat_id, thread_id). It is stored in `<runs-dir>/im/threads.json` (`IM_DIR`, `THREADS_FILE`), replaced whole on each change and read back at start.
4. **Config** (`im/config.py`).
   - `load_config(path, environ) -> dict[str, PlatformConfig]` raises `ImConfigError(ValueError)` naming the platform.
   - `PlatformConfig(platform, allowed_users: frozenset[str], token_envs, tokens)`, where `tokens` is hidden from `repr`. `TOKEN_KEYS` and `PREFIXES` give `DATA_AGENT_<PLATFORM>_`.
5. **Format** (`im/format.py`). `split_message(text, limit)`, `tables_to_code(text, max_rows=TABLE_ROWS=20, limit=None)`, `chart_count(events)` and `format_answer(text, events, limit)`.
6. **Progress** (`im/progress.py`). `status_text(events, ended=None)`. Duration runs from the first event's `ts` to `result.ended_ts`, or `result.ts` without it.
7. **Start entry.** `data_agent.im.service.start(app, configs)` is called by `serve_web` after `create_app` and looked up at call time. The extra is checked inside `start`.
8. **Run environment.** `config.run_env(environ, configs)` is given to `Popen` as `env=` and drops only the token variables the file names.
9. **Behaviour details fixed here:**
   - **Busy, or any other refusal:** a reply with `Refused.detail`. A conversation already created stays mapped and is reused by the next message in the thread.
   - **End states:**
     - success: "done" with duration and cost, Stop button removed;
     - after a clarification: waiting for the answer, which reads differently from an answered turn, and nothing more is sent;
     - failed `result`: a reply naming the cause, with the run id;
     - no `result`: "stopped" for `cancelled`, "failed" for `failure`, with the run id;
     - after Stop: the status changes and the button is removed.
   - **Language and step labels:** English, with steps grouped like the web UI's `toolKind`, plus verifying, thinking and writing the answer.
   - **`stop`:** `text.strip().casefold() == "stop"` after the mention is removed. "stop the query" is an ordinary message. With nothing running, the bot replies that nothing is running.
   - **Allowed list:** applies to messages, steers, `stop`, `/new` and button presses. An outsider gets one reply with their user id in a direct chat and nothing at all in a group.
   - **Config edges:** an unknown platform table and a file with no table are both errors. `--im` without `--data-sources` is accepted, and each turn then gets the 400 refusal as a reply.
   - **Splitting:**
     - between paragraphs;
     - a paragraph over the limit is split by lines, then by characters;
     - a code block that fits in one message is never split, and a longer one is re-fenced in each part;
     - a table keeps 20 rows, or fewer to fit one message, and the total is given.
   - **N (number of charts):** counted like the web UI's `answerCharts`.
   - **Decided beyond the item-9 list (please confirm):**
     - a group without threads (Telegram) needs the @ every time;
     - a platform without edits gets no status message, only the answer;
     - a steer sends no new status message, and whether the bot acknowledges a steer is left open;
     - `/new` with text starts the next conversation with that text;
     - unknown keys in a table are refused;
     - tokens of platforms the file doesn't name stay in the run's environment;
     - `Adapter.run` and `POLL_SECONDS` are fixed;
     - whether the last status edit waits out the throttle interval is left open.
   - **Unverified (`未确认`):** the API-error `result` uses `subtype: "success", is_error: true`. No repo fixture has a real `api_error` result to confirm it; the adapter decides by `terminal_reason`, so the test does not depend on it.

No real run was made: per the ticket, the per-platform real runs belong to 131–133.

- 2026-11-24（主会话）合并：人审通过（契约照工单 Comments 原样），PR #132 squash 合入 main（`7d1b4e6`）。CI 两次都过；Codex GitHub app："Didn't find any major issues"。
