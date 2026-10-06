# Harbor 自定义 agent 契约 / SignalPilot benchmark 独立可用性 / DataAgentBench 接入要求

日期:2026-09-21
方法:全部基于一手源码(`git clone --depth 1`)与仓库内官方文档。引用格式为 `仓库@commit: 路径:行号`。未直接核实的内容标 `未确认`。

被读取的 commit:
- harbor:`https://github.com/laude-institute/harbor`(文档内自称 `harbor-framework/harbor`)@ `71c77fd` (2026-09-19),`pyproject.toml` 版本 `0.23.0`
- data-eng-bench:`https://github.com/Snowflake-Labs/data-eng-bench` @ `a3278ad` (2026-09-02)
- livesqlbench:`https://github.com/bird-bench/livesqlbench` @ `e15cd22` (2026-05-27)
- SignalPilot:`https://github.com/SignalPilot-Labs/SignalPilot` @ `918ce5b` (2026-09-11)
- DataAgentBench:`https://github.com/ucbepic/DataAgentBench` @ `0290945` (2026-09-15)

---

## Bottom line(对一个 Claude Agent SDK / CLI 风格的自研 agent,每个 benchmark 的最小接入工作量)

- **Harbor 不要求 agent 自带 Docker 镜像。** 推荐路径是继承 `BaseInstalledAgent`,只实现 `name()`、`install(environment)`(在任务容器里装你的 CLI)和 `run(instruction, environment, context)`(在容器里以 `exec_as_agent` 跑 CLI)。用 `harbor run -a your.module:YourAgent` 传入(`--agent-import-path` 已弃用但仍可用)。指令是一个字符串,结果不由 agent 返回——由任务自带的 `tests/test.sh` 写 `/logs/verifier/reward.txt`。agent 只需(可选)在 `populate_context_post_run` 里回填 token/成本,并在 `logs_dir/trajectory.json` 写 ATIF 轨迹(上榜时必需)。
- **data-eng-bench:接入量最小。** 任务是标准 Harbor task(base image 内置 `retail.duckdb`,`DB_TYPE=duckdb` 全离线),agent 只需能在容器里读写 dbt 项目并跑 `dbt`。跑法 `harbor run -d snowflake-labs/data-eng-bench -a your.module:YourAgent -m provider/model -k 3 --upload --public`。上榜通过 `leaderboard/` 目录的 `uv run lb submit <hub job url>` 开 PR;CI 硬性要求:全部 103 任务 × ≥3 trials、无 timeout/resource override、**每个得分 trial 必须有 Hub 上的 ATIF `trajectory_path`**。自定义 agent 名不在 `display-names.json` 里时需在交互中补显示名。
- **LiveSQLBench-CLI:接入量中等,且它 vendor 了一份老版 Harbor(`0.1.45`),不是 `uv tool install harbor` 的当前版。** 老版 `BaseInstalledAgent` 抽象方法是 `_install_agent_template_path`(jinja 安装脚本)+ `create_run_agent_commands(instruction) -> list[ExecInput]` + `populate_context_post_run`,与新版 `install()/run()` **不兼容**,需要按老 API 写一份。DB 访问通过 docker-compose 的 sidecar `postgresql` 服务和环境变量 `PGHOST=postgresql PGPORT=5432 PGUSER=root PGPASSWORD=123123`(也写在 `/app/documents/db_env.sh`);agent 必须把 SQL 写到 `/app/pred.json`。agent 镜像通过 `run_adapter.py --agent-image` 指定,写入每个 task 的 `task.toml` `docker_image` 与 compose `main.image`,所以要么用它的 `Dockerfile.claudecode` 之类预装镜像,要么在安装脚本里现装。LiveSQLBench 官方榜单提交走 Google Doc 流程,不走 Harbor Hub。
- **SignalPilot `benchmark/`:可以不依赖 SignalPilot Cloud,但离不开本地 SignalPilot gateway + Postgres。** 它用 `claude-agent-sdk`(`requirements.txt` `>=0.1.0`,Docker 镜像 pin `==0.1.59`)直接 `query()`,agent 就是 Claude Agent SDK + SignalPilot MCP(stdio 子进程 `gateway.mcp.server`)+ `signalpilot-dbt` 插件 skills;**不是可插拔的**——换 agent 等于重写 `runners/direct.py` 的 `run_agent()`。目标是 Spider2-DBT(默认)、Spider2-Snowflake、Spider2-Lite、ADE-bench。评测器(`evaluation/comparator.py`)可单独复用。
- **DataAgentBench:仓库无 LICENSE 文件,GitHub API `license: null`。** 接入极简:不需要 Harbor,不需要容器化 agent;只要你的 agent 对 54 个 query 各跑 5 次、产出一个 JSON 列表 `[{dataset, query, run, answer}]` + 每次运行的 trace,开 PR。`common_scaffold` 只是参考实现(OpenAI 兼容 chat.completions 循环 + 4 个工具),不强制使用。本地基础设施:PostgreSQL ≥17.5、MongoDB(实验用 v8.2.1)、DuckDB/SQLite 文件、Docker(`python-data:3.12` 镜像跑 `execute_python`)、Git LFS + ~5GB PATENTS 数据下载。

