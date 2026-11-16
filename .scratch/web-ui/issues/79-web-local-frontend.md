# 79 — 本地版前端：提交、实时过程、取消、历史列表

Status: ready-for-agent
Blocked by: 76（done）, 78（done）
Spec: ../spec.md
Claimed by: Claude Code (Opus 5.5), 2026-11-16
Branch: worktree-79-web-local-frontend

## Goal

`data-agent web` 启动后，在页面上填任务、选类型、数据源和配置后提交，过程实时出现，可以取消，历史列表能翻；构建好的前端随 Python 包发布，使用的人不需要 Node。

## Authority

- May edit: `web/`（除验收测试外）；`evals/harbor_agent.py`（退回第 1 轮加，只动 `build_artifacts` 里构建 wheel 那次调用的环境）；`src/data_agent/` 的 web 服务模块（提供前端文件）；`pyproject.toml`、打包相关配置；`tests/test_packaging.py`；README 的本地启动一节
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、agent 的运行逻辑、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- spec "运行页"（本地版多出的部分）、"历史列表"、"总体"里"一套前端，两个数据来源"。
- 数据来源接口加后端的实现：列出运行、读事件加 SSE 续读（断线后按序号接着读，不丢不重）、提交、取消、数据源和配置列表。
- 运行中的页面用和回放同一个渲染路径：事件追加后重算视图模型。
- 提交失败（数据源不存在、已有运行在跑）要显示后端给的原因。
- 已取消的状态（2026-11-15，工单 78）：读事件的接口只返回事件，不带状态；运行的 `status`（`running`/`success`/`failure`/`cancelled`）从 `GET /api/runs` 那一行取，`cancelled` 传进 `RunNotes.cancelled`。取消接口的响应也是那一行。没给 `--data-sources` 时数据源列表为空，提交返回 400，表单要显示这个原因。
- 打包：构建好的前端文件进 wheel；仓库里不提交构建产物，由打包或 CI 步骤生成。做法先看 `tests/test_packaging.py` 和现有的打包配置。
- 只用 pnpm 和 uv。

## Acceptance

- [ ] 提交、实时显示、刷新后续读、取消、历史列表都在浏览器里真实操作过
- [ ] 历史列表显示任务、类型、配置、状态、用时、花费、开始时间，按开始时间倒序
- [ ] 从 wheel 安装后 `data-agent web` 能打开页面（打包测试覆盖）
- [ ] （2026-11-16 主会话加，spec "历史列表"）静态构建里历史列表页列出精选运行（`staticSource.listRuns()` 已只返回精选，工单 81），点一行进运行页；开发集 trial 不在这一页
- [ ] 浏览器控制台没有报错
- [ ] `make check` 通过

## Evidence required

`make check` 输出；用无头浏览器对着工单 78 的后端加假子进程走一遍提交、实时显示、取消，截图路径写进最终回复。真调模型的一次由主会话做。

## Stop conditions

