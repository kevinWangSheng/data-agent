# 149 — 网页：运行详情显示生效配置（哈希、配置文件名、可展开的完整配置）

Status: ready-for-agent
Blocked by: 146
Spec: ../spec.md（"生效配置的记录（Q6）"最后一条）
Claimed by: —
Branch: —

## Goal

网页运行详情读事件文件里的 `config` 事件（146 写），显示生效配置的哈希和参与合并的配置文件名，可展开看完整配置。没有 `config` 事件的旧运行照旧显示，标"未记录"。只改前端。

## Authority

- May edit：
  - `web/src/run-view/model.ts`：解析 `config` 事件（现在只认 `version` 等，`model.ts:354-365`；摘要 `:520-550`），加进 `RunView`/`RunSummary` 的类型。
  - `web/src/run-page/Side.tsx`（现在的 `config` 一行在 `Side.tsx:242-243`）或同目录新组件：显示哈希、文件名、展开的完整配置。
  - `web/src/run-view/*.test.ts`、`web/src/run-page/*.test.ts`（新增或修改单元测试，不是 `acceptance/` 目录）。
  - `tests/fixtures/` 下新增一份带 `config` 事件的样例事件文件（如测试需要；只带 `${VAR}` 引用）。
- Must not edit：`web/src/run-view/acceptance/`、`web/src/run-page/acceptance/`、`tests/acceptance/`；`src/`（后端：`config` 事件的字段由 146 定，本工单只读）；`web/src/local/`、`web/src/data/source.ts`（144 改表单）；`evals/`；其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none。
- Spend cap：$0（不调模型）。

## Context

行号以 main `e494e49` 为准。

- **事件从哪来**：网页经 `GET /api/runs/{run_id}/events`（`src/data_agent/web/app.py:120`）拿到整份事件，`config` 事件不需要后端新接口。运行列表的摘要只读前 8 行（`web/runs.py:21-22`），本工单不改列表。
- **字段**：以 146 合入的实现和 141 契约第 6 项为准（建议的是 `config`、`sha256`、`files`、`file_hashes`）。`config` 事件是第二条（`version` 之后，`decisions.md` Q8）；`version.config` 评测是层名、产品是配置文件名（没有文件时 `default`），现有的 `config` 一行照旧显示它。开工前读 146 的最终回复和 `agent.py` 里写 `config` 事件的地方，按真实字段写；字段和这里写的不同时照代码。
- **显示**：spec 只要求"显示哈希和配置文件名，可展开看完整配置"。哈希截短显示、完整值可复制；配置全文按 TOML 原文显示（密钥本来就是 `${VAR}` 原文）。现有的 `config` 一行（`Side.tsx:242-243`，消融名）保留。
- **在线回放**：运行页也用在在线回放（`web/src/site/`），回放的事件文件经过脱敏；没有 `config` 事件或字段缺失时不能报错。
- **前端工单**（AGENTS.md 工作流程末段，人 2026-11-17 定）：第 5 步不跑 `da-review`，只跑 `eng:code-review main`；第 7 步不等 Codex GitHub app，主会话派一个上下文干净的 Opus 5.5 子 agent（只读）审 PR 的 diff。
- 只用 pnpm。

## Acceptance

- [ ] 有 `config` 事件的运行：详情显示哈希、配置文件名，点开显示完整配置；有单元测试（喂手写事件）。
- [ ] 没有 `config` 事件的运行（旧运行、在线回放的样例）照旧显示，标"未记录"；有单元测试。
- [ ] 141 的验收测试和 `web/src/*/acceptance/` 照旧通过。
- [ ] `make check` 通过（含 web 的 typecheck 与 vitest）。

## Evidence required

`make check` 输出；新测试的 vitest 输出；一张截图：本机 `data-agent web` 打开一个有 `config` 事件的运行（可用 146 冒烟留下的事件文件，或测试夹具复制进 `--runs-dir`），展开前后各一张。截图由工单 agent 用 Playwright 自己拍（不调模型）。

## Stop conditions

- 146 的 `config` 事件没有配置文件名或哈希，要改后端才能显示。
- 要改 `acceptance/` 目录里的测试才能通过。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：前端工单，照前端工单的审查（不跑 `da-review`、不等 Codex GitHub app）。141 没给本工单写验收测试标记，验收靠本工单自己的单元测试和截图。
- 2026-11-27（主会话）：按 `decisions.md` Q8 补了 `config` 事件的位置和 `version.config` 的含义。
