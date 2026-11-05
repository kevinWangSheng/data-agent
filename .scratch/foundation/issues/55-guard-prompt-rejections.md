# 55 — 守卫误拦和提示：开头几步不再被拦，拒绝提示给出正确写法

Status: done
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

- Attempts: 2
- Review rounds: 4（前 2 轮 Fable 代替 Codex；退回第 1 轮：da-review（Codex）+ eng:code-review，修复提交 d52f05e、109c4fc，修复后没有再审）
- Model spend: $0（没有调数据 agent 模型）
- Outcome: 退回第 1 轮已修，make check 绿；服务器真实运行待主会话
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（b5f1faf、f24f89e、b9a7dae）

- 2026-11-05 实现（claude-opus-5-5，分支 worktree-55-guard-prompt-rejections，提交 b5f1faf、f24f89e、b9a7dae）
  - workspace_guard：排除型 glob `!**/<dir>/**` 放行，只比较 Grep 搜索路径以下的目录；后面的包含型 glob 重新包含该目录时仍然拦。Grep 拒绝提示给出可照抄的写法：`type: "sql"`，以及 `glob: "!**/<顶层目录>/**"`（只在每个凭据文件都在子目录里时给）。
  - command_guard：只改拒绝提示文字，`_split` 和所有放行/拒绝分支与 origin/main 逐字相同。提示会点名命中的字符和运算符（`$`、`;`、`{`、`}`、反引号、`2>/dev/null`、`||` 等）；给出 `echo $DB_TYPE` 单独执行、`--vars 'k: v'` 的写法；`ls`/`cat` 等指向 Glob/Read/Grep，`cp`/`mv` 等指向 Write/Edit。
  - agent.py：`DBT_PROMPT` 加一句"打印 DB_TYPE 时单独执行 `echo $DB_TYPE`；看目录、读文件、搜索用 Glob/Read/Grep，不用 ls/cat"。`TASK_FILE_NOTE` 写明用 Read 打开任务文件（Glob 和 Bash 都到不了）。
  - tests/test_task_file.py 的 `DBT_PROMPT_BEFORE` 同步成新句子。它是不带校验层的提示词快照，不在 tests/acceptance/ 下。
- 测试：`make check` exit=0，1743 passed、1 skipped、1 xfailed，ruff 和 basedpyright 0 错误。
- 本地真实行为验证：直接调用守卫 hook，脚本 `uv run python runs/t55-rg/demo.py`（在 worktree 内），输出摘录：
  - `echo "DB_TYPE=$DB_TYPE"; echo DUCKDB_PATH; ls /app` → deny: `$` outside a lone `echo $DB_TYPE` is not allowed; `;` is not allowed: Bash runs one command per call, optionally with 2>&1 and a pipe into tail or head; run `echo $DB_TYPE` on its own, as a separate Bash call; list, read or search files with the Glob, Read or Grep tool.
  - `ls -R /app 2>/dev/null | head -50` → deny: `2>/dev/null` is not allowed: … list, read or search files with the Glob, Read or Grep tool.
  - `dbt run --vars '{"k": "v"}'` → deny: `{`, `}` are not allowed; write --vars as YAML without braces: --vars 'k: v'.
  - `echo $DB_TYPE`、`dbt run --vars 'k: v'` → allow。
  - Grep `{"pattern":"select"}`（dbt_packages 下有 sample .env）→ deny … for example type: "sql" or glob: "!**/dbt_packages/**"。
  - Grep `glob: "!**/dbt_packages/**"` → allow。
  - Grep `glob: "!**/dbt_packages/** **/dbt_packages/**"` → deny。
- 对 ripgrep 14.1.1 实测（runs/t55-rg/check.py、escape.py）：
  - `!**/dbt_packages/**` 在搜索路径为 `.`、绝对路径、`dbt_packages` 三种写法下都会跳过该目录。
  - 后接 `**/dbt_packages/**` 会重新读到凭据文件。
  - `!dbt_packages/**` 是否生效取决于搜索路径的写法，所以守卫继续忽略它。
