# 98 — `--network` 选项：Web 工具由运行级开关控制

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-code (opus 5.5)
Branch: worktree-98-network-option

## Goal

`data-agent run --network` 时 agent 有 WebSearch、WebFetch；不带时这两个工具不加载并被禁止（和现在一样）。网页后端启动的运行默认带 `--network`，评测入口都不带，并有测试锁住。

## Authority

- May edit: `src/data_agent/cli.py`（`--network` 选项）；`src/data_agent/agent.py`（`WEB_TOOLS` 的加载和禁止，`agent.py:113-115`、`:1591-1592` 附近；`build_options` 接收 `network` 参数，默认关）；`src/data_agent/web/launcher.py`、`src/data_agent/web/conversations.py`（默认带 `--network`；联网开关只加后端参数，界面上的开关在工单 101）；`tests/`（新增测试）
- Must not edit: `evals/` 的评测行为（只能读，用来写"评测入口不带 `--network`"的测试）、`configs/*.toml`、`skills/`、`command_guard.py`、`workspace_guard.py`、`web/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "`network` 开关"一节。ADR-0010 第 4 点。
- `network` 是运行级选项，不放进 `configs/*.toml`：那些层配置是产品和评测共用的（`--config` 必填，`cli.py:177`）。
- 上游：data-eng-bench `configs/data-eng-bench-duckdb.claude-code.yaml:29`（`disallowed_tools: WebSearch,WebFetch`）；nao `nao/apps/backend/src/services/agent.ts:397-409`（Web 搜索按设置加载）。路径相对 `~/dev/AI/data-agent-refs/`。
- 2026-11-17 已实测：用项目的 `ModelSettings.sdk_env()` 走 DeepSeek 端点，WebSearch 和 WebFetch 都返回 `error=None` 的正常结果（spec 里有记录）。
- 沙箱网络接到这个选项是工单 100 的事，本工单不碰沙箱。
- 评测入口有两种：Harbor 通过 `evals/harbor_agent.py:310` 拼 `data-agent run`（`evals/ablate.py`、`evals/board.py` 都是启动 Harbor）；DAB 不启动 `data-agent run`，在进程内调用 `build_options`（`evals/dab.py:403-421`），所以 DAB 要检查的是 `build_options` 的结果。
- 产品入口有两个：`launcher.py`（运行页提交）和 `conversations.py:128-142`（对话的每一轮）。

## Acceptance

- [ ] 带 `--network` 时 SDK 选项的工具里有 WebSearch、WebFetch，`disallowed_tools` 里没有；不带时反过来（单元测试）
- [ ] `launcher.py` 和 `conversations.py` 拼出的命令行都带 `--network`（单元测试）
- [ ] `evals/harbor_agent.py` 拼出的命令行不含 `--network`；`evals/dab.py` 拿到的 `build_options` 结果里没有 Web 工具、`disallowed_tools` 里有它们（单元测试）
- [ ] 不带 `--network` 时工具清单和现在一字不变（单元测试）
- [ ] `make check` 通过

## Evidence required

`make check` 输出。真实模型运行由主会话做：最终回复写好命令，带 `--network` 跑一条需要查外部信息的问题（例如"DuckDB 最新发布版本号是多少，再查本地库里有几张表"），贴出 WebSearch/WebFetch 的调用和答案；不带 `--network` 跑同一个问题，确认没有 Web 工具调用。

## Stop conditions

- 要改 `evals/` 的行为才能过测试。
- spec 自相矛盾，或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0.016 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: merged (PR #106 → 8b1bc19)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-17（工单会话，主会话抄录）：分支 `worktree-98-network-option`：c571ceb（实现）、87c488f（审查修复）。
  - 改动：`build_options(network=False)` 打开时把 `WEB_TOOLS` 加进 tools，`disallowed_tools=[]`；`run(network=)` 往下传；`cli.py` 加 `--network`（默认关）；`Launcher.submit`、`Conversations.send` 加 `network: bool = True`，命令行带 `--network`（只加后端参数，界面开关在 101；HTTP API 还没暴露这个参数）。
  - 测试：`tests/test_network_option.py`（`build_options` 的开和关；关时每种任务类型 × 配置的工具列表逐项对照；CLI 进程内一直走到 SDK 连接前；运行表单和对话的命令行）；`tests/test_harbor_agent.py::test_a_trial_runs_without_the_network`；`tests/test_dab.py::test_a_dab_session_has_no_web_tools`（变异检查：把 `build_options` 默认改成 `network=True`，这个测试会失败）。
  - `make check`（最后一次）：ruff "All checks passed!"，"329 files already formatted"，basedpyright 0 errors，pytest "2444 passed, 1 skipped, 63 deselected"，web "295 passed"。改动前基线 2424 passed、1 skipped。
  - `da-review main`："No actionable defects found"。
  - `eng:code-review main`：Spec 没有意见。Standards 4 条判断题：(1) `test_agent_outcomes.py` 的旧测试被更严的新测试覆盖 → 已删；(2) `build_options` 里两处判断 network → 改成算一次；(3) `launcher`、`conversations` 里重复的一行 `*(("--network",) if network else ())` → 不修，只有一行；(4) 把 network/sandbox 合成一个运行选项类型 → 不修，归工单 100（`--sandbox` 走同样的路径）。
  - 遗漏，交工单 100：`evals/configs/data-eng-bench-duckdb.data-agent.yaml:43` 的注释写的是 Web 工具在"每次 data-agent run"都关，评测仍是这样，产品已不对；100 改规则和文档时一起改措辞。
- 2026-11-17（主会话）真实运行，在工单分支（87c488f）上，deepseek-flash，`--config full`，题目"DuckDB 最新发布版本号是多少，再查本地库里有几张表"，库是两张表的临时 DuckDB：
  - `uv run data-agent run --network --type query --config full --workspace runs/98-real/ws-on --duckdb runs/98-real/shop.duckdb --events-dir runs/98-real/ev-on --task "$Q"`：exit 0；工具调用 `list_tables`×1、WebSearch×1、WebFetch×1、`query_database`×1；答案 "DuckDB 最新发布版本号：2.0.0 LTS（2026-10-21 发布）；本地库表数量：2 张"，附 duckdb.org 的来源链接；$0.0107。
  - 同一命令不带 `--network`：exit 0；工具调用 `query_database`×2、`list_tables`×1、Glob×1，没有 Web 调用；答案 "v1.2.2；2"，并说明没有网络，版本来自本地引擎的 `version()`；$0.0047。
- 2026-11-17：PR #106。CI 两个 check 都 pass；Codex GitHub app（`@codex review`，审 87c488f）："Didn't find any major issues"。squash 合入 main，8b1bc19。
