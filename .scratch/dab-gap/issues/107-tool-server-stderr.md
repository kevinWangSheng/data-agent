# 107 — DAB 工具服务器：留下 stderr 和启动耗时

Status: done
Blocked by: —
Spec: 无（spec `../spec.md` 第 3 步全量重跑的后续；人 2026-11-22 确认范围，见 Comments）
Claimed by: claude-opus-5-5 (worktree 107-tool-server-stderr)
Branch: worktree-107-tool-server-stderr

## Goal

每个 DAB trial 的目录里都留下工具服务器（`evals.dab_server`）的 stderr 和各启动阶段的耗时；服务器报 `failed` 时，事件里的错误指向这份文件。下次再出现偶发启动失败，能从文件里看出卡在哪一步、为什么。本工单只留证据，不修启动失败本身。

## Authority

- May edit: `evals/dab.py`（只限工具服务器的启动参数、stderr 落盘、失败时的事件）、`evals/dab_server.py`（只限 `main()` 的启动阶段计时和日志）、`tests/test_dab.py` 和新测试
- Must not edit: `src/`、`skills/`、提示词、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话做）
- Spend cap: $0

## Context

- 现象（ADR-0008 Update 2026-11-22，`docs/adr/0008-change-acceptance.md:310`）：第 3 步全量 540 次里有 2 次，会话开始时 SDK 的 init 消息里 DAB 工具服务器状态是 `failed`，在 dab_load 之后约 35–42 秒；agent 没有库工具，`evals dab` exit 2，同参数重跑都过。两次时内存正常。stderr 没有留下，原因未确认。
- 现在的代码：
  - 服务器的启动配置：`evals/dab.py:416-423`（stdio，`sys.executable -m evals.dab_server`，参数见 `server_args`，`:261-275`）。stdio 服务器的 stderr 由 SDK/CLI 接管，没有落盘。
  - 判失败：`evals/dab.py:490-497`（`mcp_status`，`:551-556`），只记 `DAB tool server failed`。
  - 服务器启动：`evals/dab_server.py:489-530`。`main()` 里依次是 `scaffold`、`QueryTimeout.install`、建 `query_tool`/`list_tool`、建 `DockerSandbox`、`build_server(...).run("stdio")`。`:514-516` 的注释写明 SDK 等服务器启动有时限（具体多少秒未确认）。
- 未确认的假设，只作为要能验证的方向，不要据此改行为：启动阶段（连库、建工具、导入）偶尔超过 SDK 的等待时限。日志要能证实或排除它。
- 落盘要求：
  - 做法已定（2026-11-22，对齐会话）：启动命令保持 `sys.executable -m evals.dab_server ...`，不套 shell 包装，因为现有测试断言 `server["args"][:2] == ["-m", "evals.dab_server"]`（`tests/test_dab.py:895`）。`evals/dab.py` 新加一个参数（如 `--stderr-log <trial 目录里的文件>`）。`evals/dab_server.py` 在模块最顶上、其他 import 之前，只用标准库从 `sys.argv` 取出这个路径，把 fd 2 重定向到这个文件（`os.dup2`），之后的导入出错、`main()` 里任何一步出错，都会写进这个文件。
  - 这个做法留不下两种失败：Python 解释器自身启动失败，以及 `evals/__init__.py` 导入失败（它在 `dab_server.py` 的第一行之前执行，现在只有 431 字节）。这两种接受不留，写进 Comments 作为已知限制，不因此停下。
  - 每个启动阶段一行：阶段名、开始以来的秒数，最后一行是开始服务 stdio。
  - 文件放在 trial 目录（和 `dab_tool_calls.jsonl` 同级），名字固定。会话结束后照 `mask_dab_logs`（`evals/dab.py:544-548`）的做法遮盖密钥：stderr 可能带连接串。注意 `mask_dab_logs` 现在只遮 `*.jsonl`。
  - 服务器报非 `connected` 时，`error` 事件带上这份文件的相对路径和末尾若干行（已遮盖）。
- 不在范围：`runs/f18c/par.py` 遇到 exit 非 0 的任务时不记日志就跳过（运行目录里的一次性脚本，不进 git，只记成已知限制）；查清并修复启动失败（留了证据后另开工单）。

## Acceptance

- [ ] `dab_server.py` 在重定向之后的导入阶段或 `main()` 里失败（测试里制造一个导入错误或启动异常）：trial 目录里的 stderr 文件有那段 traceback（测试）。
- [ ] 正常启动：stderr 文件里有每个启动阶段的计时行，最后一行是开始服务（测试）。
- [ ] 服务器状态非 `connected`：`error` 事件带文件路径和末尾几行（扩展 `test_a_dab_server_that_failed_to_start_is_an_infra_error` 一类的测试，或新写）。
- [ ] stderr 里出现的密钥在会话结束后被遮盖（测试）。
- [ ] 服务器启动成功时，工具、提示词和事件的其他字段和现在一致（现有 `tests/test_dab.py` 不改断言也能通过）。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出。在本机用 `tests/` 里的小样本或假服务器真实跑一次启动成功、一次启动失败，贴出 stderr 文件内容和 `error` 事件。最终回复写好主会话在服务器上跑的单 trial 命令（`evals dab`，一个小数据集的一道题，一次），主会话跑完把 stderr 文件贴进 Comments。

