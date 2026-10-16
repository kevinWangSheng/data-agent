# 04 — dbt 任务端到端

Status: in-review
Blocked by: 03
Spec: ../spec.md
Claimed by: claude-code (opus-5.5)
Branch: worktree-04-run-dbt-task

## Goal

给 `data-agent run` 一个 dbt 任务和迷你 dbt 项目，agent 改模型、`dbt run`、交付，模型数值正确。

## Authority

- May edit: `src/data_agent/`、`skills/`（dbt-workflow、dbt-write、dbt-debugging）、`tests/`（除 `tests/acceptance/`）、`NOTICE`、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $2

## Context

plan §3（dbt 任务先加载 dbt-workflow，最大轮数 200）；plan §5 dbt 版本对齐（dbt 1.10.x、dbt-duckdb 1.10.0、duckdb 1.2.2）；CONTEXT 的 dbt write。

## Acceptance

- [ ] 接缝 A 中 dbt 任务的验收测试转绿
- [ ] dbt 版本与 data-eng-bench 镜像一致
- [ ] `make check` 通过

## Evidence required

一次真实 dbt 任务运行的命令、输出、模型查询结果；花费。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 6（之前 3；本轮 da-review ×2、eng:code-review ×1）
- Model spend: ~$0.22（之前 ~$0.12；本轮 3 次真实运行 $0.0424，两次 `make test-integration` 约 $0.06，未确认：事件文件未保留）
- Outcome: —
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `f1ec627`、`fee1d94`、`f3cd4b9`，基于 `b1fda51`）

**实现**：`data-agent run --type dbt` 把 dbt-workflow、dbt-write、dbt-debugging、duckdb-sql 载入 system prompt（dbt-workflow 的 `${CLAUDE_SKILL_DIR}` 换成真实目录），最大轮数 200；dbt 运行额外给 Write、Edit、Bash，query 运行不变；dbt 不在 PATH 时退出码 2。从 signalpilot-plugin 复制三个 SKILL.md 与 `scan_project.py`、`validate_project.py`（文件头注明修改，登记 NOTICE）：工具名改成本项目的；map_columns、verify_model_values 和验证 agent 只写"若可用"（07 再接），Step 8 先用 list_tables、query_database 做同样检查；删掉知识库、TaskCreate 与 domain/testing/snapshot/versioning skill；涉及评分方式或沙箱的措辞改写（ADR-0005）。修了上游 `scan_project.py` 按绝对路径跳过含 `.claude`/`target`/`macros` 路径的 bug（配测试）。workspace guard 扩到 Write、Edit（不出 workspace、不碰 DuckDB 文件及 `.wal`）。新增 `src/data_agent/command_guard.py`（Bash 的 PreToolUse hook）：只允许单条 dbt 命令（子命令在前：run/build/test/seed/snapshot/compile/parse/ls/list/deps/debug）或两个 dbt-workflow 脚本（只读 workspace），可接 `2>&1 | tail/head`；拒绝 `show`、`--inline`、`run-operation`、其他程序、`;`、`&&`、重定向、`$`、反引号、`#`、花括号与通配符；project/profiles/target/log 目录参数必须在 workspace 内。版本（dev 依赖组）：dbt-core 1.10.23（`>=1.10,<1.11`）、dbt-duckdb 1.10.0、duckdb 1.2.2，同 data-eng-bench `base-image/Dockerfile:19-21`；dbt 不作运行时依赖（Harbor 用镜像里的 dbt）。

