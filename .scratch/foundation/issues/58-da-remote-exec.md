# 58 — `da-remote exec`：透传任意命令到服务器，输出照样遮掉地址

Status: ready-for-agent
Blocked by: —
Spec: —（用户 2026-11-05 决定：不放开直接 ssh，改给 da-remote 加透传子命令）
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
