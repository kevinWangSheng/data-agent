# 资源与环境准备

开工前一次性准备。依据：ADR-0001 Update、ADR-0006、ADR-0007。2026-10-07 本机检查结果见末尾。

## 现在就要（开工前）

| # | 资源 | 说明 | 状态 |
|---|---|---|---|
| 1 | DeepSeek API key，充值约 $20 | platform.deepseek.com。一期上限暂定 $300、推算约 $250（未实测，见 `docs/plan.md` 第 7 节） | ☑ 人已填（2026-10-15）；工单 03 的真实运行用它跑通（2026-10-16） |
| 2 | Anthropic API key，充值约 $20（可选） | 只用于工单 09 的 5 题 Sonnet 5.5 对照（约 $7）；不跑 Claude 全量 | ☐ |
| 3 | x86_64 Linux 服务器 | Docker、uv、git、git-lfs；Mac 能 SSH 免密登录。台数和大小按工单 09 实测的单题峰值内存定（见 `docs/plan.md` 第 7 节"服务器与并发"）；20 GB 空闲磁盘（题目约 11 MB，DAB 数据约 13 GB，其余是镜像） | ◐ 别名 `da-server`：6 核 / 7 GB / 98 GB，SSH、Docker、uv、git、git-lfs、rsync、env 文件都通，按 plan §7 口径并发 3（`da-remote check`，2026-10-20）。这台不能升配。仓库经只读 deploy key（SSH）clone 到服务器，`da-remote setup → push → start → status → pull` 1 题冒烟跑通（2026-10-20） |
| 4 | Mac 上的 colima 运行中（Docker 运行时；数据在外接盘 sd710） | 开发期跑测试用数据库和单题 | ☑ `m1-kind` 运行中，`docker` context 已切到 `colima-m1-kind`（2026-10-16，`docker info` 通） |
| 5 | 私有 GitHub 仓库作为 remote | 代码从 Mac 同步到服务器靠它；一期跑通前保持私有，之后转公开 | ☑ `github.com/kevinWangSheng/data-agent`（2026-10-12） |

服务器只经 `da-remote` 访问（用法见脚本头注释）：`setup [<commit>]` 拉仓库到指定 commit 并 `uv sync`；`push <name>` 把本机 `runs/<name>/`（如临时 manifest、题目副本）传上去；`start <name> -- <evals 子命令与参数>` 后台跑评测，日志在服务器 `runs/<name>/run.log`，评测的 `--out` 用 `runs/<name>`；`status [<name>]` 看状态、日志末尾和花费；`pull <name>` 取回本机 `runs/<name>/`；留出集的 run（`ablate`）只取回汇总（`reports/`、`run.log`、控制文件），逐题 trial 与 Harbor 日志留在服务器（ADR-0004）。

两个榜单由 `da-remote benchmarks` 备好（可重复跑，已就绪的步骤跳过），全放在服务器仓库旁的 `da-benchmarks/`：

- `data-eng-bench/`（钉 `a3278ad`）：评测用 `--tasks-dir ../da-benchmarks/data-eng-bench/tasks`（路径相对服务器仓库，`start` 在那里跑）。
- `DataAgentBench/`（钉 `6ad9d68`，无许可证，只在服务器 clone）：数据由 DAB 自带的 `download.sh` 从 Hugging Face 取并按 `dataset_manifest.tsv` 校验（日志 `da-benchmarks/download.log`）；评测用 `--dab-root ../da-benchmarks/DataAgentBench`。
- 镜像 `python-data:3.12`（DAB 的 Dockerfile，`execute_python` 用）。
- 容器 `da-dab-postgres`（`postgres:17`，127.0.0.1:15432）与 `da-dab-mongo`（`mongo:8`，127.0.0.1:37017，WiredTiger 缓存 0.5 GB）；数据卷 `da-dab-pgdata`、`da-dab-mongodata`，删掉容器和卷即可重来。DAB 装库要调 `psql` 和 `mongorestore`，服务器不装系统包，所以 `da-benchmarks/bin/` 里的同名包装脚本经 `docker exec` 在容器里跑它们（两个容器只读挂载 DAB 目录，路径与宿主机相同）。
- 连接设置（`PG_*`、`MONGO_URI`、`PG_CLIENT` 和 `bin/` 的 PATH）写在服务器 `~/.config/data-agent/dab.env`（600，密码随机生成、不打印）；`start <name> -- dab ...` 会额外读它，并用 `--with-requirements evals/dab-requirements.txt` 跑。删了 `dab.env` 而保留 Postgres 数据卷会对不上密码，要连卷一起删。

`da-remote check` 也报这些就绪项：两个 clone 的 commit、题目数、DAB 数据大小与清单一致、镜像、`dab.env`、两个数据库从宿主机可连。

密钥只放在仓库外的 `~/.config/data-agent/env`（照 `.env.example` 填，`chmod 600`），`make test-integration` 读取；不放仓库里的 `.env`，不贴进对话或文档。

## 以后才要

| # | 资源 | 用于 |
|---|---|---|
| 6 | Harbor Hub 账号 | 上榜提交（人做），data-eng-bench 提交要 Hub 上的 job 链接 |
| 7 | 服务器上的 PostgreSQL ≥17.5、MongoDB 8（用 Docker 起） | 工单 13 DAB；jaffle-shop（工单 08）在本地用 colima 起。服务器上由 `da-remote benchmarks` 起（工单 21） |
| 8 | DAB 数据（`dataset_manifest.tsv` 36 个文件约 13 GB） | 工单 13；服务器上由 `da-remote benchmarks` 下载并校验（工单 21） |

不需要：GPU、云数仓账号、LiveSQLBench 标准答案邮件（二期）。

## 上游参考仓库

放在仓库外 `~/dev/AI/data-agent-refs/`，按 `docs/research/` 里记录的 commit 检出：harbor、data-eng-bench、SignalPilot、signalpilot-plugin、ktx、DataAgentBench、BIRD-Interact。DAB 无 LICENSE，不复制进本仓库（ADR-0007）。已于 2026-10-14 clone。

## 本机检查（2026-10-07）

- arm64 Mac，10 核，16 GB 内存；内置盘只剩约 5.7 GB（2026-10-15，98%），外接盘 sd710 剩约 650 GB → 只做开发，不跑批量评测。
- 已装：Docker CLI 29.3 + colima（profile `m1-kind`：4 核 / 8 GB / 30 GB，数据在 sd710；没有 Docker Desktop）、uv 0.10.8、Node 24、gh（已登录）、Claude Code 2.1.292、Codex CLI 0.160.1。
- Harbor 已装（`uv tool install harbor`，2026-10-15）；git-lfs、psql 到工单要用时再装。
