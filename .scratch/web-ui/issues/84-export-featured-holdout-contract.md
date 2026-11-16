# 84 — 导出验收契约：精选运行也过留出集检查

Status: ready-for-agent
Blocked by: 73
Spec: ../spec.md
Claimed by: claude-opus-5-5 (Claude Code) (filled in by the main session; the worktree session could not write the main checkout)
Branch: worktree-84-export-featured-holdout-contract

## Goal

`tests/acceptance/test_web_export.py` 的契约加上精选运行的留出集检查：精选运行的 `notes.json` 必须声明 `task_id` 或 `local_data: true`，新增的测试标 `xfail(strict)` 留给工单 80。本工单只改验收测试，由人审过才合并（plan §142）。

## Authority

- May edit: `tests/acceptance/test_web_export.py`（模块文档里的外部接口说明、新增测试、现有测试里精选运行的 `notes.json` 输入）
- Must not edit: `evals/`、`src/`、`web/`、`tests/fixtures/web/`、其他验收测试文件、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "导出命令和在线版数据"的"精选运行也过留出集检查"一条（2026-11-15 人定）。
- 起因：工单 80 第 1 次实现只按 task id 检查 Harbor trial；精选运行（`--run`）的说明里没有 task id，代码明说不检查（`.claude/worktrees/80-web-export/evals/export.py:30-36`）。导出结果会公开，这样就是 fail-open。工单 73 的契约（`test_web_export.py:9-12`）只要求 `title`、`failure_reason`、可选的 `grade`，所以要先改契约。
- 新契约：
  - 来自榜单题目的精选运行，`notes.json` 带 `task_id`；真正的本地数据运行带 `local_data: true`。至少有一个；两个都没有就拒绝，不当成本地运行。
  - 带 `task_id` 的和 trial 走同一套检查：只放行清单里开发集的题；留出集的题、清单里没有的题都拒绝。
  - 拒绝：非零退出，什么都不导出，输出（stdout、stderr）里只有数量，不出现题目 id、失败原因或结果。
  - `task_id`、`local_data` 只是输入；输出的运行说明（`meta.json`、`index.json`）字段不变，和 `web/src/run-view/model.ts` 的 `RunNotes` 一致。
- 要加的测试（都 `xfail(strict)`，标记名照现有写法留给工单 80）：
  - 开发集 `task_id` → 导出成功；
  - 留出集 `task_id` → 拒绝，输出里没有那个 id 和失败原因（照 `test_holdout_trials_are_refused` 的写法放一个独特的串检查）；
  - 清单里没有的 `task_id` → 拒绝；
  - `local_data: true` → 导出成功；
  - 两个都没有 → 拒绝。
- 现有测试里的精选运行（`test_every_run_is_exported_with_complete_notes`、`test_keys_endpoints_addresses_and_hostnames_are_scrubbed`、`test_scrubbing_cannot_be_turned_off`）的 `notes.json` 补上 `local_data: true`，断言不改。
- 用假清单，不读真实留出集（ADR-0004）。
- 不写实现。要证明测试本身没写错，可以照工单 73 的做法写一份不提交的临时参考实现对着跑。

## Acceptance

- [ ] 五个新测试都在，标 `xfail(strict)`，在 main 上按预期失败
- [ ] 现有测试只改了精选运行的 `notes.json` 输入，断言一字未改
- [ ] 模块文档的外部接口一节写明新契约
- [ ] `make check` 通过

## Evidence required

`uv run pytest tests/acceptance/test_web_export.py -q` 的输出；`make check` 输出；临时参考实现对着跑的结果（去掉预期失败标记后全过）和至少一个反向检查（例如不检查精选运行时，留出集那条会失败）。

## Stop conditions

- 新契约和 spec 或其他验收测试矛盾。
- 要改 `RunNotes` 或输出字段才写得出测试。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: returned ×1
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-15 claude-opus-5-5，第 1 次实现（worktree-84-export-featured-holdout-contract，3 个提交）：只改 `tests/acceptance/test_web_export.py`。
  - 模块文档写明新契约；五个新测试经模块级 `pytestmark` 都是 `xfail(strict)`：`test_featured_run_of_a_dev_task_is_exported`、`test_featured_run_with_local_data_is_exported`、`test_featured_run_of_a_holdout_task_is_refused`、`test_featured_run_of_an_unlisted_task_is_refused`、`test_featured_run_with_neither_task_id_nor_local_data_is_refused`。
  - 现有三个测试的精选运行只在输入里加了 `"local_data": True`；主会话核对 `git diff origin/main...HEAD`：删掉的行只有旧模块文档和这三处 `notes.json` 输入，断言未改。
  - `uv run pytest tests/acceptance/test_web_export.py -q` → "9 xfailed"；make check exit 0，"2195 passed, 1 skipped, 39 deselected, 15 xfailed"。
  - 临时参考实现（工单 80 的草稿 + 精选运行检查，未提交，已删）：`--runxfail` 下 9 passed。反向检查：关掉精选检查 → 3 条拒绝测试失败；拒绝时打印题目 id 或失败原因、或把 `task_id`/`local_data` 抄进输出 → 4 failed；拒绝后留下 `eval.json` → 3 条拒绝测试失败；先查 `local_data` 再查 `task_id` → 留出集那条失败。
  - da-review：P2"拒绝后可能留下别的文件"已修（`assert_refused` 要求输出里没有任何文件）；P1"只报数量"部分修（每个拒绝场景都带独特的失败原因和 grade detail 并检查未打印），不钉死消息格式。
  - eng:code-review：Spec 已修"留出集 `task_id` + `local_data: true` 没测"（留出集测试加第二个场景）；不修：`local_data: false` 且无 `task_id`；文档的"只报数量"比测试严。Standards 无违规，几处写法问题按判断不改。
- **退回第 1 轮（主会话，2026-11-15；只是让测试真正检查已定的契约，按人的规则由主会话直接定）**：
  1. 拒绝时不能打印被拒运行的 id 或目录路径：`assert_refused`（第 305 行）现在只查题目 id、失败原因、grade detail。精选运行的目录常按题目命名（真实夹具 `dbt-cart-abandonment` 就是），打印 run id 等于泄露留出集题名，也违反"只报数量"。三个拒绝测试都要把被拒运行的 id 和 `--run` 目录路径加进不能出现的串。
  2. `local_data: false` 且没有 `task_id` 要拒绝：在 `test_featured_run_with_neither_task_id_nor_local_data_is_refused` 里加这个场景，防止实现只看键在不在（fail-open）。
  - 在原分支上接着改；修完重跑 `pytest tests/acceptance/test_web_export.py -q`、`make check`、参考实现对着跑（含一个反向检查：拒绝时打印 run id → 失败），和两道审查。
