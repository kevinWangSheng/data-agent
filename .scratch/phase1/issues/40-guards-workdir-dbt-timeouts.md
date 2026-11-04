# 40 — 守卫与工作目录：允许进项目目录跑 dbt、打印 DB_TYPE、Grep 不被样例 .env 误拦，dbt 解析不再被 60 秒掐断

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 40-guards-workdir-dbt-timeouts)
Branch: worktree-40-guards-workdir-dbt-timeouts

## Goal

agent 能按题目要求打印 `DB_TYPE`；能在 dbt 项目目录里跑 dbt，不用为了路径去改 `profiles.yml`；能 Grep dbt 项目；validate 不会因为一次完整重新解析就超时；Bash 超时不会比 validate 先到，也就不会留下占着锁的 dbt 进程。安全边界不变：不能写数据库，不能读凭据文件的内容，不能离开 workspace。

## 已和用户定下的（2026-11-04）

- 类别：**确定性修复**（ADR-0008 Update 2026-11-04）。验证方式是单元测试，加一次单题真实运行；会改变 agent 的绕路方式（accept-2 里 97/180 个 trial 改过 `profiles.yml`），效果到统一评估里看。
- **`cd`**：只放开一种写法，`cd <目录> && <一条本来就允许的 dbt 命令或 dbt-workflow 脚本>`。
  - `<目录>` 按 workspace 根解析，跟随符号链接之后必须还在 workspace 里，不能 `cd` 出去。
  - 单独一条 `cd` 仍然拒绝。因此 cwd 始终是 workspace 根，守卫不需要读 hook 输入里的 `cwd`。
  - 拒绝时的提示要写出允许的写法。
- **环境变量**：只允许 `echo $DB_TYPE` 和 `printenv DB_TYPE` 这两条完整命令。白名单是代码里的一个常量，目前只有 `DB_TYPE`，由 harness 传给守卫。其他含 `$` 的命令照旧拒绝。依据：data-eng-bench 103 道题里有 94 道要求 `echo $DB_TYPE`，没有题要求打印别的变量。
- **Grep 凭据规则**：目录里有凭据文件时，只有这次 Grep 真的会搜到这些文件，才拒绝。
  - Claude Code 的 Grep 带 `--hidden` 调 ripgrep，会搜隐藏文件，并遵守 `.gitignore`。
  - 判断规则：Grep 的 `glob` 参数，或者 `type` 参数（只认 `sql`、`yaml`、`py`、`md`、`json`、`csv`、`txt`，按 ripgrep 的定义展开成文件名模式）能排除目录下所有凭据文件，就放行；否则拒绝。拒绝提示里列出找到的凭据文件路径，并建议加 `glob` 或 `type`。
  - `path` 本身是凭据文件，照旧拒绝。
- **Claude Code 存下来的超长输出**：允许 Read 和 Grep `~/.claude/projects/<任意>/tool-results/` 下的文件。这些文件是我们自己工具的输出，不含凭据；Write 和 Edit 照旧不允许。
- **dbt 解析与超时**：
  - `validate_project.py` 的默认超时从 60 秒改为 300 秒。上限钳制（`:70`，300 秒）不变。
  - harness 通过 SDK 的 `env` 设置 `BASH_DEFAULT_TIMEOUT_MS=360000`、`BASH_MAX_TIMEOUT_MS=600000`。Bash 默认超时 360 秒，比 validate 的上限长 60 秒。
  - **不修** target 进了 `vars_hash` 引起的完整重新解析。审查已确认，镜像用 `--target dev` 构建、题目又要求改 profile，这两件都会触发完整重新解析，避免不了（`audit-harness.md:105-116`）。只确保它不会被掐断。
  - `validate_project.py` 和上游逐字节一致。改动后要在文件开头加注释，写明来源和改了什么，并把 `NOTICE` 第 34–35 行的 "unmodified" 改成 "modified as noted at its top"。

## Authority

- May edit：`src/data_agent/command_guard.py`、`src/data_agent/workspace_guard.py`、`src/data_agent/agent.py`（只改 SDK `env` 和给守卫传白名单；2026-11-04 退回后另允许：在 `run()` 校验 `--postgres-env` 的变量名）、`skills/dbt-workflow/validate_project.py`、`NOTICE`、`tests/`（`tests/acceptance/` 除外）
- Must not edit：`skills/*/SKILL.md`、`src/data_agent/mcp_server.py`、`src/data_agent/configs/`、`evals/`、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none（服务器上的真实运行由主会话做）
- Spend cap：$0.3（单题真实运行）