---

## 1. Harbor:自定义 agent 的真实契约

### 1.1 仓库与安装
- `README.md` 未读细节;`pyproject.toml:3` `version = "0.23.0"`,`[project.scripts] harbor = "harbor.cli.main:app"`(`pyproject.toml`)。
- 依赖含 `claude-agent-sdk>=0.1.17`(`pyproject.toml`),但源码里只有 `src/harbor/cli/adapter_review.py` 引用 `claude_agent_sdk`——Harbor **没有**内置"Claude Agent SDK agent"类型,SDK 只用于 adapter review 工具。

### 1.2 Installed vs External 两种自定义 agent(官方文档)
`docs-mintlify/core-concepts/agents/custom-agents.mdx`:
- 表格:Installed agent = "Harbor installs and runs the agent CLI inside the task environment"(例 `claude_code.py`);External agent = "The agent loop runs in Harbor and controls the task environment through `BaseEnvironment`"(例 `terminus_2.py`)。
- Tip 原文:"prefer `BaseInstalledAgent`. Most agent integrations follow this design. Use `BaseAgent` only when the agent loop must remain outside the task environment."
- 运行:"Custom agents are not registered by name, so pass `module.path:ClassName` to `--agent` (`-a`). The module must be importable from the Harbor process." 示例 `harbor run -t hello-world/hello-world -a examples.agents.marker_agent:MarkerAgent`。另有 `--model/-m`、`--agent-kwarg/--ak`、`--agent-env/--ae`。
- **不需要 agent 自带 Docker 镜像**:环境镜像来自 task(`task.toml [environment] docker_image` / `environment/Dockerfile`),installed agent 在 `install()` 里把自己装进去。

### 1.3 `BaseAgent`(external)契约
`src/harbor/agents/base.py`:
- `class BaseAgent(ABC)` :29。构造函数 :133-160:`__init__(self, logs_dir: Path, model_name=None, logger=None, mcp_servers=None, skills_dir=None, *, extra_env=None, load_trajectory=None, environment_logs_dir=None, **kwargs)`。
- 抽象方法::324 `@staticmethod @abstractmethod def name() -> str`;:328 `def version(self) -> str | None`;:339 `async def setup(self, environment)`;:355 `async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None`。
- `import_path()` :332 返回 `f"{cls.__module__}:{cls.__name__}"`。
- `run()` docstring(:355-378):"Runs the agent in the environment. Be sure to populate the context with the results of the agent execution ... `instruction`: The task instruction. `context`: The context to populate with the results of the agent execution." 并说明 `self.mcp_servers`/`self.skills_dir` 需自行注册。
- 可选钩子 :408 `populate_context_post_run(self, context)`:"Agents that write logs or trajectories during execution can override this to parse those outputs after the trial syncs logs back to the host."
- 最小 external 示例:`examples/agents/marker_agent.py`(实现 `name/__init__/version/setup/run`,run 里 `await environment.exec(command="echo 'Hello, world!' > /app/hello.txt")`)。

### 1.4 `BaseInstalledAgent`(installed)契约
`src/harbor/agents/installed/base.py`:
- `class BaseInstalledAgent(BaseAgent, ABC)` :346。
- 唯一新增抽象方法 :1067-1068 `@abstractmethod async def install(self, environment)`:"Use `exec_as_root` for system packages and `exec_as_agent` for user-level installs."
- :1077 `setup()` 已实现:`mkdir -p /installed-agent` → 调 `self.install(environment)` → 可选 `get_version_command()` 探测版本。
- 辅助::1010 `exec_as_root(environment, command, env, cwd, timeout_sec)`,:1023 `exec_as_agent(...)`(以 `environment.default_user` 运行)。
- `render_instruction()`/`@with_prompt_template` 装饰器(:157-176)可套 prompt 模板。
- 文档示例(`custom-agents.mdx` Installed tab):子类只写 `name()`、`install()`(`pip install my-agent`)、`@with_prompt_template async def run()`(`my-agent {shlex.quote(instruction)}`)。

