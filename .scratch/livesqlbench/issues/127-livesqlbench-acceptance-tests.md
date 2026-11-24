# 127 — LiveSQLBench：验收测试（任务生成、划分、soft EX 规则、评测入口、答案不外泄、只读；全部不连库、不调模型、不下载数据）

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (implement session)
Branch: worktree-127-livesqlbench-acceptance-tests

## Goal

spec "Testing Decisions"（`.scratch/livesqlbench/spec.md:68-77`，含 2026-11-24 主会话的更正，spec `:101` 起的 U1–U11）的六组验收测试都写好，落在本工单定下的接缝上：
- 任务生成；
- 划分；
- soft EX 规则；
- 评测入口；
- 答案不外泄；
- 只读。

还没实现的行为标 `TICKET_128`（`xfail(strict=True)`），由 128 转绿。现在就成立、以后也要一直成立的行为写成不标记的守门测试，现在就通过。128 要实现的接口只放签名，函数体抛出 `NotImplementedError("ticket 128")`。全部不连库、不调模型、不下载数据，不碰服务器。本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit（都是新建）：
  - `tests/acceptance/test_livesqlbench_*.py`：按组分文件，文件名由本工单定。
  - `tests/acceptance/livesqlbench_support.py`：几个文件共用的辅助，照 `tests/acceptance/mcp_support.py` 的做法。
  - `tests/fixtures/livesqlbench/`：手工构造的小样例，见 Context 第 4 项。
  - LiveSQLBench 的新模块，放在 `evals/` 下：只放 Context 第 5 项的契约，即类型、常量和抛出 `NotImplementedError("ticket 128")` 的空壳。模块名由本工单定。
  - `evals/harbor_agent.py`：只给 `DataAgentOptions`（`:235-257`）加一个 `benchmark` 字段（默认是现有的 data-eng-bench 行为，另一个取值 `livesqlbench`，即 Harbor 的 `--ak benchmark=livesqlbench`，spec U5），不用它，不改别的行为。照工单 111 给 `trace` 字段放空壳的做法。
