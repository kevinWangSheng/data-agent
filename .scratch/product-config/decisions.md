# product-config：对齐会话已定的决定

写 spec 之前的对齐记录（2026-11-27，人逐条确认）。依据：`docs/research/2026-11-27-product-config-audit.md`、`docs/research/2026-11-27-config-layering-local-cloud.md`。spec 写成后以 spec 为准。

## 方向（已定）

1. 产品与评测分开。产品打开 Claude Code 的默认能力：Skill、使用者的 MCP 配置、本地插件。禁用 Bash（ADR-0010）、不读本仓库 `.claude/`（ADR-0001）保留。
2. 不写死：审计查出的写死、临时、为评测而设的东西统一改成可配置。
3. `full` = 全部启用、按需加载、少数常驻（`preload`）。agent 不读或不照做 Skill 是我们的问题，之后按轨迹调；接受这个风险。
4. 跑榜照使用者的方式配置，走同一套配置机制，不走专门代码路径。
5. 分层：代码默认值 < 配置文件 < 环境变量（只放密钥、部署模式、配置文件路径）< 命令行参数；最上面是运维锁定；加 `data-agent config show`。
6. 一期云端只做"能用 Docker 部署到服务器给 IM bot 和网页用"：部署模式开关、cloud 下默认收紧、监听地址可配、上下文可从 git 拉。登录、多用户、管理界面加数据库不做。改 `docs/plan.md:38`、`:349`。
7. 产品默认从 `no-verifiers` 改回 `full`（推翻 2026-11-08）。
8. 使用者自己的 MCP 服务器可带写库凭据：产品允许，文档写明不在只读保证之内；评测一律不加载。
9. 迁移：6 个配置改成评测配置文件后，先在小样本上验证新旧跑法一致再切换。

## 先行工单

- 140 产品路径三处安全默认（CLI dbt 默认 `scratch`、服务器级联网开关、隐藏别的运行的 `da_run_*`），不等 spec。

## Q1 配置文件放哪（已定）

- 一个文件 `config.toml`，把现有 `--data-sources`（TOML）、`--mcp-servers`（JSON）、`--im`（TOML）并成其中的节；旧的三个参数去掉，不做兼容。
- 用户级 `~/.data-agent/config.toml`；项目级 `<工作区>/.data-agent/config.toml`（不含密钥，可进 git）；不加 local 层。不放 `~/.config/data-agent/`（密钥目录，coding agent 不能读）。
- 显式 `--config <路径>` 或 `DATA_AGENT_CONFIG` 时只读这一个文件，不找用户级、项目级（云端挂载、评测配置文件都用这种）；否则 代码默认值 < 用户级 < 项目级 合并；命令行参数最后覆盖。
- 合并：按节按键合并，同一个键后面的层整个替换，列表不拼接。
- `--config` 改成配置文件路径；现有 6 个消融配置名变成 `evals/profiles/*.toml`（Q4）。
- 密钥只写 `${VAR}` 引用；出现明文密钥拒绝加载。
