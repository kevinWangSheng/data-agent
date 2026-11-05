# 基础问题取证：超时、尾延迟、固定开销（2026-11-05）

依据：`runs/t47-screen`（服务器，8bde360，工单 50 修复前，15 个 trial，开发集）的事件文件和 `result.json`，加本地复现和源码阅读。6 个只读子 agent 并行取证，主会话汇总并抽查。脚本和中间数据在本机 `runs/t47-screen/foundation-audit/`（不进 git，`a1`–`a6` 对应下面各节）。未实测的写"未确认"。

## 0. 先改正的两条

- pos-operations 是 **3/3** 撞 Harbor 4000 s（`AgentTimeoutError`，墙钟 3804–4009 s），不是 2/3；其中 2 个超时后照样 1.0（Harbor 按超时时的状态评分）。
- "主 agent 派验证器前调 41–44 次 compare_by_key" 不普遍：LQFt7xL 派前 0 次，71 次全是验证器的。

## 1. 计时口径错，报告没有分位数（a3）

- 事实：`agent.py:794-805` 的 `tool_start`/`tool_end` 来自 SDK 消息流，一批并行调用的结果一起回来，批里每个工具的耗时都等于整批最慢的那个。Glob p99 74 s、Grep 58 s、list_tables p95 76 s 全是和一个 50–80 s 的 Bash 同批的假象；只看不重叠的调用，它们的 p99 是 0.4–3 s。
- 只看不重叠的调用：compare_by_key p50 27 s、p99 281 s（全部调用算是 156 s / 613 s）；grain_facts 没有单独调用的样本。工单 47 的"验证器墙钟中位数 317 s"按 Agent 调用起止取并集，不受影响。
- 准确来源：SDK 0.2.168 的 PostToolUse / PostToolUseFailure 输入带 `tool_use_id` 和 `duration_ms`（工具执行时间），子 agent 里触发时带 `agent_id`（`claude_agent_sdk/types.py:329-379`）。按单个工具触发而不是按批，是从打包的 CLI 推断的，未确认。SDK 消息有 `parent_tool_use_id`（`types.py:1368/1383`），事件里没记。
- 修法：加 PostToolUse/PostToolUseFailure 钩子，写 `tool_timing` 事件（tool、tool_use_id、agent_id、duration_ms、failed、timed_out：按网关固定前缀 "Query timed out after" 判定）；`tool_start`/`tool_end` 补 `parent`；`evals/process.py` 的 `trial_metrics` 加每 trial 墙钟、工具调用数、验证器墙钟、各工具耗时和超时数，汇总出 p50/p90/p95/p99，经 `report.json` 带进 ablate 报告。`accept` 是否走同一路径未确认。

## 2. 没有一层知道剩余时间（a2）

- 时限分层：trial 4000 s（题目 `task.toml`，榜单禁止覆盖）；验证器只有 maxTurns 50（工单 47 分支 `verifiers.py:93`，main 上还没有）；MCP 工具无总时限（观测最长 615 s）；单条 SQL 150 s（`gateway.py:21`）；Bash 360/600 s，validate 300 s。
- Harbor 用 `asyncio.wait_for` 掐时（`harbor/trial.py:651-662`），`run()` 不接收超时值；取消后容器里的进程没被杀：FMEvAdp 到 4009–4016 s 还在调 compare_by_key，而评分约 4002 s 就开始了（未确认会不会和 dbt 写库冲突）。超时的 trial 没有 `result` 事件，token 和花费都没记。
- 先例：Harbor 的 Cline agent 把普通 kwarg `agent_timeout_sec` 传给 CLI（`cline.py:83,396-415,1150`）；榜单静态检查只拦 override 和 multiplier（`static_analysis.py:64-70,128`）。SignalPilot 和 Harbor 的 claude_code agent 都只有轮数上限。
- 建议：
  - kwarg `agent_timeout_sec`，默认 4000，CLI 换算成截止时间。
  - 剩余不到 1000 s 时派单 deny（理由里写剩余秒数），Stop 同时放行并记 `ended_unverified`。两处必须一起改，否则会循环。1000 s 的依据：14 个验证器窗口里 13 个 ≤970 s。
  - 到 T−150 s（3850 s）时主循环自己停：关掉 SDK，写 `result(deadline)`，正常退出。150 s 对应网关单条语句上限。
  - 单元测试注入时钟；真实运行 pos-operations × 1，`agent_timeout_sec=1500`。