**真实运行**（`runs/manual04/run.sh`，环境按 `make test-integration` 加载，题面为接缝 A 的 DBT_TASK，commit f3cd4b9）：
```
data-agent run --task "<DBT_TASK>" --type dbt --workspace runs/manual04/r2/ws/dbt_shop --duckdb runs/manual04/r2/ws/shop.duckdb --config full --events-dir runs/manual04/r2/events
exit=0
main.customer_ltv: rows 12 (expected 12), one row per customer True, values match True
  ('C0001', 6, 285.25) ('C0002', 4, 232.50) ('C0003', 2, 98.75) ...
{"seq": 0, "type": "version", ..., "git_commit": "f3cd4b92296e6019ce90af0d3b7ceca01e716431", "skill_version": "a3ae586fc400", "config": "full", "model": "deepseek-flash", "sdk_version": "0.2.168", "cli_version": "2.1.301"}
{"seq": 81, "type": "result", ..., "subtype": "success", "num_turns": 25, "terminal_reason": "completed", "uncached_input_tokens": 20914, "cached_input_tokens": 246912, "output_tokens": 4961, "cost_usd": 0.013708871999999999, "sdk_cost_usd": 0.352051, "cost_basis": "price_table", "errors": []}
```
`grep -c REDACTED` = 0。工具顺序符合工作流（scan → validate → Glob/Read → query_database → technical_spec.md → Write 模型 → `dbt run --select customer_ltv 2>&1 | tail -25` → 只读核对）；agent 试过一次 `find` 被拒后改用 Glob。r1（f1ec627）结果相同，$0.0139。

**检查**：`make check`（f3cd4b9）582 passed, 15 deselected, 15 xfailed（基线 506 passed）。`make test-integration`（f1ec627）4 passed，1 failed：05 的 strict-xfail 基线测试 XPASS；按 AGENTS.md 例外删了它的 xfail 标记，未重跑。输出 `runs/manual04/test-integration.txt`。

**审查**：da-review 第 1 轮 1 个 P1 + 4 个 P2 全修（fee1d94，各配先失败的测试）：shlex 把词中 `#` 当注释，`x#; cmd` 绕过 guard；`--log-path`/`--target-path` 可指向 workspace 外；Skill 的环比规则改为通用规则；dbt-write §6 强制 `COUNT(*)` 改为按指标单位决定聚合；scanner 不再把 `select * from` 模型当桩；脚本路径参数限制在 workspace 内。eng:code-review 第 2 轮修（f3cd4b9）：花括号展开绕过 guard（改为拒绝花括号与通配符）；全局参数值冒充子命令（子命令必须在前）；两个 hook 工厂补返回类型；dbt-write 让 agent 在 Bash 跑 `grep`（改为 Grep 工具）。

**不修的理由**：dbt 执行项目里的任何内容（模型、Python 模型、`on-run-start` hook、被改的 profile 都能在 `dbt run` 下跑任意 SQL 或代码）——这就是 CONTEXT 定义的 dbt 写路径，作为项目代码可审，已写进 command_guard docstring；`_protects` 区分大小写（只影响 macOS 不区分大小写的磁盘，Linux/Harbor 不受影响）；agent.py 两处 `task.type` 分支、两个 guard 的 deny 字典重复，都小且可读；`--vars '{...}'`、`--select 'path:models/*'` 被拒（拒绝花括号与通配符的代价，Skill 不用）；dbt-write 保留不少上游约定，当通用 dbt 指引，留给失败归因或人删减。

### 2026-10-16 主会话：开 PR 前集成

分支基于 `b1fda51`，工单 05（PR #6）已合并，`src/data_agent/agent.py` 冲突（`git merge-tree` 实测）。在原分支上 `git rebase origin/main` 并解决：dbt Skill 从 `config.skills_dir` 加载，Skill 层关（baseline、no-skill）时不载入；05 的基线测试 `test_baseline_leaves_no_verifier_or_context_trace` 去 xfail 后在 dbt 运行下须通过。之后 `make check` + `make test-integration` + baseline 与 full 各一次真实 dbt 运行，再跑一轮 `da-review main`。

### 2026-10-16 主会话：开 PR 前集成（完成）

