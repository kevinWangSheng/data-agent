# 58 — `da-remote exec`：透传任意命令到服务器，输出照样遮掉地址

Status: done
Blocked by: —
Spec: —（用户 2026-11-05 决定：不放开直接 ssh，改给 da-remote 加透传子命令）
Claimed by: claude (worktree 58-da-remote-exec)
Branch: worktree-58-da-remote-exec

## Goal

`da-remote exec -- <命令>` 在服务器仓库目录下执行任意命令，stdout/stderr 经过和其他子命令相同的 `mask()`，退出码原样返回。查服务器状态、看日志、跑一次性检查（例如工单 56 的只读检查）不再需要直接 ssh，AGENTS.md 和 `.claude/settings.json` 的 ssh 限制保持不变。

## Authority

- May edit: `scripts/da-remote`（加 `exec` 子命令和 Usage 一行）、`tests/test_da_remote.py`、`docs/setup.md`（da-remote 子命令列表若有）
- Must not edit: `AGENTS.md`、`.claude/`、`CODING_STANDARDS.md`、其他工单的文件、`~/.local/bin/`（装好的副本由主会话更新）
- Server access: none（真实运行由主会话跑）
- Spend cap: $0

## Context

- 现有连接方式：`host="${DA_REMOTE_HOST:-da-server}"`（`scripts/da-remote:27`）、`remote()`（`:51-55`，经 `mask()` `:44-49`，返回 `PIPESTATUS[0]`）、子命令分发 `case "$cmd"`（`:702` 起）。
- 照现有子命令的写法：`--` 之后的参数作为远端命令；在 `$dir`（`DA_REMOTE_DIR`，默认 `~/data-agent`）下执行；参数要正确转义（远端经 shell 解析），测试钉住带空格、引号、`;` 的参数不会被拆错或注入到本地。
- 测试接缝：`tests/test_da_remote.py` 现有的假 ssh 写法。

## Acceptance

- [ ] 新测试：`exec` 远端输出里的 IPv4、IPv6 地址被替换成 `<server>`（测试用文档保留段地址，例如 192.0.2.x、2001:db8::/32，照 `test_da_remote.py` 现有写法）。
- [ ] 新测试：远端退出码非 0 时 `da-remote exec` 返回同一个退出码。
- [ ] 新测试：带空格和引号的参数原样到达远端；命令在 `DA_REMOTE_DIR` 下执行。
- [ ] 新测试：缺 `--` 或没有命令时打印 usage、退出码 2。
- [ ] `make check` 通过。

## Evidence required

- 测试输出。
- 主会话真实运行：`da-remote exec -- git log --oneline -1` 和 `da-remote exec -- free -m`，输出贴到 Comments，确认无地址泄露。

## Stop conditions

- 需要改 AGENTS.md 或 `.claude/settings.json` 才能完成。
- 第二轮审查仍不过。

## Failure handling

Status `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: merged (PR #56 → 18e0c11)
- Human review minutes: —

## Comments

- 2026-11-05 ticket agent (commits a95f9e5, c674ee0):
  - Tests: `uv run pytest tests/test_da_remote.py -q -k 'exec or usage'` → 22 passed. `make check` → 1851 passed, 1 skipped (shellcheck isn't installed, so its test skipped; `uvx --from shellcheck-py shellcheck scripts/da-remote` was clean). New tests cover:
    - IPv4 and IPv6 masking (192.0.2.10 and 2001:db8::5), including stderr.
    - Remote exit codes 1, 3 and 42 coming back unchanged.
    - Arguments with spaces, quotes, `;`, `$(...)`, `*` and `""` arriving verbatim and running in `DA_REMOTE_DIR`.
    - The `~` default directory.
    - A command word starting with `-`.
    - A missing repo directory (exit 1).
    - Missing `--` or command: exit 2.
  - `da-review main`: no findings.
  - `eng:code-review main`:
    - Fixed in c674ee0: a command word starting with `-` was taken as an option to the shell's `exec` (now uses `exec --`, with a test that failed before the fix); test for the `~` default; header comment; usage and `docs/setup.md` note that stdin is closed and stderr is mixed into stdout, and that `exec` does not loosen the secret or holdout rules.
    - Not fixed:
      - The repo-directory check is duplicated with `start`'s. Two copies is small, so no shared helper.
      - Exit 255 can mean either ssh failed or the command exited 255. That comes from ssh and is documented.
      - The server-side read risk is controlled by the AGENTS.md rules (the user chose exec over direct ssh on 2026-11-05).
- 2026-11-05 main-session real run (worktree script, installed copy not overwritten):
  - `da-remote exec -- git log --oneline -1` → `b693d9a Merge remote-tracking branch 'origin/main' into worktree-47-verifier-prompt-skill-config`, rc=0. The first call printed nothing (rc=0); the second and later calls printed normally. Cause not confirmed (未确认).
  - `da-remote exec -- free -m` → `Mem: 7940 total / 1040 used / 6900 available; Swap: 4095 / 527 used`, rc=0.
  - `da-remote exec -- bash -c 'exit 7'` → rc=7.
  - No addresses leaked in any output.
  - Problem found: after about 12 ssh connections in a short burst, the server refused every connection with `Connection closed by <server> port 22` (rc=255). The existing `da-remote check` was refused the same way, so this is a server-side connection limit (MaxStartups or fail2ban, 未确认) and not a defect in exec. Ticket 56's read-only checks should be combined into one `exec -- bash -c '…'` call.
- PR #56: CI green; the Codex GitHub app reviewed c674ee0 with no findings. Squash-merged as 18e0c11.
