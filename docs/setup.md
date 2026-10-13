# 资源与环境准备

开工前一次性准备。依据：ADR-0001 Update、ADR-0006、ADR-0007。2026-10-07 本机检查结果见末尾。

## 现在就要（工单 01 开始用）

| # | 资源 | 说明 | 状态 |
|---|---|---|---|
| 1 | DeepSeek API key，充值约 $20 | platform.deepseek.com。一期上限暂定 $300、推算约 $250（未实测，见 `docs/plan.md` 第 7 节） | ☐ |
| 2 | Anthropic API key，充值约 $20（可选） | 只用于 01 的 5 题 Sonnet 5.5 对照（约 $7）；不跑 Claude 全量 | ☐ |
| 3 | x86_64 Linux 服务器 | Docker、uv、git、git-lfs；Mac 能 SSH 免密登录。建议 ≥8 核 / 32 GB / 100 GB 空闲磁盘（估计，未确认） | ☐ |
| 4 | Mac 上的 Docker 守护进程运行中 | 开发期跑单元测试和单题用 | ☐ |
| 5 | 私有 GitHub 仓库作为 remote | 代码从 Mac 同步到服务器靠它；一期跑通前保持私有，之后转公开 | ☑ `github.com/kevinWangSheng/data-agent`（2026-10-12） |

密钥只放在机器的环境变量或 `.env` 里（`.env` 不进 git），不贴进对话或文档。

## 以后才要

| # | 资源 | 用于 |
|---|---|---|
| 6 | Harbor Hub 账号 | 工单 10，data-eng-bench 提交要 Hub 上的 job 链接 |
| 7 | 服务器上的 PostgreSQL ≥17.5、MongoDB 8（用 Docker 起） | 工单 08 DAB、09 jaffle_shop |
| 8 | DAB 数据（Git LFS，约 5 GB） | 工单 08 |

不需要：GPU、云数仓账号、LiveSQLBench 标准答案邮件（二期）。

## 上游参考仓库

放在仓库外 `~/dev/AI/data-agent-refs/`，按 `docs/research/` 里记录的 commit 检出：harbor、data-eng-bench、SignalPilot、signalpilot-plugin、ktx、DataAgentBench、BIRD-Interact。DAB 无 LICENSE，不复制进本仓库（ADR-0007）。由 agent 在工单 01 开始时 clone。

## 本机检查（2026-10-07）

- arm64 Mac，16 GB 内存，磁盘剩约 25 GB → 只做开发，不跑批量评测。
- 已装：Docker 29.3（守护进程未运行）、uv 0.10.8、Node 24、gh（已登录）、Claude Code 2.1.292、Codex CLI 0.160.1。
- 未装：Harbor、git-lfs、psql。