**Rebase**：`git rebase origin/main`（origin/main = `cd9f635`），只有第 1 个提交冲突（`src/data_agent/agent.py`），后两个提交自动重放。现在提交依次是 `e09bb86`、`6cc5b6a`、`e27ee05`（原 f1ec627、fee1d94、f3cd4b9），另加 `d4ab9c6`、`268ac11`。
冲突这样解：dbt 的 system prompt 和 query 一样拆成基础段加 `SKILLS_PROMPT`；`system_prompt(task_type, config)` 只在 `config.skill` 为真时按 `config.skills_dir` 载入 dbt-workflow、dbt-write、dbt-debugging、duckdb-sql；`DBT_SCRIPTS` 改为 `dbt_scripts(config)`，也从 `config.skills_dir` 取，Skill 层关时返回空；最大轮数用 `config.max_turns`，`skill_version` 和 `layers` 沿用 05 的写法。
`d4ab9c6` 处理 Skill 层关时的痕迹：dbt 任务提示里不写 "and the dbt-workflow scripts"；command guard 拿到空脚本列表时，拒绝原因不提脚本，`python3` 一律拒绝。另补测试：full 跑 dbt 时按配置载入 dbt Skill、轮数 200；baseline 和 no-skill 跑 dbt 时没有 Skill 文本、没有脚本、任务提示不提 dbt-workflow、`skill_version` 为 `none`；guard 无脚本时的拒绝措辞。

**检查**：`make check`（`268ac11`）613 passed, 15 deselected, 15 xfailed（rebase 后集成前的 `d4ab9c6` 是 612 passed）。`make test-integration`（`d4ab9c6`）5 passed, 627 deselected, 10 xfailed，含去了 xfail 的 `test_dbt_model_built_with_correct_values` 和 `test_baseline_leaves_no_verifier_or_context_trace`。输出在 `runs/integ04/`（make-check*.txt、test-integration.txt）。

**真实运行**（`runs/integ04/run.sh <out> <config>`，题面为接缝 A 的 DBT_TASK，用 `runs/integ04/inspect.py` 对照 raw.orders 核对）：
- full @ `d4ab9c6`：exit=0；main.customer_ltv 12 行，每客户一行 True，数值一致 True（C0001 6 285.25、C0002 4 232.50、C0003 2 98.75）；`skill_version` a3ae586fc400，`layers` 全开；25 轮，$0.013840；REDACTED 0。
- baseline @ `d4ab9c6`：exit=0；12 行，数值一致；`skill_version` "none"，`layers` 全关；18 轮，$0.006315；没调用 scan/validate 脚本，事件文件里 "dbt-workflow"、"scan_project" 出现 0 次；一次 `find` 被拒，拒绝原因不提脚本，之后 agent 改用 Glob。
- full @ `268ac11`（Skill 改动后重跑）：exit=0；12 行，数值一致；`skill_version` 430f0a60e55a；26 轮，$0.013055。

**da-review（集成轮，`runs/integ04/da-review.txt`）**：2 个 P2。
- 修了（`268ac11`，配一个修复前失败的测试）：dbt-workflow 跳过 Steps 4–7 的条件只看扫描结果，扫描看不到 YAML 里没声明的新模型，"新建模型"的任务按字面会被跳过。现在条件里加了"任务要求新建尚不存在的模型"，文件头的修改说明一并更新。
- 不修：`scan_project.py` 用正则解析 YAML，遇到 indentless 序列（`models:\n- name: x`）会把列名当成模型，带引号的名字会保留引号。这是上游刻意不依赖 PyYAML 的设计：脚本跑在环境自带的 python3 下，不保证有 yaml。扫描结果只是提示，Step 3 里 agent 会自己读 YAML。真实失败出现时交给 14 做失败归因，再决定是否改成结构化解析。

### 2026-10-16 第 7 步：PR #9 退回第 1 轮（主会话）

CI 两次全绿。退回：原因如下，在原分支 `worktree-04-run-dbt-task` 上修，修完提交，不 push、不 rebase（需要同步 main 时用 `git merge origin/main`）。