## Context

- 证据：`runs/accept-2/reading/design-audit/audit-harness.md` 第 4–6 条。守卫共拦 757 次：Bash 拦 `echo $DB_TYPE`，180 个 trial 里每个都有；Grep 在 dbt 项目上被拦 205 次；超长输出存下的文件被拦 41 次。validate 91 次调用里有 18 次在 60 秒超时。
- 凭据误判的来源：dbt_utils 自带 `dbt_packages/dbt_utils/integration_tests/.env/{bigquery,postgres,redshift,snowflake}.env`，`_holds_credentials`（`workspace_guard.py:70-75`）用 `os.walk` 找到它们，于是整个项目目录都不能 Grep。
- 守卫现状：`command_guard.py:44,46` 把 `$`、`&&`、`;` 等列为禁止字符；`:118` 拒绝 `cd`；`:128` 按 workspace 根解析路径；`:148-149` 禁止 `run_in_background`。
- Bash 超时（Claude Code 打包的 CLI）：默认 120 秒，上限 600 秒，环境变量 `BASH_DEFAULT_TIMEOUT_MS`、`BASH_MAX_TIMEOUT_MS`。超时后命令会被转到后台，dbt 子进程不会被杀掉。SDK 的 `env` 在 `agent.py:665`，`sdk_env()` 在 `:526-538`。
- profile 路径：镜像里的 profile 写的是 `path: '../database/retail.duckdb'`，只有在项目目录里运行才对。

## Acceptance

- [ ] 单元测试（`tests/test_command_guard.py`、新的或现有的 workspace_guard 测试）：
  - [ ] `cd dbt_models_duckdb && dbt run --select x` 放行；`cd .. && dbt run`、`cd /tmp && dbt run`、指向 workspace 外的符号链接目录，都拒绝；单独 `cd x` 拒绝；`cd x && rm -rf y` 拒绝。
  - [ ] `echo $DB_TYPE`、`printenv DB_TYPE` 放行；`echo $HOME`、`echo $DB_TYPE; ls`、`printenv` 不带参数，都拒绝。
  - [ ] 目录里有 `.env/x.env` 时：Grep `glob="*.sql"` 放行，`type="sql"` 放行，不带过滤拒绝，并且提示里列出 `.env/x.env` 的路径。
  - [ ] Read `~/.claude/projects/p/tool-results/r.txt` 放行；Write 到同一路径拒绝。
  - [ ] SDK options 的 env 里有 `BASH_DEFAULT_TIMEOUT_MS=360000` 和 `BASH_MAX_TIMEOUT_MS=600000`。
  - [ ] `validate_project.py` 不传超时参数时，默认 300 秒（测试里 mock subprocess）。
- [ ] 原有守卫测试全部通过：写数据库、读 `.env` 内容、离开 workspace，照旧被拦。
- [ ] `tests/acceptance/` 原样全部通过。
- [ ] `make check` 通过。
- [ ] （主会话）服务器真实运行：dbt-fix-daily-cohorts × 1（full）。accept-2 里这题的 validate 第一次就超时（trial 9FNRgGg）。贴出：`echo $DB_TYPE` 的输出；用到 `cd … && dbt` 的那一步；validate 的 parse 耗时；有没有改 `profiles.yml`。

## Evidence required

单元测试输出；`make check` 输出；真实运行的轨迹片段。

## Stop conditions

- 放开 `cd` 需要读 hook 输入里的 `cwd`，或者需要放开 `cd` 以外的操作符。
- Grep 规则只能靠模拟 ripgrep 完整的忽略逻辑才能判断（上面定的 `glob` / `type` 规则不够）。
- SDK 的 `env` 传不到 CLI，Bash 超时设不上。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 4 修复轮（首轮 2 + 退回轮 2）；6 次审查
- Model spend: $0 (unit tests and local runs only; data-agent model calls only, read from run event files)
- Outcome: done（PR #41）；服务器 dbt-fix-daily-cohorts full 1/1，DB_TYPE 可打印、validate 38 s 通过；agent 仍用 --project-dir（系统提示词待改）
- Human review minutes: —

