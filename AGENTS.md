# data-agent

开源、可自托管的通用 data agent，求职作品集项目。本文件适用于所有在本仓库工作的 coding agent（Claude Code、Codex 等）；`CLAUDE.md` 只是导入本文件。

规划与讨论进度：`docs/plan.md`（可改，不是规则；第 6 节是开发模式）。词汇表：`CONTEXT.md`。决策：`docs/adr/`。取证笔记：`docs/research/`。资源：`docs/setup.md`。会话交接：`HANDOFF.md`。

## 命令

```bash
uv sync                 # 装依赖（只用 uv，不用 pip）
make check              # 快检查：ruff、格式、basedpyright、单元测试；不联网、不调模型
make fmt                # 自动修 lint 与格式
make test-integration   # 真调模型的集成测试；手动跑，需要环境里有 ~/.config/data-agent/env 的变量
da-review main          # Codex 换模型审查本分支（只读）
uv run pytest tests/test_x.py -q   # 只跑一个测试文件
```

- 每次改完代码都跑 `make check`；提交时 pre-commit 会再跑格式、lint、大文件拦截、密钥与公网 IP 扫描。
- 按改动面挑最小检查集；慢命令的输出用 `tee` 存成文件，之后反复看文件，不重复跑。

## 目录

```text
src/data_agent/   agent 本体（CLI、只读网关、MCP 工具、事件流）
skills/           给 data agent 用的 SKILL.md（不是给 coding agent 的）
evals/            评测脚本：抽样、批量运行、算分、报告
tests/            单元测试；tests/fixtures/ 放小样本数据
web/              网页界面（以后）
.scratch/<feature>/   spec 与工单（本地 issue tracker）
docs/             plan、ADR、research、agents/（tracker 约定、工单模板）
runs/             运行产出，不进 git
```

## 工作流程

照 `docs/plan.md` 第 6 节。一个工单一个 agent、一个 worktree：

工单文件只在主 checkout（`git worktree list` 第一行）里改，用绝对路径；工单分支不提交 `.scratch/*/issues/`，这样所有会话看到同一份状态。工单 agent 只在认领时改工单文件；之后的 Comments、Ledger、状态写进最终回复，由主会话抄进工单（`-w` 会话有时被拦着写主 checkout）。

开工：`claude -p -w NN-<slug> --permission-mode auto "/implement <工单绝对路径>"`（人或主会话启动均可）。`-w` 从远端 `origin/main` 建 worktree，所以派工前本地 main 不能有未推送的提交。

1. 认领：工单 `Status: ready-for-agent` 且 `Blocked by:` 都已 `done` 才能领；先把 `Status:` 改成 `in-progress`、填 `Claimed by:` 和 `Branch:` 并保存，再做别的。
2. 先跑测试拿基线，再在 spec 约定的测试接缝上 TDD 实现（`/implement`；它原文里的审查和提交顺序以本节为准）。
3. `make check` 通过后，真实运行一次改动的行为，记下命令和真实输出（第 6 步交给主会话）。
4. 提交到本工单分支。先提交再审查：两道审查都只看已提交的 diff。
5. 两道审查：先 `da-review main`（Codex，只读，换模型找 bug）；再开新上下文跑 `eng:code-review main`（点名这个，不用内置的 `code-review`；Standards、Spec 两个子 agent，对照本工单验收标准）。每条结果要么修（修完提交），要么写明不修的理由（第 6 步交给主会话）；修复最多 2 轮，还不过就停下。
6. 在最终回复里给出要写进工单的 `## Comments`（第 3 步的命令与输出、审查结果与不修的理由）、`## Ledger` 和新 `Status:`（通常 `in-review`）；主会话抄进工单。push 和合并由主会话做（GitHub PR，squash），每次由人在权限确认里批准。
7. 合并前（主会话做）：PR 的 CI 绿后，读 Codex GitHub app 在 PR 上的意见（每次 push 自动审查；新提交没出审查时评论 `@codex review`），逐条对照代码核实。属实的写成"退回：原因"交回原工单，不属实的在工单 Comments 写理由。每个 PR 最多退回 2 轮；之后的新意见记成已知限制，属于后续工单的写进那个工单的验收标准。