1. **Codex P1（`skills/dbt-debugging/SKILL.md:35`）属实，违反 ADR-0005**：Skill 里写了 `circuits.sql`、`results.sql`（Spider2-DBT 的表名）。改成通用规则：模型名与 source 表同名、且落在同一 schema 时会覆盖源数据，先查清 source 的 schema 再定模型名或在 YML 里给 source 指定 schema；不同 schema 的同名是合法的，不要拒绝。另外把三个 dbt Skill 和两个脚本整体过一遍，删掉其余榜单形状的具体名字或数值。
2. **Codex P1（`src/data_agent/command_guard.py:23`）属实，违反"唯一的写方式是改 dbt 项目后 `dbt run`"**：从允许的子命令里去掉 `build`、`seed`、`snapshot`；`test` 拒绝 `--store-failures`（及 `--store-failures-as`）。同步改 Skill 里提到这些子命令的地方。配测试。
3. **Codex P2（`src/data_agent/command_guard.py:122`）属实**：guard 只看 `command`，`run_in_background: true` 能通过。拒绝后台 Bash。配测试。
4. **Codex P2（`skills/dbt-workflow/scan_project.py:207`）属实**：`classify_sql_models` 把项目下所有 `.sql`（含 `tests/`、`analyses/`、`snapshots/`）当模型，同名时会把缺失的模型算成已完成。只扫 `dbt_project.yml` 的 `model-paths`（缺省 `models`）。配测试；文件头修改说明一并更新。

修完 `make check`、`make test-integration`、full 配置真实 dbt 运行一次，再跑一轮 `da-review main`；最终回复给出要追加的 Comments 与 Ledger（Attempts 2）。

### 2026-10-16 工单 agent：PR #9 退回第 1 轮处理完（commits `8372cca`、`585e786`、`50d0fc8`、`cf7859c`，接在 `268ac11` 后）

**退回 4 条，全部修了：**
1. **ADR-0005**：dbt-debugging §3 不再写具体表名，改成通用规则："模型名与 source 表同名、且在同一 schema 时会覆盖源数据；先用 `list_tables` 或 `information_schema` 查 source 在哪个 schema，同 schema 就换模型名或在 YML 里给 source 补 `schema:`；不同 schema 同名合法，不要拒绝。"另外把三个 dbt Skill 和两个脚本整体过了一遍，以下改成通用写法：
   - 国家名变体（Brunei/Russia、Muenchen、Côte d'Ivoire）、IBM
   - `supplier_id`/`suppliers`、`eng_headcount`、`'ACTOR'`、`return_pct` 0.65
   - `reporting_grain`、16/13 行、3 列 rank+name+metric
   - `total_parts`、NPS、guide per day、`extract_hour`/`normalize_timestamp`
   - customer/driver/order、"due to returned items"
   - CRM 列名（`converted_date`/`is_won` 等）
   - "multiple-choice"、scan 输出里的 "guaranteed fail"

   新增测试 `test_dbt_skills_carry_no_project_specific_names` 守住这些词。各文件头的修改说明已同步。
2. **command guard 子命令**：允许的子命令里去掉 `build`、`seed`、`snapshot`；`test` 拒绝 `--store-failures`、`--store-failures-as`（含 `=` 写法）。拒绝措辞只列允许的子命令。Skill 原本没有让 agent 跑这三个子命令的地方；scan 的 snapshots 提示改成"只读作参考，这里不能跑 `dbt snapshot`"。配了测试。
3. **后台 Bash**：`run_in_background: true` 一律拒绝。配了测试。
4. **scan 范围**：`scan_project.py` 只扫 `dbt_project.yml` 的 `model-paths`（缺省 `models`）。支持行内列表、跨行列表、块列表，以及老的 `source-paths`（dbt 1.10 在没有 `model-paths` 时仍读它）。分类、依赖、同目录模型、`current_date` 扫描都只看这些路径。配了测试：`tests/`、`analyses/`、`snapshots/` 里同名的 SQL 不再让缺失的模型算成已完成，并对照旧脚本确认修复前会失败。文件头说明已更新。