## Comments

### 2026-11-04 工单会话（worktree 40-guards-workdir-dbt-timeouts，commits 4225e9c、58009b3、b482143）

改动：`command_guard.py` 放开 `cd <workspace 内目录> && <dbt 命令或 dbt-workflow 脚本>`，路径按该目录解析；单独 `cd`、`cd ..`、`cd /tmp`、符号链接出去、`cd x && rm` 都拒绝，拒绝提示写出允许写法；`echo $DB_TYPE`、`printenv DB_TYPE` 整串精确匹配放行，变量表是必填参数。`workspace_guard.py` Grep 的 `glob`/`type` 能排除目录下全部凭据文件就放行，按 ripgrep 语义（`glob` 按空白和逗号拆成多个 `--glob`，最后匹配的 glob 生效，包含型 glob 优先于 `--type`），拒绝时列出凭据路径；Read/Grep 可读 `~/.claude/projects/<任意>/tool-results/`，Write/Edit/Glob 不行。`agent.py` 加 `PRINTABLE_VARIABLES = ("DB_TYPE",)`，SDK env 加 `BASH_DEFAULT_TIMEOUT_MS=360000`、`BASH_MAX_TIMEOUT_MS=600000`，**另加了工单没写的 `CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1`**：工单会话在 Claude Code 2.1.317 二进制里核实，不设它时成功的 `cd` 会带到下一次 Bash 调用，与"cwd 始终是 workspace 根"的前提冲突。`validate_project.py` 默认超时 300 s，文件头加注释，`NOTICE` 改为 "modified as noted at its top"。

**Step 3, real run.** `uv run python /tmp/wt40-demo/demo.py` (temp workspace with `dbt_models_duckdb/`, a dbt_utils-style `.env/postgres.env`, and a symlink out):

```
Bash  'echo $DB_TYPE'                                   -> allow
Bash  'printenv DB_TYPE'                                -> allow
Bash  'echo $HOME'                                      -> deny
Bash  'cd dbt_models_duckdb && dbt run --select daily_cohorts' -> allow
Bash  'cd dbt_models_duckdb && python3 .../validate_project.py .' -> allow
Bash  'cd dbt_models_duckdb'                            -> deny: cd may only precede a dbt command, as `cd <dir> && dbt ...`.
Bash  'cd .. && dbt run'                                -> deny: cd .. leaves the workspace or names no directory in it.
Bash  'cd outside && dbt run'                           -> deny (symlink out)
Bash  'cd dbt_models_duckdb && rm -rf models'           -> deny: 'rm' is not allowed.
Grep  {path: dbt_models_duckdb}                         -> deny: holds credential files dbt_models_duckdb/dbt_packages/dbt_utils/integration_tests/.env/postgres.env; add a glob or type that skips them
Grep  {path: dbt_models_duckdb, glob: '*.sql'}          -> allow
Grep  {path: dbt_models_duckdb, type: 'yaml'}           -> allow
Read  ~/.claude/projects/demo/tool-results/r.txt        -> allow
Write ~/.claude/projects/demo/tool-results/r.txt        -> deny
```

ripgrep semantics were checked against the local `rg` (`rg --hidden -g '*.env' --type sql` still reads `.env/x.env`; `-g '!*.env' -g '*.env'` reads it; reversed order does not).

**Checks.** `make check`: 1523 passed, 1 skipped. `uv run pytest tests/acceptance`: 39 passed, unchanged. Targeted files `test_command_guard.py`, `test_workspace_guard.py`: 210 passed.

**Review 1, da-review (Codex), round 1.** P1 case-folding let `!*.env` skip `POSTGRES.ENV`: fixed in `58009b3`, later tightened to case-sensitive matching after confirming the Grep tool passes `--glob`. P2 honor `.gitignore`: not fixed. The ticket's stop condition says the glob/type rule is the agreed scope and simulating ripgrep's ignore logic is out. Denial is the safe direction and the hint tells the agent how to proceed.

