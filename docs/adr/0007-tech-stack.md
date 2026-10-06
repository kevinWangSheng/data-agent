# 技术栈与运行环境（一期默认）

按「推进原则」直接定，跟着上游走，可改。

- **语言与工具链**：Python 3.12 + uv（依赖与虚拟环境）+ pytest + ruff。理由：SignalPilot `benchmark/`、Harbor、DAB、dbt 全是 Python。
- **Agent**：`claude-agent-sdk`（Python），首次安装时锁定版本（SignalPilot 镜像锁的是 `0.1.59`，以当前版本为准）。模型经 `ANTHROPIC_BASE_URL` 可配（ADR-0001 Update）。
- **MCP server**：官方 `mcp` Python SDK，stdio 方式运行，由 agent 进程拉起。
- **数据访问**：复制的 SignalPilot `engine/`（sqlglot）+ `duckdb` + `psycopg` 3；dbt 用 `dbt-core` + `dbt-duckdb` + `dbt-postgres`。
- **评测**：Harbor（`uv tool install harbor`）跑 data-eng-bench；报告脚本用 pandas/numpy 做 bootstrap。
- **形态**：一期只有 CLI，没有 Web UI、没有常驻服务。
- **许可**：本仓库 Apache-2.0；从 SignalPilot / signalpilot-plugin 复制的文件保留原版权头并在 `NOTICE` 注明来源。DAB 仓库无 LICENSE：不把 `common_scaffold` 复制进本仓库，运行时从本地 clone 按路径 import。
- **运行环境**：Mac 用于开发与单元测试；批量评测在 x86_64 Linux 服务器上跑（Docker；DAB 另需 PostgreSQL ≥17.5、MongoDB 8、Git LFS 拉数据）。

## Consequences

- Mac 只剩约 25 GB 空闲磁盘、16 GB 内存，不适合跑批量评测与 DAB。
- Harbor 任务镜像能否在 arm64 原生运行未确认，这也是把评测放到 Linux 服务器上的原因之一。