**审查：**
- **da-review 第 1 轮**：1 个 P1，修了（`585e786`）。`dbt parse --record-timing-info ../shop.duckdb` 和 `-r` 能覆写 workspace 外的任意文件，包括数据库。现在拒绝 `--record-timing-info`、`--partial-parse-file-path`，以及任何含 `r` 的单横线参数（包括 `-xr`、`-rfile` 这类合并写法）。按 dbt 1.10 的 `dbt.cli.params` 核过：`-o/--output` 只属于 `source freshness`，那个子命令本来就被拒绝。
- **eng:code-review**（Standards、Spec 两个子 agent）修了（`50d0fc8`）：
  - Skill 里剩下的 driver 和 CRM 列名；
  - `model-paths` 跨行列表和 `source-paths`；
  - snapshot 提示；
  - guard docstring 补全被拒绝的写文件参数；
  - 常量改名为 `RECORD_TIMING_SHORT`，`reason` 的类型注解移到 if/else 之前；
  - 测试里重复的 scan 调用合并成 `scan()`。
- **da-review 第 2 轮**：2 个 P2，修了（`cf7859c`）：
  - workspace 里有个叫 `123` 的符号链接指向外面时，`python3 <script> 123` 能通过 guard。现在脚本的项目参数一律按路径检查，只有 validate 的超时参数可以是纯数字。
  - `model-paths: ["../x"]` 或者符号链接的 SQL 文件能让 scan 读到项目外的内容。现在 model path、SQL、YAML、宏文件解析到项目外的都不读。

  两条都配了修复前会失败的测试。修复已到第 2 轮（上限），之后没再跑审查。

**不修的理由：**
- dbt-write §8 的 0-33/34-66/67-100、`MAX(score)` 返回 150：这是编出来的示例数字，不是哪个项目的数据。
- dbt-write §13 由"pct 必须 0-100"改成"先用项目已有的比例，项目里没说明时才用 0-100"：Spec 审查说这超出了要求。保留的理由是：只删例子，剩下的绝对规则本身就是按某个榜单的比例调出来的，"先看项目"才是通用写法。
- 单横线参数含 `r` 就拒绝，会多拒一些：这是宁严勿宽，dbt 的参数值不会以 `-` 开头。
- model-paths 有重叠时文件会列两次：输出用的是集合，结果不受影响。
- 这几条之外，`dbt test` 在 `store_failures` 配在项目里时也会写表：这和之前记的"dbt 会执行项目里的任何内容"是同一个已知限制，已写进 `command_guard` 的 docstring。

**检查：**
- `make check`（`cf7859c`）：639 passed, 15 deselected, 15 xfailed（基线 613）。
- `make test-integration`：`8372cca` 时 5 passed, 10 xfailed；`cf7859c` 时 5 passed, 654 deselected, 10 xfailed。
- 输出都在 `runs/return04/`（`make-check*.txt`、`test-integration*.txt`、`da-review*.txt`）。

**真实运行**（`runs/return04/run.sh <out> full`，题面为接缝 A 的 DBT_TASK，用 `inspect.py` 对照 raw.orders 核对）：

| commit | exit | `customer_ltv` | 轮数 | 花费 |
|---|---|---|---|---|
| `8372cca` | 0 | 12 行，每客户一行 True，数值一致 True | 27 | $0.013844 |
| `50d0fc8` | 0 | 12 行，数值一致 | 23 | $0.013635 |
| `cf7859c` | 0 | 12 行，数值一致 | 23 | $0.014912 |

- 三次的前三行都是 C0001 6 285.25、C0002 4 232.50、C0003 2 98.75；`grep REDACTED` 都是 0。
- 每次 agent 都试了一次 `ls`/`find`，被 guard 拒绝后改用 Glob。
- 工具顺序：scan → validate → `dbt run --select customer_ltv 2>&1 | tail` → 只读核对。
