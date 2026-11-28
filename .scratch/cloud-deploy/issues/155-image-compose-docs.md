# 155 — 产品镜像、Compose 示例、`docs/deploy.md`

Status: ready-for-agent
Blocked by: 152, 153, 163
Spec: ../spec.md（Implementation Decisions"沙箱""运行记录""健康检查""交付"）；决定：`.scratch/product-config/decisions.md` Q8
Claimed by: —
Branch: —

## Goal

有三样交付：
- 产品镜像的 `Dockerfile`，和沙箱镜像分开。
- `deploy/docker-compose.yml` 示例：同时构建产品镜像和沙箱镜像（Q8）；data-agent 容器挂 `/var/run/docker.sock` 起沙箱容器；`[server].runs_dir`（含 154 的 `<runs_dir>/.cache/git`）和设置库 `[server].db_path`（sqlite 单文件，和 `runs_dir` 同卷，settings spec S9）放持久卷；`/healthz` 做健康检查。
- `docs/deploy.md`：每个环境变量、令牌、Sign in with Slack 的配置、`DATA_AGENT_ADMIN_TOKEN`、socket 权限、持久卷（含设置库的备份）、升级。

照文档能在一台机器上部署起来。真实部署（网页带令牌、一个 IM 平台各跑通一题）由主会话做，不在本工单。

## Authority

- May edit：
  - 新建：`Dockerfile`（仓库根，产品镜像）、`.dockerignore`、`deploy/docker-compose.yml`、`deploy/config.example.toml`（只有 `${VAR}` 引用和 `.example` 主机名）、`docs/deploy.md`。
  - `docs/setup.md`、`README.md`：各加一行指向 `docs/deploy.md`。
  - `Makefile`：只加一个构建产品镜像的目标（照 `sandbox-image`，`Makefile:36-38`），以及镜像的冒烟检查（放进 `make test-integration` 或单独的目标，不进 `make check`）。
  - `tests/`（除 `tests/acceptance/`）：镜像或 Compose 的冒烟测试，标 `integration`。
