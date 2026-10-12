# data-agent

开源、可自托管的通用 data agent，求职作品集项目。会话间的可移植记录在 `HANDOFF.md`；
词汇表在 `CONTEXT.md`（存在时）；决策在 `docs/adr/`；取证笔记在 `docs/research/`。

## Agent skills

### Issue tracker

Local markdown：issues 与 spec 放在 `.scratch/<feature>/`（solo 仓库，issues 不放 GitHub）。See `docs/agents/issue-tracker.md`.

### Triage labels

默认五个角色标签，字符串与角色同名（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根目录 `CONTEXT.md` + `docs/adr/`。See `docs/agents/domain.md`.

## 规划、资源与环境

大致规划与讨论进度见 `docs/plan.md`（可改，不是规则）；资源与环境见 `docs/setup.md`。

## 推进原则

- 目标是尽快做出可用于求职的完整项目：能抄的直接抄，有上游实现的照上游做（对标与参考见 `HANDOFF.md` §4），引用具体文件。
- 有上游先例、能直接定的小决策不要反复拷问，给出推荐和依据即可。
- 但"怎么构建"的内容（全局设计、开发流程、spec、工单、文档）先和用户逐项讨论、确认后再落文件；不要一上来就产出。一次讨论一个主题。