### 1.5 内置 Claude Code agent 是怎么做的(可直接抄)
`src/harbor/agents/installed/claude_code.py`:
- :112 `class ClaudeCode(BaseInstalledAgent, ACPAgentMixin)`;:164 `name()`。
- :466-490 `install()`:若容器里已有满足版本的 `claude` 则跳过;否则 `ensure_system_dependencies(environment, ("curl","bash","nodejs","npm","procps"))`,再以 agent 用户 `curl -fsSL https://downloads.claude.ai/claude-code-releases/bootstrap.sh | bash`(alpine 走 `npm install -g @anthropic-ai/claude-code`)。
- :1859-1960 `run()`:设置 `ANTHROPIC_MODEL`、`CLAUDE_CONFIG_DIR=<environment_logs_dir>/sessions`、`IS_SANDBOX=1`、`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`;把 `self.skills_dir` 拷到 `$CLAUDE_CONFIG_DIR/skills/`(:1685-1696),把 `self.mcp_servers` 写成 `~/.claude.json` 的 `mcpServers`(:1714-1738);指令通过环境变量传入再 `printf "%s" | claude --verbose --output-format=stream-json --print 2>&1 | tee <logs>/claude-code.txt`(:1949-1958)。
- :1654-1683 `populate_context_post_run()`:把原生日志转成 ATIF 写到 `self.logs_dir/"trajectory.json"`,并填 `context.cost_usd / n_input_tokens / n_cache_tokens / n_output_tokens`。

### 1.6 指令来源与结果回传
- 指令:任务目录 `instruction.md`(`src/harbor/models/task/task.py:36`),可用 `extra_instruction_paths/extra_instructions` 追加(:52-65)。Trial 在 `_run_agent_phase` 里把 `instruction: str` 传给 `agent.run()`(`src/harbor/trial/trial.py:540-610`)。
- 结果:**agent 不返回分数**。`docs-mintlify/core-concepts/tasks/verifier.mdx:6`:"`tests/test.sh` ... must produce a numerical reward at `/logs/verifier/reward.txt` or `/logs/verifier/reward.json`"。
- `AgentContext`(`src/harbor/models/agent/context.py:22-56`)字段:`n_input_tokens, n_cache_tokens, n_output_tokens, cost_usd, model_usage, rollout_details, metadata`——只是用量/元数据。
- agent 身份写入结果:`trial.py:506` `self.result.agent_info = self.agent.to_agent_info()`(`AgentInfo(name=self.name(), version=self.version() or "unknown", model_info=...)`,`base.py` `to_agent_info`)。
- 轨迹上传:`src/harbor/upload/uploader.py:679-701` 从 `trial_dir/"agent"/"trajectory.json"` 取 ATIF 作为 `trajectory_path`。ATIF 规范:`docs-mintlify/core-concepts/agents/atif.mdx`、`rfcs/0001-trajectory-format.md`。

### 1.7 `--agent-import-path` 的现状
- `CHANGELOG.md:358-360`(2026-06-20 "Unified Agent, Environment, and Verifier Flags"):"`--agent`, `--env`, and `--verifier` each now accept a custom import path (`module.path:ClassName`) ... The legacy `--agent-import-path` ... still work but are hidden and log a deprecation warning".
- 解析逻辑:`src/harbor/agents/factory.py:110-140` `get_agent_class_from_config`:`name` 含 `:` 且非 ACP 简写 → 当作 import_path;`src/harbor/utils/import_path.py:9-45` `import_symbol/import_class`(`importlib.import_module` + `getattr`,校验是类且是 `BaseAgent` 子类)。
- `AgentConfig`(`src/harbor/models/trial/config.py:63-66`)有 `name`、`import_path`、`model_name` 三字段,YAML job config 里可写 `agents: [{import_path: "..."}]`。
- Hosted Harbor 的 "custom agents" 是另一回事:`docs-mintlify/core-concepts/hosted-harbor/custom-agents.mdx:6` "Custom agents run over ACP from a source repository containing a `harbor-agent.json` manifest."——只对云托管适用。

