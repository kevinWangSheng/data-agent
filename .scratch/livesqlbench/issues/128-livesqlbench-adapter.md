# 128 — LiveSQLBench：适配器、PostgreSQL 镜像、soft EX、60/120 划分、`ablate`/`report` 按榜单过滤、只读凭据、冒烟（不含官方对拍）

Status: in-review
Blocked by: 127（done）
Spec: ../spec.md
Claimed by: claude (Opus 5.5, worktree 128-livesqlbench-adapter)
Branch: worktree-128-livesqlbench-adapter

## Goal

Base-Lite 的 180 道 `Query` 题能在服务器上以 Harbor 任务跑通，并满足以下各项：
- 每个库一个 PostgreSQL 镜像，由 `da-remote benchmarks` 下载、校验、构建；
- agent 只拿只读角色，写库凭据只在验证阶段出现；
- soft EX 照抄上游；
- 60/120 划分提交进 `evals/manifest.json`，旁边是题号到库名的对照表；
- `ablate --benchmark livesqlbench --reference no-verifiers` 能跑开发集；
- 加入 `lsb-` 题号后，data-eng-bench 的 `ablate`、`report`、`compare`、`accept` 照旧可用。

127 合入的 `TICKET_128` 验收测试全部转绿。冒烟在服务器上由主会话跑：开发集 3 题 × 4 个配置，跑完、无基础设施报错，得出单题成本。

**本工单不做官方对拍，也不做"每道题都能判分"的检查（oracle）。** 标准答案（`sol_sql`、`test_cases`）是人发邮件申请的，现在不一定在手。这两项拆给工单 136，答案到手后做。spec `:21` 写明答案到手之前适配器、镜像、冒烟可以先做（冒烟只看能不能跑完、不算分）。所以本工单的范围和验收都不需要标准答案。

## Authority

- May edit：
  - `evals/livesqlbench.py`：实现 127 的契约，名字、参数、返回类型都不改。docstring 里对上游字段的描述可以按真实数据更正，例如公开记录里的字段叫 `clean_up_sqls`（Context 第 5 项）。
  - 新建：
    - `evals/livesqlbench_databases.json`（契约 `DATABASES`）；
    - `evals/configs/livesqlbench.data-agent.yaml`（job 配置，kwargs 带 `benchmark: livesqlbench`）；
    - 验证阶段的脚本和上游构建脚本的改写版（放哪里由本工单定，见 Context 第 9 项）。
  - `evals/harbor_agent.py`：`benchmark=livesqlbench` 时的运行命令（`_run_command`，`:370-395`）。别的 benchmark 行为不变。
  - `evals/ablate.py`：
    - `--reference`（spec U1）；
    - `--benchmark`：选 job 配置，按榜单过滤要跑的题和预检冒烟的题；
    - 留出集不跑无答案的任务（Context 第 8 项）。
  - `evals/report.py`、`evals/__main__.py`（`compare`、`report`，以及新的 `livesqlbench` 子命令：生成任务、算划分）、`evals/accept.py`：按榜单过滤 split（Context 第 3 项）。
  - `evals/manifest.json`：只往顶层 `dev`、`holdout` 追加 `lsb-` 题号，另加记录 LiveSQLBench 种子和数据来源的键。data-eng-bench 的条目和 `seed` 一字不改。
  - `scripts/da-remote`：
    - `benchmarks`：加 LiveSQLBench 的步骤；
    - `check`：加 LiveSQLBench 就绪项；
    - `start`：LiveSQLBench 运行要读写库口令的 env 文件，照 dab 的做法，`:497-526`。
  - `tests/`：
    - 新单元测试；
    - `tests/test_eval_manifest.py`：U2 要求更新。`:9-15` 钉死 30/30，改成只数 data-eng-bench 的条目，另加 LiveSQLBench 的 60/120；
    - 已有的非验收测试里，被本工单改动弄过时的断言：照工单 100 的做法，Comments 里逐条列出改了什么、为什么。
  - `tests/acceptance/test_livesqlbench_*.py`：**只**删 `TICKET_128` 标记，以及因此没用了的 `TICKET_128 = ...` 定义和 import（AGENTS.md 硬规矩的唯一例外）。断言、夹具、输入一字不改。
  - `NOTICE`：登记从上游复制或改写的文件，见 Context 第 4 项。
  - `docs/setup.md`：LiveSQLBench 的服务器准备、env 文件、数据来源与许可证说明。
