# coding agent 开发环境取证：说明文件、自检、权限与防泄密、hooks、并行、审查、MCP

日期：2026-10-13。方法：读上游源码与 Claude Code / Codex 官方文档。未核实处标 `未确认`。

commit：harbor@dc933be（只稀疏克隆 `docs`、`src`），ktx@49a4ae6，nao@5bde830，dash@6d86fca，SignalPilot@f5eee22；mattpocock skills 见 `~/dev/AI/agent-skills`。官方文档：code.claude.com/docs，learn.chatgpt.com/docs（`developers.openai.com/codex` 已跳转到这里）。

## 1. 说明文件

- harbor：`AGENTS.md` 391 行，`CLAUDE.md` 是指向它的 symlink；内容包括快速命令、目录树、核心概念、代码风格、CI 列表、"Common Tasks for AI Assistants"（`AGENTS.md:18-37,39-137,344-368`）。
- ktx：`AGENTS.md` 593 行，`CLAUDE.md`、`GEMINI.md` 都是 symlink；写明适用于 Codex/Claude/Gemini（`:3-6`）。内容包括 MUST / MUST NOT 规则（`:10-48`）、按改动面选检查（`:146-168`）、"When to Ask vs Act"（`:570-583`）、"Git and Worktree Safety"（`:585-593`）；设计原则放在 `docs/code-design.md`。
- nao：`CLAUDE.md`（51 行）和 `AGENTS.md`（37 行）是两份独立文件，后者只给 Cursor Cloud 用。dash：只有 `CLAUDE.md`（188 行）。SignalPilot：根目录没有，规范写在 `CONTRIBUTING.md`。
- Claude Code 官方：CLAUDE.md 控制在 200 行以内，只写从代码推断不出的内容；`@path` 导入的文件在启动时全部加载；只有 CLAUDE.md 不存在时才读 AGENTS.md，官方给的做法是在 CLAUDE.md 里写 `@AGENTS.md`（https://code.claude.com/docs/en/memory.md）。
- Codex 官方：从 git 根目录一路读到当前目录，每层最多一个 `AGENTS.md`，越靠近当前目录优先级越高；合计上限默认 32 KiB（https://learn.chatgpt.com/docs/agent-configuration/agents-md.md）。Codex 是否读 CLAUDE.md `未确认`。

## 2. 给 coding agent 的 skill、命令与子 agent

- 上游仓库都没有提交开发期 skill；ktx、dash、SignalPilot 反而把 `.claude/` 设为私有并 gitignore（ktx `AGENTS.md:98-100`，dash `.gitignore:63`，SP `.gitignore:4`）。唯一的例外是 nao 的 `.claude/skills/code-review/SKILL.md`，见第 6 节。
- Claude Code：项目 skill 放在 `.claude/skills/<name>/SKILL.md`，`.claude/commands/` 已经并入 skills；子 agent 放在 `.claude/agents/*.md`，可以设 `tools`、`disallowedTools`、`model`、`isolation: worktree`（https://code.claude.com/docs/en/skills.md，https://code.claude.com/docs/en/sub-agents.md）。
- mattpocock skills 默认环境里要有这些东西：
  - `/implement`：快速的类型检查命令，以及能按单个文件跑的测试命令（`implement/SKILL.md:9-15`）。
  - `/to-spec`、`/tdd`：事先约定好的测试接缝（`to-spec:15-17`，`tdd:22`）。
  - `/code-review`：`docs/agents/issue-tracker.md` 与 `CODING_STANDARDS.md`（`code-review:13,36-41`）。
  - `/retro`：机械性的规则一律做成检查，`CODING_STANDARDS.md` 只放需要判断的规则，`AGENTS.md` 只放指针（`retro:18-19,41-42`）。
  - `/handoff`：交接文件写在临时目录，不写进仓库（`handoff:8-14`）。

## 3. 自检回路

- 上游共识：说明文件里写死检查命令，要求每次改完都跑。
  - harbor：`ruff check --fix`、`ruff format`、`ty check`，默认只跑 `pytest tests/unit/`（`AGENTS.md:224,266`）；测试按 unit / integration / runtime 打标记（`:214-220`）；有 `oracle`、`nop` 两个测试用 agent（`:51-52`）。
  - ktx：按改动面挑最小的检查集（`:148-161`）；慢测试用 `tee` 落盘一次，之后反复看这个文件（`:163-168`）。
  - nao：`npm run lint` 加 `cli/Makefile` 里的 lint。dash：`scripts/validate.sh`。
- 不消耗 token 的测试替身：只有 SignalPilot 有，`Dockerfile.eval-stub` 用一个假的 Claude CLI 跑 e2e（`:1-6`）。所有上游都没有"录制并回放模型调用"的 fixture。
- 官方最佳实践：优先给 agent 能跑的验证（测试、构建、截图），可以用 Stop hook 做确定性门禁（https://code.claude.com/docs/en/best-practices.md）。

## 4. 权限、沙箱与防泄密

- 上游：`.env` 都被 gitignore，只提交 `.env.example`；ktx 用 pre-commit detect-secrets（`.pre-commit-config.yaml:66-92`），SignalPilot 在 CI 跑 trufflehog（`.github/security-controls.md:3-18`）；ktx 禁止 `git clean`、`reset --hard`、`stash`（`AGENTS.md:40-45`）。没有一个上游提交 `.claude/settings.json` 的权限规则或沙箱配置。
- Claude Code 的权限规则（https://code.claude.com/docs/en/permissions.md）：
  - 求值顺序是 deny、ask、allow，allow 不能给 deny 开例外。
  - `Read(./.env)` 能拦住：Read 工具、对这个路径的 Edit 和 Write，以及 Bash 里能识别出文件名的命令（`cat`、`head`、`tail`、`sed`、`tee`）和重定向。
  - 拦不住：不点名文件的命令（例如对目录 `grep -r`），以及子进程自己去打开文件。
  - 文档明确说 Bash 规则不是安全边界。
