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

## 推进原则

- 目标是尽快做出可用于求职的完整项目：能抄的直接抄，有上游实现的照上游做（对标与参考见 `HANDOFF.md` §4），引用具体文件。
- 决策都是可改的默认值，不是定稿。参考项目或常识能定的，agent 直接定、记一笔理由就往下做，不再拿出来逐条讨论；只把只有用户掌握的事（钱、账号）或确无先例的缺口交给用户，且同时给出一个能让工作继续的默认值。
