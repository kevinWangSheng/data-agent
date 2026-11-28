# 151 — 云端部署：验收测试（启动检查、网页 bearer 令牌、联网与锁定、`/healthz`、git 来源）

Status: in-review
Blocked by: product-config 143
Spec: ../spec.md（Testing Decisions"验收测试接缝"）；决定：`.scratch/product-config/decisions.md` Q7、Q8；`docs/adr/0012-settings-ui-and-platform-identity.md`；`.scratch/product-config/decisions.md`"建 settings 工单时主会话定的细节"第 1 条
Claimed by: claude (opus-5-5) 2026-11-28
Branch: worktree-151-cloud-acceptance-tests

## Goal

spec"验收测试接缝"的四组行为都有验收测试，写在约定的接缝上；`decisions.md` Q8 改过的条目照 Q8 写。

还没实现的行为标预期失败（pytest `xfail(strict=True)`），按转绿的工单分标记：
- `[server].mode`、bearer 令牌、`/healthz`、联网与锁定：`TICKET_153`；
- git 来源：`TICKET_154`。

原来标 `TICKET_156` 的 Cookie 换取与 Cookie 鉴权、`/api/options` 的联网默认值，按 ADR-0012 移到 settings 157（标 `TICKET_159`、`TICKET_160`），不在本工单。

143 合入后就成立的行为写成守门测试，不标记。本工单不改 `src/`；由人审过才合并，之后的工单不能改这些测试。

测试从配置文件读 `[server].mode`，配置文件由 product-config 143 的加载器读，所以本工单等 143 合入。

## Authority

- May edit（都是新建）：
  - `tests/acceptance/test_cloud_startup.py`：启动检查。
  - `tests/acceptance/test_cloud_web_token.py`：bearer、`/healthz`。
  - `tests/acceptance/test_cloud_locks.py`：联网、`dbt_target`、运行子进程的环境。
  - `tests/acceptance/test_cloud_git_sources.py`：git 来源。
  - `tests/acceptance/cloud_support.py`：几个文件共用的辅助。
