# 73 — 网页界面：三处接缝的验收测试

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-code (opus-5.5)
Branch: worktree-73-web-acceptance-tests

## Goal

`web/` 下有最小的前端工程（Vite + React 19 + TypeScript + Tailwind 4 + vitest，pnpm），spec 约定的三处接缝各有一组验收测试，全部标 `xfail`（vitest 用 `test.fails` 或等价写法），真实事件文件夹具已脱敏入库。本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit: `web/`（新建）；`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`（新建）；`tests/fixtures/web/`（新建）；`Makefile`、`.github/workflows/check.yml`、`.gitignore`、`.pre-commit-config.yaml`（只为把前端的类型检查和 vitest 接进 `make check` 和 CI）；`pyproject.toml`（只加测试需要的依赖，如 `fastapi`、`httpx`）
- Must not edit: `src/data_agent/` 和 `evals/` 的实现代码（本工单只写测试和接口的空壳）、已有的 `tests/acceptance/` 文件、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec 的 "Testing Decisions" 列了每处接缝要断言的行为，照它逐条写；"运行视图模型"一节定了产出的结构和旧文件的退路。
- 夹具来源（只在本机主 checkout，绝对路径）：`/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/data/runs/` 下三条运行，各有 `events.jsonl` 和 `meta.json`，已过 `evals scrub`（0 处改动）。复制进 `tests/fixtures/web/` 前再跑一次脱敏。
  - `dbt-division-by-zero/events.jsonl` 是 1.2 MB，超过 pre-commit 的 1000 KB 上限：用 gzip 存，测试里解压读。
  - 这三份是旧事件文件，没有工单 74 要加的字段，正好用来测"旧文件的退路"。新字段的行为另写小的手工夹具（几条事件即可），字段名用 spec "事件：新增和补充的字段"里的表，工单 74 用同一组。
- 样板 B 怎么读这些事件（只作参考，不复制进来）：`/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/b/src/`；事件字段说明在 `/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/BRIEF.md` 的 "Data" 一节；写事件的代码是 `src/data_agent/events.py` 和它的调用处。
- 前端验收测试只针对一个纯函数（事件列表加可选的运行说明 → 运行视图模型）。本工单定下它的 TypeScript 类型和函数签名，函数体留空（抛出未实现）。
- 后端验收测试用 FastAPI 测试客户端；`data-agent run` 子进程换成按脚本往事件文件写事件的假进程。本工单定下创建应用的入口签名（接收运行目录、数据源配置、启动子进程的函数），实现留空。先例：`tests/test_run_cli.py`、`tests/fake_model.py`、`tests/lifecycle_events.py`、已有的 `tests/acceptance/`。
- 导出命令的验收测试在命令行这一层调用。先例：`tests/test_eval_scrub.py`、`tests/test_eval_report.py`。留出集的判断对照评测清单（`evals/manifest.json` 的格式），测试用假清单和假运行目录，不读真实的留出集数据。
- 只用 pnpm，不用 npm；只用 uv，不用 pip。

## Acceptance

- [ ] 三组验收测试存在，覆盖 spec "Testing Decisions" 每处接缝列出的全部行为，每条行为至少一个测试
- [ ] 所有验收测试当前都以预期失败的方式通过检查（`xfail` / `test.fails`），没有被跳过的
- [ ] `make check` 包含前端的类型检查和 vitest；CI 也跑
- [ ] 夹具里没有密钥、地址、主机名（脱敏命令的输出为 0 处改动）
- [ ] 视图模型的类型、后端应用入口的签名都写在代码里，并在最终回复里列出，供人审
- [ ] `make check` 通过

## Evidence required

`make check` 的完整输出；`pnpm --dir web test` 的输出（显示每个验收测试的名字和预期失败）；脱敏命令的输出；最终回复里的类型和签名清单。

## Stop conditions