- Must not edit：
  - `tests/acceptance/`、`src/`、`evals/`、`skills/`、`web/`。
  - `sandbox-image/`：沙箱镜像不变，Compose 用它的现有 `Dockerfile` 构建。
  - `.github/workflows/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
  - 文档要说明的行为（如 153 的报错文字）和代码对不上时，记进最终回复，不改代码。
- Server access: none。不连任何服务器；部署由主会话做。
- Spend cap: $0。不调模型。

## Context

行号取自 main `e494e49`。

- **前置的研究笔记**：`docs/research/2026-11-27-sandbox-in-container-spike.md`，152 写。建票时这份笔记还不存在。
  - 镜像、Compose、文档里的路径方案（data-agent 容器里的工作区、运行目录、Skill 目录怎么让沙箱挂到，同名路径挂载还是命名卷）、socket 权限、colima 与 Linux 宿主的差别，全部照这份笔记。
  - 不自己另选方案。笔记不存在、或结论是"跑不通"，就停下（Stop conditions）。
- **spec**：`.scratch/cloud-deploy/spec.md`"沙箱""运行记录""健康检查""交付"；Out of Scope（不做 Kubernetes/Helm、托管 secret 管理器）。决定原文：`.scratch/product-config/decisions.md` Q7。ADR-0011 Consequences 要求文档写明"挂 Docker socket 等于给 data-agent 宿主机的 Docker 权限"。
- **153 的行为**：cloud 下每条收紧的实际表现（拒绝还是落成锁定值、报错文字、`/healthz` 的响应），照 153 的最终回复和合入的代码写进文档。
- **154**：不在 Blocked by 里。克隆目录在 `<runs_dir>/.cache/git`（Q8），Compose 的持久卷照这个设，不用等 154。154 已合入的话，文档写 git 来源的写法、令牌变量（经 `GIT_ASKPASS`）、`refresh`；没合入就写"见后续"，不猜它的写法。
- **settings 158、159、160**（ADR-0012；cloud-deploy 156 已作废）：不在 Blocked by 里。
  - 设置库：`[server].db_path` 的默认位置在 `runs_dir` 同卷（默认值以 settings 157 契约 2 和 158 合入的为准），Compose 的持久卷照这个设，不用等 158。158 已合入的话，文档写设置库的位置、启动自动迁移、备份即复制文件、首次启动用 `config.toml` 播种、之后改文件不覆盖库；没合入就写"见后续"。
  - 登录：159 已合入的话，文档写 Sign in with Slack 的配置（Slack 应用的 OpenID 重定向地址 = `[server].public_url` 加回调路径、要的 scope、登记的工作区）、部署层新键（`.scratch/product-config/decisions.md`"建 settings 工单时主会话定的细节"第 7 条）：`DATA_AGENT_SLACK_OIDC_CLIENT_ID`、`DATA_AGENT_SLACK_OIDC_CLIENT_SECRET`、`[server].public_url`（登录回调、`!configure` 和"在网页打开"的链接）、`DATA_AGENT_SESSION_KEY`（签名会话的密钥，cloud 缺它拒绝启动）；`DATA_AGENT_ADMIN_TOKEN`（没接 Slack 的部署由组织管理员用它登录设置页）、`[server].org_admins`，照 159 最终回复列的键和报错文字；没合入就写"见后续"，不猜它的写法。
  - 160 已合入的话，文档写设置页在哪、表单的联网勾选跟随生效设置；没合入就不写。
- **配置**：容器里用显式 `--config <路径>` 或 `DATA_AGENT_CONFIG`，只读这一个文件（product-config spec Q1）。部署模式用环境变量 `DATA_AGENT_MODE=cloud` 或 `[server].mode`（Q8）。所有键以 143–148 合入的为准。
- **镜像要带的东西（已核实）**：
  - `claude-agent-sdk==0.2.187`（`pyproject.toml:9`）自带 Claude Code CLI 的二进制（`claude_agent_sdk/_bundled/claude`），按平台分 wheel，Linux 镜像里要装 Linux 的 wheel。
  - 产品镜像装 `data-agent[im]`（IM 平台库，以及 Sign in with Slack 用的 `slack_sdk`，`pyproject.toml:29-34`；`.scratch/product-config/decisions.md`"建 settings 工单时主会话定的细节"第 13 条）。
  - 产品镜像装 dbt（Q8）。dbt 不是运行时依赖（`pyproject.toml:46-50` 的注释："an installed agent uses the environment's dbt"），而 `dbt_execute` 在 data-agent 一侧执行。版本照 `pyproject.toml:48-50`。
  - 网页 UI 由 wheel 构建钩子用 pnpm 打进包（`hatch_build.py:1-11`、`:57-60`），镜像构建要有 pnpm，或者多阶段构建先出 UI。
  - 只用 uv、pnpm（AGENTS.md）。基础镜像照沙箱镜像用 `python:3.12-slim`（`sandbox-image/Dockerfile:3`）。
  - 镜像里有 docker CLI。沙箱经 `docker` 命令起（`src/data_agent/sandbox.py:226-236`）。
- **沙箱镜像**：
  - `DockerSandbox` 用 `--pull never` 起 `data-agent-sandbox`（`sandbox.py:39`、`:267-271`）。这个镜像只在本地构建（`make sandbox-image`，`Makefile:37-38`）。
  - 所以 Compose 同时构建两个镜像（Q8）：产品镜像，以及从 `sandbox-image/` 构建、标签为 `data-agent-sandbox` 的沙箱镜像（只构建不运行的服务，或用 `docker compose build`）。文档写明 `docker compose build` 这一步。
- **健康检查**：Compose 的 `healthcheck` 用 `GET /healthz`（153）。
- **运行记录**：`[server].runs_dir` 放持久卷（spec"运行记录"）。
- **测试**：spec"其他"要求单元测试不起真容器，容器相关的检查放 `make test-integration`。冒烟测什么 spec 没写，最小做法：镜像能构建、容器里 `data-agent --help` 和 `GET /healthz` 正常。
- **安全**：文档、示例、测试里不写真实的密钥、服务器 IP、主机名（AGENTS.md）。示例主机名用 `.example`，密钥只写 `${VAR}`。pre-commit 的密钥与公网 IP 扫描要通过。

## Acceptance

- [ ] 本机 `docker compose -f deploy/docker-compose.yml build` 构建出产品镜像和 `data-agent-sandbox`（贴命令与输出的末尾几行）
- [ ] 冒烟：容器里 `data-agent --help` 能跑；容器以 cloud 配置起来后 `GET /healthz` 得到 200（贴输出）
- [ ] `docker compose -f deploy/docker-compose.yml config` 通过
- [ ] Compose 示例：
  - 挂 `/var/run/docker.sock`，旁边有注释写明这等于宿主机 Docker 权限。
  - 运行记录（和 154 合入时的克隆目录）、设置库（`[server].db_path`）放命名卷或 152 定的挂载方式。
  - 有 `healthcheck`。
  - 密钥都从环境变量或 `env_file` 来，不写值。
- [ ] `docs/deploy.md` 写清：
  - 每个环境变量：模型密钥与 `DATA_AGENT_BASE_URL`（cloud 照旧认，Q8）、`DATA_AGENT_ADMIN_TOKEN`（任何模式监听非本机地址都要；脚本调 API 的 bearer，权限 = 组织管理员，也是没接 Slack 时组织管理员登录设置页的凭据；没有单独的 `DATA_AGENT_WEB_TOKEN`，定的细节第 1 条）；159 合入时还有 `DATA_AGENT_SLACK_OIDC_CLIENT_ID`、`DATA_AGENT_SLACK_OIDC_CLIENT_SECRET`、`DATA_AGENT_SESSION_KEY`、`[server].public_url`；161 合入时还有多个 Slack 工作区的写法 `[im.slack.workspaces.<名字>]`、`DATA_AGENT_MODE`、配置路径、IM 令牌、数据源连接，154 合入时还有 git 令牌。
  - 怎么生成令牌、请求怎么带（bearer，脚本调用 API 用）；159 合入时还有 Sign in with Slack 的配置、`DATA_AGENT_ADMIN_TOKEN` 的生成与用法、浏览器会话 Cookie。
  - socket 权限，以及 colima 与 Linux 宿主的差别（照 152）。
  - 持久卷（运行记录与设置库同卷）、先构建沙箱镜像、升级（拉新镜像、重启、持久卷保留；158 合入时写设置库自动迁移、升级前先复制库文件备份）。
  - cloud 下每条收紧（照 153）：联网默认关、配置文件可打开、单次运行超过配置得到 409。
- [ ] `make check` 通过

## Evidence required

贴到 Comments：
- `make check` 输出。
- 镜像构建、冒烟、`docker compose config` 的真实输出（本机 Docker，$0）。

交给主会话（最终回复写出步骤）：在一台机器上照 `docs/deploy.md` 部署；网页跑通一题（浏览器登录要 settings 159 合入：Sign in with Slack 或管理令牌）、一个 IM 平台跑通一题（spec"验收"）；记下文档里不对或缺的地方。

## Stop conditions

- `docs/research/2026-11-27-sandbox-in-container-spike.md` 不存在，或者它的结论是跑不通、或没有可行的路径方案。
- 照 152 的方案做，要改 `src/`（例如 `DockerSandbox` 的挂载源路径要换算）。
- 153 合入的行为和 spec 对不上，文档没法照实写。
- 构建要联网拉基础镜像和依赖，本机拉不下来。
- 第二轮审查修复后仍不过。
- 不停止、不重启本机的 Docker/colima 守护进程，不改它的配置。只起、删本工单自己的容器、卷和镜像，按名字前缀 `da-155-` 区分。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-27（主会话建票）：cloud-deploy 的交付工单。等 152（实测）和 153 合入。依赖 152 的研究笔记 `docs/research/2026-11-27-sandbox-in-container-spike.md`，建票时还没写。真实部署由主会话照文档做。建票时发现的问题按 `decisions.md` Q8 定（两个镜像都由 Compose 构建、产品镜像装 dbt）。
- 2026-11-27（主会话）：按 ADR-0012 调整：Compose 的持久卷加设置库 `[server].db_path`（sqlite，和 `runs_dir` 同卷）；`docs/deploy.md` 加 Sign in with Slack 的配置、`DATA_AGENT_ADMIN_TOKEN`、`[server].org_admins`、设置库的迁移与备份（照 settings 158、159 已合入时写，没合入写"见后续"）；去掉"一期不做登录、多用户"；原指向 156 的三处改指 settings 159、160。Blocked by 不变。
- 2026-11-27（主会话）：按 ADR-0012 调整：按"建 settings 工单时主会话定的细节"第 1、7、13 条，产品镜像装 `data-agent[im]`；令牌只写 `DATA_AGENT_ADMIN_TOKEN`（不再有 `DATA_AGENT_WEB_TOKEN`）；文档加 `DATA_AGENT_SLACK_OIDC_CLIENT_ID`、`DATA_AGENT_SLACK_OIDC_CLIENT_SECRET`、`[server].public_url`、`DATA_AGENT_SESSION_KEY`。
- 2026-11-27（主会话）：Blocked by 加 163（Skill 进沙箱和孤儿清理的代码在 163；本工单的路径方案照 163 合入的做法写，不再需要宿主机上有 Skill 目录）。
