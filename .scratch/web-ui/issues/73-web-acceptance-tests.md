# 73 — 网页界面：三处接缝的验收测试

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

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
  - 这三份是旧事件文件，没有工单 74 要加的字段，正好用来测"旧文件的退路"。新字段的行为另写小的手工夹具（几条事件即可），字段名在本工单定下来并写进测试，工单 74 照此实现。
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
- [ ] 视图模型的类型、后端应用入口的签名、新事件字段的名字都写在代码里，并在最终回复里列出，供人审
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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
