# 55 — 守卫误拦和提示：开头几步不再被拦，拒绝提示给出正确写法

Status: in-progress
Blocked by: —
Spec: ../../../docs/research/2026-11-05-foundation-latency-audit.md（§6）
Claimed by: claude (worktree 55-guard-prompt-rejections, 2026-11-05)
Branch: worktree-55-guard-prompt-rejections

## Goal

t47-screen 里每个 trial 开头都会撞上的几类拒绝不再出现：模型知道该单独执行 `echo $DB_TYPE`、用 Glob/Read/Grep 看目录和文件；已经排除了凭据目录的 Grep 不再被拦；拒绝提示写出具体原因和正确写法。安全边界不变：不能写库、不能读凭据内容、不能离开工作区。

## 已和用户定下的（2026-11-05，grilling Q11、Q18）

- `workspace_guard` 按 ripgrep 语义识别 `!**/<dir>/**` 这类排除整个目录的 glob；被排除目录里的凭据文件不再拦这次 Grep。
- 拒绝提示写出具体原因和正确写法：点名命中的禁用字符（例如 `{`、`}`、`|`、`2>/dev/null`），`--vars` 建议 `--vars 'k: v'`；Grep 被拦时给示例 `type: "sql"` 或 `glob: "!**/dbt_packages/**"`。
- 系统提示词（`agent.py` 的 `DBT_PROMPT`）加一句：`echo $DB_TYPE` 要单独执行；看目录、读文件、搜索用 Glob/Read/Grep，不用 `ls`/`cat`。不写进 Skill，避开和工单 47 的冲突。
- `TASK_FILE_NOTE` 写明"用 Read 读 {path}"。

## Authority

- May edit: `src/data_agent/command_guard.py`（只改拒绝提示文字）、`src/data_agent/workspace_guard.py`（排除型 glob 的判定和拒绝提示）、`src/data_agent/agent.py`（只改 `DBT_PROMPT` 和 `TASK_FILE_NOTE`）、`tests/test_command_guard.py`、`tests/test_workspace_guard.py`、`tests/test_task_file.py`、新的测试文件
- Must not edit: `command_guard.py` 的放行规则（允许执行哪些命令不变）、`skills/`、`src/data_agent/agents/`、`src/data_agent/verifiers.py`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（真实运行由主会话跑）
- Spend cap: $0

## Context

t47-screen 15 个 trial 共拒绝 66 次：

| 模式 | 次数 | 原因 |
|---|---|---|
| `echo "DB_TYPE=$DB_TYPE"; echo DUCKDB_PATH; ls /app` | 15 | 题面要求 echo，模型合成一条；`command_guard.py:120` 只认单独一条，其余落到 `:131` 回"only a single command" |
| `ls -R … \| head`、`cat`、`cp` | 约 20 | `agent.py:137` 只说 Bash 只能跑 dbt，没说用什么代替；`2>/dev/null` 落到 `:131`，提示有误导 |
| Grep `path=/app` 或项目目录 | 18 | `dbt_packages/dbt_utils/integration_tests/.env/*.env` 触发 `workspace_guard.py:250-256`；有 2 次模型已加 `glob: "!**/dbt_packages/**"` 仍被拦：`_glob_matches`（`:131-133`）遇到结尾 `**` 返回 None，排除型 glob 被忽略（`:144-152`） |
| `--vars '{"k": "v"}'` | 1 | `{}` 在 `FORBIDDEN_CHARS`（`:46`），提示太笼统 |
| Glob/`cat` 任务文件 | 6 | `TASK_FILE_NOTE` 没写用什么工具；Read 只放行这个确切路径（`workspace_guard.py:196-199`） |

- 工单 40 定下的 Grep 规则按 ripgrep 语义判定（`glob` 按空白和逗号拆成多个 `--glob`，最后匹配的生效，包含型优先于 `--type`），本工单在这个框架里补上排除整个目录的情况。
- 数据：本机 `runs/t47-screen/foundation-audit/a5/`。

## Acceptance

- [ ] 新测试：带 `glob: "!**/dbt_packages/**"` 的 Grep，在凭据文件只出现在 `dbt_packages/` 下时放行（修复前会失败）；凭据在别处时仍然拦；按 ripgrep 语义，后面的包含型 glob 重新包含该目录时仍然拦。
- [ ] 新测试：各类拒绝提示含具体原因和正确写法（禁用字符、`--vars 'k: v'`、Grep 示例）。
- [ ] 测试断言 `DBT_PROMPT` 含单独 echo 和 Glob/Read/Grep 那句，`TASK_FILE_NOTE` 含"Read"。
- [ ] 现有守卫测试全部通过（放行和拒绝的集合除排除型 glob 外不变）。
- [ ] `make check` 通过。

## Evidence required

- 测试输出。
- 主会话的真实运行：服务器 `dbt-fix-daily-cohorts` × 1（full）。合成 echo、`ls -R`、Grep `/app` 这几类拒绝为 0，或明显少于 t47-screen 每 trial 的平均值（约 4.4 次）。命令和输出贴到 Comments。

## Stop conditions

- 必须放宽 `command_guard` 的放行规则，或者让凭据内容变得可读。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