- Must not edit：
  - `src/`：不加空壳，见 Context"接缝"和 Stop conditions。
  - `evals/`、`skills/`、`web/`（前端测试归 settings 160）。
  - 已有的 `tests/acceptance/` 文件（`conftest.py`、`seam_support.py`、`test_mcp_*.py`、`test_im_*.py` 等）。要用的辅助照抄进 `cloud_support.py`。
  - `tests/fixtures/` 里已有的文件。
  - `Makefile`、`pyproject.toml`、`.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0。不调模型、不起真容器、不联网。

## Context

行号取自 main `e494e49`。140（进行中）、141、143、144 会移动 `cli.py`、`web/app.py`、`web/launcher.py`，并去掉 `--data-sources`、`--mcp-servers`、`--im`（Q8 人定 1）。认领时按符号名，在 143 合入后的 main 上重新核对。

- **决定**：
  - spec `.scratch/cloud-deploy/spec.md`"Implementation Decisions（Q7）"和"验收测试接缝"。
  - `.scratch/product-config/decisions.md` Q7，以及 **Q8**（"人定的 5 个"第 4、5 条、"主会话定的实现细节"的云端与 git 两条）。两者不一致时以 Q8 为准。
  - ADR-0012 取代 Q7"网页：不登录"和 Q8"人定的 5 个"第 3 条；令牌只有一个 `DATA_AGENT_ADMIN_TOKEN`（`.scratch/product-config/decisions.md`"建 settings 工单时主会话定的细节"第 1 条），Q7、Q8 里的 `DATA_AGENT_ADMIN_TOKEN` 都读作它。
  - ADR-0011 第 5 条；ADR-0010 Update 2026-11-27。
- **配置层**：
  - 加载器的名字、`EffectiveConfig` 的形状、`--config` 接在哪些子命令上，以 141/143 合入的为准。`--config` 可写多次，后面的覆盖前面的（Q8）。
  - `[policy]` 和命令行参数冲突时报错拒绝，不悄悄改成锁定值（Q8）。
  - 部署模式也可以由环境变量 `DATA_AGENT_MODE` 给（Q8）。测试主要写配置文件里的 `[server].mode`，另用一条测试覆盖 `DATA_AGENT_MODE=cloud`。
  - `[server].host`、`port`、`runs_dir` 由 144 接到 `data-agent web`（Q8）。本工单的测试依赖 144 合入后的行为，但 144 不在 Blocked by 里：认领时 144 没合入，host 相关的测试照写、标 `TICKET_153`（153 Blocked by 144）。
- **现状**：
  - 网页只监听 `WEB_HOST = "127.0.0.1"`（`src/data_agent/cli.py:37-38`），在 `uvicorn.run(app, host=WEB_HOST, ...)` 处使用（`cli.py:608`）。没有任何访问控制。
  - `create_app` 的签名已经被之前的验收测试钉住（`src/data_agent/web/app.py:73-79`）。
  - `/healthz` 现在不存在：它落进 UI 的兜底路由（`app.py:210-224`），UI 构建过时返回 200 的 `index.html`，没构建时返回 503。
  - IM 允许名单为空在任何模式下都已被拒绝（`src/data_agent/im/config.py:115-117`）。
  - 网页表单和对话的请求体，`network` 默认 `True`（`app.py:47-52`、`:59-61`）。IM 的回合不传它（`im/adapter.py:302`），落到 `web/conversations.py:97` 的默认 `True`。140 加 `data-agent web --no-network`：关了联网时，要求联网的请求拒绝 409。
  - `/api/options`（`app.py:107-114`）现在只给数据源、任务类型、配置名。
  - 网页的 dbt 运行总带 `--dbt-target scratch`（`web/launcher.py:178`），总用 docker 沙箱（`launcher.py:50`）。`data-agent run` 的 `--sandbox` 默认 docker（`cli.py:343`）。
  - 运行子进程由 `serve_web` 的 `start_run` 起（`cli.py:573-589`）。带 `--im` 时，它用 `im_config.run_env` 去掉 IM 令牌的变量（`im/config.py:127-132`）。
  - `runs_dir` 里隐藏名字的目录不算运行（`web/runs.py:44-51`）。git 克隆放 `<runs_dir>/.cache/git`（Q8），不会出现在运行列表里。
- **接缝**（不加 `src/` 空壳）：
  - **启动检查**：整条 `data-agent web --config F`。照 `tests/acceptance/test_mcp_web.py:170-188`、`test_im_config.py:146-180` 的做法，monkeypatch `uvicorn.run`，记下它收到的 `app` 和 `host`；`create_app` 不替换。
    - 拒绝启动时同时断言三件事：退出码 2、stderr 给出原因（点名变量或键）、`uvicorn.run` 没被调用。
    - `run` 一侧（沙箱后端）用整条 `data-agent run --config F`，在 SDK transport 处截停，照 `tests/acceptance/test_chat_eval_unchanged.py:80-99` 的 `captured`。
  - **令牌、`/healthz`**：用上面截下的 `app` 建 `TestClient`（照 `tests/acceptance/test_web_api.py:117`）。这样令牌怎么进 app 由 153 定，测试不碰 `create_app` 的签名。`start_run` 用记参数的替身，假进程可只读复用 `tests/fixtures/chat/fake_chat_run.py`。
  - **联网与锁定**：
    - 整条 `data-agent run --config F`，在 transport 处截停，从截下的选项和事件文件读结果。
    - 网页一侧走 `POST /api/runs`、`POST /api/conversations/{id}/messages`，看替身 `start_run` 收到的参数和环境。
  - **git 来源**：没有现成的函数可调。由本工单定一个模块和函数名（契约，人审），测试经 `cloud_support.py` 的辅助去取：先 `assert` 模块和属性存在，再调用。这样今天的失败是 `AssertionError`，不是 `ImportError`（照 115 的做法）。
- **预期失败的标记**：照 `.scratch/mcp/issues/115-mcp-acceptance-tests.md` Context"预期失败的标记"：
  ```python
  TICKET_153 = pytest.mark.xfail(
      strict=True, raises=AssertionError, reason="ticket 153: [server].mode is not wired yet"
  )
  ```
  - `TICKET_154` 照写。
  - 每个带标记的测试至少有一条今天会失败的正向断言。不能只断言"退出码 2"：143 合入后，配置里写了还不认识的键，也会以 2 退出。
  - `/healthz` 不能只断言状态码：要断言响应是 JSON，并且等于 `{"ready": true}`。
  - 起子进程或 git 仓库的夹具先确认自己成功了，失败就抛 `RuntimeError`，不让它藏成预期失败。
  - 转绿时 153、154 只删自己的标记，以及因此没用了的 import 或定义（AGENTS.md 硬规矩的唯一例外）。
- **各组要断言的行为**：
  - **启动检查**（`test_cloud_startup.py`）：
    - `TICKET_153`：
      - 任何模式（local、cloud 各一遍）下，`[server].host` 是非本机地址（如 `0.0.0.0`），又没设 `DATA_AGENT_ADMIN_TOKEN`：拒绝启动，stderr 点名 `DATA_AGENT_ADMIN_TOKEN`（Q8）。
      - 同样的配置、设了令牌：`uvicorn.run` 收到的 `host` 是配置里的值。这是正向断言。
      - cloud 下 `[sandbox].backend = "local"`：拒绝启动，stderr 点名 `backend` 或 `local`。网页和 `run` 各一条。
      - `DATA_AGENT_MODE=cloud`，配置文件不写 `mode`：表现同 cloud（用 `[sandbox].backend = "local"` 被拒这条来证明）。
    - 守门，不标记，在所有模式下（Q8）：
      - 缺引用的 `${VAR}` 拒绝启动，stderr 点名变量；
      - IM 名单为空拒绝启动，stderr 点名平台。
    - 守门，local：不写 `[server]` 时 `uvicorn.run` 收到 `127.0.0.1`，不要令牌。
  - **令牌、`/healthz`**（`test_cloud_web_token.py`）：
    - `TICKET_153`，设了令牌的服务器（cloud；local 监听非本机地址时同样适用，测 cloud 一种即可）：
      - 不带凭据、带错的 bearer（含前缀写错、空令牌），以下各得到 401：`GET /api/runs`、`GET /api/options`、`POST /api/runs`、`GET /api/runs/{id}/events`（JSON 和 `Accept: text/event-stream` 两种）、`GET /api/conversations`、`POST /api/conversations/{id}/messages`。
      - 带对的 `Authorization: Bearer <令牌>`：同样的请求不是 401。
      - 401 的响应体和日志（`caplog`）里没有令牌的值。
    - `TICKET_153`：`GET /healthz` 不带凭据得到 200，且 `== {"ready": true}`。local、cloud 两种模式都测（Q8）。
    - 守门，local、监听本机地址、不设令牌：不带凭据照旧 200。
  - **联网与锁定**（`test_cloud_locks.py`）：
    - `TICKET_153`，cloud，配置文件没打开联网（Q8 人定 4）：
      - `run --network`：拒绝。退出码 2，stderr 点名 `network`，没有会话启动。
      - 网页表单带 `network: true`、对话的一轮带 `network: true`：409，替身 `start_run` 没被调用。
      - 网页表单带 `network: false`、IM 的一轮（经 `conversations.send` 不传 `network`）：照常起运行，参数里没有 `--network`。
      - `run --type dbt --dbt-target task`：拒绝，退出码 2，stderr 点名 `dbt_target`（Q8：和 `[policy]` 冲突报错拒绝）。
      - 运行子进程的环境里没有 `DATA_AGENT_ADMIN_TOKEN`（Q8）。看替身 `start_run` 收到的环境。若 `start_run` 的签名没有环境参数，就看 `subprocess.Popen` 的 `env=`，照 `test_im_config.py:209` monkeypatch `cli.subprocess.Popen`。
    - `TICKET_153`，cloud 且配置文件打开联网：`network: true` 的请求照常起运行，参数里有 `--network`。
    - 守门，local（不写 `[server]`）：`run --network` 照旧联网。网页的 `network: true` 照旧带 `--network`。
    - 端点：不写测试（Q8 人定 5：cloud 认环境变量 `DATA_AGENT_BASE_URL`；单次运行现在本来就改不了端点）。
    - 记录：`config` 事件（product-config 146）记的是生效配置。本组不另断言它，只断言拒绝行为。
  - **`/api/options` 的联网默认值**：按 ADR-0012 移到 settings 157（`TICKET_160`），本工单不写。
  - **git 来源**（`test_cloud_git_sources.py`，`TICKET_154`）：
    - 测试在 `tmp_path` 里 `git init` 一个本地仓库当替身，提交一个 Skill 目录或说明文件，用 `git+file://…@<分支>#<子目录>` 引用。
    - 启动拉取：解析出的本地路径在 `<runs_dir>/.cache/git` 下（Q8），里面有提交的文件，克隆是浅的（`git rev-list --count HEAD` 为 1）。
    - 刷新：替身仓库再提交一次，调一次刷新，新解析出的路径下看到新内容。刷新前拿到的旧路径，在刷新后仍是旧内容（Q8：先克隆到新目录再原子切换，正在跑的运行用旧目录）。
    - 拉失败：把替身仓库移走或改坏，刷新时解析出的路径仍是上一次的内容，异常不抛到调用方，并有一条点名该来源的错误日志（`caplog`）。
    - `@<提交>`：钉一个提交时拿到的是那个提交的内容。
    - 令牌：配置里写 `${VAR}` 引用，设一个假令牌值。断言它不在克隆目录的 `.git/config`、克隆用的命令参数、日志里（Q8：令牌经 `GIT_ASKPASS`）。`file://` 仓库不用令牌，这条只断言值不落盘、不进日志。
    - 守门：普通本地路径的值原样不变，不去 clone。