**Review 2, eng:code-review, Spec axis.** Three real defects, all fixed in `b482143`: newline bypass in the variable match (`printenv\nDB_TYPE` would have dumped the environment), multi-pattern `glob` treated as one pattern, and `type` wrongly overriding an including glob. Plus the cwd-persistence premise, fixed by the env variable above. Standards axis: made `variables` required, flattened `_grep_credentials` parameters, derived the tool-results root from the layout constant, renamed `base` to `cwd`, tests now cover every `GREP_TYPES` entry. Not changed: module split of `workspace_guard.py` (a new module is outside Authority) and the DB_TYPE constant being benchmark-driven (user decision in the ticket).

**Review 3, da-review on the final diff.** Same `.gitignore` point, same answer. New P2: a Postgres run started with `--postgres-env DB_TYPE` would let `echo $DB_TYPE` print the connection URL, and a database env could overwrite the Bash timeout variables. Not fixed: it needs the operator to pick that exact name, the fix is a validation in `run()` outside the "SDK env and allow-list only" authority on `agent.py`, and both fix rounds are used. Suggest a follow-up line rejecting a `--postgres-env` name in `PRINTABLE_VARIABLES` or starting with `BASH_`/`CLAUDE_`.

**Known limitations for the main session.** `skills/dbt-workflow/SKILL.md:373` still says "default 60" for validate's timeout, and the dbt system prompt in `agent.py:130` still says to pass `--project-dir`; both are outside this ticket's Authority. The denial message teaches the `cd` form, so the agent will learn it on first refusal.
- 2026-11-04 主会话：PR #41 CI 绿。Codex app 一条 P1，核对属实，**退回**：
  - 退回：`run()`（`agent.py:817-821`）把 `--postgres-env` 指定的变量原样放进 SDK env；若操作者传 `--postgres-env DB_TYPE`，`PRINTABLE_VARIABLES` 会让 `echo $DB_TYPE` 把带密码的 Postgres URL 打给模型。这是工单会话在 Comments 里自己也指出的那条。修法：在 `run()` 里加一条校验，`--postgres-env` 的名字在 `PRINTABLE_VARIABLES` 里、或以 `BASH_`/`CLAUDE_` 开头时抛 `ConfigError`（后者防数据库 env 覆盖本工单设的 Bash 超时和 cwd 变量），配一个修复前会失败的测试。Authority 已为此放开一行。在原分支 `worktree-40-guards-workdir-dbt-timeouts` 上接着修，修完提交，重新跑两道审查后把 Comments/Ledger 写进最终回复。

### 2026-11-04 工单会话，退回修复（commits 88429c2、48983dc、3a8fa22）

**退回项（Codex P1，`--postgres-env DB_TYPE`）。** `run()` 在检查变量是否设置之前先校验名字：在 `PRINTABLE_VARIABLES` 里、在 `settings.sdk_env()` 的键里、或以 `BASH_`/`CLAUDE_` 开头，都抛 `ConfigError`（`agent.py:817-830`）。测试 `tests/test_postgres.py::test_run_rejects_a_database_variable_the_agent_could_print_or_that_configures_the_cli`，四个用例 `DB_TYPE`、`BASH_MAX_TIMEOUT_MS`、`CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR`、`ANTHROPIC_BASE_URL`；修复前三个用例全部 `DID NOT RAISE ConfigError`。

**Step 3 真实运行**（临时 workspace，含 `dbt_packages/dbt_utils/integration_tests/.env/postgres.env` 和 `models/sub/.env`）：

```
--postgres-env DB_TYPE            -> ConfigError: DB_TYPE cannot hold the Postgres URL: the agent may print it, or it configures the Claude Code CLI; use another --postgres-env name
--postgres-env ANTHROPIC_BASE_URL -> ConfigError (same text)
--postgres-env DA_TEST_URL        -> run started
Grep {pattern}                                   -> deny: ... denied: . (holds credential files dbt_packages/dbt_utils/integration_tests/.env/postgres.env; add a glob or type that skips them)
Grep {glob: '!dbt_packages/*.env'}               -> deny
Grep {glob: '!models/*.env'}                     -> deny
Grep {path: dbt_packages, glob: 'dbt_packages/**'} -> deny
Grep {glob: '*/dbt_packages/**'}                 -> deny
Grep {glob: 'models/**/*.sql'}                   -> allow
Grep {glob: '*.sql'} / {glob: '!*.env'} / {type: sql} -> allow
```