### 1.8 data-eng-bench 对自定义 agent 的要求
仓库 `Snowflake-Labs/data-eng-bench @ a3278ad`:
- `README.md`:103 个 dbt 任务;"It runs on Harbor, so any Harbor-supported agent ... is evaluated with one command";要求 Harbor ≥0.21.0(部分 flag 改名);前置 `uv tool install harbor`、`git lfs pull`(`retail.duckdb` ~489MB)、`docker build base-image/ -t ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0`。
- **DB_TYPE**:`README.md` 表格 "DuckDB (default) `DB_TYPE=duckdb` Snowflake account: Not required (fully hermetic)";`base-image/Dockerfile:88-90` `ENV DB_TYPE=duckdb`;任务 compose `tasks/dbt-fix-division-by-zero/environment/docker-compose.yaml` `DB_TYPE=${DB_TYPE:-snowflake}`、`DUCKDB_PATH=${DUCKDB_PATH:-/app/database/retail.duckdb}`(注意 compose 默认是 snowflake,所以 job config 必须显式 `environment.env: [DB_TYPE=duckdb]`,见 `configs/data-eng-bench-duckdb.claude-code.yaml:19-21`)。healthcheck `python3 /snowflake_clone.py` 在 duckdb 下是 no-op(`task.toml [environment.healthcheck]`)。
- 任务结构:`tasks/<name>/{instruction.md, task.toml, environment/Dockerfile(FROM base:1.0.0), tests/test.sh, solution/solve.sh}`。`instruction.md` 要求 agent 先 `echo $DB_TYPE` 再改 `/app/dbt_models_duckdb/...` 或 `/app/dbt_models_snowflake/...`。
- job config 示例 `configs/data-eng-bench-duckdb.claude-code.yaml`:`n_attempts: 3`,`agents: [{name: claude-code, model_name: ..., kwargs: {reasoning_effort: high, disallowed_tools: WebSearch,WebFetch}}]`。自定义 agent 只需把 `name` 换成 `import_path`。
- 完整性:README "Benchmark integrity" 建议 `harbor run ... --allow-agent-host <model-api-host>` 做网络白名单。
- 不用 Harbor 也能跑单任务:`docs/cortex-code.md:125-150` "Without Harbor ... Every task is a plain Docker image plus a shell script that writes a reward file",需自己挂 `/tests` 与 `/logs/verifier`。

### 1.9 LiveSQLBench-CLI 对自定义 agent 的要求
仓库 `bird-bench/livesqlbench @ e15cd22`,子目录 `LiveSQLBench-CLI/`:
- **vendor 的 Harbor**:`LiveSQLBench-CLI/pyproject.toml` `name = "harbor"`, `version = "0.1.45"`, `harbor = "harbor.cli.main:app"`;README 用 `uv sync` + `uv run harbor run ...`。`scripts/run_openhands_gpt55_low.sh:8` 用 `.venv-harbor/bin/harbor`。
- 老版 installed 契约 `LiveSQLBench-CLI/src/harbor/agents/installed/base.py`:抽象 `_install_agent_template_path` (:45)、`create_run_agent_commands(self, instruction) -> list[ExecInput]` (:52)、`populate_context_post_run` (:70);`ExecInput(command, cwd, env, timeout_sec)` (:14-18);`setup()` 把 jinja 渲染的安装脚本上传到 `/installed-agent/install.sh` 执行 (:86-124)。自定义 import path 仍走 `--agent-import-path`(`src/harbor/cli/jobs.py:297-300`)。
- vendored claude-code:`install-claude-code.sh.j2`(已有 `claude` 则跳过,否则 `curl -fsSL https://claude.ai/install.sh | bash`);运行命令 `claude --verbose --output-format=stream-json --permission-mode=bypassPermissions ... --print -- <instruction> | tee /logs/agent/claude-code.txt`(`claude_code.py:937-945`),环境变量透传 `ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL/CLAUDE_CODE_OAUTH_TOKEN` (:810-822)。
- **DB 访问方式**:`adapters/livesqlbench/template/environment/docker-compose.yaml`:`main` 服务 `image: livesqlbench-main-openhands:latest`(adapter 用 `--agent-image` 替换,`adapter.py:213-228`),`environment: PGHOST: postgresql, PGPORT: "5432", PGUSER: root, PGPASSWORD: "123123"`,挂载 `./db_assets:/app/db_assets:ro`、`./documents:/app/documents:ro`;`postgresql` sidecar `image: ${LSB_PG_IMAGE:-livesqlbench-postgresql:latest}`,由 `init-db.sh` 从 dump 建库。同样信息写在 `template/documents/db_env.sh`(`export PGHOST="postgresql" ... PGDATABASE="{db_name}"`)。每个 DB 需先 `bash adapters/livesqlbench/build_db_images.sh --db-dir ...` 构建镜像(README "Build Docker images")。
- **agent 输出格式**:`template/documents/README_TASK.md`:"Write a file in `/app/pred.json` with exactly this JSON: `{"instance_id": ..., "predicted_sql": ["<SQL1>", ...]}`";`tests/test.sh` 检查 `/app/pred.json` 存在后跑 `/tests/evaluator.py`,写 `/logs/verifier/reward.txt`。
- 生成 task:`python3 adapters/livesqlbench/run_adapter.py --data-root ... --db-dump-root ... --eval-src-dir evaluation/src --output-dir datasets/... --agent-image <img>`(README);`task.toml` 写 `docker_image = "{agent_image}"`、`agent.timeout_sec = 1800`(`adapter.py:296-320`)。
- 预置 agent 镜像:`template/environment/Dockerfile.{openhands,codex,claudecode,opencode}`;`Dockerfile.claudecode` `npm install -g @anthropic-ai/claude-code@2.1.22`。
- 榜单:根 `README.md:313` "submitting your model performance ... refer to the LiveSQLBench submission document"(Google Doc),与 Harbor Hub 无关。