## 3. grain_facts 的 78 s 是排队，不是单次耗时（a1）

- 事实：单次调用在服务器上约 3.5–4 s，本地 main 上 0.56 s，其中 SQL 0.24–0.33 s。78 s 是一批 grain_facts 在 MCP 线程池里抢 GIL 串行排队（`mcp/.../func_metadata.py:164` 的 `anyio.to_thread.run_sync`）：批越大越慢，大致成正比。工单 48 的 `readOnlyHint` 对纯 Python 工具没有带来真正的并行。
- 根因：每次调用都重读不变或少变的东西：快照 YML（`project.py:285`，整个 run 期间不变）、工作区模型文件映射（`project.py:341` → `files_under`）；contract_check 内部还完整调一次 model_diff。
- 工单 50 已降一半：21 个一批，本地从 9.4 s 降到 4.5 s。再缓存快照 YML 和 `model_files`（按目录 mtime）能降到 0.5–0.6 s（monkeypatch 模拟）。先例：SignalPilot `gateway/dbt/cache.py:11-73`。服务器上能到多少未确认。
- 和 a3 的出入：a1 说一批 21 个，a3 说 4–7 个，"批"的定义不同。#1 修好后用 `duration_ms` 一测就清楚。

## 4. 主 agent 例行自查，参考 SQL 写两遍（a6）

- 按 Agent 调用窗口归因（窗口内的调用数和 `verifier_end.tool_calls` 对得上）。主 agent 派前调用：pos 32/21/0，cross-sell 10/2/2，web 0/8/5，全部是例行自查，没有一次是回应 FAIL。
- 驱动它的是工单 47 分支 `SKILL.md:258-275`："whether or not the verifier ran, run the key-level check yourself… For every model… call `compare_by_key`"，STOP 条件也绑在这次自查上（:284-288）。SignalPilot 的第 8 步没有主 agent 自查（`signalpilot-plugin/.../SKILL.md:261-272`）。
- 验证器自己：每个模型一次 grain、约一次 compare；完全相同输入的重复调用只有 3 次；重试来自报错和超时。
- 按每次 compare 2 s 估算，pos 三个 trial 从 3800–4000 s 降到约 1160–1870 s，之后剩下的是轮数和花费（假设调用串行，未确认）。
- 建议：删掉例行自查，只在没有验证器、复现 FAIL、粒度类 NEEDS_INFO 三种情况下主 agent 自己调；工具侧不必批量化。
- 排除的疑点："keys only in the model: 0" 下面却列出样例，是 8bde360 的计数和样例两条语句各自取哨兵值（`LIMIT 1` 不定序）造成的；main 上计数和样例都来自同一个 `AS MATERIALIZED` 的 `d`（`compare.py:378-397`），从结构上不会再出现，没用那份数据重跑。

## 5. dbt 固定开销（a5-A）

