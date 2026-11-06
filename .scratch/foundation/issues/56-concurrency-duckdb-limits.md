# 56 — 评测并发改回 3，DuckDB 设内存和临时文件上限，memory.log 多记 CPU 与压力

Status: needs-info
Blocked by: 58（da-remote exec）；之后主会话用 `da-remote exec` 跑服务器只读检查（命令见下）；之后人定 DuckDB 上限的数值
Spec: ../../../docs/research/2026-11-05-foundation-latency-audit.md（§7）
Claimed by: —
Branch: —

## Goal

服务器上同时跑的 trial 数和 plan §7 一致；每个 DuckDB 连接有明确的内存和临时文件上限，几个实例加起来不会超过宿主机内存、也不会写满磁盘；`memory.log` 能看出 CPU 争用和内存压力。

## 已和用户定下的（2026-11-05，grilling Q12、Q13、Q14）

- 并发：`evals/configs/data-eng-bench-duckdb.data-agent.yaml:15` 的 `n_concurrent_trials` 从 4 改成 3，和 `docs/plan.md:145-151` 一致。
- DuckDB 上限：先跑服务器只读检查，再由人定 `memory_limit` 和 `max_temp_directory_size` 的数值（起点 2 GB 和 4 GB）。数值定下后补进本节，Status 改为 `ready-for-agent`。
- `scripts/da-remote` 的采样（`:498-520`）加上 `docker stats` 的 CPUPerc、`free` 的 Swap 行、`/proc/loadavg`、`/proc/pressure/{cpu,memory,io}`。

## 服务器只读检查（编排会话做，结果贴到 Comments）

找一个正在跑的 trial 容器 `<c>`（或起一个题的容器），通过 `da-remote` 执行：

```
nproc; free -m; stat -fc %T /sys/fs/cgroup; cat /proc/pressure/cpu /proc/pressure/memory
docker inspect --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.CpusetCpus}}' <c>
docker exec <c> sh -c 'nproc; cat /proc/self/cgroup /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max'
docker exec <c> <agent 的 python> -c "import duckdb;print(duckdb.connect().sql(\"select current_setting('threads'),current_setting('memory_limit'),current_setting('max_temp_directory_size')\").fetchall())"
docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}'
```

预期：`cpu.max` 为 `200000 100000`，`memory.max` 为 `8388608000`，threads=2，memory_limit 约 6.2 GiB。agent 的 python 路径见 `evals/harbor_agent.py:194-219`（`AGENT_DIR/venv/bin/python`）。

## Authority

- May edit: `evals/configs/data-eng-bench-duckdb.data-agent.yaml`（只改并发）、`src/data_agent/gateway.py`（只改 `connect_duckdb` 的 config）、`scripts/da-remote`（只改采样）、`tests/test_gateway.py`、新的测试文件
- Must not edit: `src/data_agent/verifier_v2/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（服务器检查和真实运行由主会话跑）
- Spend cap: $0

## Context

- DuckDB 1.2.2（`uv.lock`）读 cgroup：threads 取 ceil(quota/period)（`config.cpp:457-469`、`cgroups.cpp:138-191`），容器里是 2；memory_limit 读 `memory.max`，乘 0.8（`config.cpp:422-430,476-499`），容器里约 6.25 GiB，各实例各自一份（gateway 每条语句一个连接，`gateway.py:113-117`；dbt 是独立进程）。宿主机只有 7.7 GB，上面还跑着别的服务的容器。`max_temp_directory_size` 默认是磁盘可用空间的 90%；工单 50 见过写出几 GB 临时文件。
- `connect_duckdb`（`gateway.py:124-133`）设了 `lock_configuration`，上限只能在连接时写进 config。
- CPU 争用不是主因：pos 的 `grain_facts` 只有 1 个 trial 在跑时 p50 78 s，4 个时 93 s；t47-screen 峰值内存 3676/7940 MB，单 trial 最高 1762 MiB。
- 榜单静态检查不管 `n_concurrent_trials`（`static_analysis.py:71,130`）。
- 风险：上限压低后，个别重查询会更多地写临时文件、变慢；超过临时文件上限时报明确的错，不再写满磁盘。

## Acceptance

- [ ] 测试：`connect_duckdb` 的连接上 `current_setting('memory_limit')`、`current_setting('max_temp_directory_size')` 等于设定值（修复前会失败）。
- [ ] 配置文件 `n_concurrent_trials: 3`。
- [ ] `da-remote` 采样多出的字段有说明（脚本头注释）；`make check` 通过。

## Evidence required

- 测试输出。
- 主会话下一次服务器运行的 `memory.log` 里有 CPU%、Swap、PSI 字段；同时运行的 trial 数不超过 3。

## Stop conditions

- 上限导致现有测试或已知的真实查询（工单 50 的 cross-sell 参考 SQL）报错：交回由人重定数值。
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