### 1.10 Harbor Hub 榜单是否接受自定义 agent
- Hub 榜单机制(`docs-mintlify/core-concepts/harbor-hub/leaderboards.mdx`):榜单 = 数据集 owner 定义的表;"Harbor Hub intentionally does not calculate row scores from linked trials";行由 owner 用 `harbor hub leaderboard row ...` 写入,可链接 trial id。"Creating a leaderboard requires owner access to the dataset's organization." → **提交方式由各数据集 owner 决定,Hub 本身不限制 agent 类型。**
- 上传:`harbor run ... --upload --public` 或 `harbor upload <job-path> --public`(`docs-mintlify/core-concepts/harbor-hub/upload.mdx`)。
- data-eng-bench 的流程(`leaderboard/SUBMIT.md`):`harbor run -d snowflake-labs/data-eng-bench -a <agent> -m <provider/model> -k 3 --upload --public` → `cd leaderboard && uv run lb submit https://hub.harborframework.com/jobs/<uuid>` → 每个 (agent, agent version, model, reasoning effort) 一个 JSON、一个 PR → CI static analysis → promotion(克隆 trial 到榜单组织)→ 维护者 `/judge`(LLM 审轨迹)→ `/apply` → merge 自动写榜。
  - CI 硬约束(`leaderboard/src/leaderboard/ci/static_analysis.py:46` `MIN_TRIALS_PER_TASK = 3`;:174-175 "`missing trajectory_path`" 对每个 reward 成功的 trial;:101-109 `matching_agent` 按 `config.agents[].name`+model 匹配 `source_filter`);`core/hub.py:26-27` 固定 `DATASET = "snowflake-labs/data-eng-bench"`、`DATASET_REF = "sha256:de31878d..."`(v1.0);:95-96 trial 按 `agent_name`/`agent_version` 过滤。
  - `display-names.json` 现只有 claude-code/codex/gemini-cli/terminus-2/mini-swe-agent/cursor-cli;新 agent 会在 `lb metadata` 时交互补 `display_name/display_url/display_org/org_url`(SUBMIT.md §2)。
  - 因此自定义 agent 上 data-eng-bench 榜的关键工作:`name()` 返回稳定名、`version()` 非 None、在 `populate_context_post_run` 写出 ATIF `trajectory.json`(否则 CI "agents that only upload custom logs fail here")。

---

## 2. SignalPilot `benchmark/`:能否脱离 SignalPilot Cloud 独立运行

仓库 `SignalPilot-Labs/SignalPilot @ 918ce5b`。

### 2.1 SDK 与依赖
- `benchmark/requirements.txt`:`claude-agent-sdk>=0.1.0`, `python-dotenv>=1.0.0`, `httpx>=0.28.0`。
- `benchmark/Dockerfile.dbt-agent`:`FROM python:3.12-slim`,装 Node 22 + `npm install -g @anthropic-ai/claude-code@2.1.156`,`pip install /tmp/gateway-install/`(SignalPilot gateway 包)+ `dbt-duckdb duckdb pandas pyarrow "claude-agent-sdk==0.1.59"`,再 `claude plugin install signalpilot-dbt@signalpilot --scope user`,并写 `/app/benchmark/mcp_baked_config.json`(stdio 子进程 `cd /app/signalpilot/gateway && exec python3 -c "from gateway.mcp.server import main; main()"`,`SP_DISABLE_SANDBOX=1`);装 `libfaketime` 并给 `dbt` 包一层 FAKETIME wrapper。
- `benchmark/agent/sdk_runner.py:11-24` `from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ..., query)`;:57-64 `ClaudeAgentOptions(model, max_turns, permission_mode="bypassPermissions", cwd=work_dir, mcp_servers=load_mcp_servers(), debug_stderr=True, thinking={"type":"enabled","budget_tokens":20000})`。

