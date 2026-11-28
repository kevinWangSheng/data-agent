# 146 — 评测配置文件与迁移第一步：`evals/profiles/`、所有评测入口改读配置文件、快照逐项相同、`config` 事件与哈希、`check_request`

Status: ready-for-agent
Blocked by: 143、144
Spec: ../spec.md（"评测配置文件（Q4）""生效配置的记录（Q6）""迁移（Q5）"第一步）
Claimed by: —
Branch: —

## Goal

每个榜单一份基础配置 `evals/profiles/{data-eng-bench,dab,livesqlbench}.toml`，消融是基础配置加 `evals/profiles/layers/{full,baseline,no-skill,no-verifiers,no-context,full-old-skill}.toml`，名字不变。所有评测入口改读这些文件，给 `data-agent run` 传两次 `--config`（基础配置、层文件，后面覆盖前面）；`version.config` 写层名。改完后删掉 143 留下的过渡：`run`、`mcp-server` 的 `--config` 不再接受旧消融名。评测配置文件复现现在的行为（`[skills].preload` = 现在注入的全部 Skill、Skill 工具关、上限和提示词片段不变、`[mcp]`/`[plugins]`/`[skills].dirs` 为空、`[policy]` 锁 `network = false`），141 的快照守门组逐项相同。每次运行的事件文件第二条（`version` 之后）是 `config` 事件（生效配置全文、sha256、文件列表、Skill/说明文件/插件目录每个文件的哈希，不含解析后的密钥），`ablate` 的 `check_request` 把生效配置哈希算进请求。141 合入的 `TICKET_146` 验收测试全部转绿（只删标记）。

## Authority

- May edit：
  - 新建 `evals/profiles/`（三份基础配置、`layers/` 六份层文件）。
  - `evals/ablate.py`、`evals/board.py`、`evals/dab.py`、`evals/livesqlbench.py`、`evals/jaffle.py`、`evals/harbor_agent.py`、`evals/attribute.py`（`:43`、`:148` 用 `load_config`/`CONFIG_NAMES` 找 Skill 目录）：改读配置文件。`evals/lsb_single_shot.py`、`evals/lsb_single_shot_agent.py`：只从 `livesqlbench.toml` 读 `[models]`/`[roles]`（见 Context"lsb-single-shot"）。
  - `evals/configs/*.yaml`：属于 agent 行为的值移进配置文件，Harbor 自己的 job 字段（并发、超时、retry）留下。
  - `src/data_agent/run_config.py`、`src/data_agent/configs/`：6 份消融配置变成 `evals/profiles/`，删掉 `src/data_agent/configs/` 和旧名的读法（`CONFIG_NAMES`、`load_config(<名>)`）。
  - `src/data_agent/agent.py`：只加 `config` 事件（`run` 里 `version` 事件附近，`agent.py:2245-2259`）和评测用的 `[skills].tool = false`（`build_options` 的 `skills=` `:1721` 保持 `[]`）、`[skills].preload` 决定注入哪些 Skill（`injected_skills` `:1339-1341`）。Skill 工具打开、插件、MCP 的部分是 147 的。
  - `src/data_agent/cli.py`：只删 143 给 `run --config`（`cli.py:290`）、`mcp-server --config`（`:244`）留的旧消融名，并让 `build_options` 拼的 MCP server 参数（`agent.py:1621`）在新方式下可用。
  - `pyproject.toml`：只在选择"配置文件随 wheel 进 Harbor 任务容器"时，给 `[tool.hatch.build.targets.wheel.force-include]`（`pyproject.toml:70-72`）加一行。
  - `tests/acceptance/` 里 141 标了 `TICKET_146` 的文件（`test_config_snapshot.py`、`test_config_event.py`，以及 141 改写过的已有文件，清单见 141 最终回复）：**只删** `TICKET_146` 标记，以及因此没用的定义和 import。
  - `tests/`（除 `tests/acceptance/`）：新增或修改评测入口的单元测试（`tests/test_harbor_agent.py`、`tests/test_dab.py`、`tests/test_eval_jaffle.py` 等里断言 `--config <消融名>` 的地方改成新方式）。
  - `docs/setup.md`：评测配置文件一节。
- Must not edit：`tests/acceptance/` 里除 `TICKET_146` 标记以外的一切；`tests/fixtures/product_config/`（快照不重录）；`skills/`、`skills-frozen/`；`agent.py` 的提示词文字（148）、模型设置（145）、Skill 工具/插件/MCP（147）；`web/`（149）；其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none。
- Spend cap：工单 agent $0（不能 source 模型的 env 文件）。每个榜单 1 题的冒烟由主会话跑。

## Context

行号以 main `e494e49` 为准；140 合入后 `harbor_agent.py`、`jaffle.py` 的行号会移动。