- **本工单定下、最终回复里逐项列出供人审的契约**：
  1. 启动拒绝：退出码 2、stderr 点名、`uvicorn.run` 没被调用。
  2. bearer：`Authorization: Bearer <DATA_AGENT_ADMIN_TOKEN>`；所有 `/api/*` 都要；`/healthz` 不要。UI 页面和 `/assets/*` 不要凭据，这样浏览器才能打开登录页（settings 159、160）。
  3. （已移走）Cookie：按 ADR-0012 改由 settings 157 契约 5 定（签名会话，Sign in with Slack 或管理令牌换取）。
  4. （已移走）`/api/options` 联网默认值的字段名：由 settings 157 契约 6 定。
  5. git 来源的模块和函数（名字、参数：git 值、`runs_dir` 或克隆根目录、环境变量；刷新函数）。
  6. 测试用 `git+file://` 当 `git+https://` 的替身，因此实现要接受 `git+file://`。
- 只用 uv。

## Acceptance

- [ ] spec"验收测试接缝"四组和本票 Context 列的每一条都有断言，落在 Authority 列的测试文件里；契约 1、2、5、6 写在测试文件的 docstring 里（3、4 已移到 settings 157）
- [ ] `TICKET_153`、`TICKET_154` 标记的测试当前都以预期失败通过检查，没有被跳过的；守门测试当前都通过，且不带标记
- [ ] 每个预期失败的测试，失败原因是功能没做（如 `host` 仍是 `127.0.0.1`、没凭据也是 200、git 模块不存在），不是夹具起不来。最终回复给每组贴一个 `--runxfail` 跑出的真实失败原因
- [ ] 临时写一个参考实现（不提交，结束后 `git status src/` 干净），证明去掉标记后各组都能通过。每组至少一个反向检查会失败，例如：
  - 令牌比较对空令牌放行；
  - `/healthz` 要令牌；
  - SSE 流不认 bearer；
  - cloud 下 `run --network` 照旧联网；
  - 运行子进程的环境带着 `DATA_AGENT_ADMIN_TOKEN`；
  - 刷新时原地改写旧目录；
  - 拉失败时清空了旧内容。
