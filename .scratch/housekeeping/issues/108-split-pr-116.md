# 108 — 把混进 PR #116 的工单记录拆到 main

Status: done
Blocked by: —
Spec: 无（人 2026-11-22 定，见 Comments）
Claimed by: 编排会话（2026-11-22）
Branch: —（直接在主 checkout 的 `main` 上提交，再给 PR #116 的分支追加一个提交）

执行者：编排会话（主会话），在主 checkout 里做，不用 `claude -p -w`。原因：要在 `main` 上提交工单记录，还要往 PR 分支 push，按 AGENTS.md 这两件事只由主会话做。

## Goal

PR #116（`fix/eval-page-dab-release`）的 diff 里只有 `web/src/eval-page/EvalPage.tsx`。工单 105 的 wontfix 记录、`HANDOFF.md` 的已知限制改动、新工单 107 和"第 3 项上下文管理待对齐"的记录，作为一个单独的提交进 `main`。

## Authority

- May edit: `HANDOFF.md`、`.scratch/dab-gap/issues/105-analysis-rules.md`、`.scratch/dab-gap/issues/107-tool-server-stderr.md`（只提交，不改内容）、本工单文件
- May run（人 2026-11-22 批准）：在 `main` 上提交并 push 到 `origin/main`；checkout `fix/eval-page-dab-release`，追加一个提交并普通 push 到同名远端分支
- Must not：改写 `9fc1448` 或任何历史、force-push、`git reset --hard` / `git stash` / `git checkout .`；改 `web/`；合并 PR #116（照前端工单流程另做）
- Server access: none
- Spend cap: $0

## Context

- 怎么混进去的：对齐会话按规矩在主 checkout 里改工单 105 和 `HANDOFF.md`，没有提交；同一时间另一个会话在同一个工作区建了 `fix/eval-page-dab-release`，提交 `9fc1448` 时把工作区里所有改过的文件都带上了。现在 `main`（`dc4a652`）上没有这两处改动，PR #116 open，远端分支就是 `9fc1448`。
- 要进 `main` 的内容：
  1. 工单 105 和 `HANDOFF.md`：内容照 `git show 9fc1448 -- .scratch/dab-gap/issues/105-analysis-rules.md HANDOFF.md`，用补丁搬过来：`git show 9fc1448 -- .scratch/dab-gap/issues/105-analysis-rules.md HANDOFF.md | git apply`。不要用 `git checkout 9fc1448 -- <路径>` 整个覆盖：`main` 上的这两个文件之后可能又有别的改动。补丁打不上时，照 `git show` 的内容手工改这两处，结果和补丁一样即可，不用停下。
  2. `.scratch/dab-gap/issues/107-tool-server-stderr.md`：主 checkout 里未跟踪的文件，原样提交。如果 107 已经被认领，`Status:` 等字段以当时文件为准，不要改回去。
  3. `HANDOFF.md` 2026-11-22 收尾节"之后的建议顺序"下面加一行：
     `- 2026-11-22 对齐会话：下一批是工单 107（DAB 工具服务器 stderr，不花模型钱）和第 3 项上下文管理（每轮都有撞上下文上限没答案的 trial；还没 spec，先对齐；建议第一步只读轨迹诊断，不花模型钱）。duckdb/postgres Skill 的 ROW_NUMBER 人定 wontfix（工单 105 Comments）。`
- PR #116 分支要追加的提交：把这两个文件恢复成 `dc4a652` 的版本（`git checkout dc4a652 -- .scratch/dab-gap/issues/105-analysis-rules.md HANDOFF.md`；这个分支上 `9fc1448` 是唯一改过这两个文件的提交，所以这里可以整个覆盖），提交说明写明"移出误带的工单记录，已在 main 的 <提交号> 单独提交"。

## Acceptance

- [ ] `git show origin/main --stat` 只有上面那 3 个文件，加上本工单文件（写好 Comments、Status 改 `done` 后一起提交），没有别的。
- [ ] `gh pr diff 116 --name-only` 只列出 `web/src/eval-page/EvalPage.tsx`。
- [ ] `main` 上工单 105 末尾有 wontfix 记录，`HANDOFF.md` 有已知限制的改动和新加的那一行。
- [ ] 主 checkout 最后停在 `main`；做之前工作区里别人未提交的改动原样还在。

## Evidence required

贴出上面每条检查的命令和真实输出，以及两个新提交的提交号。

## Stop conditions

- 开始前先看 `git branch --show-current` 和 `git status --short`：主 checkout 不在 `main` 上，或者有不属于本工单的已跟踪改动，就停下问人，不要切分支，也不要动这些改动。
- PR #116 已经合并：只确认 `main` 上有这些内容，再补上第 3 条那一行，不再动分支。
- push 被拒绝（远端有新提交）：停下交回，不 force。

## Failure handling

Status 改 `ready-for-human`，Comment 写明做到哪一步、哪条命令失败了、两个分支现在各自的提交号。

## Ledger

- Attempts: 1
- Review rounds: 0（只搬记录、不改代码，不走两道审查）
- Model spend: $0
- Outcome: done（走 Stop conditions 的「PR #116 已经合并」分支）
- Human review minutes: —

## Comments

- 2026-11-22（对齐会话）：人定拆开，要求把这件事做成工单，另开会话执行；PR 分支上追加一个提交、不改写历史，这个做法已获批准。
- 2026-11-22（编排会话）：开工时 PR #116 已经 squash 合入（7a686f9，`gh pr view 116` 的 files 只有 `web/src/eval-page/EvalPage.tsx`；合入前那个会话已追加提交移出误带的两个文件）。按 Stop conditions 不再动分支，只在 `main` 上提交：工单 105 的 wontfix 记录和 `HANDOFF.md` 已知限制的改动（主 checkout 里未提交的改动，内容与 9fc1448 相同）、工单 107（原样）、`HANDOFF.md` 第 3 条那一行、本工单文件。
