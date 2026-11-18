# 工具边界：从过滤命令改为隔离环境加控制凭据

agent 现在只有查数的 MCP 工具和读文件工具；dbt 任务的 Bash 只能跑 dbt（工单 04 的 `command_guard.py`）；任何运行都不能联网。查出数据之后的计算、合并、画图都做不了，dbt 之外的命令事先也列不全。

我们对照了上游（`~/dev/AI/data-agent-refs/`）：没有一家把 Bash 限制成只能跑 dbt。
- SignalPilot 跑榜 agent 用 `bypassPermissions`，开全套工具，容器就是边界。
- SignalPilot 产品用 `sandbox_exec`，在每次运行一个的 VM 里执行任意命令，VM 里没有仓库凭据；连库的 dbt 只走 `dbt_execute`。
- nao 用 `execute_sandboxed_code`，micro-VM，联网，可以 pip；Web 搜索由项目设置控制。
- dash 由 Postgres 的 `default_transaction_read_only` 保证只读。

决定（人 2026-11-17 逐条确认，细节见 `.scratch/agent-tools/spec.md`）：

1. **隔离环境**：agent 进程在宿主机上，shell 和代码走 MCP 工具 `sandbox_exec`，产品里每次运行一个 Docker 容器；跑 data-eng-bench 时 Harbor 的任务容器就是隔离环境，`sandbox_exec` 直接本地执行。内置 Bash 和 `command_guard.py` 去掉。
2. **沙箱拿不到数据库**：不放凭据、不挂库文件；代码要用的数据由查询结果写成文件传入。只读网关保留为查数工具，不再是安全边界；数据库层只读由网关的只读连接保证。
3. **写的路径**：`dbt_execute`，参数和命令白名单照 SignalPilot，只有它拿得到 profile；产品里写到每次运行专用的临时 schema，跑榜时写题目的库。
4. **网络**：运行级选项 `--network` 同时控制沙箱联网和 WebSearch、WebFetch；不放进 `configs/*.toml`，因为那些层配置是产品和评测共用的。网页后端默认带上，评测入口都不带，CLI 不带时关。
5. **规则**：AGENTS.md、CODING_STANDARDS.md、CONTEXT.md 里"所有 SQL 走只读网关""唯一的写是 `dbt run`"改成"沙箱和 agent 拿不到写库凭据，写只走 `dbt_execute`；跑榜和评测的入口不开 `--network`"，和代码同一张工单（100）改。
6. **验收**：新增能力只要求不退步：开发集 full 每题 3 次（不带 `--network`）不低于 main、回归集不退步；data-eng-bench、DAB 各一次小范围验证。

## Considered Options

- **保留命令白名单，按需加命令**：列不全，每加一条就是一次绕过审查（工单 04 两轮审查都在补绕过：`#`、花括号、后台运行）；上游没有这样做。
- **宿主机上开不受限的 Bash**：能直接打开库文件写入，也能读到宿主机上的凭据。
- **micro-VM**：隔离更强，但依赖更重（nao 的 boxlite 还有已知 bug，`execute-sandboxed-code.ts:63-66`）；先用 Docker。
- **沙箱里给只读数据库连接**：上游两家都不给；超大结果集的场景出现了再议。

## Consequences

- 取代工单 04 的命令守卫设计；ADR-0002、ADR-0005 末尾加"见 ADR-0010"。
- 产品自托管要装 Docker。
- 已知限制：跑 data-eng-bench 时，沙箱的 `local` 后端能直接写题目的 DuckDB 文件，"只经 `dbt_execute` 写"在跑榜时不强制（SignalPilot 跑榜同样如此）。
- 产品默认联网，查出的数据可能被代码或 WebFetch 发到外部；设置里可以关，README 写明默认开。
- 提示词和 Skill 会变，两个榜单的成绩可能波动，所以验收两个榜单都跑。