### 2.2 需要的环境变量 / 密钥 / 服务
- 模型凭据:`benchmark/README.md` "Prerequisites: ... OAuth token in `.env`",re-run 示例 `-e CLAUDE_CODE_OAUTH_TOKEN="$TOKEN"`;根 `.env.example` 列 `ANTHROPIC_API_KEY=` / `CLAUDE_CODE_OAUTH_TOKEN=`(二选一)。`sdk_runner.py` 本身不读密钥,依赖 Claude Agent SDK 默认环境变量。
- **本地 gateway 必需**:`benchmark/README.md` "SignalPilot gateway running (`docker compose up gateway`)";`benchmark/core/mcp.py:24` `SP_GATEWAY_URL`(默认 `http://localhost:3300`)、:30 `DATABASE_URL` 注入 MCP 子进程;`register_local_connection()` (:48-100) 直接用 `gateway.store.Store` 往 gateway 的 Postgres 写 DuckDB 连接,失败时回退到 `POST {SP_GATEWAY_URL}/api/connections`。`SP_ORG_ID` 默认 `local`。
- gateway 的 Postgres:`signalpilot/gateway/gateway/db/engine.py:34` `os.environ.get("DATABASE_URL")`;`docker-compose.yml:61` `DATABASE_URL: postgresql+asyncpg://signalpilot:changeme_dev_only@db:5432/signalpilot`。
- 数据集路径:`benchmark/core/paths.py`:`SPIDER2_DBT_DIR`(默认 `~/spider2-repo/spider2-dbt`)、`SPIDER2_SNOWFLAKE_DIR`、`SPIDER2_LITE_DIR`、`BENCHMARK_WORK_DIR`、`BENCHMARK_AUDIT_DIR`(默认 `/data/benchmark-audit`)、`ADE_BENCH_DIR`(`benchmark/ade/tasks.py:14-19`)。Snowflake suite 读 `.env` 里的 `SNOWFLAKE_ACCOUNT/SNOWFLAKE_USER/SNOWFLAKE_TOKEN/...`(`runners/sql_runner.py:58-78`);BigQuery 用 `gcp-service-account.json`(`paths.py` `BIGQUERY_SA_FILE`)。
- **Cloud 依赖**:`benchmark/` 内 `.py` 无任何对 `app.signalpilot.ai` / cloud API 的调用(grep `httpx.post|requests.` 仅命中 `core/mcp.py` 的本地 gateway `/api/connections`)。根 README 提到的 SignalPilot Cloud 只是可选托管(`README.md:13,40`);gateway auth 目录注释 "Clerk JWT (cloud) / local auth"(`README.md:297`)。→ **结论:不需要 Cloud;需要本地 gateway + Postgres(docker compose)+ Claude 凭据 + Spider2 数据集本地 checkout。**
- 另注意 `docker-compose.evalprod.yml` / `Dockerfile.eval-runner` 是 gateway 内置的另一套 "evals"(`SP_EVAL_*` 变量,`e2e-eval/`),与 `benchmark/` 目录无关。

### 2.3 目标 benchmark
- `benchmark/run_direct.py:38-52`:`--suite ade-bench` → `benchmark.ade.runner`;`--suite spider2-snowflake|spider2-lite` → `benchmark.runners.sql_runner`;默认 → `benchmark.runners.direct`(Spider2-DBT)。
- `benchmark/core/suite.py:24-27` `BenchmarkSuite = {DBT: "spider2-dbt", SNOWFLAKE: "spider2-snowflake", LITE: "spider2-lite"}`;`DBBackend = {duckdb, snowflake, bigquery, sqlite}`。
- ADE-bench:`benchmark/ade_tasks.json` "59 ready tasks (duckdb+dbt variant)",任务从 `ADE_BENCH_DIR/tasks/<id>/task.yaml` 读取(`ade/tasks.py`)。
- `benchmark/README.md` 描述 64 个 Spider2-DBT 任务、`run4.sh` 编排——**但 `git ls-files benchmark` 中不存在 `run4.sh`、`gold_build_dates.json`、`mcp_test_config.json`**(README 与代码不一致;`derive_gold_dates.py` 存在)。