- 打包方式需要改 CI 或发布流程里本工单没被授权的部分。
- spec 自相矛盾或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 2
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: returned ×1 (PR #92); round-1 fix in review
- Human review minutes: — (filled by the human at merge, optional)

## Comments
- 2026-11-16 主会话：76 已合入（PR #91）。数据来源接口在 `web/src/data/source.ts`，本工单加后端实现；运行切换和基础路径默认运行是 76 为打开三条夹具加的，在线版的部分归 81。工单 81 同时在做，两边都改 `web/`：本工单只动本地版需要的部分（数据来源后端实现、提交表单、取消、历史列表、实时追加、打包），不改 81 的评测页和第一屏；谁后合入谁 rebase 解决冲突。
- 2026-11-16 工单 agent（Claude Code, Opus 5.5）（fe7b7ac、c349e43；PR #92）：
  - 实现：`web/src/data/source.ts` 加 `localSource`，读后端接口；运行中的用 SSE 跟读、按 `seq` 去重；断线浏览器带 Last-Event-ID 续读，浏览器放弃后按最后序号重开。构建开关 `VITE_DATA_SOURCE=local`（`pnpm --dir web build:local`）。本地版基础地址是历史列表；顶栏 New run 表单；运行中显示 Cancel。`RunNotes.failed`：进程退出且没有 `result` 事件，优先级 cancelled > grade > result > failed。`app.py` 提供构建好的前端（`/assets/` 和 `/api/` 以外回 `index.html`；没构建时 503 并说明怎么构建）。`hatch_build.py` 在 PATH 有 pnpm 时把前端构建进每个 wheel，没有 pnpm 时照常出 wheel、不带前端并警告。README 加 "Web UI" 一节。
  - 真实运行（假子进程，$0）：`uv run python runs/79-e2e/serve.py`（真实 `create_app`，子进程换成每秒写一条事件、等 gate 文件的 `slow_fake_run.py`）+ 不带数据源的 `data-agent web --port 8798`，无头 Playwright：提交后跳到运行页，事件约 0.5 s 内出现；刷新后断点续读，打开 gate 后 SUCCESS、`$0.012`、4 turns、"from query #3"，无重复事件；取消后 CANCELLED，服务端日志没有重连循环；409 显示 "run … is still going; one run at a time"；400 显示 "no data source is configured; start the server with --data-sources FILE"；历史列表七列、按开始时间倒序，深浅色正常。截图在 worktree 的 `runs/79-e2e/shots/01–11*.png`。
  - 控制台：正常路径 0 条；故意触发 409 时 Chrome 自己记一条 "Failed to load resource: 409"，页面无法屏蔽，409 是契约规定的状态——主会话认可，这条验收按"除浏览器网络日志外无报错"算满足。
  - make check：pytest 2305 passed、1 skipped；vitest 179 passed；basedpyright 0 errors。打包测试：构建 wheel、从中启动 `python -m data_agent web`，页面、`/runs/<id>` 深链接、脚本都能取到。
  - da-review（Codex）：无可执行问题。eng:code-review，c349e43 修：断线期间运行结束会丢最后的事件 → `onerror` 发现行已结束先读 `events?after=last`；行不存在时每 2 s 永远重开 → 直接结束；恢复逻辑改成一个 async 函数；共用 `errorText`；过时注释；NewRun 重复的 task_type 回退。
  - 不修（理由）：`RunNotes.failed` 新状态规则（spec 写"没有 result 且没有其他信息才算运行中"，后端行就是其他信息；主会话认可并写进 spec 状态来源）；409 控制台日志（浏览器行为）；静态版不再内存缓存已加载的运行（有 HTTP 缓存）；不是本服务启动的运行也显示 Cancel（后端会拒绝并显示原因；区分要给后端行加字段，超出范围）；hook 在没有 `node_modules` 时会 `pnpm install`（见下面主会话的意见）；`build:local` 和 hook 构建命令重复、dev 代理端口和 `WEB_PORT` 重复等风格项。
- 2026-11-16 主会话：PR #92 CI 绿。Codex GitHub app 对 c349e43 一条 P2"回到历史列表后旧的运行加载错误还显示"——不属实：`useRun` 返回 `loaded?.id === id ? loaded : null`（`App.tsx:57`），历史路由上 `id` 为 `null`、旧的 `loaded.id` 是字符串，hook 返回 `null`，`shownError`（第 139 行）只剩运行列表的错误。已在 PR 上回复。
- **退回第 1 轮（主会话 2026-11-16 定；评测不能依赖网页构建）**：`hatch_build.py` 让每次非 editable 的 wheel 构建在有 pnpm 时都构建前端；评测每次都用 `uv build --wheel` 建 agent 的 wheel（`evals/harbor_agent.py:154`），于是评测会：每次多一遍前端构建；在钉住的评测 worktree（没有 `node_modules`）里联网 `pnpm install --frozen-lockfile`；前端构建失败就让评测失败。修法：
  - `hatch_build.py` 认一个环境变量（如 `DATA_AGENT_WHEEL_UI=0`）跳过前端构建，跳过时不警告（是有意的）；默认仍构建（给用户装的 wheel 照旧带前端）。
  - `evals/harbor_agent.py` 的 `build_artifacts` 构建 wheel 时设这个变量（只改那一次 `subprocess.run` 的 `env`）。
  - 测试：`tests/test_packaging.py` 加一条——设了变量构建出的 wheel 没有 `data_agent/web/static`、也不调用 pnpm（可以用 PATH 里放一个会失败的假 pnpm 证明）；评测那边有单元测试覆盖 `build_artifacts` 传了这个变量（看现有 `tests/` 里有没有 harbor_agent 的测试，照它的写法）。
  - 在原分支上接着修；修完重跑 `make check` 和两道审查。
- 2026-11-16 主会话：工单 81 先开 PR；81 合入后本工单 rebase 到 main，解决和 81 在 `App.tsx`、`RunPage.tsx`、`source.ts` 的冲突，并补上面新加的那条验收（在线版历史列表）。
- 2026-11-16 工单 agent（Claude Code, Opus 5.5）退回第 1 轮（c050f78、af03aa4）：
  - 修：`hatch_build.py` 在 `DATA_AGENT_WHEEL_UI=0` 时跳过前端构建，在找 pnpm 之前返回，不警告；默认照旧构建。`evals/harbor_agent.py` 的 `build_artifacts` 只在 `uv build --wheel` 那次调用上设 `env=os.environ | {"DATA_AGENT_WHEEL_UI": "0"}`（加 `import os`）。
  - 测试：`tests/test_packaging.py::test_wheel_built_with_the_ui_turned_off_never_calls_pnpm`（PATH 前放总是失败的假 pnpm：不设变量构建失败、stderr 有 "pnpm was called"；设变量构建成功、stderr 不含 "pnpm"、wheel 没有 `data_agent/web/static`、有 `BUILD_COMMIT`）；`tests/test_harbor_agent.py::test_the_agents_wheel_is_built_without_the_web_ui`（断言构建 wheel 那次 env 有 `DATA_AGENT_WHEEL_UI=0` 且保留 PATH）。都先红后绿；变异检查：跳过分支加警告 → stderr 断言失败。`tests/test_harbor_agent.py` 是退回意见要求的评测单元测试，主会话认可在范围内。
  - 真实运行：假 pnpm 在 PATH 上直接调 `build_artifacts()` → wheel 和 requirements.txt，`web/static entries: 0`，`BUILD_COMMIT: True`，没有 pnpm 警告。
  - make check（af03aa4）：exit 0；ruff 通过；basedpyright 0 errors；pytest 2307 passed、1 skipped；vitest 179 passed。
  - da-review（Codex，c050f78）：无意见。eng:code-review：无阻塞；af03aa4 修三条（"不警告"补断言；对照构建先去掉 shell 里已有的 `DATA_AGENT_WHEEL_UI`；注释"不联网"改成"no pnpm and no frontend build"）。不修：变量名两处各写一次（评测代码不 import 构建 hook，单元测试钉住字面值）；只认 `"0"`（内部开关）；测试 monkeypatch `subprocess.run`（会还原）；打包测试多跑两次 `uv build`（对照需要）。
  - 本轮没做"在线版历史列表"那条验收（是本轮开始后才加的），等 81 合入后 rebase 时做。
- 2026-11-16 主会话：PR #92 对 af03aa4 CI 绿；Codex 再次报同一条"回到历史列表后旧错误还显示"，理由同前不属实（`App.tsx:57`），已在 PR 上回复。本 PR 没有属实的新意见；等 81 合入后 rebase 并做在线版历史列表。
- **2026-11-16 主会话：81 已合入（PR #93）。本轮任务**：在原分支 rebase 到 origin/main，解决和 81 在 `App.tsx`、`RunPage.tsx`、`source.ts` 等处的冲突（81 的第一屏、精选切换、评测页、`home.json`、`site-data.ts` 都要保留）；做验收里"静态构建里历史列表页列出精选运行"那条；重跑 `make check`、本地版无头浏览器走一遍（提交、实时、取消、历史列表）、静态构建（`SITE_DATA`）的第一屏和历史列表，和两道审查。这不算退回，是计划内的合并后续。