- Must not edit（参考实现例外：为了验证测试可以临时改，但不提交，验证完撤掉）：
  - `evals/` 里已有的文件：`ablate.py`、`board.py`、`report.py`、`sample.py`、`export.py` 等；`harbor_agent.py` 除上面那个字段以外的部分。
  - `evals/manifest.json`、`evals/registry.json`、`evals/regression.json`、`evals/configs/`。
  - `scripts/da-remote`。
  - `src/` 下的任何文件。
  - 已有的测试：
    - `tests/test_eval_manifest.py`、`tests/test_da_remote.py`、`tests/test_harbor_agent.py`、`tests/test_eval_board.py`、`tests/test_eval_ablate.py`；
    - `tests/acceptance/` 里已有的文件，包括 `conftest.py`、`seam_support.py`、`test_chat_eval_unchanged.py`；
    - `tests/fixtures/` 里已有的文件。
    - 要用它们的辅助函数，import 能用就 import，不能就照抄进 `livesqlbench_support.py`。
  - `NOTICE`、`README.md`、`docs/`、`Makefile`、`pyproject.toml`、`uv.lock`。
  - `.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0（不调模型）
- 网络：只为读上游，把 LiveSQLBench clone 到 `~/dev/AI/data-agent-refs/livesqlbench` 并 checkout `e15cd22`（spec `:97`），在 Comments 记下 commit。不下载数据集、dump、镜像，也不发邮件。

## Context

1. **行为来源**：
   - spec "Testing Decisions" `:68-77`，逐条照写，每条至少一个测试。
   - spec 文末 "Update 2026-11-24：主会话对照代码更正"（`:101` 起，U1–U11）：参照配置、清单、调优命令、抽样、Harbor 适配器、只读、答案不外泄、agent 的输入、许可证、预测 SQL 格式、旧行号。它们已经定了，本工单照办，不再问。
   - 背景："标准答案（Q1）" `:17-21`、"数据集与划分（Q2、Q4）" `:23-30`、"适配器（Q3）" `:32-40`、"工单" `:58-66`、"Out of Scope" `:87-93`。
   - 取证笔记：`docs/research/2026-11-23-livesqlbench.md`。§1.4 是许可证，§3 是评分、公开字段和隐藏字段。
   - ADR：`docs/adr/0004-ablation-holdout.md`（留出集只看汇总）、`0005-dab-integration.md`（不写针对榜单题目的内容）、`0010-sandbox-and-credentials.md`（写库凭据）。

2. **代码现状**（main `6b7ada6`，逐条读过代码）：
   - **LiveSQLBench 还什么都没有。** `grep -rIil livesql evals src tests scripts skills` 没有结果。下面每个 LiveSQLBench 接缝都是新的。
   - **Harbor 适配器只会跑 dbt 和 DuckDB。**
     - `evals/harbor_agent.py:364-389` `_run_command` 写死了 `run --type dbt`、`--duckdb`、`DB_TYPE != duckdb` 就退出，以及 `--sandbox local`。
     - 不带 `--network`，注释在 `:378-379`。
     - `MANIFEST` 在 `:94`；`split_tier` 在 `:138-146`，读清单顶层的 `dev`；`DataAgentOptions` 在 `:235-257`，字段是 `run_config`、`agent_timeout_sec`、`trace`。
   - **`ablate` 只认 data-eng-bench。**
     - `JOB_CONFIG` 是 data-eng-bench 的 job 配置（`evals/ablate.py:130`）；`--tasks-dir` 的 help 写的是 "data-eng-bench tasks/"（`:161`）。
     - `REFERENCE = "full"`（`:105`），`--config` 里必须有 `full`（`:829-831`），差值都是 full 减各配置（`:744-746`）。
     - 把 `manifest[args.split]` 整个当作这次要跑的题（`:833-835`），preflight 冒烟跑 `dev[:1]`（`:893`）。
     - `harbor_run_args` 在 `:444-457`。
   - **`board` 只跑 registry 里的 data-eng-bench。**
     - 用 `-d DATASET_VERSION` 跑（`evals/board.py:176`；`DATASET` 在 `:65-67`）。
     - 没有 `--split` 选项（`:96-128`）。
     - 留出题从 `MANIFEST`（`:70`）读（`:638-640`），`build_report`（`:408` 起）里只出汇总。
   - **划分清单**：
     - `evals/manifest.json` 是 `{"seed": 20261015, "dev": [30 题], "holdout": [30 题]}`；`tests/test_eval_manifest.py:9-15` 钉死了 seed 和 30/30。
     - 假清单：`tests/fixtures/eval_manifest.json`，题名是 `dbt-alpha` 等。
     - 读清单的地方：
       - `evals/report.py:29` `REPO_MANIFEST`；
       - `:348-362` `split_trials`，要求 split 里每题都有 trial；
       - `:365-382` `check_dev_list`。
     - 现有抽样：`evals/sample.py:37-52` `sample` 按 `difficulty` 分层，开发集是给定的清单；`:17-34` `allocate` 按比例、最大余数分配。
   - **`da-remote pull`**（`scripts/da-remote:623-735`），按服务器 checkout 里的 `evals/manifest.json` 判断：
     - board 运行：按每个 trial `result.json` 的 `task_name` 是否在顶层 `holdout` 里决定取不取回（`:637-677`）。
     - ablate 运行：只有每个 job、trial、冒烟、日志都能证明是开发集题时才整目录取回（`:679-731`）。没有 `result.json` 的 trial 按 Harbor 的 trial 名判断：`task[:32]` 去掉末尾的 `-` 或 `_`（`:687-693`）。
     - 测试脚手架：
       - `tests/test_da_remote.py` 开头的假 `ssh`、`rsync`、`uv`；
       - `SERVER_MANIFEST`（`:468`）、`ablate_run`（`:475`）、`pulled_whole`（`:499`）、`SUMMARY_FILTERS`（`:411`）；
       - 留出题 trial 的用例在 `:535` 起。
   - **`da-remote benchmarks`**（`scripts/da-remote:344-472`）现在只准备 data-eng-bench 和 DAB。
   - **Harbor 0.24**（`pyproject.toml:35` `harbor==0.24.0`）：
     - 任务目录由 `.venv/lib/python3.12/site-packages/harbor/models/task/task.py:32-82` 的 `Task(task_dir)` 读入：`instruction.md`、`task.toml`、`environment/`、`solution/`，加上 `tests/`（"copied to container @ /tests by Evaluator"）。
     - 验证阶段才上传 `tests/`：`harbor/verifier/verifier.py:173-186`。spec `:20` 把这一行为留给实现时核实，本工单的测试不钉死。
     - 任务结构测试用 Harbor 自己的 `Task(...)` 读生成的目录。
   - **Harbor 自带的参考适配器**：在 `~/dev/AI/data-agent-refs/harbor` @ `dc933be` 的 git 树里，但这是稀疏 checkout，`adapters/` 不在磁盘上。
     - 读法：`git -C ~/dev/AI/data-agent-refs/harbor show dc933be:adapters/bird_bench/src/bird_bench/adapter.py`。
     - `bird_bench` 把标准 SQL 写进 `solution/solve.sh` 和 `tests/gold.sql`（`adapter.py:238-253`），任务模板在 `task-template/`。
   - **评测入口守门的先例**：
     - `tests/test_harbor_agent.py`：`LocalEnvironment`（`:87`）、`local`（`:128`）、`make_agent`（`:140`）、`container_env`（`:157`）、`run`（`:169`）、`stub_args`（`:173`）；`--network` 的断言在 `:343-348`。
     - `tests/acceptance/test_mcp_eval_unchanged.py:66`。
     - `tests/acceptance/test_charts_switch.py:110`，它从 `test_chat_eval_unchanged` import 辅助（`:32`）。
   - **`make check` 里没有 Postgres**：连 Postgres 的测试都标 `integration`（`tests/test_postgres.py:249-271`），`pyproject.toml:85-88` 默认不跑它们。所以评分测试只测纯函数（SQL 文本处理、结果集比较），不执行 SQL。
   - **验证子 agent 只用于 dbt**：`src/data_agent/agent.py:1385-1387` `uses_verifiers`（spec U11 已更正旧行号）。
   - **本地沙箱的环境**：`--sandbox local` 的命令只带 `local_env` 和任务自己的 `DBT_` 变量（`src/data_agent/sandbox.py:81`、`:196-201`），`--postgres-env` 指向的变量不在里面。
   - **上游的库凭据**：模板把 `root/123123` 同时给了 agent 所在的 `main` 服务（`LiveSQLBench-CLI/adapters/livesqlbench/template/environment/docker-compose.yaml:13-14`）和数据库边车（`:24-25`），这个角色是 SUPERUSER（`bootstrap-db-image.sh:36`）；上游评测脚本要用它建、删 `<库>_process_1`（`template/tests/evaluator.py:46`、`:91`、`:109`、`:119-127`）。Harbor 0.24 的 `[verifier] env` 只在验证阶段合并（`harbor/verifier/verifier.py:190`）。见 spec U6。

3. **上游 LiveSQLBench @ `e15cd22`**（取证时用的版本，已读过）：
   - **许可证**：
     - 根目录 `LICENSE` 是 MIT，"Copyright (c) 2024 bird_sql"。按文件位置推断它覆盖 `evaluation/src/` 等（推断，仓库没写明）。
     - `LiveSQLBench-CLI/LICENSE` 是 Apache-2.0 正文，版权行是没填的模板 `Copyright [yyyy] [name of copyright owner]`。按文件位置推断它覆盖 `LiveSQLBench-CLI/` 下的 `adapter.py` 和模板（推断）。spec U9 已据此更正。
     - `LiveSQLBench-Agent/LICENSE` 是 MIT，"Copyright (c) 2025 BIRD-Interact Authors"。
     - 仓库里没有 `NOTICE`；`adapter.py`、`test_utils.py` 都没有逐文件的版权头。
     - 数据集的许可证写法不一致，CC BY 4.0 还是 CC BY-SA 4.0 未确认（取证笔记 §1.4）。
     - 本工单的测试不复制上游代码和数据。测试里描述上游行为时，在 docstring 里写上游的 `路径:行号`。
   - **字段**：
     - 公开字段见取证笔记 §3：`instance_id`、`selected_database`、`query`、`preprocess_sql`、`clean_up_sql`、`category`（`Query` / `Management`）、`high_level`、`conditions`、`difficulty_tier`。这份清单来自 HF 数据集卡的摘要，clone 里没有样例 JSONL，未逐条核对。
     - 代码里核对过的只有 `category` 的取值：`evaluator.py:173` 比较 `"Query"`；`adapter.py:300` 比较小写的 `management`。
     - 隐藏字段按 `instance_id` 合并进来：`integrate_gt_data.py:47` 的 `sol_sql`、`test_cases`、`external_knowledge`。
   - **上游适配器会把 `external_knowledge` 写进题面**：`LiveSQLBench-CLI/adapters/livesqlbench/adapter.py:254,270`。spec 要求题面不含它，这一处不能照抄。
   - **查询题的评分**：
     - `LiveSQLBench-CLI/.../template/tests/evaluator.py:173-175`：`category == "Query"` 时强制用 `TEST_CASE_DEFAULT`，并带上 `conditions`。
     - `evaluation/src/test_utils.py:394-411` `test_case_default` 对预测 SQL 和标准 SQL 都做三步：
       - `remove_comments`（`:375-392`）；
       - `remove_distinct`（`:193-227`），保留 `DISTINCT ON`，靠 `:220` 的前瞻；
       - `remove_round`（`:101-116`），去掉 `ROUND(...)`、留下第一个参数，嵌套的也去掉。
     - 然后 `ex_base`（`:255-285`）比较执行结果：
       - 任一边执行出错或超时，记 0；
       - 两边都经过 `preprocess_results`（`:136-159`）：日期写成 `YYYY-MM-DD`；Decimal 和 float 四舍五入（ROUND_HALF_UP）到 2 位，见 `:118-134`；dict、list 写成 `sort_keys` 的 JSON；
       - 任一边结果为空，记 0；
       - `conditions.order` 为真时按列表比，行顺序要一致；否则按集合比，重复行合并；
       - 只比每行的值组成的元组，不比列名。
     - `ex_base` 的 docstring 说会去掉 ORDER BY，代码里没有做。以代码为准，测试不钉 docstring 的说法。
   - 上游执行 SQL 时会把每条查询打进日志：`evaluation/src/db_utils.py:302`，标准 SQL 也会打。

4. **夹具**：
   - 全部手工构造：`instance_id`、库名、题面都是编的，和真实的 Base-Lite 对不上。
   - 不从 HF 数据集复制任何一行，理由有三：
     - 数据许可证不明；
     - 复制的题可能正好落在留出集里（ADR-0004）；
     - Skill 和测试都不该带榜单题目的内容（ADR-0005）。
   - 字段名照公开字段（Context 第 3 项）。样例里要有一道 `Management` 题，用来测"跳过"。
   - 答案替身（spec `:70` 的"替身"）：`sol_sql`、`test_cases`、`external_knowledge` 的值在测试代码里拼出来，是编造的、各不相同的哨兵串，不写进夹具文件，也不是真实答案。没有它们，"不含标准答案""路径不出现"这类断言对任何实现都成立，测不出东西。
   - 划分测试要上百条记录，在测试代码里生成，不写进夹具文件。
   - 不写密钥、地址、主机名：`uv run python -m evals scrub tests/fixtures/livesqlbench` 要是 0 处改动。

5. **本工单定下的契约**（写进代码和测试的 docstring，最终回复逐项列出，人在合并时审）：
   - 128 要实现的函数和类型，只写签名，函数体 `raise NotImplementedError("ticket 128")`：
     - 任务生成：输入公开记录、答案（替身）、库的 `db_assets` 目录，输出 Harbor 任务目录；
     - 划分：按库分层，输出 `lsb-<instance_id>` 的开发、留出两份清单，加题号到库名的对照表；
     - soft EX：SQL 处理（去注释、`DISTINCT`、`ROUND`）和结果集比较（带 `conditions`）；
     - 评测脚本的入口：执行 SQL 的部分可以换成替身，这样不连库也能看它往输出里写了什么。
   - 已定的（spec U1–U11），照办：题号 `lsb-<instance_id>`、前 32 个字符互不相同；进清单顶层的 `dev`、`holdout`；对照表提交在清单旁边；Harbor kwarg 是 `benchmark=livesqlbench`。
   - 本工单定：模块名、函数名、参数名、返回类型、对照表的文件名和格式、`benchmark` 字段的类型、夹具写法，以及任何 spec 没写到的细节。都写进 docstring，最终回复列出，不停下来问。

6. **写法要求**（照 111、115、118 的教训）：
   - 标记照工单 88、115：每个文件
     ```python
     TICKET_128 = pytest.mark.xfail(strict=True, raises=..., reason="ticket 128: ...")
     ```
     - `raises=` 收窄到"功能没做"会抛的异常，例如 `NotImplementedError`、正向断言的 `AssertionError`。
     - 取值类的 `KeyError`、`ValueError` 先 `assert` 再取（`tests/acceptance/mcp_support.py:16` 的做法）。
     - 128 只删 `TICKET_128` 标记，以及因此没用了的 import 和定义。
   - **strict 的预期失败不能 XPASS**：本组大多数断言是否定式的，今天就成立。例如：
     - 题面里没有标准答案；
     - 命令行没有 `--network`、`--mcp-servers`、`--charts`；
     - 路径不出现在某处。

     这些要和一条今天会失败的正向断言写在同一个用例里，正向断言放在前面。正向断言例如：生成了任务、命令是 LiveSQLBench 的查数运行、清单里有 LiveSQLBench 的条目。否则就写成不标记的守门测试。
   - **每个预期失败的用例，失败原因都是功能没做**：空壳的 `NotImplementedError("ticket 128")`，或者正向断言不成立。不能是夹具解析失败、导入出错、被跳过。最终回复每组贴一个用 `--runxfail` 跑出的真实失败原因。
   - **不钉死 spec 留给实现时核实的东西**：
     - agent 交 SQL 还是交结果、`/app/pred.json` 的写法（spec `:38`、U10，由 128 核实）；
     - Harbor 0.24 什么时候拷入 `tests/`（spec `:20`）；
     - PostgreSQL 的版本号。
   - **评分的期望值**：
     - 照 `tests/acceptance/test_seam_c_eval_cli.py:21-22` 的做法，期望值用上游函数在本地算出来，不手推。
     - 在 scratch 脚本里调上游 `test_utils.py` 的纯函数：`remove_comments`、`remove_distinct`、`remove_round`、`preprocess_results`，加上 `ex_base` 末尾的集合比较或列表比较。上游 `test_utils.py` 会 import `db_utils` 和 `psycopg2`；拿不到时，在 scratch 脚本里放替身模块。脚本不提交。
     - 命令和输出写进 Comments；测试的 docstring 写明期望值的出处。
   - **参考实现**：
     - 临时写、不提交，补丁存到 `runs/ref127/`（不进 git）。用它证明去掉标记后各组都通过，每组至少一个反向检查会失败。
     - 不要像 111 那样建 `--no-verify` 的提交分支。
   - **本工单不测**（不在 "Testing Decisions" 里）：
     - 镜像构建和 `da-remote benchmarks`；
     - 2×2 消融的参照配置和差值表；
     - 冒烟；
     - 官方评测脚本的对拍（spec `:74`：拿到标准答案后补成集成测试，不在本工单）；
     - 报告（129）。

7. **每组要覆盖的断言**（spec `:72-77`，逐条）：
   - **任务生成**（`TICKET_128`）：
     - 用 `tests/fixtures/livesqlbench/` 的样例生成 Harbor 任务目录，每个都能被 Harbor 的 `Task(...)` 读入。
     - 题面有这道题的 `query` 文字和库名；任务类型是 `query`。
     - agent 能看到这个库公开的 `db_assets`（知识库、列含义、schema；夹具里放编的小文件），照官方任务（spec U8）。
     - `Management` 题不生成任务：生成的数目等于 `Query` 题的数目，生成的任何文件里都没有那道题的 `query` 文字。
     - 任务里不含 `sol_sql`、`test_cases`、`external_knowledge` 的任何内容：Context 第 4 项的哨兵串都不出现在题面和 `environment/` 里。
   - **划分**：
     - 用生成的上百条记录（多个库，题数各不相同，包括只有 1 道题的库）测划分函数（`TICKET_128`）：
       - 同一个种子两次结果相同；
       - 开发集 60、留出集 120，两边不相交、合起来等于全部；
       - 题数允许时，每个库在两边都有题。"允许"按至少 2 道题理解，这是本工单的解读，最终回复列出供人审。
     - 题号都是 `lsb-<instance_id>`，前 32 个字符互不相同（`TICKET_128`，用生成的记录测划分函数的输出）。
     - 提交的 `evals/manifest.json` 和对照表（`TICKET_128`）：
       - 顶层 `dev` 里有 60 个、`holdout` 里有 120 个 `lsb-` 开头的题号，不相交；对照表正好覆盖这 180 个题号，每个库在两边都有题（题数允许时）；
       - 和 data-eng-bench 的条目（`evals/registry.json` 的 `tasks`、清单里已有的条目）不冲突，`lsb-` 题号的前 32 个字符也不和它们重合。
     - `da-remote pull` 把 LiveSQLBench 的留出题当留出题处理：照 `tests/test_da_remote.py` 的假 `ssh` 脚手架写，服务器清单里放 `lsb-` 开头的开发题和留出题。
       - ablate 运行里有一个 `lsb-` 留出题的 trial（有 `result.json` 的一例，只有 Harbor trial 名的一例）时，只取回汇总；
       - 只有 `lsb-` 开发题时整目录取回。
       - 这组靠的是清单顶层 `dev`、`holdout`（spec U2），现有的 `pull` 今天就应该成立：写成不标记的守门测试。今天不成立就是新发现，写进最终回复，那个用例标 `TICKET_128`。
     - 不测 `evals board`（spec U3）。
   - **评分**（`TICKET_128`，纯函数，不执行 SQL）：期望值照 Context 第 6 项算出。spec `:74` 点名的规则各至少一个用例：
     - 去 `DISTINCT`，并保留 `DISTINCT ON`；
     - 去 `ROUND`，包括嵌套的；
     - 行顺序：没有 `conditions.order` 时顺序不同也判对；`order` 为真时顺序不同判错；
     - 列名不同、值相同判对。

     官方 soft EX 里还有几条规则（Context 第 3 项）：去注释、小数按 2 位四舍五入、日期写成 `YYYY-MM-DD`、任一边结果为空判错。spec `:37` 写"评分：照官方 soft EX"，所以这几条也各写一个用例。这是本工单的解读，最终回复列出供人审。
   - **评测入口**（`TICKET_128`）：
     - 用 `make_agent(..., benchmark="livesqlbench")` 跑一次，照 `tests/test_harbor_agent.py:343-348` 用假的 `data-agent` 取参数。
     - 先断言这是一次 LiveSQLBench 的查数运行（正向，今天失败）：`--type query`，库由 `--postgres-env <变量名>` 给出，没有 `--duckdb`。
     - 再断言没有 `--network`、`--mcp-servers`、`--charts`。
     - 不带 `benchmark` 时命令和现在一样（`--type dbt`）：不标记的守门测试。
   - **答案不外泄**（`TICKET_128`，先有正向断言）。"路径"指路径和内容两样（spec U7），用 Context 第 4 项的哨兵串和答案替身文件的路径来查：
     - agent 能看到的 workspace 和任务文件（题面、`environment/`、`db_assets`）里没有；
     - agent 的事件文件里没有：用 Harbor 适配器跑一次 LiveSQLBench trial，假的 `data-agent` 写一份事件文件，查这份文件和传给它的参数、环境；
     - 评测脚本的输出（Harbor 放进 trial `verifier/` 的东西）只有 reward 和不含答案的诊断：用替身执行器跑评测脚本的入口，判对、判错、执行出错各一次，输出里都没有标准 SQL 和标准结果的哨兵串；
     - `da-remote pull` 取回的 `runs/` 里没有：开发集 trial 的 `verifier/` 里放上面评测脚本写出的输出，整目录取回后查。
   - **只读**（spec U6，`TICKET_128`，先有正向断言）。在 `make check` 能到的两个接缝上测：
     - Harbor 适配器：`benchmark=livesqlbench` 时，传给 `exec_as_agent` 的环境和命令行里没有写库凭据的替身值，也没有 `123123`；连接串不出现在命令行里（只给 `--postgres-env` 的变量名）。
     - 生成的任务目录：agent 所在服务能看到的环境（`environment/` 里给 agent 容器的设置）和所有 agent 能看到的文件里，没有写库凭据的替身值和 `123123`；写库凭据只出现在验证阶段用的地方。
     - 测不到的：连库用的角色是不是真的只读、沙箱能不能用别的凭据写库。这要真的 Postgres，`make check` 没有（Context 第 2 项）。在测试模块的 docstring 里写明这一点，由 128 在冒烟时实测并写进它的 Comments。

8. **并行**：
   - 121、122（可观测性）在改 `src/data_agent/` 和 `web/`；
   - 125（skill-improve 验收）、130（IM 验收）还是 `ready-for-agent`。
   - 本工单只新建测试、夹具和 `evals/` 下的一个新模块，加 `harbor_agent.py` 的一个字段，应该不冲突。

9. 只用 uv。

## Acceptance

- [ ] spec "Testing Decisions" 每一条都有断言（Context 第 7 项逐条对照）
- [ ] 标 `TICKET_128` 的用例当前都以预期失败通过，没有被跳过的，也没有 XPASS；守门测试当前通过，且不带标记
- [ ] 每个预期失败的用例，失败原因都是功能没做（`NotImplementedError("ticket 128")` 或正向断言不成立），不是夹具、导入或脚手架出错；最终回复每组贴一个 `--runxfail` 的真实失败原因
- [ ] 每组都临时写过参考实现（不提交，补丁在 `runs/ref127/`），去掉标记后能通过。每组至少一个反向检查会失败，例如：
  - 题面带上 `external_knowledge`（照上游 `adapter.py:270`）；
  - 管理题也生成了任务；
  - 划分不按库分层，有库只落在一边；
  - 去 `DISTINCT` 时把 `DISTINCT ON` 也去掉了；
  - 忽略 `conditions.order`；
  - 比较了列名；
  - 命令行带上 `--network`；
  - 标准答案路径进了题面；
  - 评测脚本把标准 SQL 打进输出（照上游 `db_utils.py:302`）；
  - agent 的环境里带上 `PGPASSWORD=123123`；
  - 题号不带 `lsb-` 前缀
- [ ] 评分用例的期望值都由上游函数算出，命令和输出贴在 Comments
- [ ] 夹具里没有真实题目、密钥、地址、主机名：`uv run python -m evals scrub tests/fixtures/livesqlbench` 是 0 处改动；pre-commit 的密钥与公网 IP 扫描通过
- [ ] 不复制上游代码；`git diff main --stat` 只有 Authority 列的新文件，加上 `evals/harbor_agent.py` 里那一个字段
- [ ] Context 第 5 项的契约写在代码和 docstring 里，最终回复逐项列出
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 的完整输出。
- `uv run pytest tests/acceptance/test_livesqlbench_*.py -v` 的输出：每个用例的名字，XFAIL 还是 PASSED。
- 每组一个 `--runxfail` 的失败原因。
- 参考实现和反向检查的结果。
- 上游 clone 的 commit；评分期望值的计算命令和输出。
- 脱敏命令的输出。
- 契约清单。

## Stop conditions

- 第二轮审查修复后仍不过。

spec 没写到的细节不是停下的理由：按 Context 第 5 项由本工单定，写进 docstring，最终回复列出，人在合并时审。只读组里测不到的部分照 Context 第 7 项写明理由，不停下。守门测试今天不通过时，把它改标 `TICKET_128`，写进最终回复。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: accepted

## Comments

- 2026-11-24（工单 agent 最终回复，主会话照抄）：

### 合并时请人审的点

1. **"题数允许"按"至少 2 题"理解。**
2. **评分在 spec 点名的规则外多了几条，都照官方 soft EX 写：** 去注释、小数 2 位四舍五入、日期、空结果判错、重复行合并、JSON 值。
3. **写库凭据给验证阶段的方式。** `[verifier] env` 里放凭据值，或放 `${LSB_PG_WRITE_PASSWORD}` 模板，两种都判对。agent 这边连这个变量名都不能出现。Harbor job 级的 `verifier_env` 这条路，这组测试没有覆盖。
4. **`--postgres-env` 指的变量必须由任务的 agent 容器提供**（`[environment] env` 或 compose 的 `main` 服务）。由适配器或 `--ae` 给的话，这条测试不过。
5. **取回 `runs/` 的外泄检查查的是服务器上的 run 目录。** 假 rsync 不拷文件；先断言了这次是整目录取回，再查这个目录。
6. **Harbor 会把 job 的 `--ae` 注入容器。** 所以 job 配置本身不能经 `--ae` 传写库凭据，适配器的测试管不到这一层。这条留给 128 的 job 配置。

### Comments（抄进工单）

**上游与 clone**
- `bird-bench/livesqlbench` 已 clone 到 `~/dev/AI/data-agent-refs/livesqlbench`，在 `e15cd22`（"Merge pull request #9 …"）。
- 没有下载数据、dump 或镜像，也没有连服务器。

**基线**
- 改动前跑的 `make check`：2850 passed、2 skipped（`runs/ref127/baseline-check.txt`）。

**评分期望值的来源**
- 脚本 `runs/ref127/expected_soft_ex.py`（不提交）。它从上游 `evaluation/src/test_utils.py` import `remove_comments`、`remove_distinct`、`remove_round`、`preprocess_results`；上游要的 `db_utils`、`psycopg2` 用 `runs/ref127/upstream_stubs/` 里的替身模块。
- SQL 按 `test_case_default` 的顺序清洗；结果比较照 `ex_base` 最后几行（`:276-285`）。
- 命令：`uv run python runs/ref127/expected_soft_ex.py runs/ref127/upstream_stubs ~/dev/AI/data-agent-refs/livesqlbench/evaluation/src`
- 输出原文在 `runs/ref127/expected_soft_ex.out`。摘录：
  - `distinct_on` 保持原样
  - `round_nested` 得到 `"SELECT price * 1.1 FROM items"`
  - `comments` 得到 `"SELECT a FROM t WHERE b = 1"`
  - `order_true_permuted` 0；`order_none_permuted` 1；`duplicates_merged` 1
  - `decimal_half_up` 1；`float_half_up` 1；`decimal_differs` 0
  - `date_vs_datetime` 1；`date_vs_string` 1
  - `json_key_order` 1；`pred_empty`、`gold_empty`、`both_empty` 都是 0
- 测试里的期望值全部照抄这份输出，没有手推。

**`uv run pytest tests/acceptance/test_livesqlbench_*.py -v` 的结果**
- 5 passed、48 xfailed、0 XPASS、0 skipped。
- PASSED（守门测试，不带标记）：
  - `split::test_the_generated_records_are_what_the_split_tests_assume`
  - 3 条 `da-remote pull` 守门：`lsb-` 留出题 trial 有 `result.json` 的一例和只有 trial 名的一例都只取回汇总；只有 `lsb-` 开发题时整目录取回
  - `entry::test_without_the_benchmark_kwarg_a_trial_is_the_data_eng_bench_dbt_run`
- 其余都是 XFAIL：tasks 4、split 6、scoring 27、entry 2、leaks 7、readonly 2。
- 用例逐条的名字在 `runs/ref127/lsb-verbose.txt`。

**`--runxfail` 的失败原因（每组）**

| 组 | 失败原因 |
|---|---|
| 任务生成 | 4 条都是 `evals/livesqlbench.py:72: NotImplementedError: ticket 128` |
| 划分 | 4 条 `:93 NotImplementedError`；2 条 `AssertionError: 0 LiveSQLBench dev ids in the manifest` |
| 评分 | 27 条：`:118`、`:127` 的 `NotImplementedError` |
| 评测入口 | 1 条 `:72 NotImplementedError`；1 条 `assert '/bin/data-agent run --type query ' in 'if [ "$DB_TYPE" != duckdb ]…'` |
| 答案不外泄 | 5 条 `:145`、2 条 `:72`，都是 `NotImplementedError` |
| 只读 | 2 条 `:72 NotImplementedError` |

没有一条是因为夹具、导入或脚手架出错而失败。

**参考实现与反向检查**
- 临时写的参考实现补丁在 `runs/ref127/ref.patch`，`runs/ref127/ref_livesqlbench_tail.py` 是它的实现部分。补丁同时带了一份临时的 60/120 清单和对照表（`runs/ref127/ref_manifest.py` 生成）。
- 用 `--runxfail`（等于去掉标记）跑：53 passed。
- 反向检查用 `LSB_REF=<flag>` 切换（`runs/ref127/reverse.sh`，输出在 `runs/ref127/reverse-checks.txt`）。每个开关都让对应的用例失败：
  - `leak_ek`（题面带 `external_knowledge`）：3 失败
  - `management`（管理题也生成任务）：5 失败
  - `unstratified`（不按库分层）：1 失败
  - `drop_distinct_on`（连 `DISTINCT ON` 一起去掉）：1 失败
  - `ignore_order`（忽略 `conditions.order`）：1 失败
  - `compare_columns`（比较列名）：4 失败
  - `network`（命令行带 `--network`）：2 失败
  - `leak_path`（答案路径进题面）：2 失败
  - `log_sql`（评测脚本打印标准 SQL）：3 失败
  - `agent_password`（agent 的环境带写库凭据）：1 失败
  - `password_in_main`（`main` 服务带 `PGPASSWORD=123123`）：2 失败
  - `noprefix`（题号不带 `lsb-`）：6 失败
- 参考实现用 `git apply -R` 撤掉、新文件按路径删除，`evals/manifest.json` 和原文件逐字节一致。分支上没有参考代码。

**脱敏与扫描**
- `uv run python -m evals scrub tests/fixtures/livesqlbench`：`changed 0 file(s) of 7; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0`。
- 三次提交的 pre-commit 都过了，包括 "Detect hardcoded secrets"。

**改动范围**
- `git diff main...HEAD --stat`：16 个文件，+1342 / −1。
- 都是 Authority 列出的新文件，加上 `evals/harbor_agent.py` 里的 `benchmark` 字段和它要的 `Literal` import。没有复制上游代码。

**审查**
- `da-review main`（Codex）："No actionable defects found against ticket 127's acceptance-test scope."
- `eng:code-review main`，Standards 一侧：没有硬性违规。
  - 已修：两处函数缺返回类型；`pulled_whole` 改成从 `tests/test_da_remote.py` import，不再照抄一份。
  - 不修 `ablate_run` 的复制：原函数写死了自己的清单，要改它就得改 `tests/test_da_remote.py`，那不在 Authority 里。
  - 不修 `benchmark` 字段暂时没人用：这是工单定下的空壳，128 会用它。
  - 不修 `dev_size` 参数：它是契约的一部分。
  - 不修各文件各自定义 `TICKET_128`：照工单 88、115 的约定，128 要逐个文件删。
  - 不修测试里直接写 `"lsb-"`：故意钉死 spec 的题号格式。
- `eng:code-review main`，Spec 一侧：
  - 已修：入口测试原来要先生成任务才能碰到适配器。现在加了一条只测适配器的用例，今天失败在 `--type query` 那条正向断言上。
  - 已修：写库凭据原来只能以明文值放进 `[verifier] env`。现在明文值和 `${LSB_PG_WRITE_PASSWORD}` 模板都接受，并禁止这个变量名出现在 agent 能看到的地方。
  - 已修：前 32 个字符互不相同那条原来是空检查（生成的题号太短）。现在生成的题号都超过 32 个字符，只有前 10 个字符不同。
  - 不修，交人审："`--postgres-env` 的变量来自容器"和"取回 `runs/` 查服务器副本"，即上面第 4、5 条。
- 修复 1 轮。修完重跑了参考实现和全部反向检查，结果同上。

### 契约清单（写在 `evals/livesqlbench.py` 的 docstring 里）

**常量**
- `BENCHMARK = "livesqlbench"`
- `TASK_PREFIX = "lsb-"`
- `DEV_SIZE = 60`，`HOLDOUT_SIZE = 120`
- `DATABASES`：`evals/livesqlbench_databases.json`，格式 `{"lsb-<instance_id>": "<selected_database>"}`，正好覆盖清单里的 LiveSQLBench 题号
- `WRITE_PASSWORD_ENV = "LSB_PG_WRITE_PASSWORD"`

**函数和类型**
- `task_id(instance_id) -> str`：返回 `lsb-<instance_id>`，instance id 原样不变。这也是 Harbor 任务目录名，也就是 trial 的 `task_name`。
- `generate_tasks(public, answers, db_assets, out, *, write_password) -> list[Path]`
  - `public` 是公开字段的 JSONL；`answers` 是按 `instance_id` 对应的隐藏字段 JSONL；`db_assets` 下是 `<db>/` 目录。
  - 每道 `Query` 题生成一个 `out/lsb-<id>`，`Management` 题不生成。
  - `task.toml` 的 `[metadata]` 里写 `task_type = "query"`；题面含 `query` 和库名；`db_assets` 原样放在 `environment/` 下。
  - 隐藏字段和答案文件路径都不进题面和 `environment/`。
  - 写库凭据只进 `[verifier] env`；`environment/` 和 `[environment] env` 里都没有它、没有它的变量名、也没有 `123123`。
- `split(records, seed, dev_size=DEV_SIZE) -> Split(dev, holdout, databases)`：按 `selected_database` 分层；同一个种子结果相同；至少 2 道题的库两边都有题。
- `Result(columns, rows)`；`ExecutionError`；`Execute = Callable[[Sequence[str]], Result]`。
- `clean_sql(sqls) -> list[str]`：照上游 `test_case_default` 的顺序，128 照抄上游函数（spec U9）。
- `results_match(predicted, gold, conditions) -> bool`：照 `ex_base` 末尾的比较，不比列名。
- `evaluate(payload, predicted_sql, execute, out) -> int`
  - 写 `out/reward.txt`（`1` 或 `0`）；任一边 `ExecutionError` 记 0。
  - 写出、打印、记日志的内容里都没有标准 SQL 和标准结果的值。
  - 怎么读 `/app/pred.json` 不钉死（spec U10）。

**适配器字段**
- `DataAgentOptions.benchmark: Literal["data-eng-bench", "livesqlbench"]`，默认 `"data-eng-bench"`。

**夹具**
- 3 道编造的题：两道 `Query`，一道 `Management`；两个编造的库各有 3 个 `db_assets` 文件。
- 答案替身（哨兵串）在测试代码里拼出来，不写进夹具文件。

**只读组测不到的部分**
- agent 连库用的角色是不是真的只读、沙箱能不能用别的凭据写库：`make check` 没有 Postgres，测不到。已写进模块 docstring，由 128 在冒烟时实测并写进它的 Comments。

- 2026-11-24（主会话）合并：人审通过（契约照工单 Comments 原样），PR #133 squash 合入 main（`69dd3b8`）。CI 两次都过；Codex GitHub app："Didn't find any major issues"。