- [ ] `git diff main -- src/` 为空
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 的输出。
- `uv run pytest tests/acceptance/test_cloud_*.py -v`：每个测试名，XFAIL 还是 PASSED。
- 每组的 `--runxfail` 失败原因。
- 参考实现和反向检查的结果。
- 契约 1–6。

## Stop conditions

- 某条行为在约定接缝上测不了，或要加 `src/` 空壳才能测（例如 143 合入后 `data-agent web` 不收 `--config`）。
- 断言需要人还没定的选择。字段名、接口路径这类细节不算，由本工单定、人审。
- spec、Q8 和 143 合入的配置层互相矛盾。
- 第二轮审查修复后仍不过。
- 不停止、不重启本机的 Docker/colima 守护进程。本工单不需要 Docker。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3（前两次因本机内存不足中断）
- Review rounds: 2
- Model spend: $0
- Outcome: —

## Comments

- 2026-11-27（主会话建票）：cloud-deploy 的验收测试工单，人审后合入。等 product-config 143（配置加载器）合入后才能领。建票时发现的问题已由人和主会话在 `decisions.md` Q8 定下，本票照 Q8 写。
- 2026-11-27（主会话）：按 ADR-0012 调整：原 `TICKET_156` 的测试（用令牌换 Cookie、只带 Cookie 的 API 与 SSE、Cookie 值不是令牌原文、伪造或过期的 Cookie、`/api/options` 联网默认值）移到 settings 157，分别标 `TICKET_159`、`TICKET_160`；契约 3、4 移到 settings 157 的契约 5、6，编号保留不重排（154 引用契约 5、6）；标题、Goal、Authority、Acceptance 去掉 156。bearer、启动检查、联网与锁定、git 来源不变。
- 2026-11-27（主会话）：按 ADR-0012 调整：按"建 settings 工单时主会话定的细节"第 1 条，测试里的令牌变量从 `DATA_AGENT_WEB_TOKEN` 改为 `DATA_AGENT_ADMIN_TOKEN`（bearer，权限 = 组织管理员）。
- 2026-11-28（工单 agent，第二次中断后接续；分支 `worktree-151-cloud-acceptance-tests`，`6a4f60e`…`55ddb85`）：
  **`make check`（在 `55ddb85` 上）：** 通过。
  - ruff：`All checks passed!`；格式：`547 files already formatted`；basedpyright：`0 errors, 0 warnings, 0 notes`。
  - pytest：`3589 passed, 3 skipped, 81 deselected, 92 xfailed in 988.85s`。
  - web：`Test Files 30 passed`、`Tests 448 passed`。

  **`uv run pytest tests/acceptance/test_cloud_*.py -v`：** `9 passed, 30 xfailed`，没有被跳过的。
  - **PASSED（9 个守门测试）：**
    - 启动：不写 `[server]` 时监听 `127.0.0.1` 且不要令牌；缺 `${VAR}` 拒绝启动（local、cloud 各一）；IM 名单为空拒绝启动（local、cloud 各一）。
    - 令牌：local、本机地址、不设令牌，不带凭据照旧 200。
    - 联网：local 的 `run --network` 有 web 工具；local 网页的一轮带 `network: true` 照旧带 `--network`。
    - git：普通本地路径原样不变，也不去 clone。
  - **XFAIL（30 个）：** 其余全部。
    - `TICKET_153`：启动 5 个、令牌 10 个、联网 9 个。
    - `TICKET_154`：git 6 个，其中 `test_resolve_gives_a_plain_value_back_and_clones_nothing` 是本轮新加的。

  **`--runxfail` 下的真实失败原因（30 个都是 AssertionError，都是"功能没做"）：**
  - **启动：**
    - `the server started (host … {'host': '127.0.0.1', …}) despite the problem`
    - `assert '127.0.0.1' == '0.0.0.0'`
    - cloud 下 `run` 用 local 后端：`a session started despite the refusal`
  - **令牌：**
    - `assert {'none': 200, …} == {'none': 401, …}`
    - `/healthz`：`503: 'The web UI is not built …'`（它落进了 UI 的兜底路由）
  - **联网与锁定：**
    - `run --network`、`--dbt-target task`：`a session started despite the refusal`
    - 对话的一轮：`201: {"kind":"turn",…}`（应得 409）
    - IM 的一轮：`'--network' not in [...]`
    - 运行环境：`the run's environment has DATA_AGENT_ADMIN_TOKEN`
  - **git：**
    - `no module data_agent.git_sources`
    - 令牌那条：`the config is refused: … server.git_token: unknown key`
  - **已知的依赖：** 走表单的 153 测试今天得到 `422 … "config" Field required`。表单按 144 的契约不再带 `config` 字段，而 153 本来就 Blocked by 144。

  **参考实现（临时写，没提交，已删）：**
  - 范围：`cli.py`、`web/app.py`、`config.py`，加一个新的 `git_sources.py`。
  - 去掉标记后 39 个全过。
  - 下面每个反向检查都让目标测试失败：

  | 改坏的地方 | 目标测试的失败 |
  |---|---|
  | 令牌比较对空令牌放行 | `[200, 401, …] == [401, …]` |
  | `/healthz` 要令牌 | `401` |
  | SSE 流不认 bearer | `{'none': 200, …}` |
  | cloud 下 `run --network` 照旧联网 | `a session started` |
  | 网页联网不锁 | 拿到 201，没有 409 |
  | 运行环境带着管理令牌 | `has DATA_AGENT_ADMIN_TOKEN` |
  | 监听地址留在本机 | `'127.0.0.1' == '0.0.0.0'` |
  | 忽略 `DATA_AGENT_MODE` | `server started` |
  | 放行 `dbt_target task` | `a session started` |
  | 刷新时原地改写旧目录 | 新旧路径相同 |
  | 拉失败时清空旧内容 | `no demo/SKILL.md` |
  | 令牌拼进 URL | 测试失败 |
  | 普通路径也去 clone | `a value was cloned` |

  联网的两项是在联网键改成 `[sandbox].network` 之后重跑的结果。

  **契约（供人审）：**
  1. **启动拒绝：** 退出码 2；stderr 点名变量或键；`uvicorn.run` 没被调用。`run` 一侧同样退出码 2、stderr 点名、不起会话。docstring 在 `test_cloud_startup.py`。
  2. **bearer：** cloud 模式，或监听非本机地址时：
     - 所有 `/api/*` 都只认 `Authorization: Bearer <DATA_AGENT_ADMIN_TOKEN>`，整串比较，先于其他检查返回 401。
     - 401 的响应体和日志里都没有令牌的值。
     - `/healthz` 不要凭据，返回 200 且等于 `{"ready": true}`。
     - UI 页面和 `/assets/*` 不要凭据。
     - docstring 在 `test_cloud_web_token.py`。
  3. 契约 3、4 已移到 settings 157。
  4. （同上）
  5. **git 模块：** `data_agent.git_sources.GitSources(root, environ, token=None)`，带 `resolve(value) -> str` 和 `refresh() -> None`。
     - 令牌来自 `[server].git_token = "${VAR}"`，只经 `GIT_ASKPASS` 交给 git。
     - 每次刷新克隆到新目录；拉失败时保留上一次的内容，记一条 ERROR 日志，不抛异常。
     - 本文件不测两件事：服务器是否真把 `<runs_dir>/.cache/git` 交给 `GitSources`，以及首次拉取失败时怎么办。
     - docstring 在 `test_cloud_git_sources.py`。
  6. 实现要接受 `git+file://`，当作 `git+https://` 的替身。

  另有两项本工单自定的选择，请人一并审：
  - **"配置文件打开联网"写作 `[sandbox].network = true`**。依据是 settings 160 第 46 行把 `[sandbox].network` 定为联网的设置键；而 143 的加载器里，`policy.network = true` 只表示"不锁"，不表示"打开"。第一轮里原来写的是 `[policy].network = true`。
  - **git 令牌只有一个全局的 `[server].git_token`**，不按来源分别配。

  **审查：**
  - **`da-review main`：** 两轮都是 "No actionable defects"。
  - **`eng:code-review main` 第一轮，已修（`a4cfe18`、`3b87da5`）：**
    - 删掉没人用的 `key_of`（唯一的硬违规）。
    - 新加 `resolve(普通值)` 原样返回的测试。
    - cloud 测试都设上会话密钥，这样拒绝原因只会是被测的那一项。
    - 联网键改为 `[sandbox].network`。
    - `Served` 里的重复代码合并；去掉没用的 `name` 参数；改正一条误导的注释。
    - 克隆根目录允许存在但为空，不过度约束 154。
  - **`eng:code-review main` 第二轮，已修（`55ddb85`）：**
    - 普通路径的守门测试同样放宽到"`.cache/git` 不存在或为空"。
    - git 契约里写明本文件不断言什么，并说明钉提交要按提交 id 拉取。
  - **不修的理由：**
    - **local 表单联网的守门测试：** 今天表单请求得到 422；标成 xfail 的话，144 合入后它会意外通过、让 144 的 CI 变红。这条留给 144/153。
    - **运行环境里的 `DATA_AGENT_SESSION_KEY`、OIDC 密钥：** spec.md:66 要求去掉它们，本工单只要求管理令牌。这一项属于 settings。
    - **git 守门测试钉住了加载器把相对路径解析成绝对路径：** 这是 143 已合入的行为，钉住它是守门测试的本意。
    - **小问题，属于判断题：** `ask_turn` 与 `message` 相近；名字 `Git`、`pulled`、`run_`；`served` 正常返回时退出码是 `None`（`run_cli` 是 `0`）；`write_run` 的返回值没人用；两处 `config()` 重复；连续三元表达式；`server.created.state.conversations` 链式访问；`KeyError` 和 `AttributeError` 可能出现的两处。最后这两处出事时，在 `raises=AssertionError` 下会直接报 FAILED，不会藏成 XFAIL。第二轮 Standards 审查没有硬违规。

  **给主会话：** 自 merge-base `1535551` 以来，main 上碰 `src/` 或 `tests/acceptance/` 的提交只有 `8449231`，只改了 `test_seam_a_run_cli.py`，不影响这些 strict xfail。