- 服务器真实运行（`dbt-fix-daily-cohorts` × 1，full）还没跑，交主会话（本工单 Server access: none）。
- 审查 1（`da-review main`）：Codex 连续两次 "Selected model is at capacity"，输出在 runs/t55-rg/da-review-{1,2}.txt。按惯例改用 model:fable 只读审查代替。
  - HIGH，已修（f24f89e）：fnmatch 把 `[^x]` 当作含 `^` 的字符类、把 `\` 当字面量，ripgrep 却分别当否定和转义。实测 `!**/[^s]ecrets/**`（本工单引入）、`\.env`、`![^.]env`（工单 40 起就有）都被守卫放行，而 rg 实际读到凭据文件。现在新增 `_name_matches`，遇到含 `\` 或 `[^` 的 glob 不做判定：包含型照样保留文件，排除型不跳过任何文件。
  - LOW，已修：`2>&1 >/dev/null` 被说成 `1>/dev/null`；反引号显示成三个反引号。
- 第 2 轮 Fable 复审（审 f24f89e）：
  - HIGH，已修（b9a7dae）：花括号里带 `/` 的包含型 glob（如 `{secrets/**,*.sql}`）守卫放行而 rg 读到凭据，工单 40 起就有。
  - MEDIUM，已修（b9a7dae）：空的花括号选项（如 `!.env{,.*}`）rg 会丢掉，守卫却当成匹配，结果放行。两种 glob 现在都不做判定。
  - 复审确认 command_guard：对 80 条怪命令做模糊测试，放行/拒绝结果与 origin/main 完全一致，没有异常。
  - 已知、不修：单独一个 `\` 的提示写成 "a quote is left open"（shlex 报 "No escaped character"），无害。
  - 未确认：Claude Code 2.1 的 Grep `glob` 参数传给 rg 的是 `--glob` 还是 `--iglob`。只影响大小写不一致的包含型 glob，本工单没有改变这部分判定。
- 审查 2（eng:code-review main）：
  - Spec：
    - 已修（f24f89e）：`$` 的提示自相矛盾（先说 `$` 不允许，又建议 `echo $DB_TYPE`），现在写成 "`$` outside a lone `echo $DB_TYPE`"。
    - 已修（f24f89e）：管道接 grep/less 时没有替代写法，现在对前面的程序给 Glob/Read/Grep 提示。
    - 已修（f24f89e）：把 lexer 抽成 `_tokens` 动到了决策路径，超出 Authority，已改回，`_split` 与 origin/main 逐字相同。
    - 不修：`FILE_WRITING_PROGRAMS` 和 Write/Edit 提示不在 Q11/Q18 的约定里。理由：`cp` 在工单统计的约 20 次拒绝里，这句只是提示文字，不改放行规则。
  - Standards：
    - 已修（f24f89e）：`tail`/`head` 作为允许的管道目标时误触发文件工具提示。
    - 已修（f24f89e）：测试 helper `grep_reason` 和 `decide` 重复，已合并成 `hook_output`。
    - 不修：`_operators` 和 `_split` 都写了"哪些运算符允许"。共用一个判定就得改 `_split`，而 Authority 不允许改决策路径。
    - 不修：提示词里写死了 `echo $DB_TYPE`，没从 `PRINTABLE_VARIABLES` 生成。Authority 只允许改 `DBT_PROMPT` 文本，生成要改调用处；而且这句原文是 Q11/Q18 定下的。
    - 不修：`_shape_denial` 偏长、`_below_directory` 推导式偏密，属于判断项，文件仍能一遍读完。
- 安全边界的变化只有两类：一是排除型 `!**/<dir>/**` 的放行（工单要求）；二是上面几种 fnmatch 与 ripgrep 解读不一致的 glob 改为拒绝，只会更严。

### 2026-11-05 主会话（真实运行与退回第 1 轮）

- PR #53：CI 绿；Codex GitHub app 审查了 b9a7dae，已完成，没有意见。
- 服务器真实运行 `t55-real`（commit b9a7dae）：`scripts/da-remote start t55-real -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest evals/manifest.json --split dev --task dbt-fix-daily-cohorts --out runs/t55-real --budget 2 --config full --trials 1`，exit 0，reward 1（1/1），实际价 $0.1043（闲时）。守卫共拒绝 5 次（t47-screen 平均每个 trial 4.4 次），验收没过：
  - 合成 echo：0 次（t47-screen 每个 trial 1 次）。这一类已经修好。
  - Bash 串命令：2 次（seq 14、16）。`cd /app/dbt_models_duckdb && find models -name "*.sql" | head -200 && echo …`，以及 `… && cat dbt_project.yml && … && ls -la && cat profiles.yml 2>/dev/null`。拒绝提示写对了（点名 `*`、`&&`，并指向 Glob/Read/Grep），agent 第二次被拒后改用 Glob。提示词新加的那句没能阻止第一次。
  - Grep：3 次（seq 69 path=/app，seq 185 path=/app/dbt_models_duckdb，seq 219 path=/app）。
- **退回：Grep 拒绝提示建议的排除 glob 会把整个 dbt 项目排除掉。** seq 69 搜 `/app` 时，凭据文件都在 `/app/dbt_models_duckdb/dbt_packages/dbt_utils/integration_tests/.env/` 下，提示却给出 `glob: "!**/dbt_models_duckdb/**"`，也就是搜索路径下的顶层目录。照做的话，Grep 会跳过全部模型，等于告诉 agent 搜不了。agent 没照做，改用 Read 逐个读文件。修法：提示建议的排除目录必须只覆盖凭据文件、不覆盖项目的模型和 yml。例如选凭据文件路径上最靠近凭据、并且是所有凭据共同祖先的那个目录名（这里是 `!**/dbt_packages/**` 或 `!**/integration_tests/**`），并且要对实际文件树确认：照这个建议改后的 Grep 会被守卫放行，而 rg 仍能搜到 models/ 下的文件。没有这样的目录时，只给 `type: "sql"`。配一个修复前会失败的测试：搜索路径是项目上一层，凭据在 `<project>/dbt_packages/...` 下。
- 状态改回 `ready-for-agent`，在原分支 `worktree-55-guard-prompt-rejections` 上修，这是第 1 轮退回。

### 2026-11-05 工单会话（退回第 1 轮：ef3f702、7309b0a、d52f05e、109c4fc）

- 合并 origin/main（cd921e6，含 51）：无冲突（ef3f702）。
- 修复（7309b0a）：
  - Grep 拒绝提示的排除 glob 改由新函数 `_excluding_retry` 计算（`workspace_guard.py`）。
  - 取仍会读到的凭据文件的最深公共祖先目录，提示写成调用原有 `glob` 加上 `!**/<该目录名>/**`。
  - 两个条件都满足才给出：守卫按这个 glob 重新判定后，每个凭据都会被跳过；同时它不跳过搜索路径下任何非凭据文件。否则只给 `type: "sql"`。
  - 判定基于实际文件树，每次拒绝时现算。
  - 在 t55-real 的文件树上，提示是 `!**/.env/**`：`integration_tests/` 里还有包自带的 models，所以按"只覆盖凭据"的规则不选它，也不选 `dbt_packages`。
  - 只做了一个重构：原来的 `_credentials_below` 拆成 `_files_below` 和 `_is_credential_path`，走一遍目录树同时拿到全部文件和凭据文件，判定结果不变。
- 新测试（tests/test_workspace_guard.py）：
  - `test_grep_denial_excludes_only_the_directory_holding_the_credentials`：搜索路径是项目上一层，凭据在 `proj/dbt_packages/dbt_utils/integration_tests/.env/` 下。修复前失败，原代码给出 `glob: "!**/proj/**"`；Spec 审查也用 ef3f702 的旧代码复现了这一点。
  - `test_the_suggested_exclusion_still_lets_ripgrep_read_the_models`：从拒绝提示里取出 glob，交给真实的 `rg --hidden --files` 跑。结果 `proj/models/` 下的文件仍能读到，没有任何 `.env`。没装 rg 时跳过。
  - 只给 `type` 的两种情况：凭据和项目模型在同一目录；凭据分散在两个目录，搜索路径下没有公共祖先。两种情况的断言都是提示里没有 `glob:`。
  - `test_grep_denial_keeps_the_glob_already_given`：调用原有的 glob 会保留在提示里。
  - 本工单早先写的 `test_grep_denial_shows_a_filter_that_would_run` 的期望值从 `!**/dbt_packages/**` 改成 `!**/.env/**`（它不在 tests/acceptance/ 下）。
- `make check`：exit=0，1775 passed、1 skipped、1 xfailed，ruff 和 basedpyright 0 错误（runs/t55-rg/make-check-r3c.txt）。
- 本地真实行为：`uv run python runs/t55-rg/demo_r3.py`，在 t55-real 形状的 `/app` 树上调用真实的守卫 hook 和 rg 14.1.1：
  - `Grep path=/app` → deny，提示 glob 为 `!**/.env/**`；照提示重试 → allow。
  - `rg --hidden --files --glob '!**/.env/**' /app` → 列出 `dbt_models_duckdb/models/daily_cohorts.sql`、`dbt_project.yml`、`integration_tests/models/t.sql`，没有任何 `.env`。
  - `path=/app/dbt_models_duckdb` 结果相同。
- 审查 1（`da-review main`，Codex 这次可用，输出在 runs/t55-rg/da-review-r3.txt）：
  - P1，已修（d52f05e）：`_below_directory`（本工单引入）把 `{}` 按字面匹配。实测 `!**/secrets{prod}/**` 守卫放行，而 rg 展开了花括号，读到 `secrets{prod}/.env`。现在含 `{` 或 `}` 的段不做判定，排除型 glob 不跳过任何文件，提示也不会给出这种 glob。配了修复前会失败的测试。
  - P1，不修：Codex 称 `tests/test_workspace_guard.py:442` 会失败。不属实：该用例期望 `none`，实际通过；Spec 审查也独立确认了。
- 审查 2（eng:code-review main）：
  - Spec：返回要求已满足。
    - 已修（109c4fc）："只给 type"测试原先只断言两个特定 glob 不出现，现在断言没有 `glob:`。
    - 不修：退回意见括号里的例子是 `dbt_packages` 或 `integration_tests`，实现选的是 `.env`。理由：按退回给出的规则（最靠近凭据的公共祖先，不覆盖项目文件），`.env` 是更窄的选择。
  - Standards：没有硬违规。
    - 已修（109c4fc）：花括号规则并入 `_name_matches`，和 `\`、`[^` 放在同一份清单里。这样只会更严：剩余花括号的 glob 不做判定。
    - 已修（109c4fc）：rg 测试改为从提示里取 glob，不再写死。
    - 已修（109c4fc）：测试 docstring 改成描述行为，不再记录审查历史；变量 `p` 改名为 `path`。
    - 不修：`glob` 和 `file_type` 成对传递（Data Clumps）。模块小，打包要改已有函数的签名，超出本次修复。
- 未确认：Claude Code Grep 是否给 rg 传 `--hidden`。只影响 rg 实际跳过多少文件，不影响守卫的判定。
- 服务器真实运行待主会话：`dbt-fix-daily-cohorts` × 1（full）。看 Grep `/app` 类拒绝在第一次拒绝之后是否消失，也就是 agent 是否照提示改用 `!**/.env/**`。
- 合并后 origin/main 又前进到 18fb985（含 52）。与本分支没有重叠文件，`git merge-tree` 无冲突，没有再次合并。

### 2026-11-05 主会话（服务器复测 `t55-real2`，109c4fc）

`scripts/da-remote start t55-real2 -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest evals/manifest.json --split dev --task dbt-fix-daily-cohorts --out runs/t55-real2 --budget 2 --config full --trials 1`：1/1，$0.0796。

- 守卫拒绝 4 次（t55-real 5 次；t47-screen 平均每 trial 4.4 次）。
  - `echo $DB_TYPE` 合成：0 次（t47-screen 15/15 个 trial 都有）。
  - Bash 串命令：3 次（seq 19、21 `cd … && find`/`cat … && ls`，seq 43 Bash 里跑 `grep`）。提示词那句没拦住，拒绝后 agent 改用 Glob/Read/Grep。
  - Grep 被拦：1 次（seq 75，path 为项目目录）。之后 agent 改搜 `models/` 加 `glob: "**/*.yml"`，再没被拦（t55-real 3 次）。
- 结论：Grep 类从每次都撞变成撞一次就绕开；Bash 串命令仍有，属于提示词没压住的部分，记为已知限制。