- scan 每次 74 s，全花在 YAML 上，不调 dbt：`skills/dbt-workflow/scan_project.py:576-590` 对每个缺 SQL 的模型都重新 rglob、`safe_load` 全部 yml（34 × 31 = 1055 次，本地 cProfile 占 40 s 里的 35.7 s）。修法：每个 yml 只解析一次，建好模型到文件的映射，或用 CSafeLoader；单次预计约 8 s（推算）。这是上游文件，改了要在 NOTICE 和文件头注明。
- 完整重新解析只发生在每个 trial 第一次和来回切 `--vars` 时。第一次的原因是镜像用 `--target dev` 构建（`base-image/Dockerfile:64`），target 算进了 vars_hash。**工单 40 的"避免不了"可以推翻**：本机 dbt 1.10.23 实测，环境变量 `DBT_TARGET=dev` 和 `--target dev` 的 hash 相同（dbt `cli/params.py:678`），可以走部分解析。修法：SDK env 加 `DBT_TARGET=dev`，每个 trial 省 20–50 s。服务器上未验证；容器里镜像自带的 `partial_parse.msgpack` 能不能用也未验证。
- Skill 加"不要来回切 `--vars`"：receivables 因此多了 5 次 60–68 s 的完整解析。
- 单模型 `dbt run` 30 s 主要是 2350 个模型项目的固定成本：部分解析约 10 s、建图 6–9 s，模型本身 0.5 s。

## 6. 守卫拒绝（a5-B）

15 个 trial 共拒绝 66 次，按批时间戳约浪费 13 s/trial，主要代价是轮数。

| 模式 | 次数 | 原因 | 修法 |
|---|---|---|---|
| `echo DB_TYPE; echo DUCKDB_PATH; ls /app` | 15 | 题面要求 echo，模型合成了一条；`command_guard.py:120` 只认单独一条 | DBT_PROMPT 一句：单独执行 `echo $DB_TYPE`，看目录、读文件用 Glob/Read/Grep |
| `ls -R \| head`、`cat`、`cp` | 约 20 | `agent.py:137` 没说用什么工具代替；`2>/dev/null` 落到 `:131`，提示有误导 | 同上，守卫不放宽 |
| Grep `path=/app` | 18 | dbt_utils 样例 `.env` 触发 `workspace_guard.py:250-256`；排除型 glob `!**/dbt_packages/**` 被 `_glob_matches`（`:131-152`）忽略 | 按 ripgrep 语义识别排除整个目录的 glob；拒绝提示给示例 |
| `--vars '{"k": "v"}'` | 1 | `{}` 是禁用字符（`:46`），提示太笼统 | 拒绝提示写出具体字符，建议 `--vars 'k: v'` |
| Glob/cat 任务文件 | 6 | `TASK_FILE_NOTE` 没写用什么工具读 | 写明"用 Read 读 {path}" |

## 7. 资源：并发与 DuckDB（a4）

- 事实：并发是 4（`evals/configs/data-eng-bench-duckdb.data-agent.yaml:15`；ablate 不传 `-n` 就用这个值），plan §7（`plan.md:145-151`）定的是 3；4 × 2 核 > 6 核。榜单静态检查不管 `n_concurrent_trials`。
- DuckDB 1.2.2 在容器里读 cgroup 配额：threads = 2，memory_limit = 8000 MiB × 0.8 ≈ 6.25 GiB（`config.cpp:422-499`、`cgroups.cpp:138-191`）。每个实例各自按这个上限算，宿主机却只有 7.7 GB；`max_temp_directory_size` 默认是磁盘可用空间的 90%（工单 50 见过写出几 GB 临时文件）。本机是 10 线程、12.7 GiB，这很可能是"本地快"的原因之一，未确认。
- CPU 争用不是主因：pos 的 grain_facts 只有 1 个 trial 在跑时 p50 78 s，4 个时 93 s（+19%）。峰值内存 3676/7940 MB，单个 trial 最高 1762 MiB。`memory.log` 没有 CPU%、swap、PSI。
- 建议：并发改回 3；`connect_duckdb` 的 config 设 `memory_limit` 约 2 GB、`max_temp_directory_size` 约 4 GB（设了 `lock_configuration`，只能在连接时设）；`memory.log` 加 CPUPerc、Swap、loadavg、PSI。
- 服务器只读检查（主会话用 `da-remote`）：容器内 `cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max`、`nproc`、DuckDB 的 `current_setting('threads'|'memory_limit'|'max_temp_directory_size')`、`docker stats` 带 CPUPerc、`/proc/pressure/*`。预期结果是 `200000 100000`、`8388608000`、threads=2。
