# data-agent

开源、可自托管的通用 data agent，求职作品集项目。本文件适用于所有在本仓库工作的 coding agent（Claude Code、Codex 等）；`CLAUDE.md` 只是导入本文件。

规划与讨论进度：`docs/plan.md`（可改，不是规则；第 6 节是开发模式）。词汇表：`CONTEXT.md`。决策：`docs/adr/`。取证笔记：`docs/research/`。资源：`docs/setup.md`。会话交接：`HANDOFF.md`。

## 命令

```bash
uv sync                 # 装依赖（只用 uv，不用 pip）
make check              # 快检查：ruff、格式、basedpyright、单元测试；不联网、不调模型
make fmt                # 自动修 lint 与格式
make test-integration   # 真调模型的集成测试；手动跑，密钥只交给测试进程
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

1. 认领：工单 `Status: ready-for-agent` 且 `Blocked by:` 都已 `done` 才能领；先把 `Status:` 改成 `in-progress`、填 `Claimed by:` 和 `Branch:` 并保存，再做别的。
2. 先跑测试拿基线，再在 spec 约定的测试接缝上 TDD 实现（`/implement`）。
3. `make check` 通过后，真实运行一次改动的行为，把命令和真实输出贴进工单 `## Comments`。
4. 换模型审查（Codex），修复最多 2 轮；还不过就停下。
5. 填工单 `## Ledger`，`Status:` 改成 `in-review`，提交到本工单分支。合并由人做。

停下交回时：`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。工单格式见 `docs/agents/ticket-template.md`。

## 硬规矩

- **MUST**：改文件前先读；只改工单 `Authority` 允许的范围。
- **MUST**：新行为配测试；bug 修复配一个修复前会失败的测试。
- **MUST**：所有对数据源的 SQL 走只读网关；唯一的写方式是改 dbt 项目后 `dbt run`。
- **MUST**：从上游复制的文件保留原版权头，并登记到 `NOTICE`。
- **MUST NOT**：改 spec 约定的验收测试来让它变绿；觉得验收测试错了就停下交回。
- **MUST NOT**：在 Skill 里写针对某个榜单题目的内容（ADR-0005）。
- **MUST NOT**：查看或打印留出集的逐题失败细节；留出集只看汇总分数（ADR-0004）。
- **MUST NOT**：读 `.env*`、`~/.ssh`、密钥文件；在代码、文档、测试、运行产出里写密钥或服务器 IP。连服务器只用 `da-remote`，不直接执行 `ssh` / `scp` / `rsync`。
- **MUST NOT**：用 `pip`（用 `uv`）、`npm`（用 `pnpm`）。

## Git 与 worktree

- **MUST NOT**：`git reset --hard`、`git clean`、`git checkout .`、`git stash`、`git push --force`，除非用户明确要求这条命令。
- **MUST NOT**：合并到 main、push、提交到榜单；这些由人做。
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

审查者读 `CODING_STANDARDS.md`（只放需要判断的规则；能自动检查的都在 `make check` 和 pre-commit 里）。

## 和用户协作

- 目标是尽快做出可用于求职的完整项目：能抄的直接抄，有上游实现的照上游做（对标与参考见 `HANDOFF.md` §4），引用具体文件。
- 有上游先例、能直接定的小决策不要反复拷问，给出推荐和依据即可。
- 但"怎么构建"的内容（全局设计、开发流程、spec、工单、文档）先和用户逐项讨论、确认后再落文件；不要一上来就产出。一次讨论一个主题。