- Must not edit：
  - `tests/acceptance/` 里除删 `TICKET_128` 以外的任何内容；
  - `tests/fixtures/livesqlbench/`；
  - `src/`：需要改 `src/` 才能跑通时，停下交回，见 Stop conditions；
  - `evals/board.py`：U3，不跑本榜单；
  - `evals/registry.json`、`evals/regression.json`；
  - data-eng-bench 的 job 配置；
  - `skills/`：ADR-0005，不写针对榜单的内容；
  - `README.md`、`docs/report.md`：留给 129；
  - `.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、`.github/workflows/`、其他工单的文件。
- Server access：工单 agent 没有。理由：
  - `da-remote setup` 从 GitHub 取提交（`scripts/da-remote:306-341`），工单 agent 不 push。工单 22 就是因此由主会话 push 分支后在服务器上跑的（`.scratch/phase1/issues/22-ablate-prefetch-wheels.md:65`、`:81`）。
  - 工单 agent 加载不了模型环境（工单 112 `:28` 的先例）。

  服务器上的事由主会话做，见 Context 第 7 项。
- Spend cap：
  - 工单 agent：$0。
  - 主会话：$4。冒烟按 16 个 trial 估：开发集 3 题 × 4 配置 = 12，加 `ablate` 预检每个配置 1 个（`evals/ablate.py:893`、`:508-511`），共 16。
    - 单题成本参照 `docs/report.md:244-249`（模型 `deepseek-flash`，`:31`）：
      - 留出集消融 $0.080/trial；
      - data-eng-bench 全量 $0.049/trial；
      - DAB 查数 $0.019–0.055/trial。
    - 查数题按 $0.05–0.08 估，16 个 trial 约 $0.8–1.3。
    - 上限 $4 够两次冒烟，加几次单题排错。每次运行本身封顶 $1（四个配置都是 `max_budget_usd = 1.0`，例如 `src/data_agent/configs/no-verifiers.toml:6`）。
    - 属于小额运行（不到 30 题），已预先批准。范围不能再扩大。
- 网络（工单 agent）：
  - 只读上游：`~/dev/AI/data-agent-refs/livesqlbench` @ `e15cd22` 已在本机（127 clone 的）。
  - 可以下载 HF 公开数据集 `birdsql/livesqlbench-base-lite` 固定在 revision `507d7d98f477b9d6a990628ea021bd2501cfd6e2`：只下到 `runs/128/`，不提交。
  - 不下载 dump 和镜像：spec `:39`，dump 只放服务器。不发邮件。

## Context

1. **行为来源**：
   - spec `.scratch/livesqlbench/spec.md`：
     - "适配器（Q3）" `:32-40`、"运行（Q5、Q6）" `:42-56`、"工单"表 128 行 `:63`、"验收" `:79-85`；
     - 文末 U1–U11（`:101-135`），全部已定，照办，不再问；
     - "标准答案（Q1）" `:17-21`：答案只放服务器 `da-benchmarks/`。
   - 取证笔记 `docs/research/2026-11-23-livesqlbench.md`：§2.3 版本与 compose，§3 评分与隐藏字段，§5 体量。
   - ADR：
     - `0004`：留出集只看汇总；
     - `0005`：不写针对榜单的内容；
     - `0010`：写库凭据。
   - AGENTS.md 硬规矩：
     - 沙箱和 agent 拿不到写库凭据；
     - 评测入口不开 `--network`；
     - 复制上游代码保留版权头并登记 `NOTICE`；
     - 只用 `da-remote`。

2. **127 定下、人在 2026-11-24 批准的契约**：`evals/livesqlbench.py:30-145`，以及 127 工单 Comments 的"契约清单"`:383-417`。已定，照实现。
   - 常量：`BENCHMARK`、`TASK_PREFIX = "lsb-"`、`DEV_SIZE = 60`、`HOLDOUT_SIZE = 120`、`DATABASES`、`WRITE_PASSWORD_ENV = "LSB_PG_WRITE_PASSWORD"`。
   - 函数：`task_id`、`generate_tasks(public, answers, db_assets, out, *, write_password)`、`split(records, seed, dev_size)`、`Result`、`ExecutionError`、`Execute`、`clean_sql`、`results_match`、`evaluate(payload, predicted_sql, execute, out)`。
   - 适配器字段：`DataAgentOptions.benchmark`（`evals/harbor_agent.py:251-256`），已经有了，只是没用。
   - 127 交人审、人已批准的几点（127 工单 `:289-294`）：
     1. "题数允许"按至少 2 题理解；
     2. 评分照官方多出的几条规则；
     3. 写库凭据可以是 `[verifier] env` 的值，也可以是 `${LSB_PG_WRITE_PASSWORD}` 模板；
     4. `--postgres-env` 指的变量由任务的 agent 容器提供：`[environment] env`，或 compose 的 `main` 服务；
     5. 取回外泄的检查查服务器上的 run 目录；
     6. job 配置不能经 `--ae` 传写库凭据。Harbor 会把 `--ae` 注入容器，这一层本工单的 job 配置负责。
   - 要转绿的测试：`tests/acceptance/test_livesqlbench_{tasks,split,scoring,entry,leaks,readonly}.py`。main 上现在是 5 passed、48 xfailed（建票时实跑 `uv run pytest tests/acceptance/test_livesqlbench_*.py -q`，2026-11-24）。
   - 测试里几条硬约束，设计时要满足：
     - `agent_visible` 是题面、`environment/` 下每个文件、`[environment] env` 加 compose `main` 的 `environment`（`tests/acceptance/livesqlbench_support.py:135-147`、`:164-167`）。所以 compose 文件里不能有写库口令、`LSB_PG_WRITE_PASSWORD` 这个名字和 `123123`，数据库边车的环境也一样。上游 compose 把口令写在边车上（`template/environment/docker-compose.yaml:24-25`），这一处不能照抄。
     - 评测入口的测试要 `/bin/data-agent run --type query `，并且 `--postgres-env <VAR>` 后面跟一个空格（`tests/acceptance/test_livesqlbench_entry.py:59-60`）。
     - 评测脚本的替身执行器在报错里引用失败的 SQL（`test_livesqlbench_leaks.py:80-101`），所以评测脚本不能原样打印执行错误。

3. **代码现状**（main `0a8305c`，下面每条都对照代码读过；`7333271` 之后只有工单文件变了）：
   - **Harbor 适配器**：
     - `_run_command`（`evals/harbor_agent.py:370-395`）写死 `run --type dbt`、`--duckdb`、`DB_TYPE != duckdb` 就退出，并且用 `--sandbox local`（`:386`）。
     - 工作目录 `WORKSPACE = /app`（`:271`）。
     - `install()` 在容器里探 `python3`（`:300`），用 `python3 -m venv --without-pip` 建 venv（`:339`）。项目要 Python ≥ 3.12（`pyproject.toml:6`）。
     - 模型变量只取 `MODEL_ENV`（`:350-354`）。
   - **查数运行**：
     - `data-agent run --type query --postgres-env VAR` 从变量里读连接串，连接串不进 argv（`src/data_agent/cli.py:75-82`）。
     - 网关以 `read_only` 连接、在 READ ONLY 事务里执行（`src/data_agent/gateway.py:256`、`:263`）。
     - `--sandbox local` 的命令只带白名单变量（`src/data_agent/sandbox.py:80`、`:196-201`），连接串变量不在里面。
     - 查数题的提示词要求最后一条消息给出答案（`src/data_agent/agent.py:212-218`、`:249-263`），没有写文件的要求。
     - 查数运行有 Write、Edit 工具（`BUILTIN_TOOLS = FILE_TOOLS`，`agent.py:129`；`FILE_TOOLS` 在 `src/data_agent/workspace_guard.py:31`），只能写工作目录里的文件（`workspace_guard.py:1-16`）。所以写 `/app/pred.json` 是做得到的。
     - 验证子 agent 只用于 dbt（`agent.py:1386-1388`）。
     - 四个配置的查数轮数都是 50（例如 `src/data_agent/configs/no-verifiers.toml:10`），和上游任务说明的"最多 50 步"一致（`LiveSQLBench-CLI/adapters/livesqlbench/template/documents/README_TASK.md:52`）。
   - **`ablate`**：
     - `REFERENCE = "full"`（`evals/ablate.py:105`），`LAYERS` 是"full 减各配置"的标签（`:106-113`），`--config` 里必须有 `full`（`:828-831`），差值用 `REFERENCE` 算（`:743-749`）。
     - `JOB_CONFIG` 写死 data-eng-bench（`:130`）。
     - 要跑的题是 `manifest[args.split]` 整个列表（`:834`）。预检冒烟跑 `dev[:1]`（`:893`），这里的 `dev` 是整个清单的 `dev`（`:835`）：加入 `lsb-` 以后，冒烟的那一题可能是别的榜单的。
     - `--task` 只配 `--split dev`（`:172-174`）。
     - 冒烟 trial 没有 reward 就停（`:519`）。
     - `prefetch_wheels` 用 `wheels.task_image`（`:421-428`）。`task_image` 读 `task.toml` 的 `docker_image`，没有就读 `environment/Dockerfile` 的 `FROM`（`evals/wheels.py:221-240`）。
     - 开发集的报告会另写一份只含这次所跑题目的临时清单（`:730-732`），所以开发集报告已经只含本榜单的题。留出集没有这一步。
   - **把一个 split 当成单个榜单全部题的地方**，加入 `lsb-` 后都会坏：
     - `split_trials`（`evals/report.py:348-362`，要求 split 里每题都有 trial）；
     - 调用它的地方：
       - `report`（`evals/report.py:569`）；
       - `compare --manifest --split`（`evals/__main__.py:56-60`）；
       - `accept`（`evals/accept.py:90` 把 `manifest["dev"]` 整个传进去，`:132-134`）；
       - `ablate` 的 `problem`（`evals/ablate.py:262-267`）。

     spec U2 只点了前两个，后两个是本工单对照代码找到的，也要改。
   - **会坏的已有测试**：`tests/test_eval_board.py:193-194` 断言清单的 `dev`、`holdout` 都在 registry 里，加入 `lsb-` 就不成立。改成只比 data-eng-bench 的条目（May edit 的"被弄过时的断言"），Comments 写明。
   - **不受影响的地方**：
     - `board` 先按 registry 过滤 dev，`holdout` 只用来判断成员（`evals/board.py:638-640`、`:412-452`）；
     - `tests/test_dbt_skills.py:412-418` 用清单里的题名查 Skill 有没有写到题目，`lsb-` 题名只会多查几个名字；
     - `evals/attribute.py:307`、`:518`，`evals/diverge.py:162`：只判断成员；
     - `split_tier` 只判断 `task in dev`（`evals/harbor_agent.py:138-146`）；
     - `da-remote pull` 按清单顶层判断（`scripts/da-remote:687-693`），127 的 3 条守门测试已经证明它照旧有效。
   - **`evals sample` 会整份重写清单**（`evals/__main__.py:44-48`；用法在 `evals/__init__.py:3-8`）：再跑一次会丢掉 `lsb-` 条目。
   - **`da-remote`**：
     - `benchmarks`（`scripts/da-remote:344-473`）现在只准备 data-eng-bench 和 DAB。它给 DAB 生成随机口令，写进 mode 600 的 env 文件（`:411-427`，`DABENV` 在 `:121`），口令只经环境传给 docker。本工单照这个做法生成写库口令。
     - `start` 只给 `dab` 运行读额外的 env 文件（`:497-526`）。
     - `check` 在 `:210` 起。
     - 测试脚手架：`tests/test_da_remote.py` 的 `Env`（`:62` 起），假 `ssh`、`rsync`、`uv`。

4. **上游 LiveSQLBench @ `e15cd22`**（`~/dev/AI/data-agent-refs/livesqlbench`，本工单读过的部分）：
   - **任务说明**（`LiveSQLBench-CLI/adapters/livesqlbench/template/documents/README_TASK.md`）：
     - agent 把答案写进 `/app/pred.json`，格式 `{"instance_id", "predicted_sql": [...]}`（`:19-27`）；
     - 给 agent 的是 `db_assets/<db>_column_meaning_base.json`、`_kb.jsonl`、`_schema.txt`（`:10-12`）；
     - 题面只有 `query`，没有 `conditions`（`:54-59`）。
   - **评测**：
     - `template/tests/test.sh` 等库就绪；没有 `/app/pred.json` 就写 `0.0`（`:36-40`）；跑 `evaluator.py`；`debug.txt` 会 `cat` 出 `pred.json`（`:44-53`）。
     - `evaluator.py` 读 `/tests/task_payload.json`。缺 `sol_sql` 时打印 FAIL、返回 0（`:130-143`）。没设 `PGPASSWORD` 时用 `123123`（`:156`）。`Query` 题强制用 `TEST_CASE_DEFAULT`（`:173-175`）。
   - **soft EX**（`evaluation/src/test_utils.py`，根目录 `LICENSE` 是 MIT，"Copyright (c) 2024 bird_sql"）：
     - `remove_round` 在 `:101-116`；`preprocess_results` 在 `:136-159`；`remove_distinct` 在 `:193-227`；
     - `ex_base` 在 `:255-285`：`conditions.order` 为真时按列表比，否则按集合比；
     - `remove_comments` 在 `:375-392`；`test_case_default` 在 `:394-411`。
     - spec U9：照抄，不重写。
     - 上游执行 SQL 时逐条打日志，标准 SQL 也打（`evaluation/src/db_utils.py:302-304`），不照抄。
   - **数据库镜像**（`LiveSQLBench-CLI/`，Apache-2.0，版权行是没填的模板）：
     - `build_db_images.sh` 每个库建一个 `livesqlbench-db-<db>:latest`：已有就跳过，`--force` 重建，默认 `postgres:14.12`（`:27`、`:212-249`）。
     - `template/environment/Dockerfile.db` 把 `ENV POSTGRES_PASSWORD=123123` 写进镜像（`:4-7`），构建时跑 `bootstrap-db-image.sh`。
     - `bootstrap-db-image.sh` 建 SUPERUSER 角色（`:36-38`），`pg_hba` 用 `scram-sha-256`（`:19`、`:24`），然后建 `<db>_template`。
     - `db-entrypoint.sh` 每次启动从模板克隆 `<db>`，再跑 `preprocess.sql`（`:32-38`）。
     - `init-db.sh` 是按表顺序恢复 dump 的逻辑（`:76-198`）。
   - **agent 镜像**（`template/environment/Dockerfile.claudecode`）：`python:3.10-slim` 加 `psycopg2-binary` 等（`requirements.txt:1-10`）。它装的是 Claude Code CLI，和我们的安装方式不同，不照抄。
   - **dump 的来源**：Base-Lite 的 dump 在 Google Drive，文件 id `1QIGQlRKbkqApAOrQXPqFJgUg8rQ7HRRZ`（`LiveSQLBench-CLI/README.md:53`）。
     - 上游没有给校验和（README 和脚本里都没有）。
     - 官方的体量期望值：175 张表、2286 列、约 207.15 MB（上游根目录 `README.md:204-207`，由 `evaluation/check_db_metadata.py` 算）。
   - **上游适配器写的 `task.toml`**：agent 1800 s，验证 1200 s，2 CPU / 4096 MB / 20480 MB（`adapter.py:297-320`，取证笔记 §2.3）。

5. **数据事实**：HF `birdsql/livesqlbench-base-lite`，revision `507d7d98f477b9d6a990628ea021bd2501cfd6e2`，2026-03-02，没有门禁。对照会话 2026-11-24 下载 `livesqlbench_data.jsonl`，只打印了汇总，没有打印 `query`：
   - 270 条记录：`Query` 180 条、`Management` 90 条。18 个库，每个库正好 10 道 `Query` 题。
   - 字段：`instance_id`、`selected_database`、`query`、`preprocess_sql`、`clean_up_sqls`（注意是复数）、`sol_sql`、`external_knowledge`、`test_cases`、`category`、`high_level`、`conditions`、`difficulty_tier`。
     - 三个隐藏字段有键，但 270 条全是空值。
     - `conditions` 的键是 `decimal`、`distinct`、`order`，180 题都有。
   - 题号：`lsb-<instance_id>` 长 10–18 个字符，只含 `[A-Za-z0-9_-]`，前 32 个字符互不相同。
   - 每个库的公开 `db_assets` 在数据集仓库的 `<db>/` 下：`_column_meaning_base.json`、`_kb.jsonl`、`_schema.txt`。

6. **Harbor 0.24**（`.venv/lib/python3.12/site-packages/harbor/`）：
   - 验证阶段才上传 `tests/`（`verifier/verifier.py:173-186`）。spec `:20` 要求核实的事，这里核实了。
   - 验证阶段合并环境变量 `task.config.verifier.env`、job 级的 `verifier_env`、`override_env`（`:188-192`）。
   - `${VAR}` 模板从宿主进程的 `os.environ` 解析（`utils/env.py:94-110`）。
   - `VerifierConfig` 还支持把验证放进单独的容器（`environment_mode = "separate"`，`models/task/config.py:562-589`）。
   - 有 `environment/docker-compose.yaml` 时走 compose。给了 `docker_image` 就用 prebuilt 的 `main`，`image: ${PREBUILT_IMAGE_NAME}`（`environments/definition.py:26-36`、`environments/docker/docker-compose-prebuilt.yaml`、`environments/docker/docker.py:358-420`）。
   - **未确认**：
     - 本地 tag、没推到仓库的 prebuilt 镜像，Harbor 起 compose 时会不会去拉。按 compose 默认的 `pull_policy: missing` 推断不会拉，本工单实测；
     - Harbor 会不会把解析后的 `verifier.env` 写进 trial 的 `config.json` 或 `result.json`。这决定写库口令会不会进 `runs/`，本工单在冒烟里查。

7. **服务器分工**（工单 22、112 的做法）。工单 agent 写好代码、单元测试、本地证据，两道审查都过以后，在最终回复里给出下面的命令清单，Status 写 `in-review`。主会话接着做：
   1. push 本工单分支（不是 main）。用仓库里的 `scripts/da-remote`：`~/.local/bin/da-remote` 可能是旧版，见工单 109 `:26`。
   2. `scripts/da-remote setup <本分支提交>`。
   3. `scripts/da-remote benchmarks`：下载、校验、构建镜像，写 env 文件，生成任务。
   4. `scripts/da-remote check`。
   5. 只读实测：Acceptance 的"只读实测"一项。
   6. `scripts/da-remote start lsb-smoke -- ablate --benchmark livesqlbench --tasks-dir <服务器上生成的任务目录> --manifest evals/manifest.json --split dev --task … ×3 --config baseline --config no-skill --config no-context --config no-verifiers --reference no-verifiers --trials 1 --out runs/lsb-smoke --budget 4`。
   7. `status`、`pull`，把输出贴进本工单 Comments。

   任何一步失败，主会话在 Comments 写"退回：服务器运行"并附上（已遮盖的）输出，Status 回到 `ready-for-agent`，工单 agent 在原分支上修。服务器这一轮最多退回 2 次，之后停下交人。PR 和 Codex PR 审查照 AGENTS.md 第 6、7 步，在冒烟通过之后进行。

8. **答案不在手时的任务**（本工单定行为，具体做法见第 9 项的推荐）：
   - 契约里 `answers` 是必填的 `Path`。答案文件里没有某道题时，这道题也照常生成任务。它的验证脚本不比较结果：写 `reward.txt` 为 `0`，另写一份不含答案的诊断，标明"没有标准答案"。所以冒烟能跑完，不会因为 `reward is None` 停下（`ablate.py:519`）。
   - 这个 0 不能被当成分数读：
     - 生成的任务在 `task.toml` 的 `[metadata]` 里标明有没有标准答案；
     - `ablate --benchmark livesqlbench --split holdout` 遇到没有答案的任务就拒绝开跑，非零退出，只报数目；
     - `report` 遇到这样的 trial 时在报告里注明数目。
   - `da-remote benchmarks` 在 `da-benchmarks/` 下找答案文件（路径由本工单定，写进 `docs/setup.md`）：有就用，没有就生成无答案的任务，并打印"无标准答案，任务只能冒烟"。答案到手后重跑 `benchmarks` 重新生成任务，这一步在 136。

9. **主会话 2026-11-24 定的做法**（由 spec、人批准的 127 契约和代码推出来，照做；只有代码事实和这里写的不符时才改，改了在 Comments 写明理由，不是停下的理由）：
   1. **agent 怎么交 SQL**：照官方，题面写明把 SQL 写进 `/app/pred.json`，格式照 `README_TASK.md:19-27`，只写格式要求（spec U10）。不从事件文件里推测 agent 用了哪条查询。
   1b. **保留 agent 交的 SQL**：没有看到 Harbor 0.24 会把 `/app` 下的文件带回 trial 目录（未确认）。136 的对拍要用开发集 trial 的 `pred.json`，所以：
       - 适配器在 agent 跑完后，把 `/app/pred.json` 拷进 agent 的日志目录（和事件文件同一处，是 agent 自己的输出）；
       - 验证脚本的诊断记下 `pred.json` 的状态：没有、解析不了、正常。

       U7 只限定了 `verifier/` 里的内容，agent 自己的 SQL 不是标准答案，所以不放进 `verifier/`。
   2. **题面内容**：库名、`query`，以及 `/app/db_assets/` 下三个文件的路径；不写 `conditions`，和官方一致。不照抄上游的 `db_env.sh` 和 `db_guidelines.md`：它们教的是 `psql` 和凭据，我们的 agent 走 MCP 工具。
   3. **`main` 镜像**：由 `da-remote benchmarks` 在服务器上建一个本地 tag，`task.toml` 的 `[environment] docker_image` 指向它。镜像从 `python:3.12-slim` 起，装验证脚本要的 Postgres 驱动（`psycopg`，钉版本），工作目录 `/app`。不推送到任何镜像仓库（spec `:39`）。`db_assets` 用 compose 挂到 `/app/db_assets:ro`，照上游 `docker-compose.yaml:16-17`。
   4. **验证脚本**：评分部分放进一个只依赖标准库和 `psycopg` 的独立文件，由 `generate_tasks` 拷进每个任务的 `tests/`；`evals.livesqlbench` 仍然导出契约里的名字。原因是验证在 `main` 容器里跑（Harbor 默认 shared），那里没有本仓库。上游 MIT 的函数照抄进这个文件，保留出处，登记 `NOTICE`。
   5. **两个库角色**：
      - 写库角色（evaluator 建、删 `<db>_process_1` 要用，上游 `evaluator.py:46`、`:91`、`:109`、`:119-127`）；
      - 只读角色：只有 `CONNECT` 和 `SELECT`。对模板库设 `ALTER DEFAULT PRIVILEGES`，保证克隆出来的库、`preprocess.sql` 建的表也只读。

      两个角色都在服务器上构建镜像时创建。写库口令随机生成，写进 mode 600 的 env 文件（照 `scripts/da-remote:411-427`），经 `docker build --secret` 传入，不进镜像的 `ENV`、构建参数和 `environment/`。只读口令可以出现在 `[environment] env` 的连接串里：它是只读的，而且测试只禁写库口令（127 契约）。
   6. **写库口令给验证阶段**：`task.toml` 的 `[verifier] env` 写 `PGPASSWORD = "${LSB_PG_WRITE_PASSWORD}"`，任务目录里不含口令本身。`da-remote start` 只给 LiveSQLBench 运行 source 这个 env 文件，只进 `harbor` 宿主进程的环境。job 配置里不写，也不经 `--ae`（127 第 6 点）。
   7. **dump 校验**：上游没有校验和。
      - 第一次下载时记下归档的 sha256，存在服务器上，之后每次比对；
      - 导入后用库的元数据对官方期望值：175 张表、2286 列（上游根目录 `README.md:204-205`）。
      - sha256 打印出来，由主会话抄进 Comments。下载用 `uv run --with gdown`（钉版本）；拿不到就停，见 Stop conditions。
   8. **镜像命名和 PostgreSQL 版本**：`da-lsb-db-<db>:<版本>`，`postgres:14.12`（spec `:36`）。上游的 `livesqlbench-db-*:latest` 不用：`:latest` 区分不出我们改过的构建。
   9. **划分的种子和来源**：在 `evals/manifest.json` 加两个键：`livesqlbench_seed`（例如 `20261124`），`livesqlbench_source`（`birdsql/livesqlbench-base-lite@507d7d9`）。`seed` 仍是 data-eng-bench 的 `20261015`（`tests/test_eval_manifest.py:11` 钉死）。
      - 算划分的脚本只读 `instance_id`、`selected_database`、`category` 三个字段。它打印每个库在两边的题数，不打印任何一道题的其他字段（ADR-0004：留出题的题面不看）。
      - 每个库 10 题，60/120 按库分层后，每个库开发集 3 或 4 题。
   10. **按榜单过滤**：
       - 题号以 `TASK_PREFIX` 开头的是 LiveSQLBench，其余是 data-eng-bench。
       - 在一处写一个"取某个榜单的某个 split"的函数，`report`、`compare`、`accept`、`ablate` 都用它。
       - `ablate`、`report`、`compare` 加 `--benchmark`，默认 `data-eng-bench`，现有命令行为不变。`accept` 只用于 data-eng-bench（ADR-0008），固定过滤成 data-eng-bench。
   11. **`--reference` 下的标签**：`LAYERS` 是"full 减各配置"的说法。以 `no-verifiers` 为参照时，三个差值的标签照 spec `:54`：`baseline` 是两层合起来，`no-context` 是上下文工具，`no-skill` 是 Skill。
   12. **`evals sample`**：不改它。在 `evals/__init__.py` 的说明里补一句：LiveSQLBench 的条目由新的划分命令追加，重跑 `sample` 之后要再追加一次。

10. **并行**：
    - 126（`evals improve`）的工单已在 main（`0a8305c`），代码还没写。它会调 `ablate --split dev --task …`（126 工单 `:125-127`）。按榜单过滤之后，它不带 `--benchmark` 就仍是 data-eng-bench。两张工单谁后合入，谁负责 rebase，并跑 `tests/test_eval_manifest.py`、`tests/test_eval_ablate.py`。
    - 131（IM）只改 `src/data_agent/im/` 一带，不冲突。

11. 只用 uv。服务器地址、口令都不写进代码、文档、测试、运行产出。

## Acceptance

- [ ] **127 的验收测试全部转绿**：`uv run pytest tests/acceptance/test_livesqlbench_*.py -v` 是 53 passed、0 xfailed、0 skipped。`git diff main -- tests/acceptance/` 里只有删掉的 `TICKET_128` 标记、定义和 import。
- [ ] **任务生成**：
  - 用 HF 公开数据（revision `507d7d9`，下到 `runs/128/`）和一份空的答案文件生成任务：恰好 180 个，每个都能被 Harbor 的 `Task(...)` 读入，元数据标明"没有标准答案"。
  - 90 道 `Management` 题不生成任务。
  - 只贴数目和一道开发集题的目录结构，不贴题面。
- [ ] **没有答案的任务**：
  - 验证脚本写 `0` 和"没有标准答案"的诊断（单元测试，用替身执行器）；
  - `ablate --benchmark livesqlbench --split holdout` 遇到这样的任务拒绝开跑（单元测试）。
- [ ] **划分已提交**：
  - `evals/manifest.json` 有 60 个开发、120 个留出的 `lsb-` 题号，`evals/livesqlbench_databases.json` 正好覆盖这 180 个；
  - 同一个种子重算，结果逐字节相同。Comments 贴命令，以及每个库在两边的题数。
  - `tests/test_eval_manifest.py` 分开检查两个榜单：data-eng-bench 仍是 30/30、种子 `20261015`；LiveSQLBench 60/120。
- [ ] **加入 `lsb-` 以后，data-eng-bench 照旧**：每条都有单元测试，清单是提交的这份（含 `lsb-` 条目），job 里只有 data-eng-bench 的 trial：
  - `report --split dev/holdout`、`compare --manifest --split`、`accept`、`ablate`（不带 `--benchmark`）的行为都和现在一样；
  - `ablate` 的预检冒烟选本榜单的开发题。
- [ ] **`ablate --reference`**：
  - 默认 `full`，现有行为和测试不变；
  - `--reference no-verifiers` 时 `--config` 必须包含它，差值是参照减各配置；
  - 单元测试覆盖这两种情况。
- [ ] **job 配置和适配器**：
  - `evals/configs/livesqlbench.data-agent.yaml` 的 kwargs 带 `benchmark: livesqlbench`；
  - 配置里没有写库口令和它的变量名，也不经 `--ae` 传；
  - 运行命令里没有 `--network`、`--mcp-servers`、`--charts`（127 的入口测试，加一条读 job 配置的单元测试）。
- [ ] **`da-remote`**：
  - `benchmarks` 加上 LiveSQLBench 的步骤：
    - 下载 HF 数据，钉 revision；
    - 下载并校验 dump：首次记 sha256，之后比对，导入后核对表数和列数；
    - 构建 18 个库镜像和 `main` 镜像；
    - 生成写库口令的 env 文件，mode 600；
    - 生成任务。
  - 可以重复跑，已就绪的步骤跳过；输出不含口令和地址。
  - `check` 增加就绪项：数据、18 个镜像、env 文件的权限、任务目录和"有没有标准答案"。
  - `start` 只给 LiveSQLBench 运行读这个 env 文件。
  - 单元测试照 `tests/test_da_remote.py` 的假 `ssh` 脚手架写，不连服务器。
- [ ] **许可证**：
  - 照抄的上游代码保留出处。来自根目录 MIT 的写明"Copyright (c) 2024 bird_sql"；来自 `LiveSQLBench-CLI/`（Apache-2.0）的保留原头，并注明改动。
  - `NOTICE` 新增 LiveSQLBench 一节：commit `e15cd22`，逐个列出文件，写明两种许可证。
  - `docs/setup.md` 写明：数据和镜像只在服务器上，不重新发布。
- [ ] **服务器冒烟**（主会话跑，Context 第 7 项）：
  - 开发集 3 题 × 4 个配置各 1 次，加上预检，全部跑完，没有基础设施报错。
  - 每个 trial 都有 `verifier/reward.txt`，以及"没有标准答案"的诊断。
  - agent 写出了 `/app/pred.json`，并且已拷进 trial 的 agent 日志目录（Context 第 9 项 1b），诊断里记了它的状态。贴每个配置写出 `pred.json` 的 trial 数，不贴 SQL。
  - 记下单题成本（总花费 ÷ trial 数），并按它估留出集全量的花费：4 配置 × 120 题 × 3 次 = 1440 个 trial。按 $0.05–0.08/trial 先估是 $72–115，以实测为准。
- [ ] **只读实测**（主会话在服务器上跑，命令由工单 agent 写进 `da-remote check` 或一个脚本，只打印通过或失败）：
  - 用只读角色连一个库，`CREATE TABLE`、`INSERT`、`UPDATE`、`DROP` 都被拒；
  - 从 `main` 容器里不带口令或用 `123123` 连库，都连不上；
  - agent 阶段的容器环境里没有写库口令。
  - 结果写进 Comments：127 只读组的 docstring 把这一项留给了本工单（`tests/acceptance/test_livesqlbench_readonly.py:26-29`）。
- [ ] **口令不进运行产出**：在服务器上，写库口令在 `runs/lsb-smoke/` 下出现的次数是 0。检查只打印次数，不打印口令。`pull` 回本机的目录用同样的办法查一遍。
- [ ] `make check` 通过。

## Evidence required

贴进 Comments：
- `make check` 的完整输出；
- 127 验收测试的 `-v` 输出；
- 任务生成和划分的命令与数目（不含题面）；
- 新单元测试的名字；
- 改动的非验收测试，逐条说明理由；
- `NOTICE` 的新增内容；
- 主会话在服务器上跑的每一条命令和（已遮盖的）输出：`setup`、`benchmarks`、`check`、只读实测、`start`、`status`、`pull`。其中包括 dump 的 sha256、单题成本和全量估算。

## Stop conditions

- 要改 `src/` 才能让 LiveSQLBench 跑通，例如查数提示词和写 `pred.json` 冲突、agent 写不了 `/app`。停下交回，说明原因，不自己改。
- 要改 127 的契约（名字、参数、返回类型）或任何验收测试的断言。
- （主会话的服务器步骤）服务器从 Google Drive 下不到 dump（被限流、要确认、要登录），或者 HF 下载失败：不改成手动下载；备用办法由主会话在开始服务器步骤之前和人定好。工单 agent 写代码时不受这条影响。
- 校验对不上：表数、列数和官方期望值不一致，或者 sha256 前后两次不同。
- 服务器要 sudo、装系统包或改系统配置；磁盘或内存不够放 18 个镜像。
- 冒烟的花费到 $4。
- 两道审查第二轮修复后仍不过；服务器运行退回 2 次后仍失败。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。服务器上已经建好的镜像和 env 文件留着，Comment 写明它们的名字和路径（不写口令）。

## Ledger

- Attempts: 2
- Review rounds: 2
- Model spend: $0
- Outcome: return 1 fixed (`0f499f2`, not pushed); server steps pending
- Human review minutes: —

## Comments

- 2026-11-24（主会话建票）：拆分如下。
  - 本工单做 spec 128 行除"评分对拍"以外的全部，以及无答案的冒烟。
  - 官方对拍和 oracle 检查拆给 136，答案到手后做。
  - 留出集里程碑全量要等 136 完成。

- 2026-11-25（工单 agent 最终回复，主会话照抄；分支 `worktree-128-livesqlbench-adapter`，`96b32e0`，未 push）：

  - **Local evidence**
    - `make check`: exit 0. Ruff, format and basedpyright are clean (0 errors). pytest: 3075 passed, 2 skipped, 59 xfailed (107 before; 48 of these are now green). Web: 448 passed. Output is in `runs/128/make-check-3.txt`.
    - `uv run pytest tests/acceptance/test_livesqlbench_*.py -v`: 53 passed, 0 xfailed, 0 skipped (`runs/128/acceptance-v.txt`).
    - **Split:** `python -m evals livesqlbench split --public runs/128/hf/livesqlbench_data.jsonl --seed 20261124`
      - 4 dev / 6 holdout: cross_db, crypto, gaming, solar, vaccine, virtual.
      - 3 dev / 7 holdout: the other 12 databases.
      - Total 60 / 120. Run twice: `sha256sum -c` reports OK for both files. data-eng-bench's entries and `seed` are unchanged; those lines only gained a trailing comma.
    - **HF data:** revision 507d7d9: 270 records (180 Query, 90 Management), 18 databases, hidden fields all empty. No Query record has `preprocess_sql`. Only counts were printed, no question text.
    - **Task generation:** with an empty answer file, 180 tasks are written and every one loads with Harbor's `Task()`. All 180 have `gold_answer = false` and `task_type = query`, and their ids equal the manifest's `lsb-` ids. No Management task is generated.
      - Layout of one dev task (`lsb-virtual_9`): `environment/{db_assets/virtual_*.{json,jsonl,txt}, db_init/{db_name.txt,preprocess.sql}, docker-compose.yaml}`, `instruction.md`, `task.toml`, `tests/{livesqlbench_verifier.py, task_payload.json, test.sh}`.
    - **Local dry run of the database scripts:** the local Docker has no buildx, so I ran `Dockerfile.db`'s steps by hand in the local `postgres:17` image, with a synthetic one-table dump and a throwaway credential (`runs/128/scripts/dryrun_db.py`).
      - The 15 read-only probe checks all print PASS: the read-only role can read, its CREATE TABLE, CREATE TEMP TABLE, INSERT, UPDATE, DELETE and DROP are refused, and `postgres`, `root`, `lsb_writer` and `lsb_reader` cannot connect with no password or with `123123`.
      - The read-only role cannot connect to `template1`, `x_template` or `postgres`, and can read the table that preprocess creates.
      - The write role clones the process database, queries it and drops it.
      - A multi-statement entry is scored by its last result.
      - `evaluate` gives reward 1 on a DISTINCT/comment case.
      - The credential is not in the container log.
      - This was PostgreSQL 17, not 14.12, and without the actual BuildKit build.

  - **New tests**
    - `tests/test_livesqlbench.py` (43 tests): the split's allocation, tasks without answers, the verifier (no gold answer, no prediction, preprocess errors, error class only, `pred.json` states, the script's own failure, stdlib + psycopg only, last result set), the job config, the adapter's `pred.json` copy and its env without `DBT_TARGET`, and the server steps (fetch, dump dirs and `__MACOSX`, table order, images and secret, sha256 on every call, counts, check, `leaks`, `readonly`).
    - In `tests/test_eval_ablate.py`, `test_eval_report.py`, `test_eval_accept.py` and `test_da_remote.py`, the new tests are the ones whose names mention livesqlbench, lsb, benchmark, reference or gold.

  - **Changed non-acceptance tests**
    - `tests/test_eval_manifest.py`: it pinned 30/30 over the whole manifest. It now counts data-eng-bench's entries only (still 30/30, seed 20261015) and adds LiveSQLBench's 60/120, seed and source.
    - `tests/test_eval_board.py:193-194`: these asserted that every manifest entry is a registry task. They now check data-eng-bench's entries only, since `lsb-` ids are not registry tasks.
    - `tests/test_da_remote.py` scaffolding: the fake `uv` gained `livesqlbench prepare` and `check` cases, and the fake `docker` gained `image ls`. No existing assertion changed, and the 134 existing tests still pass.

  - **NOTICE:** a new LiveSQLBench section at e15cd22.
    - Under MIT (root, "Copyright (c) 2024 bird_sql"): `evals/livesqlbench_verifier.py`.
    - Under Apache-2.0 (`LiveSQLBench-CLI/`): `evals/livesqlbench_images/{init-db.sh, bootstrap-db-image.sh, db-entrypoint.sh, Dockerfile.db}`, each with its modifications noted at the top.
    - The Python rewrites of `build_db_images.sh` and the adapter's task layout are listed as well.

  - **Reviews (2 rounds)**
    - **Round 1, `da-review main` (Codex):** 2 findings, both fixed.
      - psycopg 3 exposed the *first* result of a multi-statement string; it now advances to the last result, as upstream's psycopg2 does.
      - The ablation table dropped the "no gold answer" mark; it now carries `no_gold_answer_trials` and a **Not scored** banner.
    - **Round 2, `eng:code-review main`:** fixed
      - a `leaks` command that prints a count only;
      - `readonly` now uses the task's own `[environment] env` and checks the write role;
      - the dump's sha256 is compared on every run;
      - `template1` and `<db>_template` are closed to PUBLIC;
      - NOTICE wording;
      - a list-level benchmark filter;
      - the unreachable fallback layer label is removed.
    - After the advisor's check: the verifier swallows cleanup errors as upstream does, the healthcheck goes over TCP, builds force BuildKit and `check` reports buildx, and `__MACOSX` is skipped.
    - **Not fixed, with reasons (please decide):**
      - **`repair_json`:** upstream repairs a malformed `pred.json`; ours uses strict `json.loads`, because Context 9.4 limits the verifier to the standard library and psycopg. This is a parity risk for 136.
      - **Shared container:** the verifier runs in the same `main` container as the agent (Harbor's default). A background process the agent left behind via `--sandbox local` could read the verifier's environment. Harbor's `environment_mode = "separate"` would close this; I didn't take it on here because it is a larger change. Recorded as a known limitation against spec U6.
      - **Read-only password:** `READER_PASSWORD` is a constant, which Context 9.5 allows.
      - **Not refactored:** the module's size, and the duplicated command tail in the adapter.
    - **Other notes:**
      - `readonly`'s check that `main` has no write credential inspects a container started with the task's declared env, not one Harbor launched.
      - The pulled-directory leak count can't run on the Mac, because the credential stays on the server. The server count over `runs/lsb-smoke/` covers the pulled subset. Please accept or reject that reading.

  - **Deviations from Context 9, with reasons**
    - The server steps live in Python (`evals/livesqlbench_server.py`, `python -m evals livesqlbench …`), called by `da-remote`, not in bash.
    - The image tag is a digest of `evals/livesqlbench_images/`, so any change to the build files gets a new tag.
    - Restoring a dump needs a `root` role to exist, so one is created without LOGIN.
    - `--reference` accepts `full` or `no-verifiers`, the two references that have layer labels.
    - The run record's benchmark commit for LiveSQLBench is a `tree:` digest of the generated tasks, so a run can be resumed even though the tasks are no git clone.

- 2026-11-25（主会话）服务器步骤：PR #138；`da-remote setup 96b32e0` 成功；服务器缺 buildx，人批准后 `apt-get install docker-buildx`（0.30.1）。`da-remote benchmarks`：dump sha256 `fb1aa1f4108284e5d3af8d72b8e25c36b028832d3c525d500f9a4299e6370b44`（首次记录），18 个镜像建成，计数检查停下："166 tables, 2154 columns (upstream: 175, 2286)"。逐库对 `init-db.sh` 的 DATABASE_MAPPING：17 个库表数一致，`da-lsb-db-cybermarket` 是 0 表 0 列。

- **退回：cybermarket 镜像是空库。** 根因：上游 `livesqlbench-base-lite-dumps/cybermarket_table_orders.txt` 写的是 `Markets Vendors Buyers Products Transactions Communication RiskAnalysis SecurityMonitoring Investigation`（首字母大写），dump 文件是小写（`markets.sql` 等）。`table_order()` 原样写进 `table_order.txt`，`init-db.sh` 找 `$source_dir/Markets.sql`，Linux 区分大小写，每张表打一行 WARNING 后跳过，构建仍然成功。上游 dump 在 macOS（不区分大小写，压缩包带 `__MACOSX`）上做，所以上游没暴露。其余 17 个库的顺序文件都是小写。要求：
  - 表顺序里的名字按 dump 目录里实际的 `.sql` 文件名解析（不区分大小写，只匹配到一个文件才算）；解析不到的名字让构建失败，不再静默跳过（`init-db.sh` 里映射文件缺失时的 `WARNING … skipping` 改成报错，或在 Python 一侧先校验）。
  - 测试：一个首字母大写的顺序文件加小写 dump 文件，导入后表数对；一个顺序里有、dump 里没有的名字，构建失败。
  - 服务器上的 cybermarket 镜像要重建：镜像 tag 是 `evals/livesqlbench_images/` 的摘要，若只改 Python 一侧，tag 不变，`images()` 会认为镜像已有而跳过；说明怎么让它重建（例如改动落在 images 目录里，或 `prepare` 对计数不对的镜像重建）。
  - `cybermarket_template/enum_definitions.sql` 不在顺序里：`markets.sql` 自带 `CREATE TYPE`，所以不影响；照上游不导入，在 Comments 说明即可。

- 2026-11-25（主会话）退回范围加两项（人定：照上游、用成熟做法；同一轮退回一起修）。上一个 agent 刚开工就被主会话停下，worktree 里留有未提交的改动（`NOTICE`、`init-db.sh`、`livesqlbench_server.py`、`tests/test_livesqlbench.py`），先看再接着用或丢弃。
  - **`pred.json` 照上游先修复再解析（人定，改 Context 9.4）。** 上游 `LiveSQLBench-CLI/adapters/livesqlbench/template/tests/evaluator.py:32,136`（`e15cd22`）：`from json_repair import repair_json`，`pred_data = json.loads(repair_json(pred_text))`；版本钉在 `environment/requirements.txt:10` 的 `json-repair==0.58.5`（PyPI：MIT，无必需依赖，Python ≥3.10）。照做：验证脚本允许的依赖变成标准库、psycopg、`json-repair==0.58.5`，钉同一版本装进验证脚本运行的镜像；测试覆盖一个格式坏了但能修好的 `pred.json` 能判分。
  - **验证在独立容器里跑（人定，关掉 spec U6 的共享容器风险）。** 用 Harbor 自带的 separate verifier（项目锁定的 harbor 0.24.0 已有）：`[verifier] environment_mode = "separate"`。依据（`.venv/.../harbor`）：`models/task/config.py:555-600`（`VerifierEnvironmentMode`；不设 `[verifier.environment]` 时用顶层 `[environment]` 的一份新拷贝）；`trial/single_step.py:36-55`（separate 时先收集 artifacts、停掉 agent 环境，再起验证环境）；`trial/trial.py:900-965` 和 `trial/artifact_handler.py:171-205`（声明的 artifact 按原路径上传进验证环境）。所以：任务里把 `/app/pred.json` 声明成 artifact，`test.sh` 照旧读 `/app/pred.json`；写库口令只在 `[verifier.env]`，agent 的容器在验证开始前已经停掉。要确认并在 Comments 写明：验证环境里的 postgresql sidecar 是新起的（验证脚本本来就从模板库克隆，结果不应变）；每个 trial 多出的环境启动时间（冒烟里量）；`readonly` 检查和 `leaks` 计数在新模式下是否仍然成立。测试覆盖生成的 `task.toml` 带 `environment_mode = "separate"` 和 artifact 声明；Harbor `Task()` 能加载。
  - 冒烟仍由主会话在服务器上跑，这两项的真实效果（separate 模式能判分、修复后的 `pred.json` 被读到）在冒烟里看。

- 2026-11-25（工单 agent 第 1 轮退回的交回，主会话照抄；`0f499f2`，未 push；工单范围外的 4 项改动待对齐会话定）：

  ### Changes outside the ticket's Authority or return request

  1. **`pyproject.toml` and `uv.lock`** now pin `json-repair==0.58.5` as a dev dependency. These files are outside the listed Authority, but the tests import the verifier, which now imports `json_repair`.
  2. **`ablate` passes `harbor run -y` for LiveSQLBench runs only.** I missed this in round 1. Before reading the write password from the host (`[verifier.env]`), Harbor 0.24 asks "Proceed? (Y/n)". With no terminal, as under `da-remote start`, it aborts. My first local trial showed exactly that: "Aborted.". In `harbor run`, `-y` also skips the `--diff` and hub-sharing prompts, which we never use, and `job resume` doesn't ask at all.
  3. **A verifier that fails itself now gives no score.** If the database can't be reached, the process database can't be created, or the script crashes, it writes `diagnostics.json` (`outcome: verifier_error` plus the error class) and no `reward.txt`. Harbor then records `RewardFileNotFoundError` and the smoke stops. Round 1's rule was "scores 0", so this reverses it.
     - Known limit: `RewardFileNotFoundError` is in `NOT_RETRIED` (`evals/report.py`), so a real run's report still counts such a trial as a failure. I left that unchanged and suggest it go to ticket 136.
  4. **`--reference no-verifiers` limits which configs run.** With no `--config`, it runs only its four: no-verifiers, baseline, no-skill, no-context. A config it has no layer label for is refused with exit 2. Before this, it ran all six and then crashed while writing the report (a Codex finding).

  ### Comments (to copy into the ticket)

  **Cybermarket**
  - I kept the previous agent's uncommitted fix:
    - Table-order names are matched to the dump's `.sql` files ignoring case, and each must match exactly one file.
    - A name that matches nothing, or more than one file, stops `prepare` before any image is built.
    - In `init-db.sh`, a name with no dump file is now an error instead of a skipped warning.
  - Added: if a dump has `<db>_full.sql` or `<db>_template_full.sql`, the order file is not resolved, because `init-db.sh` restores the full dump without reading it.
  - Local dry run in `postgres:17` (`runs/128/dryrun-order.txt`):
    - With a capitalised order file over lower-case dump files, both tables are restored: 2 tables, 5 columns, exit 0.
    - With the unresolved names (the old behaviour), it prints "ERROR: Mapped dump file not found … Markets.sql" and exits 1.
    - That run is the only evidence for "the table count is right after import". The repo tests check the resolved order and the order file passed to `docker build`, and only grep `init-db.sh` for the new error.
  - **Rebuild:** `init-db.sh` lives in `evals/livesqlbench_images/`, so the image tag changes. All 18 database images and `main` are rebuilt under the new tag. The old-tag images stay on the server; watch the disk stop condition.
  - `enum_definitions.sql` is not imported, as upstream does; `markets.sql` creates its own types.

  **pred.json**
  - Parsed with `json.loads` first, then `json.loads(repair_json(text))` if that fails. This matches upstream, because `repair_json` 0.58.5 itself tries `json.loads` first. Diagnostics record which path was taken (`pred_json: ok` or `repaired`).
  - `Dockerfile.main` pins `json-repair==0.58.5`.
  - A test feeds a broken but repairable `pred.json` through `main()` and gets reward 1 with `pred_json: repaired`.

  **Separate verifier**
  - `task.toml` now declares `artifacts = ["/app/pred.json"]` and `[verifier] environment_mode = "separate"`.
  - The test reads the loaded Harbor `Task()` rather than the file text. It checks:
    - the separate mode resolves;
    - the artifact is declared;
    - the verifier environment is the task's own compose environment and `main` image.
  - Local $0 trials with stand-in images:
    - **Trial 1** (`runs/128/sep-trial.txt`, `harbor -a oracle`):
      - reward 1, `pred_json: repaired`;
      - the artifact was collected to `artifacts/app/pred.json`;
      - a sentinel password appears 0 times in the job directory.
    - **Trial 2** (`runs/128/sep-trial2.txt`): a copy of the real job config with only the agent swapped to oracle.
      - In the agent's environment, the oracle created a database `agent_marker` through the task's own connection string.
      - In the verifier's environment, the count of that database was 0, giving reward 1. So the verifier's database is newly started.
      - Sentinel count 0.
  - These two trials also settle Context 6's two open points, on stand-in images:
    - Harbor did not try to pull the local `pull_policy: never` images.
    - The resolved password is not written into the trial directory.
  - Extra time per trial: about 11 s between the end of the agent and the start of the verifier, and the verifier phase took 18 s (local Docker).
  - `readonly` still inspects a `main` container started with the task's `[environment] env`, so separate mode doesn't change it. `leaks` counts over `runs/`, and the trials above showed 0.

  **`check` additions**
  - Counts tasks whose `docker_image` isn't the current `main` image (tasks are regenerated on every `prepare`).
  - Runs `import psycopg, json_repair` in the `main` image with `--network none`.

  **Tests**
  - New tests in `tests/test_livesqlbench.py`:
    - table-order resolution (case, missing name, two matches, full dump);
    - the resolved order reaching the build, and an unresolved name building no image;
    - repairable `pred.json` cases;
    - the image pins match `uv.lock`;
    - separate mode and the artifact;
    - the unreachable database giving no score;
    - the `check` stale-task count and import probe.
  - New in `tests/test_eval_ablate.py`: `-y` for LiveSQLBench and not otherwise; `--reference` defaults and refusal.
  - Changed existing tests, with reasons:
    - **Table order:** the expectation went from `"b a\n"` to `"b\na\n"`, because names are now resolved and written one per line.
    - **Import-set test:** expects `{"psycopg", "json_repair"}`.
    - **Script-failure test:** now expects no `reward.txt`, `reward: null` and exit 1 (change 3 above).

  **Checks**
  - `make check`: exit 0. Ruff and basedpyright have 0 errors. pytest: 3093 passed, 3 skipped, 59 xfailed. Web: 448 passed. Output in `runs/128/make-check-r5.txt`.
  - Acceptance: `tests/acceptance/test_livesqlbench_*.py`, 53 passed.

  **Reviews**
  - **`da-review main` (Codex):** 2 findings, both fixed (changes 3 and 4 above).
  - **`eng:code-review`:** it covered only `96b32e0..HEAD` (this round), not all of `main...HEAD`.
    - Fixed:
      - the failed verifier writes its diagnostics directly instead of writing a reward and deleting it;
      - two docstrings;
      - the full-dump case;
      - the docs had claimed a report keeps a reward-less trial out of scoring, which is not so.
    - Not fixed:
      - `init-db.sh`'s `exit 1` is tested only by a string match; the dry run covers its behaviour.
      - The `benchmark == livesqlbench` checks in `ablate` are left inline; there are only three.
      - Harbor doesn't pass a job's extra mounts or compose files to the separate verifier. Our job config sets neither, so nothing is affected today.
