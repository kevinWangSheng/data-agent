# 设置界面、数据库和平台身份

日期：2026-11-27。决定见 `docs/adr/0012-settings-ui-and-platform-identity.md` 和对齐记录 `.scratch/product-config/decisions.md`"设置界面 + Slack 权限"一节（S1–S9，人逐条确认）。取证：`docs/research/2026-11-27-chatbot-agent-config.md`、`docs/research/2026-11-23-im-gateway-designs.md`。依赖 `.scratch/product-config/spec.md` 的配置层（143 配置加载、144 接到运行上）和 `.scratch/cloud-deploy/spec.md` 的部署模式（153）。

## Problem Statement

按 ADR-0011，配置只能由部署的人改服务器上的文件；团队在 Slack 里用 bot 时，使用的人没法调整模型、数据源、说明、Skill，也看不到这次运行用了什么设置。网页没有登录，只能监听本机。

## Solution

- 设置分两类：部署层只认文件和环境变量；设置类存 sqlite，在网页设置页和 IM 里改（S7）。
- 四层作用域：组织 → 工作区 → 频道 → 个人；频道里用工作区 + 频道，私聊用工作区 + 个人（S2、S5）。
- 权限接平台：Sign in with Slack；访问和花费类只有工作区管理员改，行为类频道成员可改（S1）；没接 Slack 的部署用管理令牌（S6）。
- IM 里：`!configure` 打开本频道设置；消息和线程里的临时覆盖（S4）；回复带"在网页打开"链接（S8）。
- 评测不碰数据库（ADR-0011 第 3 条）。

## Implementation Decisions

### 设置的两类键（S7）

- 部署层：密钥（`${VAR}`）、`[server]`、`[im.*]` 的 token 变量名与允许名单、`[sandbox]`、`[sources.*]`（连接本身）、`[models.*]`（可选的模型及价格等）、`[policy]`、组织管理员名单（`[server].org_admins`，各平台用户 id）、管理令牌（`DATA_AGENT_ADMIN_TOKEN`）。设置页可看不可改。
- 设置类：`[roles].main` 等模型选择（从部署层登记的里选）、频道选用哪些 `[sources.*]`、`[mcp]` 选用、`[skills]`（dirs、disabled、preload）、`[plugins]`、`[instructions]`、`[limits]`（不超过 `[policy]`）、`[sandbox].network`（不超过 `[policy]`）、`effort`、是否要 @ 才回复。字段与 `config.toml` 同一套有类型定义（143 的 schema），按作用域各存一份覆盖。
- 首次启动用 `config.toml` 的设置类键播种组织层；之后改文件不覆盖数据库。设置页和 `config show` 标每个值的来源（代码默认 / 文件 / 数据库 / 锁定）；文件与数据库不一致时提示，并给管理员"用文件的值覆盖"。
- 每次运行开始解析一次生效设置：代码默认 < 文件 < 数据库（组织 < 工作区 < 频道，或 组织 < 工作区 < 个人）< 单次覆盖（S4）；`[policy]` 最后裁剪，越界报错（同 product-config"`[policy]` 和命令行参数冲突：报错"）。

### 作用域与权限（S1、S2、S5、S6）

- 组织：默认一个，组织管理员 = `[server].org_admins`；local 模式（只监听本机）不登录，本机用户即组织管理员（主会话补：沿用现在本机无登录的行为）。
- 工作区：Slack 工作区 / Discord 服务器 / Telegram 每个 bot 一个；每个工作区一套 token（环境变量名在部署层）。
- 频道：Slack 频道 / Discord 频道 / Telegram 群；私聊对应个人层。
- 谁能改：

| 设置 | 组织层 | 工作区层 | 频道层 | 个人层 |
|---|---|---|---|---|
| 访问和花费类（模型、数据源选用、MCP、Skill 与插件、联网、上限） | 组织管理员 | Slack Owner/Admin；Discord/Telegram 为组织管理员 | 同工作区层 | 不可设（个人层只影响私聊里的行为类） |
| 行为类（说明、effort、是否要 @） | 组织管理员 | 同上 | 频道成员（`[policy]` 可收紧到管理员） | 本人 |

- 频道层任何改动，bot 在频道发一条"谁改了什么"。
- Slack 角色与成员：每次请求查 `users.info`（`is_admin`、`is_owner`）与 `conversations.members`，缓存 5 分钟；不另存角色表。

### 临时覆盖（S4）

- 频道或线程里任何人可在消息里写覆盖（语法由 157 定，候选 `model=`、`effort=`、`source=`、`mcp=`）。模型和 effort 在 `[policy]` 范围内、对整个线程生效直到再换；数据源和 MCP 只能从本频道已开放的里挑。Skill 和说明在线程开始时固定。越界：bot 回复不允许，不悄悄替换。覆盖记进运行事件。

