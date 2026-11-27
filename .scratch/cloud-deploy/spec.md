# 云端部署：用 Docker 部署到服务器，给 IM bot 和网页用

日期：2026-11-27。决定见 `docs/adr/0011-product-config-and-eval-profiles.md` 第 5 条和 `.scratch/product-config/decisions.md`（方向 6、Q7，人逐条确认）。取证：`docs/research/2026-11-27-config-layering-local-cloud.md`（调研第 4、5、6、8 条：部署层与应用层、部署模式开关、运维锁定、上下文从 git 拉）。依赖 `.scratch/product-config/spec.md` 的配置层（工单 143、144）。

## Problem Statement

`data-agent web` 只监听 127.0.0.1（`cli.py:37-39`），没有访问控制；IM bot 一般跑在服务器上，现在只能在开发机上开着。部署到服务器还有两个问题：网页没有登录，监听公网地址等于谁都能查库、花额度；ADR-0010 要求沙箱拿不到凭据，data-agent 在容器里时要能另起沙箱容器。

## Solution

- `[server].mode = "local" | "cloud"`（默认 `local`，行为照旧）。`cloud` 默认收紧有风险的选项。
- 一个镜像和一份 `docker-compose.yml` 示例：data-agent 容器挂 Docker socket 起沙箱容器，运行记录放持久卷。
- Skill、说明文件、数据源文档可以写成 git 地址，启动时拉、按间隔再拉。
- 一期不做登录、多用户、管理界面加数据库。

## Implementation Decisions（Q7）

`mode = "cloud"` 时：

- **网页**：要访问令牌 `DATA_AGENT_WEB_TOKEN`（请求带 `Authorization: Bearer`）；没设令牌时拒绝监听非本机地址（照 ktx `ktx mcp` 的做法，调研附录 A）。`local` 模式不变。
- **IM**：允许用户名单（`[im.<平台>]`）为空就拒绝启动。
- **密钥**：配置里引用的 `${VAR}` 缺一个就拒绝启动，报出缺哪个变量。
- **联网**：默认关，可在配置里打开；`local` 模式照 ADR-0010 默认开。
- **dbt**：`[policy].dbt_target = "scratch"` 为默认锁定。
- **模型端点**：单次运行（网页表单、命令行参数）不能覆盖端点，只用配置文件里的。
- **沙箱**：必须 Docker 后端；Compose 挂 `/var/run/docker.sock`，data-agent 起沙箱容器；沙箱不挂凭据、默认无网络；`local` 后端拒绝。文档写明挂 socket 等于宿主机 Docker 权限。容器内外的工作区路径不同（data-agent 容器里的路径 ≠ 宿主机路径），由工单 152 实测定方案（例：运行目录用同名路径挂载，或用命名卷）。
- **上下文从 git**：`[skills].dirs`、`[instructions].files`、`[sources.*].docs` 的值可以是 `git+https://…@<分支或提交>#<子目录>`；启动时浅克隆到持久卷，按 `[server].refresh`（如 `"1h"`，省了不刷新）再拉；拉失败时继续用上一次的内容并写一条错误日志。git 的令牌走 `${VAR}`。
- **运行记录**：`[server].runs_dir` 放持久卷。
- **健康检查**：`GET /healthz`，不要令牌，只返回是否就绪。
- **交付**：`Dockerfile`（产品镜像，和沙箱镜像分开）、`deploy/docker-compose.yml` 示例、`docs/deploy.md`（每个环境变量、令牌、socket 权限、持久卷、升级）。

## 工单（待人确认拆分）

| # | 工单 | Blocked by |
|---|---|---|
| 151 | 验收测试（人审后合入） | product-config 143 |
| 152 | 实测：容器里挂 Docker socket 起沙箱、工作区路径能挂进沙箱、`dbt_execute` 和值字典在容器里可用；跑不通就停下回对齐 | — |
| 153 | `[server].mode`：cloud 下的令牌、监听地址、缺密钥拒绝启动、IM 名单、联网默认、`policy` 锁定、端点不可覆盖、`/healthz` | 144、151 |
| 154 | 上下文从 git：`git+https` 地址、浅克隆、`refresh`、失败回退 | 143、151 |
| 155 | 镜像、Compose 示例、`docs/deploy.md`；主会话在一台机器上按文档部署一次、IM 和网页各跑一题 | 152、153 |

## Testing Decisions

### 验收测试接缝（待人确认）

- **启动检查**：`mode = "cloud"` 时，没令牌却要监听非本机地址、缺引用的密钥、IM 名单为空、沙箱后端为 `local`，各自拒绝启动并给出原因；`local` 模式下这些都和现在一样。
- **网页令牌**：cloud 下不带或带错令牌的请求得到 401，`/healthz` 不要令牌。
- **锁定**：cloud 下单次运行要求联网、改端点、`dbt_target = task` 都被拒绝或按 `policy` 落成锁定值，并记进 `config` 事件。
- **git 来源**：用本地 file:// 仓库做替身：启动拉取、刷新后看到新提交、拉失败时继续用旧内容。

### 其他

- 152、155 的真实部署由主会话做；单元测试不起真容器（用替身），容器相关的检查放 `make test-integration`。

## 验收

- 151–155 合入；按 `docs/deploy.md` 在一台机器上部署，网页（带令牌）和一个 IM 平台各跑通一题。

## Out of Scope

- 登录、多用户、权限、管理界面加数据库（ADR-0011 第 5 条）。
- Kubernetes / Helm。
- 托管的 secret 管理器（AWS、k8s secret 引用）；一期只用环境变量。

## Further Notes

- `docs/plan.md` §1（`:38`、`:349`）的"不做公网部署"改为"一期只做 Docker 部署，不做登录、多用户"。