- **现在的评测入口和它们给的配置**：
  - Harbor（data-eng-bench dbt、LiveSQLBench 查数）：`DataAgentOptions.run_config`（`harbor_agent.py:240-242`，校验 `:262-267`）拼进 `--config`（`:393`、`:421`）。命令在任务容器里跑装好的 wheel（`install` `:301-317`，`upload_file`）；wheel 只带 `skills`、`skills-frozen`（`pyproject.toml:70-72`），所以 `evals/profiles/` 要么进 wheel、要么由 `install` 上传，选一种写明理由。
  - `ablate`：`--config` 选消融名（`ablate.py:217`），经 `--ak run_config=<名>` 交给 Harbor（`:511`）；`conditions` 用 `load_config` 算 `skill_version`（`:374-380`）；`check_request`（`:413-440`）。
  - `board`：`--config`（`board.py:100`）、`--ak run_config=`（`:178`）、`check_request`（`:230`）。
  - DAB：进程内调 `build_options` 再换掉系统提示词和 MCP server（`dab.py:515-530`），自己写 `version` 事件（`:471-492`），`max_turns` 用 `large`（`:224-225`），`--config`（`:814`、`:863`）。
  - jaffle：`--config` 默认 `full`（`jaffle.py:446`），拼进命令（`:360`）。
  - `evals/configs/data-eng-bench-duckdb.data-agent.yaml`、`livesqlbench.data-agent.yaml`：job 字段，加 `kwargs.run_config` 等 agent 值（`livesqlbench.data-agent.yaml` 的 `kwargs`）。
  - lsb-single-shot：不经 `data-agent run`，直接调模型，用 `agent.PRICES`、`DEFAULT_MODEL`、`DEFAULT_BASE_URL`（`lsb_single_shot_agent.py:59-67`），`version` 记 `CONFIG_NAME = "single-shot"`（`:74-75`）。spec 把它列进"改成基础配置 + 层文件"的入口，但它没有层开关；`decisions.md` Q8 定：它只从 `livesqlbench.toml` 读 `[models]`/`[roles]`（模型、价格），不套层文件；`CONFIG_NAME` 照旧。
- **迁移第一步的判定**（spec"迁移"、`decisions.md` Q5）：141 的快照守门组（`test_config_snapshot.py`）逐项相同才算完成；本工单不重录快照、不改规范化规则。快照里 MCP server 参数的 `--config` 一项按 141 定的规则比。
  - 现在注入的 Skill：查数 `sql-workflow` + 方言（`agent.py:111`、`:115`），dbt 4 个（`:113`）；`skill_names` 换方言（`:1329-1336`）。
  - `skills=[]`（`:1721`）、`strict_mcp_config=True`（`:1714`）、`setting_sources=["user"]`（`:1716`）在评测运行里不变。
  - `version` 事件的 `config` 字段今天是消融名（`agent.py:2250`），`web/runs.py:180`、`evals/attribute.py:433`、`evals compare` 读它；spec 定结果目录、报告、`evals compare` 照旧用消融名，所以迁移后它仍要是消融名，从哪里得到（层文件名、配置里的一个键）由本工单定，写明。Q8 定：评测写层名（例如层文件名 `full`）。
- **答题规则**：迁移第一步所有评测配置文件（含 LiveSQLBench、jaffle）保留现在的答题规则（Q8）。本工单时答题规则还是代码常量，评测配置文件不用写；148 加 `[prompts]` 时再写进每份评测配置文件。
- **`config` 事件**（spec Q6）：第二条（`version` 之后，Q8）；字段照 141 的契约第 6 项（人审结果）。Skill、说明文件、插件目录每个文件的哈希照 harbor `lock.json` 的做法；`skill_version`（`agent.py:1289-1296`）照旧记。密钥只记 `${VAR}` 原文；`EventLog(secrets=...)`（`:2242`）的兜底脱敏照旧。
- **`check_request`**：spec 定"同一 `--out` 哈希不同就拒绝续跑"。已有 `--out` 的 `request.json` 没有哈希：照 `agent_kwargs` 的先例（`ablate.py:424-425`，"an OUT written before them stays reusable"）还是拒绝，由本工单按 141 的测试定，写明。
- **并行工单**：147、148 Blocked by 本工单，会在评测配置文件里加 `[skills]`、`[prompts]` 的键；145 改模型设置。后合入的一方 rebase。
- **守门测试依赖的接口，保留名字和签名**（141 合并前审查第 9 条；验收测试之后不能改，改名会让守门永久失败）：`context_tools.build_interruptible`、`agent.build_interruptible`；`evals.jaffle.clone_project`、`reset_schemas`、`dbt` 和 `evals.jaffle` 模块的 `subprocess` 属性；`DataAgent(run_config=…, benchmark=…)` 和 `_run_command`；`evals dab` / `evals jaffle` 的命令行参数；`profile_path` 要求容器里的路径含 `profiles/` 一段。见 141 的 `tests/acceptance/product_config_support.py`（`dictionary_from_duckdb`、`jaffle_entry`、`profile_path`）。
- **保留 `run_config.load_config(<EffectiveConfig>)`**：141 的 `product_config_support.run_config_of` 和约 98 个迁移过的验收测试经它取 RunConfig；Authority 里"删掉 `load_config(<名>)`"只删按名字取的那种用法，这个签名保留。
- **快照守门的用途**（人 2026-11-27 定，`decisions.md`"调研后定"第 4 条）：只证明迁移第一步新旧机制一致。本工单合入后快照守门完成任务；之后的重录归主会话（迁移第二步或有意的 Skill 改动），重录要审 diff 并记进工单。本工单合入前不合入改 `skills/` 的提交。

