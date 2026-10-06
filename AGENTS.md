# data-agent

开源、可自托管的通用 data agent，求职作品集项目。会话间的可移植记录在 `HANDOFF.md`；
词汇表在 `CONTEXT.md`（存在时）；决策在 `docs/adr/`；取证笔记在 `docs/research/`。

## Agent skills

### Issue tracker

Local markdown：issues 与 spec 放在 `.scratch/<feature>/`（无 remote 的 solo 仓库）。See `docs/agents/issue-tracker.md`.

### Triage labels

默认五个角色标签，字符串与角色同名（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根目录 `CONTEXT.md` + `docs/adr/`。See `docs/agents/domain.md`.