### 网页登录（S5、S6）

- cloud 模式：Sign in with Slack（OpenID Connect），只接受配置里登记的工作区；登录后只看、只改自己所在工作区和自己的个人层；组织管理员看全部。没接 Slack 的部署：组织管理员用 `DATA_AGENT_ADMIN_TOKEN` 登录设置页。
- 浏览器会话：HttpOnly Cookie，值是签名的会话（product-config decisions.md 改票后的决定）；cloud 带 `Secure`。
- 取代 cloud-deploy 工单 156 的"输一次令牌换 Cookie"；bearer 令牌仍可用于脚本调用 API。

### IM（S4、S8、S1）

- `@bot !configure`（Slack）/ `/configure`（Telegram、Discord）：回一个只有本人可见（Slack ephemeral；Telegram、Discord 私信）的链接，打开本频道设置页。
- 回复带"在网页打开"链接（运行详情页）。
- 频道改动公告。

### 会话查看（S8）

- 一个线程一次会话（不变），`/new` 开新会话。运行详情页按身份检查：频道会话该频道成员可看、私聊本人、组织管理员全部；Telegram、Discord 的会话先只有组织管理员。网页只看不接着聊。链接不带权限。

### 存储与审计（S9）

- sqlite 单文件放持久卷（和 `runs_dir` 同卷，`[server].db_path`），带版本号迁移、启动自动升级。表：组织、工作区、设置覆盖（按作用域）、会话索引（线程 → 运行、所属频道）、改动记录。运行记录照旧是文件。
- 改动记录：谁（平台用户 id）、何时、哪层、哪个键、改前改后；密钥类只记"改过"。设置页按层看历史。

## 工单

| # | 工单 | Blocked by |
|---|---|---|
| 157 | 验收测试（人审后合入） | product-config 143 |
| 158 | 设置存储：sqlite、迁移、四层、播种、生效设置解析（文件 + 数据库 + 锁定 + 单次覆盖）、改动记录 | 143、144、157 |
| 159 | 平台身份：Sign in with Slack、Slack 角色与频道成员（缓存 5 分钟）、管理令牌、按 S1 的权限检查、浏览器会话 | cloud-deploy 153、157 |
| 160 | 设置页（前端为主）：分层编辑、来源标注、锁定灰显、文件与数据库不一致的提示与覆盖、改动历史；前端由 Opus 审 | 158、159 |
| 161 | IM 里的设置：`!configure`、频道改动公告、临时覆盖（S4）、"在网页打开"链接、Telegram/Discord 的作用域映射 | 158 |
| 162 | 会话查看权限：运行详情页按 S8 检查 | 159 |

对已有工单的调整：product-config 141 不变（设置层的接缝归 157）；144 读"文件 + 数据库"的生效设置部分交给 158，144 只做文件层；cloud-deploy 153 的网页鉴权改由 159 提供；155 的 Compose 加 sqlite 持久卷；156 作废（由 159、160 取代）。

## Testing Decisions

### 验收测试接缝（待人确认）

- **生效设置解析**：给定文件、数据库里的各层覆盖、单次覆盖和 `[policy]`，解析出的生效设置和每个键的来源；频道里个人层不参与；越界报错。
- **权限**：用替身的 Slack 客户端（`users.info`、`conversations.members`）断言谁能改哪层哪类键；缓存 5 分钟内不重复查询；被撤管理员后过期即失去权限。
- **登录**：替身 OpenID 提供方；只接受登记的工作区；Cookie 是签名会话、cloud 带 `Secure`；管理令牌只给组织管理员。
- **播种与不一致**：首次启动播种；改文件后数据库不变、来源标注出现不一致；管理员覆盖后一致。
- **IM**：替身平台适配器；`!configure` 回本人可见的链接；频道改动公告；临时覆盖在线程内生效、越界被拒；回复带链接。
- **会话查看**：运行详情页对频道成员、非成员、私聊本人、组织管理员的结果。
- **评测隔离**：数据库里有任何设置时，评测入口的运行参数不变。

### 其他

- 单元测试不联网；真实的 Slack 登录和 IM 回合由主会话在部署后跑。

## 验收

- 157–162 合入；按 cloud-deploy 的部署文档部署后，用 Slack 登录、改一个频道设置、在该频道问一题看到新设置生效、点"在网页打开"看到运行详情；非频道成员打不开。

## Out of Scope（排在后面，见 decisions.md"后续"）

- 设置页里新增数据源连接和 Slack 工作区（需要加密存储）。
- 频道里用个人自己的连接器和凭据。
- Discord、Telegram 自己的网页登录和平台角色；配对码。
- Postgres 存储。