停下交回时：最终回复写明 `Status: ready-for-human` 和 Comment（试了什么、哪里失败、分支名），由主会话抄进工单。被退回时（Comments 里有"退回：原因"，Status 回到 `ready-for-agent`）在原分支上接着修。工单格式见 `docs/agents/ticket-template.md`。

## 硬规矩

- **MUST**：改文件前先读；只改工单 `Authority` 允许的范围。
- **MUST**：新行为配测试；bug 修复配一个修复前会失败的测试。
- **MUST**：所有对数据源的 SQL 走只读网关；唯一的写方式是改 dbt 项目后 `dbt run`。
- **MUST**：从上游复制的文件保留原版权头，并登记到 `NOTICE`（MIT 和 Apache-2.0 都要求保留版权与许可声明；上游自带 `NOTICE` 的，如 data-eng-bench，相关内容一并带上）。
- **MUST NOT**：从没有许可证的仓库（DataAgentBench）或 nao 里标 `@license Enterprise` 的文件复制代码，只能参考思路。
- **MUST NOT**：改 spec 约定的验收测试来让它变绿；觉得验收测试错了就停下交回。唯一例外：本工单让某个验收测试转绿时，删掉它的 `xfail` 标记，其余一字不改。
- **MUST NOT**：在 Skill 里写针对某个榜单题目的内容（ADR-0005）。
- **MUST NOT**：查看或打印留出集的逐题失败细节；留出集只看汇总分数（ADR-0004）。
- **MUST NOT**：读 `.env*`、`~/.config/data-agent/`、`~/.ssh`、密钥文件；在代码、文档、测试、运行产出里写密钥或服务器 IP。连服务器只用 `da-remote`，不直接执行 `ssh` / `scp` / `rsync`。
- **MUST NOT**：用 `pip`（用 `uv`）、`npm`（用 `pnpm`）。

## Git 与 worktree

- **MUST NOT**：`git reset --hard`、`git clean`、`git checkout .`、`git stash`、`git push --force`，除非用户明确要求这条命令。
- **MUST NOT**（工单 agent）：push、合并到 main。主会话可以 push 和 `gh pr merge --squash`，`.claude/settings.json` 的 ask 规则让人逐次批准。任何会话都不提交到榜单，这由人做。
- 提交前看 `git status --short`，只提交本工单的文件；不回滚不是自己改的文件。
- 每个 worktree 用自己的 DuckDB 文件、Postgres 库或端口、`runs/` 目录，不和别的 worktree 共用。

## Agent skills

### Issue tracker

Local markdown：issues 与 spec 放在 `.scratch/<feature>/`（solo 仓库，issues 不放 GitHub）。See `docs/agents/issue-tracker.md`.

### Triage labels

五个角色标签（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`），外加工作状态 `in-progress` / `in-review` / `done`。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根目录 `CONTEXT.md` + `docs/adr/`。See `docs/agents/domain.md`.

### Code review

审查者读 `CODING_STANDARDS.md`（只放需要判断的规则；能自动检查的都在 `make check` 和 pre-commit 里），并对照本分支对应工单（`.scratch/*/issues/` 里 `Branch:` 行）的验收标准；只报问题、按严重程度排序、带 `file:line`，不改文件。

## 和用户协作

- 目标是尽快做出可用于求职的完整项目：能抄的直接抄，有上游实现的照上游做（对标与参考见 `HANDOFF.md` §4），引用具体文件。
- 有上游先例、能直接定的小决策不要反复拷问，给出推荐和依据即可。
- 但"怎么构建"的内容（全局设计、开发流程、spec、工单、文档）先和用户逐项讨论、确认后再落文件；不要一上来就产出。一次讨论一个主题。