**Checks.** `make check`: 1533 passed, 1 skipped (`/tmp/wt40-check4.txt`). `uv run pytest tests/acceptance`: 39 passed, unchanged. Guard and postgres test files: 234 passed.

**Review 1, da-review (Codex) on 88429c2.** P1: `fnmatch` lets `*` cross `/`, so `glob="!models/*.env"` counted as excluding `models/sub/.env` while ripgrep still reads it. Confirmed with local `rg` (and found that where rg anchors a slash glob depends on how the search path is passed). Fixed in 48983dc. P2 honor `.gitignore`: not fixed, same reason as before (ticket stop condition; denial is the safe side). P2 credential-named directory as `path` denied even with a filter: not fixed, the ticket says "`path` 本身是凭据文件，照旧拒绝".

**Review 2, eng:code-review on 48983dc.** Spec axis: the mirror-image defect, an including slash glob that fails to match counted as skipping the file (`glob="*/dbt_packages/**"` or `path=dbt_packages, glob="dbt_packages/**"` allowed a search that reads the credential). Standards axis: `--postgres-env ANTHROPIC_BASE_URL` passed the check and would override the SDK endpoint, since the database env is merged last. Both fixed in 3a8fa22: `_glob_matches` now judges a slash glob only by its last segment (if that cannot match the file name, the glob cannot match under any anchoring; otherwise the answer is unknown and `_filter_skips` reads it the safe way: including keeps, excluding is ignored), and `run()` also rejects names in `sdk_env()`. Other standards items (Data Clumps on `glob, file_type`, `TOOL_RESULTS` None wildcard, module size) were judgement calls from earlier commits, not changed. Codex's second pass on 48983dc raised the same including-glob P1 (covered by 3a8fa22) and a P2 that `dbt_packages/*.env` over-denies; not fixed, over-denial is the safe direction and the hint teaches name globs.

**Review 3, da-review on the final diff (3a8fa22), report only, both fix rounds used.** P2: `--postgres-env` set to `DATA_AGENT_MODEL`, `DATA_AGENT_BASE_URL`, or `PATH` still passes. True, but these break the run loudly (wrong model name or no subprocess) rather than leak the URL, and need the operator to choose that name. Follow-up candidate: also reject `DATA_AGENT_*` and `PATH`. P2: `_credentials_below` walks the whole Grep path on every call without early exit. True; the previous `_holds_credentials` also walked but stopped at the first hit. Benchmark workspaces are small and no timeout was seen in tests. Follow-up candidate if large workspaces appear: cache per guard instance.

**Known limitations carried over.** `skills/dbt-workflow/SKILL.md:373` still says validate's default is 60; the dbt system prompt still says to pass `--project-dir`. Both outside Authority.
- 2026-11-04 主会话：服务器真实运行 `t40-real`（commit 3a8fa22；`ablate --split dev --config full --task dbt-fix-daily-cohorts --trials 1 --budget 2`）：reward 1（1/1），54 轮，cost_usd $0.0833；连冒烟实际价 $0.0971（闲时）。轨迹（`runs/t40-real/jobs/full/dbt-fix-daily-cohorts__dyUWt4t/agent/events/`）：`echo $DB_TYPE` 放行，输出 `duckdb`；validate 调用 38.4 s，报告 `Status: PASS`、`Parse time: 38240ms`（accept-2 同题 trial 9FNRgGg 首次 60 s 超时）；守卫共拦 4 次（`echo "DB_TYPE=$DB_TYPE"; echo ...; ls` 多命令、Bash 里用 python 读 tool-results 文件各 1 次等）。**没有用到 `cd … && dbt`**：agent 用 `dbt run --project-dir dbt_models_duckdb --profiles-dir dbt_models_duckdb`，并 Edit 了 `/app/dbt_models_duckdb/profiles.yml`。原因是 `agent.py:130` 的 dbt 系统提示词仍教 `--project-dir`、`SKILL.md:373` 仍写 validate 默认 60 s，都在本工单 Authority 之外（工单会话已列为已知限制）。建议对齐会话决定是否开一张小工单改这两处。PR #41 在 3a8fa22 上 CI 绿、Codex app 无新意见，squash 合入。