### 2.4 被测 agent 如何接入(是否可插拔)
- `benchmark/runners/direct.py:9-10` docstring:"Uses the Claude Agent SDK with MCP config for SignalPilot integration."; :57-59 注释 "Skills are provided by the signalpilot-plugin (installed at user scope). The agent discovers skill names through the plugin."
- `run_agent()`(:62-100+)拼 system prompt(`prompts/dbt_local_system.md`)与用户 prompt(`agent/prompts.py`),调 `run_sdk_agent()`;后续还有 `run_quick_fix_agent`、`run_name_fix_agent` 和 verifier subagent(`prompts/dbt_verify_subagent.md`)。
- `core/workdir.py:44-54` 每个任务写 `.mcp.json`(同一份 SignalPilot MCP 配置)并 `git init`;`benchmark/mcp_config.json` 指向 `gateway.mcp_server`(该模块在 `signalpilot/gateway/gateway/` 下不存在,只有 `gateway/mcp/server.py`;Docker 镜像用的是 `mcp_baked_config.json`,`paths.py:41-42` 优先取 baked)。
- MCP 工具集:`signalpilot/gateway/gateway/mcp/tools/`:`connections, dbt_execute, dbt_project, knowledge, model_map, model_map_hints, model_verify, notebook, notion, query, refresh_mart, reports, sandbox_vm, semantic_layer, workspace_projects`。
- 没有 agent 抽象层、没有 `--agent` 参数(`direct.py` argparse 只有 `instance_id/--model/--max-turns/--skip-agent/--no-reset`)。→ **不可插拔;可复用的是评测器 `evaluation/comparator.py`(复刻 Spider2 `compare_pandas_table()`)、`evaluation/db_utils.py`、日期确定性方案(`derive_gold_dates.py` + faketime wrapper)。**

---

## 3. DataAgentBench(DAB)

仓库 `ucbepic/DataAgentBench @ 0290945`。

### 3.1 LICENSE
- `find . -maxdepth 3 -iname 'license*'` 无结果;README/SUBMISSION_RUBRIC/docs 无 license 字样。
- GitHub API `GET /repos/ucbepic/DataAgentBench` 返回 `license: null`(2026-09-21 查询);GitHub 页面 About 无 license 标签。→ **无许可证 = 默认保留所有权利,商用/再分发前需联系作者。**

### 3.2 `common_scaffold/` 的接入方式
- 入口 `run_agent.py`:参数 `--dataset --query_id --llm --iterations --use_hints --root_name`;读 `query_<dataset>/db_description.txt`(+`db_description_withhint.txt`)、`db_config.yaml`,构造 `DataAgent(query_dir, db_description, db_config_path, deployment_name, exec_python_timeout=600, max_iterations, root_name)` 并 `run()`。
- `common_scaffold/DataAgent.py`:
  - LLM 客户端按 `deployment_name` 关键字选:`gpt`→`AzureOpenAI`,`gemini`/`kimi`/`claude`→OpenAI 兼容 `OpenAI(base_url=...)`(claude 走 `https://api.anthropic.com/v1` + `ANTHROPIC_API_KEY`)(:75-97)。
  - 循环:`call_llm()` 用 `chat.completions.create(model, messages, tools=[tool.get_spec()...])`(:214-221);`handle_reponse()` 无 tool call 则以正文为最终答案并 `terminate_reason="no_tool_call"`(README 说明这种情况判 invalid);`return_answer` 工具终止(:277-282);超过 `max_iterations` 终止。
  - 工具:`query_db`(`QueryDBTool`,`check_load=True` 时自动把 dump 装入 Postgres/Mongo)、`list_db`、`execute_python`(`ExecTool`,Docker `python-data:3.12`,600s 超时,启动后断网 `ExecTool.py:16-29`)、`return_answer`(:146-172)。工具基类 `tools/BaseTool.py`:实现 `get_spec()`(OpenAI function schema)、`_check_args()`、`_exec()`、`clean_up()`;`exec()` 统一记 `tool_calls.jsonl`。
  - 输出:`query_dir/logs/data_agent/<root_name>/{final_agent.json, llm_calls.jsonl, tool_calls.jsonl, exec_tool_work_dir/}`;`final_agent.json` 含 `final_result`、`terminate_reason`、`messages`(README "Execution Logs")。