- 发现 spec 的某条行为没法在约定的接缝上测。
- 夹具脱敏后仍含看起来像密钥或地址的内容。
- spec 自相矛盾或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: accepted (PR #82, human-approved 2026-11-15; CI green, Codex no findings)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 实现（分支 worktree-73-web-acceptance-tests，提交 37ce21b、9a3a380、099db9e；PR #82）：
  - web/：Vite 8 + React 19 + TS 7 + Tailwind 4 + vitest 5，pnpm 10.33；视图模型的类型和函数空壳，43 条前端验收测试（test.fails）。
  - src/data_agent/web/app.py：create_app、DataSource 空壳；tests/acceptance/test_web_api.py 13 条 xfail(strict)（工单 77 负责 7 条，78 负责 6 条）；tests/acceptance/test_web_export.py 4 条（工单 80）。
  - tests/fixtures/web/：real/ 三条真实运行（1.2 MB 那条 gzip）、各自 meta.json、new-fields/ 手写新字段夹具、fake_run.py。夹具目录叫 real/ 不叫 runs/，因为 .gitignore 的 runs/ 规则会忽略它。
  - make check 和 CI 加了前端类型检查和 vitest，依赖安装走 --offline，CI 先跑 make web-install；新依赖 fastapi（运行时）、httpx（开发）。
- 测试本身的核对：三处各写临时参考实现（未提交），去掉预期失败标记后前端 43/43、后端 13/13、导出 4/4 通过；反向检查：服务绑 0.0.0.0、导出不脱敏，对应测试都会失败。
- 运行：`pnpm --dir web test` → "Tests 43 expected fail (43)"；`uv run pytest tests/acceptance/test_web_api.py tests/acceptance/test_web_export.py` → "17 xfailed"；`make check` → ruff 通过，basedpyright 0 errors，"2168 passed, 1 skipped, 39 deselected, 17 xfailed"，web typecheck 通过，"43 expected fail"。
- 脱敏：`python -m evals scrub` 对全部 8 个夹具文件（大文件先解压）→ "changed 0 file(s) of 8; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0"；pre-commit gitleaks 通过。工单会话没有 DATA_AGENT_* 变量，"真实 endpoint 和密钥的值"一项未查。
- da-review（Codex）：[修] 阶段断言太松 → 要求 orient、explore、build 都出现且第一步属于 orient；[修] contextToolUses 加测试；skills 不测（见待人定 1）；[第二轮修] make check 联网 → 改 --offline，加 make web-install。
- eng:code-review：Standards [修] make check 联网、.gz 读取兜底、命名（fresh、STORY_*）、辅助函数位置；[不修] read_events 重复（共享处 seam_support.py 是已有验收文件，本工单不能改）；[不修] verdict 用 string（真实数据有 NEEDS_INFO）；[不修] 测试文件偏长（验收测试按接缝一个文件）。Spec：每处接缝列出的行为都有测试。
- **待人审时定**：
  1. spec 第 101 行"加载了哪个 Skill（从工具调用推出）"前提不成立：Skill 直接写进系统提示（agent.py:744-747），事件里看不出。`skills` 字段保留在类型里，没测；spec 这句要改。
  2. 导出目录结构已被测试定死（`index.json` + `runs/<id>/{events.jsonl,meta.json}`），工单 80 "结构由本工单定"一句要改。
  3. 旧文件里的拒绝：Bash/文件工具 hook 拒绝算 guard（agent.py:941-953），结果含 "read-only gateway" 算 gateway，其他 hook 拒绝（验证者 hook）算 `unknown`。工单 74 只写 guard/gateway，新文件里验证者 hook 拒绝没有 `rejected_by`，会显示成普通报错，新旧不一致；这条规则也没写进 spec 或工单 75。
  4. 阶段只测了顺序和部分边界（spec 没给工具归属规则）：顺序、验证与修复边界精确、两条 dbt 运行都有 orient/explore/build 且第一步属 orient；"自查"和查数运行的"回答"可不出现；每种阶段至多出现一次。
  5. 两处没定：评测运行回放到一半显示什么状态；`data-agent web` 的数据源配置文件必须可选（有后端测试不给配置文件启动），工单 78 没写。
  6. 工单 80 注意：`evals` 命令行靠 `args.run` 分派子命令，`--run` 必须 `dest="runs"`。
- 接口清单（供人审）：
  - 前端 `web/src/run-view/model.ts`：`buildRunView(events: readonly RunEvent[], notes?: RunNotes): RunView`；`RunNotes`（导出 meta.json 共用）`id/title/task/task_type/data_source/grade{passed,detail}|null/failure_reason/featured`，本地版另有 `cancelled`；`RunView` = `summary/steps/dispatches/phases/keyMoments/answer/skills/contextToolUses`；`Step.rejectedBy: 'guard'|'gateway'|'unknown'|null`；`Phase.kind` ∈ orient/explore/build/self-check/answer/verify/fix；`KeyMoment.kind` ∈ verdict/rejection/error/answer。
  - 后端 `src/data_agent/web/app.py`：`create_app(runs_dir, data_sources, start_run) -> FastAPI`；`DataSource(name, duckdb=None, postgres_env=None, dbt_project=None)`；`StartRun = Callable[[Sequence[str], Path], Popen[bytes]]`；路由 `GET /api/options`、`GET /api/runs`、`GET /api/runs/{id}/events?after=N`（`Accept: text/event-stream` 时 SSE，帧带 `id: <seq>`，按 `Last-Event-ID` 续读）、`POST /api/runs`（201；数据源不存在 4xx 带 detail；已有运行 409）、`POST /api/runs/{id}/cancel`；`data-agent web --runs-dir DIR --port N`，无改监听地址的参数。
  - 导出：`python -m evals export --manifest F --out D [--run DIR]... [--job DIR]... [--hostname H]...`