- Claude Code 沙箱（https://code.claude.com/docs/en/sandboxing.md）：
  - 机制：macOS 用 Seatbelt，Linux 用 bubblewrap。
  - 范围：只管 shell 命令，Read/Edit、MCP、hooks 不在沙箱里。
  - 默认：能读机器上大部分文件，包括 `~/.ssh`；网络默认不能出站。
  - 要限制读取，用 `sandbox.filesystem.denyRead` 或 `sandbox.credentials`；`allowUnsandboxedCommands: false` 禁止退出沙箱。
- 设置文件优先级：`.claude/settings.local.json` 高于 `.claude/settings.json` 高于 `~/.claude/settings.json`（https://code.claude.com/docs/en/settings.md）。
- Codex：
  - 沙箱有 `read-only`、`workspace-write`、`danger-full-access` 三种；审批策略有 `on-request`、`never`、`granular`（https://learn.chatgpt.com/docs/agent-approvals-security.md）。
  - 按路径禁止读取：permissions profile 写 `"deny"`（beta），只对本地沙箱里执行的命令生效（https://learn.chatgpt.com/docs/permissions.md）。approvals 页又说不能按文件拒绝读取，两页说法冲突，`未确认`，需要本机实测。
  - 项目级 `.codex/config.toml` 只在仓库被信任时加载。
  - 本机现状：`~/.codex/config.toml` 是 `danger-full-access` + `never`。
- `ssh` 和 `denyRead ~/.ssh` 能否共存：官方没说明，`未确认`。让 agent 通过主机别名去服务器跑评测，需要单独设计。

## 5. hooks

- 上游：没有任何 Claude hooks。git hooks 有：nao 用 husky pre-commit（lockfile、lint、format、migration，`.husky/pre-commit:1-7`），ktx 用 pre-commit。
- Claude Code（https://code.claude.com/docs/en/hooks.md）：
  - PreToolUse 退出码 2 会拦下这次工具调用，且不能绕过 deny 规则。
  - PostToolUse 用 `matcher: "Edit|Write"`，可以在改完文件后自动格式化。
  - Stop 退出码 2 会让 agent 继续干活。
  - 连续拦截的次数上限 `未确认`。

## 6. 多个 agent 并行

- 上游：只有 ktx 有并行方案，用 Conductor 的 workspace（`conductor.json`、`scripts/conductor-setup.sh:25-37`）；ktx 还规定提交前看 `git status --short`、不回滚别人的改动（`AGENTS.md:587-590`）。SignalPilot 只定了分支命名、Conventional Commits、squash 合并（`CONTRIBUTING.md:213-259`）。
- Claude Code（https://code.claude.com/docs/en/worktrees.md）：
  - `claude -w <name>` 会建 `.claude/worktrees/<name>/` 和分支 `worktree-<name>`；子 agent 可以设 `isolation: worktree`。
  - 被 gitignore 的文件要靠 `.worktreeinclude` 复制进去，不要把 `.env` 放进去。
  - 在 worktree 里点"不再询问"，会写进主 checkout 的 `settings.local.json`。

## 7. 审查

- nao 的 `/code-review` 流程（`.claude/skills/code-review/SKILL.md:14-55`）：
  - haiku 先做预检。
  - 3 个审查者并行：1 个查是否符合 CLAUDE.md，2 个 Opus 找 bug。
  - 每条问题再派子 agent 复核，只留高信号的。
  - nao 还要求 PR 写明所用的模型 id（`CONTRIBUTING.md:40-47`）。
- harbor：CI 里有 `claude-code-review.yml`（`AGENTS.md:274-275`），内容 `未确认`（`.github` 没克隆下来）；它要求文档、PR 描述、issue 由人写（`CONTRIBUTING.md:11-20`）。
- mattpocock `/code-review`：对 `git diff <fixed>...HEAD` 做 Standards 和 Spec 两个子 agent 并行审查。
- Codex：
  - `/review` 是只读审查，范围可以选 base 分支、未提交改动或某个 commit。
  - 用哪个模型由 `review_model` 设定（https://learn.chatgpt.com/docs/code-review.md）。
  - `codex exec` 默认只读，可加 `--json`、`-o`（https://learn.chatgpt.com/docs/non-interactive-mode.md）。
  - 本机 0.162.1 有 `codex review` 子命令，但官方文档没写，`未确认`。
- Codex 模型（https://learn.chatgpt.com/docs/models.md）：`gpt-6-astra`（最强）、`gpt-6.1-sol`（接近 Astra，成本更低）、`gpt-6-luna`（高吞吐）。推理强度可选 `low` 到 `ultra`，具体可用取决于模型。

## 8. 开发期 MCP

- 上游：ktx 要求用 Context7 查外部文档（`AGENTS.md:561-568`）；harbor 规定 Supabase 优先用 CLI，不用 MCP（`:382`）；SignalPilot 的 `.mcp.json` 是空的。
- Claude Code：项目级 `.mcp.json` 支持 `${VAR}`；交互模式第一次使用要批准，`claude -p` 直接加载（https://code.claude.com/docs/en/mcp.md）。

## 没有上游做过的

提交进仓库的权限与沙箱配置、Claude hooks、devcontainer、worktree 并行约定（只有 ktx 用 Conductor）、PR 模板、录制回放模型调用的测试 fixture、`CODING_STANDARDS.md` 以及从 spec/工单到审查的打通（只有 mattpocock skills 提供）。
