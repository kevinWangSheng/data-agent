# 103 — Web HTTP API 透传 `network` 参数 + `sandbox_exec` 记录 `outputs/` 产出文件

Status: done
Blocked by: 100
Spec: ../spec.md
Claimed by: Antigravity subagent (ticket-103), 2026-11-21
Branch: worktree-103-web-network-and-sandbox-outputs

## Goal

为前端工单 101 补齐两处必需的后端/事件数据支持：
1. `src/data_agent/web/app.py` 的 `Submit`（`POST /api/runs`）和 `Message`（`POST /api/conversations/{conversation}/messages`）新增可选字段 `network: bool = True`，并透传给 `launcher.submit(..., network=body.network)` 和 `conversations.send(..., network=body.network)`。
2. `src/data_agent/sandbox.py` 在 `execute_tool()` 执行期间记录沙箱在 `outputs/` 下新增或更新的文件（相对 workspace 的 POSIX 路径，排序），在 `reply_text()` 返回文本末尾（有产出文件时）以固定、易解析的结构化段落输出（如 `output_files:\n- outputs/chart.png`），方便前端展示产出文件清单；没有产出文件时保持原有输出格式不变。

## Authority

- May edit: `src/data_agent/web/app.py`、`src/data_agent/sandbox.py`、`tests/test_web_*.py`、`tests/test_sandbox*.py`
- Must not edit: `web/`、`evals/`、`skills/`、已有验收测试（`tests/acceptance/`）的断言和输入、其他工单文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- 工单 101 开工前核对源码发现这两处缺口，人于 2026-11-21 确认单开前置工单 103 补齐后再继续 101。
- `Launcher.submit(..., network: bool = True)`（`src/data_agent/web/launcher.py:141`）和 `Conversations.send(..., network: bool = True)`（`src/data_agent/web/conversations.py:97`）已支持 `network` 参数，只差 `app.py` 暴露和透传。
- `sandbox.py` 的 `OUTPUTS_DIR` 约定为 workspace 下的 `outputs/` 目录（见 `spec.md` 第 56 行与 `sandbox.py`）。注意处理软链接/越界路径（只列 workspace 内 `outputs/` 下的真实普通文件，不跟随指向 workspace 外的软链接）。

## Acceptance

- [ ] `POST /api/runs` 接收可选 `network: bool = True`：省略时默认 `True`；传 `false` 时 `launcher.submit` 收到 `network=False`，启动命令不含 `--network`（单元测试）
- [ ] `POST /api/conversations/{id}/messages` 接收可选 `network: bool = True`：省略时默认 `True`；传 `false` 时 `conversations.send` 收到 `network=False`，启动命令不含 `--network`（单元测试）
- [ ] `sandbox_exec` 执行期间在 `outputs/` 下新建或修改文件时，返回文本末尾包含结构化 `output_files:` 列表（相对 workspace 路径，排序）；未在 `outputs/` 下新建/修改文件时，不输出 `output_files:`（单元测试）
- [ ] `outputs/` 下指向 workspace 外的软链接不会被列为产出文件（单元测试）
- [ ] 真实运行验证（本地 `TestClient` 或 CLI/local sandbox 实跑一次验证产出文件输出）
- [ ] `make check` 通过

## Evidence required

单元测试输出、真实运行验证输出、`make check` 输出、两道审查结果（`da-review main` 与 Standards/Spec 审查）。

## Stop conditions

- 需要修改已有验收测试的断言才能过。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2 (da-review main: round 1 报 2 条 P2 已修，round 2 通过)
- Model spend: $0 (data-agent model calls only)
- Outcome: 已合并（PR #110，squash）；CI 全绿，Codex GitHub app 审查零问题。
- Human review minutes: —

## Comments

- 2026-11-21（工单会话，主会话抄录）：分支 `worktree-103-web-network-and-sandbox-outputs`，提交 `2c3a6cb`、`e666b03`。
  - **改动说明**：
    1. `src/data_agent/web/app.py`：`Submit` 与 `Message` 增加可选字段 `network: bool = True`；`POST /api/runs` 透传 `body.network` 给 `launcher.submit`，`POST /api/conversations/{conversation}/messages` 透传 `body.network` 给 `conversations.send`。
    2. `src/data_agent/sandbox.py`：新增 `outputs_snapshot()`（基于 `os.open(..., O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=...)` 与 `os.lstat(..., dir_fd=...)` 逐级遍历 `workspace/outputs/`，只记录真实普通文件的 `(mtime_ns, size, inode)`，不跟随任何软链接且杜绝 TOCTOU 目录替换越界）。`execute_tool()` 在命令执行前后对比快照，将新建或更新的文件按 POSIX 相对路径排序后传给 `reply_text()`；有产出文件时在末尾追加 `output_files:\n- outputs/...`（内联上限 `MAX_OUTPUT_FILES = 100` / `OUTPUT_FILES_CHARS = 4_000`，超出时截断并将完整清单写入 `sandbox_logs/<stem>.output_files.txt`）；无产出文件时不追加；超时路径（`done.timed_out`）同样附带已产出文件清单。
    3. 单元测试：`tests/test_web_submit.py`、`tests/test_web_conversations.py` 覆盖 `network` 省略（默认 `True`）、`False`、`True`；`tests/test_sandbox_tool.py` 覆盖新建/更新/未改动/无产出/子目录 POSIX 排序/指向 workspace 外文件与目录软链接、`outputs` 本身软链接、超时产出、TOCTOU 目录替换软链接回归测试、大清单截断落盘。
  - **真实运行验证（不调模型）**：
    - `LocalSandbox` + `execute_tool` 写 `outputs/summary.csv`：`exit_code: 0`、`stdout: done`、`output_files:\n- outputs/summary.csv`。
    - FastAPI `TestClient` 发送 `POST /api/runs` 与 `POST /api/conversations/{id}/messages` 携带 `network: false`：均返回 `201`，启动命令 `has_--network: False`。
  - **`make check`（`e666b03`）**：ruff check / format 通过，basedpyright 0 errors，pytest `2539 passed, 2 skipped`，web typecheck & test `295 passed`。
  - **本地审查（`da-review main`）**：Round 1（`2c3a6cb`）报 2 条 P2（TOCTOU 软链接竞态、`output_files` 无长度上限），均在 `e666b03` 修复并补测试；Round 2（`e666b03`）通过（No actionable regressions）。
  - **独立 Standards & Spec 审查（`e666b03`）**：0 issues found，5 条验收标准及安全/资源边界全部确认通过。
- 2026-11-21（主会话）：PR #110 在 `e666b03` 上两项 CI 全绿；评论 `@codex review` 后 Codex GitHub app 返回 "Didn't find any major issues"。已 `gh pr merge 110 --squash` 合入 `main`，工单 103 标为 `done`。
