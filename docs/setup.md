# 资源与环境准备

开工前一次性准备。依据：ADR-0001 Update、ADR-0006、ADR-0007。2026-10-07 本机检查结果见末尾。

## 现在就要（开工前）

| # | 资源 | 说明 | 状态 |
|---|---|---|---|
| 1 | DeepSeek API key，充值约 $20 | platform.deepseek.com。一期上限暂定 $300、推算约 $250（未实测，见 `docs/plan.md` 第 7 节） | ☑ 人已填（2026-10-15）；工单 03 的真实运行用它跑通（2026-10-16） |
| 2 | Anthropic API key，充值约 $20（可选） | 只用于工单 09 的 5 题 Sonnet 5.5 对照（约 $7）；不跑 Claude 全量 | ☐ |
| 3 | x86_64 Linux 服务器 | Docker、uv、git、git-lfs；Mac 能 SSH 免密登录。建议 ≥8 核 / 32 GB / 100 GB 空闲磁盘（估计，未确认） | ◐ 别名 `da-server`，SSH、Docker、uv、git、git-lfs、env 文件都通；只有 6 核 / 7 GB、仓库未 clone（`da-remote check`，2026-10-16）。延后到工单 12 全量 / 13 前升配 |
| 4 | Mac 上的 colima 运行中（Docker 运行时；数据在外接盘 sd710） | 开发期跑测试用数据库和单题 | ☑ `m1-kind` 运行中，`docker` context 已切到 `colima-m1-kind`（2026-10-16，`docker info` 通） |
| 5 | 私有 GitHub 仓库作为 remote | 代码从 Mac 同步到服务器靠它；一期跑通前保持私有，之后转公开 | ☑ `github.com/kevinWangSheng/data-agent`（2026-10-12） |

密钥只放在仓库外的 `~/.config/data-agent/env`（照 `.env.example` 填，`chmod 600`），`make test-integration` 读取；不放仓库里的 `.env`，不贴进对话或文档。

## 以后才要

| # | 资源 | 用于 |
|---|---|---|
| 6 | Harbor Hub 账号 | 上榜提交（人做），data-eng-bench 提交要 Hub 上的 job 链接 |
| 7 | 服务器上的 PostgreSQL ≥17.5、MongoDB 8（用 Docker 起） | 工单 13 DAB；jaffle-shop（工单 08）在本地用 colima 起 |
| 8 | DAB 数据（Git LFS，约 5 GB） | 工单 13 |

不需要：GPU、云数仓账号、LiveSQLBench 标准答案邮件（二期）。

## 上游参考仓库

放在仓库外 `~/dev/AI/data-agent-refs/`，按 `docs/research/` 里记录的 commit 检出：harbor、data-eng-bench、SignalPilot、signalpilot-plugin、ktx、DataAgentBench、BIRD-Interact。DAB 无 LICENSE，不复制进本仓库（ADR-0007）。已于 2026-10-14 clone。

## 本机检查（2026-10-07）

- arm64 Mac，10 核，16 GB 内存；内置盘只剩约 5.7 GB（2026-10-15，98%），外接盘 sd710 剩约 650 GB → 只做开发，不跑批量评测。
- 已装：Docker CLI 29.3 + colima（profile `m1-kind`：4 核 / 8 GB / 30 GB，数据在 sd710；没有 Docker Desktop）、uv 0.10.8、Node 24、gh（已登录）、Claude Code 2.1.292、Codex CLI 0.160.1。
- Harbor 已装（`uv tool install harbor`，2026-10-15）；git-lfs、psql 到工单要用时再装。