- 校验:`common_scaffold/validate/validate.py` `validate(query_dir, llm_answer, reason)`:读 `ground_truth.csv`,动态 import 该 query 的 `validate.py` 并调用 `validate(llm_output: str) -> (bool, reason)`(README "Query" 节)。示例 `query_crmarenapro/query1/validate.py` 为字符串包含匹配。
- README "Create Your Customized Agents":scaffold 提供自动连库、只读查询、Docker Python 沙箱;`DataAgent.py` 是"template",**不要求自定义 agent 继承任何接口**。

### 3.3 榜单 PR 要求
`README.md` "How to Submit to the Leaderboard" + `SUBMISSION_RUBRIC.md`:
- 5 runs × 全部 query × 全部 dataset(54 query → 270 trials,`SUBMISSION_RUBRIC.md` §3)。
- 单个 JSON:`[{"dataset": "<name>", "query": "<id>", "run": "0-4", "answer": "<text>"}, ...]`。
- PR 内含:JSON、每次运行的 execution traces、agent 描述(名称、backbone 模型版本、是否用 hints、其他设置)。缺 run 或缺 trace 可能被拒。
- 维护者用官方 validator 重跑答案,并查 trace 的数据泄漏(§2:禁止联网下载答案、禁止读 `ground_truth.csv`/`validate.py`/其他 run 结果;prompt 中不得注入答案);合法 prompt 输入仅 `query.json`、`db_description.txt`、`db_description_withhint.txt`(用了即标 "Hints ✓")与 harness 样板。

### 3.4 本地基础设施与资源
- README "Setup Databases":PostgreSQL **最低 17.5**(实验 17.7);MongoDB Community(实验 v8.2.1);SQLite & DuckDB 为文件,无需服务。Docker 28.4.0,`docker build -t python-data:3.12 .`(`Dockerfile`:python:3.12-slim + pandas + pyarrow)。
- 连接配置默认值(`common_scaffold/tools/db_utils/db_config.py:13-25`):`PG_HOST=127.0.0.1 PG_PORT=5432 PG_USER=postgres PG_PASSWORD="" PG_DB=test MONGO_URI=mongodb://localhost:27017/`,可用 `.env` 覆盖。
- 数据:`git lfs install` 后 clone,`bash download.sh`(PATENTS `patent_publication.db` ~5GB 从 HF `ruiyingm/DataAgentBench-data` 下载,并按 `dataset_manifest.tsv` sha256 校验)。
- Python 环境:`environment.yaml`(conda,python 3.12.12;含 `autogen-ext==0.7.5`、`docker==7.1.0`、`duckdb==1.3.1`、`psycopg2==2.9.9`、`pymongo==4.13.2`、`openai==2.9.0`、`litellm==1.80.7`),`sudo apt-get install libpq-dev`。
- 除 `execute_python` 600s 超时、LLM 调用 600s 超时外,README/rubric 未给出 CPU/内存等资源要求。

---

## 未确认
- Harbor 仓库主地址:我 clone 的是 `laude-institute/harbor`(成功),文档链接全部指向 `harbor-framework/harbor`;两者是否为同一仓库的重定向 `未确认`。
- `uv tool install harbor` 装到的 PyPI 版本号是否等于源码 `0.23.0` `未确认`(未查 PyPI)。
- Harbor Hub 上 `snowflake-labs/data-eng-bench` 数据集当前版本/是否公开可 `harbor run -d` `未确认`(只看了 README 徽章与 `hub.py` 的 sha256 引用,未访问 Hub)。
- LiveSQLBench-CLI vendored Harbor `0.1.45` 与上游哪个 commit 对应、其 `--agent-import-path` 在自定义 installed agent 上是否被实际测试过 `未确认`。
- LiveSQLBench 官方榜单(Google Doc 提交流程)是否接受 CLI-agent 结果 `未确认`(未打开该文档)。
- SignalPilot `benchmark/README.md` 提到的 `run4.sh`、`gold_build_dates.json`、`mcp_test_config.json` 不在仓库中;是否被 `.gitignore`(`benchmark/.gitignore` 未读全)或已删除 `未确认`。
- SignalPilot gateway 在 `docker compose up gateway` 下是否需要额外认证(local auth 模式细节,`gateway/auth/`)`未确认`,未读。
- DataAgentBench `docs/` 站点(`ucbepic.github.io/DataAgentBench`)是否另有 license 声明 `未确认`;论文 arXiv 页面的 license 未查。
- DAB 内置 `DataAgent` 对 Claude 使用 OpenAI 兼容 endpoint `https://api.anthropic.com/v1`,该兼容层当前是否可用 `未确认`。
