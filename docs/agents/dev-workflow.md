# Agent 开发流程

谁干什么、一张工单怎么从开始走到合并。Claude Code 与 Codex 都读 `AGENTS.md`，本文由它指向。

## 角色

| 角色 | 由谁 | 负责 |
|---|---|---|
| 人 | 你 | 定方向、准备资源（`docs/setup.md`）、看结果、做最终验收；不在 agent 之间传话 |
| 实现 | Claude Code，Opus 5.5（`claude-opus-5-5`） | 规划、写 spec/工单、按工单 TDD 实现、跑检查与基准、处理审核意见、提交 |
| 审核 | Codex CLI，`gpt-6-astra` | 每张工单合并前做一次独立的跨模型代码审核；只审不改 |

实现 agent 自己调用审核（`codex review` 非交互），不需要人转述意见。`gpt-6-astra` 出现在本机 Codex 的模型列表里，账号能否调用未确认，工单 01 首次审核时验证；不可用就退回 Codex 默认模型。

## 一张工单的流程

1. **开新上下文**：`/clear` 后运行 `/eng:implement .scratch/phase1/issues/NN-<slug>.md`。工单自带全部上下文，上一张的对话可以丢。
2. **开分支**：`git switch -c tNN-<slug>`（从 `main`）。
3. **TDD**：按 `eng:tdd` 一次一个红绿切片。
4. **本地检查**：`uv run pytest`、`uv run ruff check`、`uv run ruff format --check` 全绿。
5. **审核**：
   - `eng:code-review`（`/implement` 收尾自带）：对照仓库规范和工单验收条件。
   - `codex review --base main -c model="gpt-6-astra"`：跨模型独立审核。
   - 确认的问题就修；不同意的写理由进工单 `## Comments`，不静默忽略。
6. **基准验收**（工单要求时）：在 Linux 服务器上 `git pull` 后跑 Harbor，用 `run_in_background`；分数、token、成本写进工单 Comments。
7. **合并**：fast-forward 合进 `main`，工单改为 `Status: done`，推到私有 remote。

工单之间按 `Blocked by:` 顺序；03 / 04 / 05 无相互依赖，可以并行（各开一个 worktree）。

## 规则

- 不改工单范围外的东西；发现新问题就在 `.scratch/phase1/issues/` 开新工单，`Status: needs-triage`。
- 决策按 `AGENTS.md`「推进原则」：能定的直接定并写进 ADR 或工单 Comments。
- 密钥只走环境变量 / `.env`，不进 git、不进日志、不进轨迹。
- 评测纪律：留出集的失败细节在开发期不看（ADR-0004）；不写针对基准的 prompt（ADR-0005）。
- 单题运行设轮数上限，防止跑飞烧钱；具体值在 01 实测后定。
- 评测产物：分数与成本的汇总（小 JSON/CSV）进 git 的 `results/`；完整轨迹和 Harbor job 目录留在服务器，上榜时上传 Harbor Hub，不进 git。