- **数据服务器 `--config` 的形状**（141 契约复核第 5 条，`product_config_support.py` 的 `assert_server_configs`、`config_arg_rule`）：评测里给我们自己的 `mcp-server` 传的 `--config` 每个值要么是等于 `version` 事件 `config` 的消融名，要么是 `evals/profiles/` 下或临时目录（不在测试自己的目录、假家目录、工作区下）的绝对路径；写成两个参数 `--config X`，不用 `--config=X`；重复的 `--config` 要相邻。合并后的配置不能写进 jaffle 的 `out/` 或事件目录。

## Acceptance

- [ ] `TICKET_146` 标记全部删掉；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_config_snapshot.py tests/acceptance/test_config_event.py tests/acceptance/test_config_eval_isolation.py -v` 全部 PASSED，0 个 xfail；快照守门组没有改动就通过（迁移第一步的判定）。
- [ ] 其他已合入的验收测试照旧通过（141 改写过的已有文件里标 `TICKET_146` 的也转绿）。
- [ ] `data-agent run --config full`（旧名）被拒绝，退出码 2；`src/data_agent/configs/` 已删。
- [ ] lsb-single-shot 的模型、价格从 `livesqlbench.toml` 读，有单元测试。
- [ ] 每个评测入口（Harbor 两种、ablate、board、DAB、jaffle）的单元测试断言它传的是 `evals/profiles/` 的文件；`ablate`、`board` 的 `--config` 仍收 6 个消融名。
- [ ] `evals/configs/*.yaml` 里不再有 agent 行为的值（只剩 job 字段）。
- [ ] `docs/setup.md` 写了评测配置文件、层文件、`config` 事件。
- [ ] `make check` 通过。
- [ ] `config` 事件不记逐键来源（Q6；单元测试，141 第 2 轮复核 N3）。
- [ ] 评测入口改成 `--config <路径>` 后同时传 `--no-dictionary-cache`（144 交回：144 认"评测"靠旧消融名或这个参数；不传会悄悄复用值字典缓存）；有单元测试钉住每个评测入口都传。

## Evidence required

`make check` 输出；上面三个验收文件的 `-v` 输出；`git diff main -- tests/acceptance | grep '^[-+]' | grep -v '^[-+][-+]'`；`git diff main --stat -- evals/ src/data_agent/configs/`。不调模型的真实运行（$0）：一次 Harbor 命令的拼法（`_run_command` 的输出）；一次 `data-agent run --config <evals/profiles 里的文件>` 截停前的 `config` 事件（例如本机 `--sandbox local` 加不可用的模型端点，跑到第一条 SDK 错误为止，贴事件文件前几行）。

交给主会话（spec"验收"：每个榜单真跑 1 题冒烟；写出命令，确认路径和 worktree 名）：

- data-eng-bench：`uv run python -m evals ablate --tasks-dir <data-eng-bench>/tasks --manifest evals/manifest.json --out runs/146-smoke-deb --budget 5 --split dev --task dbt-fix-division-by-zero --trials 1 --config full`（照 `ablate.py:74-76` 的示例）。
- LiveSQLBench：同上加 `--benchmark livesqlbench`，开发集一题。
- DAB：`uv run --with-requirements evals/dab-requirements.txt python -m evals dab --dab-root ../DataAgentBench --dataset stockindex --query 1 --config no-context --out runs/146-smoke-dab`（照 `dab.py:17-20`）。
- 每个冒烟确认：运行成功或失败与迁移无关；事件文件有 `config` 事件；`version` 的 `config`、`skill_version`、`skills` 与迁移前同一题的事件相同。

## Stop conditions

- 快照守门组不改规范化规则就过不了（说明行为变了，或规则当初录错了）。
- 删掉旧消融名会让 Authority 以外的代码（例如 `web/`、`evals/report.py`）跑不了。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：迁移第一步，只换机制不换行为；第二步（评测改用产品新默认、配对比较）不是工单，主会话做。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：两次 `--config`、`version.config` 写层名、`config` 事件第二条、lsb-single-shot 只读 `[models]`/`[roles]`、删过渡期的旧名；去掉"已有验收测试""lsb-single-shot 待定""`version.config` 得不到消融名"三条停止条件。
- 2026-11-27（主会话）：按 141 合并前审查和调研后的决定补 Context：保留 `load_config(EffectiveConfig)`、守门依赖的接口、快照守门的用途与失效点。
- 2026-11-27（主会话）：按 141 退回第 1 轮后的契约复核补 Context：数据服务器 `--config` 的形状（快照守门依赖）。
- 2026-11-27（主会话）：按 141 第 2 轮契约复核补验收标准：`config` 事件不记逐键来源。
- 2026-11-28（主会话）：按 144 交回补验收标准：评测入口传 `--no-dictionary-cache`。