## Stop conditions

- 照上面定的做法，现有 `tests/test_dab.py` 的某条断言非改不可，或者服务器启动成功时的行为（工具、返回、事件）会变：停下交回。
- Codex 不可用（`da-review` 报 at capacity 或 model not supported）：不停，照 memory 和 HANDOFF 的规矩用 Fable 子 agent 只读审查代替，Comments 注明。
- 发现启动失败的原因已经能确认：写进 Comments，不在本工单里修。
- 第二轮审查还不过。

## Failure handling

Status 改 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2 (da-review 1 fix; eng:code-review 1 fix)
- Model spend: billed $0.0103 (main-session server trial)
- Outcome: accepted, merged (PR #119)
- Human review minutes: —

## Comments

- 2026-11-22（主会话）：人确认范围：只留证据，不修启动失败，不花模型钱。同一轮人还定了：duckdb-sql、postgres-sql 里 `ROW_NUMBER` 的例子不开工单（wontfix，理由记在工单 105 的 Comments）。
- 2026-11-22 (ticket agent, claude-opus-5-5): branch worktree-107-tool-server-stderr, commits d4d6d6c, 5a6a37b, 9f9a51d.
  - Baseline: tests/test_dab.py 113 passed. Final: make check passed (ruff ok, basedpyright 0 errors, pytest 2584 passed / 2 skipped, web 320 passed). 7 new tests: import error after the redirect and a failure in main() both leave their traceback in the file; a normal start writes every stage line, the last being `serving stdio`; the runner passes --stderr-log in the trial directory; the error event carries the path and a masked tail; the file is masked after the session; libpq's resolved address is masked.
  - Real runs (fake DAB from tests/fixtures, no model calls): `real_start.py` gave 6 tools; stderr file: started 0.000s / imported 1.051s / scaffolded 1.058s / query timeout installed 1.058s / tools built 1.058s / sandbox built 1.058s / serving stdio 1.071s. `real_fail.py` (real server from the runner's MCP config, DAB root without tools, SDK stand-in reports `failed`): server exit 1; file = imported 1.156s + traceback ending `ModuleNotFoundError: No module named 'common_scaffold'`; error event {"source":"mcp","error":"DAB tool server failed","stderr_log":"dab_server_stderr.log","stderr_tail":[last 20 lines]}. Scripts in the worktree's runs/107/.
  - da-review main, round 1: P2, the stderr file kept the address libpq resolved PG_HOST to. Fixed in 5a6a37b, with a regression test.
  - eng:code-review main. Standards: no hard violations. Fixed: alias mask_text renamed to mask_event_text (9f9a51d). Not fixed: secrets_of(env) computed 5 times in run_once (existing, outside ticket); *.jsonl logs still use read_text (existing, not in Authority); stderr_log=None optional so the existing test helper stays unchanged; repeated test setup (readable as is). Spec: every acceptance item implemented; no existing assertion changed.
  - Known limitations: (1) nothing kept when the interpreter fails to start or evals/__init__.py fails to import (agreed). (2) masking runs in the runner's finally block; lines the server writes after that stay unmasked (same race as dab_tool_calls.jsonl; CLI owns the process). (3) stderr_tail is read when the init message arrives; later lines only in the file. (4) runs/f18c/par.py skips non-zero exits without logging (outside repo).
  - Root cause of the start failure: not established. Imports ~1s locally; server run to show stage times there.
- 2026-11-22 (main session): PR #119 opened. Server single trial (agnews query2 × 1, a few cents; ticket's $0 cap is for the agent, the ticket's Evidence asks the main session for this run) waits until ticket 110's server runs finish.
- 2026-11-22 (main session): server trial at 9f9a51d: `scripts/da-remote start 107-stderr -- dab --dab-root ../da-benchmarks/DataAgentBench --dataset agnews --query 2 --config no-context --runs 1 --out runs/107-stderr --budget 1` → exit 0, billed $0.0103, 0 `error` events. `runs/107-stderr/agnews/query2/run0/dab_server_stderr.log` start-up lines:
  ```
  dab_server started at 2026-11-22T21:46:18Z pid 954102: 0.000s
  dab_server imported: 5.705s
  dab_server scaffolded: 7.287s
  dab_server query timeout installed: 7.287s
  dab_server tools built: 7.290s
  dab_server sandbox built: 7.290s
  dab_server serving stdio: 7.373s
  ```
  On the server start-up takes 7.4 s (imports 5.7 s, scaffold 1.6 s), against ~1.05 s locally. The two failures were reported ~35–42 s after dab_load; this run doesn't say whether a slow start under load explains them (needs a failing trial's file).
  - Known limitation found in the real run: DAB's `BaseTool` logs every call's args and result to stderr (rich INFO lines), so the file is 172,238 bytes for one trial (60 `BaseTool.py` lines), repeating `dab_tool_calls.jsonl`. It goes into trace bundles. Not an acceptance item; lowering DAB's log level would be a follow-up ticket if size matters.
  - PR #119: CI green; Codex GitHub app on 9f9a51d: no findings (`has_any_findings: false`). Squash-merged.
