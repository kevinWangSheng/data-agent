# 调研：部署后经 Slack 等聊天工具使用的 agent，配置怎么组织

日期：2026-11-27。只取证，不做设计决定。三路只读子 agent：同类产品官方文档（Onyx、Dust、Devin、Cursor、Codex、Glean、Dify）、Claude in Slack（Claude Tag）与 Claude Code 云端环境的官方文档、本地上游仓库（SignalPilot、nao、dash；ktx 无 Slack 代码）。未核实处在附录里标 (unverified)；Dust 的用户映射页 404，Claude Tag 的账号关联流程没找到，没有任何产品写明配置改动是即时生效还是要重启（上游代码里 SignalPilot 每个事件重读配置、nao 配置变了就重建 bot）。

起因：配置分层定为"配置文件 + 环境变量 + git 上下文，无用户层、无管理界面"（ADR-0011）后，人质疑这不像真实的聊天 bot 产品，要求看一般架构。

## 反复出现的做法

1. **安装一次，行为在产品自己的管理面里配。** 工作区管理员装一次 Slack 应用；之后的行为配置放在产品的管理界面（库里），环境变量和文件只放部署参数（令牌、地址、限流）。Onyx、Dust、Devin、Cursor、Glean、Codex、Dify；Claude Tag（claude.ai/admin-settings/claude-tag，Owner 或 Claude Tag Admin）；SignalPilot、nao 自托管也有网页管理界面，改配置要管理员（SignalPilot `OrgAdmin`、nao `adminProtectedProcedure`），聊天用户改不了。dash 没有管理界面，全靠环境变量和文件。
2. **频道是绑定单位：工作区默认 + 频道覆盖。** 频道绑定到一个 agent、一组知识、一个仓库或环境。Onyx（agent + 文档集，可"关掉默认"）、Dust（每频道一个 agent）、Cursor（每频道默认仓库）、Devin（企业版频道对应组织）、Glean（频道允许名单）、Claude Tag（组织 > 工作区 > 频道 > 线程/个人，下层继承上层、频道覆盖工作区；模型、连接器、Skill/插件、自定义说明、仓库、环境都能按频道配）。上游三家较弱：一个工作区绑一个项目，频道只有允许名单（SignalPilot `allowed_channel_ids`）。
3. **单条消息能覆盖频道默认。** Dust `+Agent`、Cursor `repo=`/`model=`、Devin `!org`、Codex 在请求里点名仓库或环境；Claude Tag 在请求或线程里点名仓库才克隆。
4. **频道配置能在 Slack 里改。** Cursor `@Cursor settings`、Devin `!org`、Claude Tag `@Claude !configure` 和频道的 Configure 链接。改动记谁改的（Cursor）。
5. **身份：大多数是共享服务身份，按需再关联个人账号。** SignalPilot、dash 所有请求走一个共享身份，只记提问者的 Slack id；Claude Tag 的频道和群聊走 Claude 自己的服务账号，凭据在代理层注入、不进沙箱，只有一对一私聊且成员关联了 claude.ai 账号时才用成员自己的账号和计费；个人连接器只用于本人的任务，要先批准。按邮箱或一次性链接关联账号（Devin、Cursor、Codex、Glean、nao），只在有按人权限或计费时才需要；按提问者权限过滤数据只见于企业产品（Glean、Codex）。
6. **Slack 侧的策略是简单的允许规则：** 哪些频道、只在被 @ 时回复、谁能触发、不进外部/Slack Connect 频道（Glean、Onyx、Codex、Cursor）。
7. **线程就是会话。** Dust、Devin、Codex、Cursor、SignalPilot、nao（`/new` 重置）、dash。
8. **上下文是文件，可从 git 来。** dbt 项目、nao 项目目录（容器启动时克隆、按计划刷新）、dash `knowledge/`；Claude Tag 从克隆的仓库读 CLAUDE.md、`.claude/rules`、Skill、agent、命令（仓库的 hooks 和 `.mcp.json` 不运行）。
9. **限流和预算在运营层。** Onyx 用环境变量设每分钟请求数和每日上限；Claude Tag 在管理面设用量上限；nao 有按人预算。没见到按频道的预算。
10. **云端环境（Claude Code on the web）：** 环境在选择器或管理面配置；环境变量是 `.env` 格式、同环境的会话都能读，所以凭据放网络密钥或连接器；网络分 None / Limited（默认，放行包管理器）/ Full；setup 脚本以 root 在 Ubuntu 24.04 上、在环境变量设好之前跑；云端会话读仓库的 CLAUDE.md、rules、Skill、agent 和（单仓库时）settings.json，不读 `~/.claude/settings.json`、`settings.local.json`、设备上的 managed 文件，只认服务端下发的 managed settings。
11. **环境变量可以给管理面播种，管理界面因此可选。** nao 启动时用 `SLACK_*` 填默认项目的 Slack 设置，界面里存过的不覆盖；SignalPilot 有不用 OAuth、不用界面的纯环境变量模式。

## 两个子 agent 各自推断的"单人自托管的最小版"（未验证，仅供讨论）

- 保留 `config.toml` 和环境变量，配置文件是唯一的事实来源；管理界面等非技术管理员要改时再加。
- 加频道绑定：工作区默认 + `[channels."<频道 id>"]` 覆盖，指向一套设置（模型、数据源与工具、Skill、说明）。
- 加单条消息的覆盖（如 `profile=名字`）。
- Slack 用户 id 和频道 id 的允许名单；拒绝共享和外部频道；不做账号关联。
- 数据访问用共享服务身份，允许名单就是权限边界；每次运行记 Slack 用户 id 供审计；按人凭据等用户数据权限不同时再做。
- 线程即会话；限流和每日上限在运营层。

---

## 附录 A：同类产品官方文档

# Web research: Slack bot configuration in deployed agent products (accessed 2026-11-27)

Evidence is official docs only. Pages were fetched through a summarizing tool, so wording is paraphrased. "(unverified)" marks anything the docs did not state.

## Per product

### Onyx (Danswer), self-hostable
Sources: https://docs.onyx.app/admins/getting_started/slack_bot_setup.md , https://docs.onyx.app/overview/onyx_anywhere/slack
- Scopes: a Default Configuration applies to all channels and DMs. A per-channel Channel Configuration overrides it. "Disable Default Configuration" makes the bot answer only in configured channels, and it also stops DM answers.
- Per-channel settings: which Agent answers, which Document Sets to search, respond-to-tags-only, citations required, ephemeral replies, and "respond to specific users or groups". The docs advise using an agent with a narrow knowledge scope.
- Editor and location: Admin Panel -> Slack Integration. An admin adds bots with Bot token, App-level token (`connections:write`) and an optional User token. It uses Socket Mode, so no public URL is needed.
- Env vars: set on the background worker, for operational knobs only. Examples are `ONYX_BOT_MAX_QPM`, `ONYX_BOT_RESPONSE_LIMIT_PER_TIME_PERIOD` (5000 per 86400 s), `ONYX_BOT_NUM_DOCS_TO_DISPLAY` and `ONYX_BOT_FEEDBACK_VISIBILITY`.
- Identity: the page does not say whether Slack users map to Onyx users, or whether document ACLs apply to bot answers (unverified). The only access control described is who may trigger the bot.
- Storage: not stated. The UI-created config presumably lives in the DB (unverified).

### Dust
Source: https://docs.dust.tt/docs/slack
- Workspace level: an admin enables the Slack Bot once for the whole workspace (Admin > Integrations) and installs the Slack app. The bot is separate from the Slack data connection, which syncs channels as knowledge, and from the Slack tools agents can call.
- Channel level: an admin links an agent to a channel from that agent's "Slack Channel Settings". @Dust in that channel goes to the linked agent, which can optionally reply to every message.
- Routing: `@Dust +AgentName` picks an agent. One agent answers per message. The fallback order is channel default agent, then @dust, then another default agent. DMs need no mention. Replies are in threads.
- Identity and per-user permissions: not in the fetched page (unverified). The Slack-to-Dust user mapping page returned 404.

### Devin
Source: https://docs.devin.ai/integrations/slack
- Workspace level: an admin connects the workspace in Settings > Connections > Slack. One Slack workspace maps to one Devin org or one Enterprise.
- Identity: every user must click "Link user profile" once. Matching is by email, and linking fails if the emails differ.
- Channel scope: with Enterprise and multiple orgs, admins map channels to orgs ("Channel mapping"), and unmapped channels use a default org. DMs and unmapped channels use the user's own default org (`!org`). A thread stays in its session's org.
- Per-message options are bang commands (`!new`, `!fast`, ...). "Code channels" are dedicated to one session.
- Repo and environment binding is not described on the Slack page (unverified). It comes from org-level config.

### Cursor Slack / background agents
Source: https://cursor.com/docs/integrations/slack
- Repo resolution order: message text (`repo=`, `branch=`, `model=`), recent activity, routing rules, channel default, personal default repo. Model resolves similarly, with a default set in the Dashboard.
- Channel level: `@Cursor settings` in a channel sets that channel's default repo. Channel settings override personal defaults. Any team member can change it, and the bot posts who did.
- Identity: the user links their Slack account in the Cursor dashboard. If the user is on several teams, they pick a "Team for Slack Agents". Matching uses the Slack identity scopes.
- Admin: reply mode (follow threads or mentions only), a team default self-hosted pool (Enterprise), and who can follow up.
- Edited via: the dashboard plus in-Slack commands.

### OpenAI Codex / ChatGPT in Slack
Source: https://learn.chatgpt.com/docs/third-party/slack (redirected from developers.openai.com/codex/integrations/slack)
- Workspace level: a workspace owner or admin sets up the Slack deployment. Cloud delegation needs a published environment shared with the workspace.
- Identity: workspace setup and personal account connection are separate. If prompted, the user connects their account and Slack, then re-mentions the bot. With RBAC on, both the deployment service account and the user need access.
- Environment and repo: there is no per-channel binding. The bot picks from workspace-shared environments the user can access, guided by the repo or environment named in the request. Personal environments are excluded.
- Channels: public and private channels in the workspace. Slack Connect (external) channels are not supported.

### Glean (enterprise assistant)
Source: https://docs.glean.com/administration/platform/embedded-integrations/slackbot/admin-guide/manage-permissions-security
- Install: a Slack workspace admin approves the app. A Glean admin configures it in the Admin console.
- Channel allowlist: admins choose all public channels, only channels where the app is added, or a specific set. It never responds in channels with external parties, and that cannot be changed.
- Identity: only users who have logged into Glean once and hold an Assistant license get answers. Answers are filtered by the asking user's own source permissions. Two users in the same channel can see different results. How Slack maps to a Glean identity is not stated (unverified).
- Public Mode only uses broadly shared content. "Find more information" gives a private reply that can include restricted sources.

### Dify (self-hosted)
Source: https://docs.dify.ai/en/self-host/use-dify/workspace/channels/slack.md
- Dify has a Slack "Channel" at Settings > Channels. It is for human-input cards in workflows, not a general chat bot. You enter the Slack app credentials, and `TRIGGER_URL` must be reachable from Slack.
- Identity: Dify matches Slack members to Dify workspace members by email (`users:read.email`) and syncs them with "Sync now".
- Binding: Slack is chosen per Human Input node inside a workflow, not per channel.
- A separate Marketplace Slack Bot plugin forwards messages to a Dify app by webhook (https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/develop-a-slack-bot-plugin). Details not read.
- Open WebUI: no official Slack bot found (unverified). The search returned nothing for it.

## Recurring patterns (products that show each)
1. Two-layer install and config. A workspace admin installs the Slack app once and sets connection secrets. Behavior config then lives in the product's own admin UI, not in files. Shown by: Onyx, Dust, Devin, Cursor, Glean, Codex, Dify.
2. Channel is the binding unit, with a workspace default and per-channel override. A channel is bound to an agent or persona, a knowledge subset, or a repo. Shown by: Onyx (agent plus document sets), Dust (agent per channel), Cursor (default repo per channel), Devin (channel to org on Enterprise), Glean (channel allowlist).
3. Per-message override sits on top of the channel default. Shown by: Dust (`+Agent`), Cursor (`repo=`, `model=`), Devin (`!org`, bang commands), Codex (name the repo or environment in the request).
4. Channel config can also be edited from inside Slack by a slash or mention command. Shown by: Cursor (`@Cursor settings`), Devin (`!org`), Dust (partly; it uses its web UI).
5. Identity links the Slack user to a product user. It uses email auto-match or a one-time link. It is required where per-user permissions or billing matter. Shown by: Devin (Link user profile, email match), Cursor (link in dashboard), Codex (personal account connect), Glean (must have logged in once), Dify (email sync).
6. Per-user permission filtering is a feature of enterprise assistants and of agents that touch private resources. Glean filters by the asker's permissions. Codex checks the user's environment access. Onyx and Dust docs do not state it (unverified).
7. Slack-side policy is simple allow rules: which channels, tag-only, who may trigger, no external or shared channels. Shown by: Glean, Onyx, Codex, Cursor (reply mode).
8. Config lives in a database behind an admin UI. Files and env vars carry only deployment and operational knobs such as rate limits, tokens and URLs. Shown by: Onyx (env vars for QPM and limits, UI for channels), Dify (`TRIGGER_URL`). DB storage itself is inferred, not stated in the docs.
9. Threads are the unit of session and context. A follow-up in the thread continues the session. Shown by: Dust, Devin, Codex, Cursor.
10. Budgets and rate limits are mostly operator-level. Onyx has global QPM and daily response caps via env vars. Cursor needs usage-based pricing enabled. No product showed per-channel budgets (not found).

## What a minimal version for a solo self-hosted project would keep
Reading from the patterns above. This is inference, not a documented fact:
1. Keep the files and env vars. Patterns 1 and 8 show products put only deployment settings (tokens, rate limits, URLs) in env, and that matches a solo, single-tenant setup. The admin UI exists in these products because they are multi-user and multi-tenant (inference).
2. Add one channel-binding table in config. A workspace default plus per-channel overrides, as in pattern 2. A channel maps to a profile, which is a model, a set of data sources and tools, a Skill set and instructions. This is the one concept every product has and `config.toml` lacks. It can be a `[channels."C0123"]` section.
3. Add a per-message override syntax such as `profile=name` or `+name`. It is cheap and covers pattern 3.
4. Add a Slack user allowlist and a channel allowlist. This covers pattern 7 (who may trigger, which channels, no shared channels). The ids can be Slack user ids listed in config, so no linking flow is needed.
5. Use a shared service account for data access, and treat the allowlist as the permission boundary. Per-user credentials and OAuth linking (pattern 5) are only needed once users have different data rights. Defer them. The docs show that need arises in Devin, Cursor and Glean because they act on per-user resources.
6. Keep thread-as-session (pattern 9).
7. Keep operator-level rate limits and a daily cap in env, as Onyx does.
8. Skip: admin UI, SSO, per-user permission filtering, channel edits from inside Slack. Add in-Slack editing only if hand-editing the file becomes a pain, with an audit line like Cursor's "who changed it".
9. Open question for the owner: does config change need a restart or a live reload? The docs do not state this for any product (unverified).


---

## 附录 B：Claude in Slack 与 Claude Code 云端环境

# Claude Tag (Claude in Slack) and Claude Code cloud configuration: research notes

Access date for all URLs: 2026-11-27. Claude Tag is in public beta per its docs; behavior may change.
Claude Code's earlier Slack integration is retired: "the earlier Claude Code in Slack is retired" (https://code.claude.com/docs/en/slack.md). Team/Enterprise use Claude Tag; Pro/Max/Ultra cannot use Claude Tag and are pointed to Projects or cloud sessions.

## 1. Configuration scopes

Scopes: the whole organization ("Slack" page), a Slack workspace, a channel. A scope without its own setting inherits from its parent; a channel setting overrides its workspace's (https://claude.com/docs/claude-tag/admins/workspaces.md is not fetched; inheritance stated in https://claude.com/docs/claude-tag/admins/setup-overview.md and https://claude.com/docs/claude-tag/admins/customize.md). Per-user scope exists separately as personal connectors and DM settings.

| Scope | What lives there | Who edits | Where |
|---|---|---|---|
| Organization / Slack page | Enable switch ("Respond in all channels"), DMs, default Model, Slack instructions, default environment for channels, bundles, connectors, skills/plugins, auto-mode allow rules, who can invoke Claude, guest handling | Owner, or Claude Tag Admin (Enterprise) | claude.ai/admin-settings/claude-tag |
| Workspace | Pairing, Model (or Inherit), instructions, connectors/bundles, environment (Advanced tab), allow rules | Owner / Claude Tag Admin | Channels tab under "Claude's access" |
| Channel | Channel instructions, Model, repositories, connections, plugins, environment, "How Claude participates" (reply without @-mention), allow rules | Owner / Claude Tag Admin; channel managers (Enterprise) for model, repos, connections, plugins; channel members can edit instructions and participation unless blocked | Configure link in reply footer, `@Claude !configure`, or admin Channels tab |
| Thread | Locked skills, plugins, instructions when thread starts; model can switch per thread | Anyone in the thread (model switch via Slack request) | Slack thread |
| Personal (user) | claude.ai connectors used in DMs and for own tasks in channels | The member | claude.ai Customize > Connectors; Claude Tag Home tab in Slack for approval mode |
| Usage | Spend limit org-wide and per channel | Admin | claude.ai/admin-settings/usage/claude-tag |

Sources: https://claude.com/docs/claude-tag/concepts/settings-map.md ; https://claude.com/docs/claude-tag/admins/customize.md ; https://claude.com/docs/claude-tag/admins/setup-overview.md

Per-layer content (from https://claude.com/docs/claude-tag/admins/customize.md):
- Connectors: systems and credentials Claude uses (service accounts, not persons).
- Plugins and skills: instructions teaching tool use; repo skills (.claude/skills) apply only in sessions that have that repo cloned. A skills repository can supply skills to a whole scope (https://claude.com/docs/claude-tag/admins/skills-repo.md).
- Custom instructions: standing guidance per scope; outranks channel memory.
- Channel memory: facts saved by talking to Claude; workspace notes saved from public channels are read in all channels of the workspace.
- Models: per scope; footer of each reply names the model. Allowlist behavior differs by plan (see customize.md "Models your organization allows").
- Permissions: auto-mode allow rules per scope (up to 50 rules, 1,024 chars each; inherit downward). Sessions run in auto mode; a permission checker runs on Claude Sonnet 5.
- Repos: granted per place (Slack / workspace / channel) or via bundles (https://claude.com/docs/claude-tag/admins/configure-github.md).

Repo/MCP files inside a session: see section 5.

## 2. Who edits what, and where

- Admin console: https://claude.ai/admin-settings/claude-tag (Claude's access: Bundles, Claude's connectors, Personal connectors, Skills and plugins, Channels). Setup requires the Owner role on Team/Enterprise (https://claude.com/docs/claude-tag/admins/setup-overview.md).
- Usage/spend: https://claude.ai/admin-settings/usage/claude-tag.
- Cloud environments: admin page "Cloud environments" in admin settings; org default at https://claude.ai/admin-settings/claude-code (https://code.claude.com/docs/en/cloud-environments.md).
- Git providers / GitHub: https://claude.ai/admin-settings/source-control (shared with Claude Code).
- Slack commands (verified in docs):
  - `/invite @Claude` in a channel adds Claude (run from message box, not a thread). https://claude.com/docs/claude-tag/users/getting-started.md
  - `@Claude connect` (sent as a message with no other text) returns a pairing code; a Slack workspace admin runs it. https://claude.com/docs/claude-tag/admins/setup-overview.md
  - `@Claude !configure` returns the channel Configure link. https://claude.com/docs/claude-tag/users/commands.md (linked from settings-map)
  - `@Claude !restart` archives and restarts a thread's session; `!fast` toggles fast mode. https://claude.com/docs/claude-tag/concepts/how-it-works.md , https://claude.com/docs/claude-tag/admins/customize.md
- Slack app Home tab: personal connector approval mode (Auto mode / Ask every time / Allow with review). https://claude.com/docs/claude-tag/concepts/personal-connectors.md
- Repo files: CLAUDE.md, .claude/ (rules, skills, agents, commands) are read from the clone (section 5). Hooks and .mcp.json from a repo are NOT run in Claude Tag sessions (https://claude.com/docs/claude-tag/admins/configure-github.md, "What loads from a repository").
- Channel Configure page instructions and admin instructions write the same field (settings-map.md).

## 3. Slack user to Claude account mapping, and credentials

- Two identities (https://claude.com/docs/claude-tag/concepts/agent-identity.md):
  - Channel and group DM: Claude acts with its own service accounts (Claude Slack app posts; Claude GitHub App authors PRs; a service account per other tool). Work bills to the organization. Credentials are held in a credential store and injected by Agent Proxy at the network boundary; they are never placed in the sandbox.
  - One-to-one DM from a member who has connected a Claude account: runs on that member's own claude.ai account, with their connectors; attributed to them; billed to their seat. PRs still authored by the Claude GitHub App.
- Personal connectors in a channel: a member's own connectors are used only for that member's own requests, after approval (Allow / Allow with review / Don't allow). Other people's requests in that thread use the channel's access. Results can be held for review (sensitive-content screen; Enterprise can force review of every result). Routines and Claude-initiated work use channel access, never personal connectors. Source: https://claude.com/docs/claude-tag/concepts/personal-connectors.md
- Account linking (Slack user to a claude.ai account): the docs say a member "has connected a Claude account" and an admin can restrict linking by verified email domain (Enterprise), but the exact user-facing linking flow was NOT confirmed in the pages read (unverified). Sources: https://claude.com/docs/claude-tag/admins/restrict-access.md ; https://claude.com/docs/claude-tag/users/getting-started.md (DMs run on own account).
- Members without a linked account: DMs from them can bill to the organization; admins can stop them (restrict-access.md "Direct messages from members without a Claude account"). Exact linking mechanics: unverified.
- Admin-side Claude accounts for other tools: create an email for Claude, invite it as a member of each tool (least privilege), create an API key as that account, add as connector. Claude's actions show in each tool's audit log under Claude's name. https://claude.com/docs/claude-tag/admins/setup-overview.md
- Connectors credentials: not in env vars; use connectors. Env vars are readable by all sessions on the environment (https://claude.com/docs/claude-tag/admins/customize.md).

## 4. How a channel or thread picks repo and environment

- Repositories: granted per place (Slack, workspace, or channel) on the GitHub page under Claude's connectors, or via a bundle (applies to channels the bundle covers). Granting does not clone. A session starts with no repos checked out; Claude clones one when the request names it or someone in the thread names it. Channel managers can add only repos their GitHub account administers. Source: https://claude.com/docs/claude-tag/admins/configure-github.md
- Environment (channel sessions only use org-level environments: shared or self-hosted, not personal ones): precedence is (1) channel Environment setting, (2) workspace setting, (3) Slack page setting, (4) org default at claude.ai/admin-settings/claude-code. Set on the scope's Advanced tab under Sessions. Source: https://claude.com/docs/claude-tag/admins/customize.md ; https://code.claude.com/docs/en/cloud-environments.md ("Set the environment a Claude Tag channel uses").
- Channel with guests: sessions run on the standard environment regardless of these settings (customize.md).
- Model: channel > workspace > organization; a new thread picks up scope changes, running threads switch at the next message unless a model switch was already made there (settings-map / customize.md).
- Threads lock skills, plugins, and instructions at start; connectors and domain rules apply per request. New threads pick up all changes (how-it-works.md).

## 5. Cloud environments (Claude Code on the web and Claude Tag)

Source: https://code.claude.com/docs/en/cloud-environments.md (access 2026-11-27) and https://code.claude.com/docs/en/claude-code-on-the-web.md.

- Where configured: environment selector at claude.ai/code, or Cloud environments page in admin settings for org-shared environments. Shared environments show under an Organization heading. Personal environments can be shared via "Who can use it".
- Environment variables: `.env` format, one KEY=value per line; available to commands Claude runs; read at session creation and again on VM restore or rebuild (existing sessions keep old values until then). OTEL_* variables are reserved for telemetry. Anyone who uses the environment can read the values; use network secrets for keys. The session sets `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` itself, which overrides a value you add.
- Network secrets: a key the agent proxy attaches to matching requests after they leave the session; the key stays out of the sandbox. Adding a secret to a shared environment requires an org admin; a non-admin path exists (see doc).
- Network access levels: None (no outbound), Limited (default allowlist of package registries, GitHub, cloud SDKs while "Allow package managers" is checked, plus listed "Allowed domains"), Full (any domain). Default environment is Limited. Saved "Trusted"/"Custom" open as Limited or Full. GitHub traffic goes through a separate GitHub proxy and MCP connector traffic is not subject to the allowlist. No org-wide domain allowlist exists; each environment has its own list (per the doc: "there's no organization-level allowlist"). Claude Tag: see agent-identity.md "Agent Proxy" (three allow layers: credential rule, allowed domain entry, environment network setting).
- Setup script: Bash, runs as root on Ubuntu 24.04, runs before Claude Code starts, before the environment variables are set (so set needed values in the script). Must exit 0 (use `|| true` on non-critical lines). Keep under ~5 minutes so the snapshot cache builds; cache rebuilds when the script or allowed hosts change and after about seven days. With None network access installs fail. Setup scripts vs SessionStart hooks: the doc recommends setup scripts for VM-level tools and SessionStart hooks for project setup like npm install.
- Default environment: no variables, no setup script, Limited network. Selection rules differ by surface (claude.ai/code selector; CLI uses /remote-env; Claude Tag uses org-level environments as above).
- Config files read in a cloud session (what carries over, cloud-environments.md table): repo CLAUDE.md (yes); repo .claude/settings.json hooks and permission rules (yes, in a single-repo session; with several repos only enabledPlugins and extraKnownMarketplaces are read in Anthropic-hosted environments); .mcp.json (yes, single-repo); .claude/rules, skills, agents, commands (yes). Marketplaces in repo settings are not added in Anthropic-hosted environments. Not read: ~/.claude/settings.json and .claude/settings.local.json (stay on the machine); a managed-settings.json or MDM profile on a device. Server-managed settings from claude.ai do reach cloud sessions (https://code.claude.com/docs/en/settings.md "Settings in cloud sessions").
- Permission-mode and /config in cloud: /config opens Settings > Code in the browser; to change a session setting, set an env var on the environment, or commit to the repo's .claude/settings.json in a single-repo session.
- Claude Tag uses the same sandbox engine but the repo's hooks do not run there (configure-github.md).

## 6. Unconfirmed / gaps (unverified)

- Exact Slack-to-claude.ai account linking UI and steps: unverified (not found in pages read).
- Exact Claude Tag per-channel "Channel instructions" storage path in the repo: none; instructions live in the admin/Configure page, not a repo file. Repo CLAUDE.md is read in the clone (verified).
- Whether a Claude Tag session reads the repo's .claude/settings.json permission rules: unverified; the configure-github.md page says hooks and .mcp.json do not run/load, and does not state permission rules explicitly.
- Claude Tag docs page workspaces.md and security-and-data.md were not fetched in full.

## 7. Design implications (for the decision that prompted this)

- Put shared, team-wide behavior in repo files (CLAUDE.md, .claude/skills, rules) so it works both in cloud sessions and in Claude Tag threads once the repo is cloned; put channel-specific rules in channel instructions; put credentials only in connectors, never env vars.
- Anything that must run before Claude starts (toolchains) goes in the environment setup script; project dependencies can live in CLAUDE.md as preconditions (configure-github.md).
- Repo hooks and .mcp.json will not apply in Claude Tag; wire MCP as Claude Tag connectors instead.
- Per-user behavior requires personal connectors and the member's approval; do not rely on it for team channels.


---

## 附录 C：上游仓库（SignalPilot、nao、dash）

# Upstream Slack deployment config (local clones, read 2026-11-27)
Root: /Users/shenghuikevin/dev/AI/data-agent-refs. ktx has no Slack code (only community links: ktx/README.md:14,23,218; docs-site building-context.mdx:113 mentions Slack exports as context source). signalpilot-plugin not read for Slack (no hit in listing beyond none).

## SignalPilot (SP = SignalPilot/signalpilot/gateway/gateway)
Q1 scopes: org -> installation -> (channel) ; no per-user layer on the Slack path.
- Slack install row is per (org_id, team_id, app_id): db/models/integrations.py:228-252. Config row per installation: enabled, default_project_id, default_branch, analysis_branch_mode, allowed_channel_ids (models/slack.py:19-25; store/slack.py:176-203).
- Model/tools/data: not Slack-specific. Model = org Anthropic key (worker.py:757-763 resolve_anthropic_key(db, org_id)); delivery model env SIGNALPILOT_DELIVERY_MODEL (docs/docs/self-host/configuration.md:143). Data/knowledge come from the bound dbt "project".
- Budgets/permissions are org-level RBAC: admin vs member, `integrations.write` admin-only (docs/docs/product/roles.mdx:65; org resources "admins write, members read" :14-17).
Q2 who edits: admin web UI "Integrations" page (OAuth connect, then Provision with default project + allowed channels): docs/docs/integrations.mdx:19-30; API api/slack.py:177-231 (OrgAdmin required). Secrets (client id/secret, signing secret, app token) are env only: .env.example:27-47, configuration.md:187-193. Env-only PoC mode also exists: SLACK_BOT_TOKEN, SLACK_POC_DEFAULT_PROJECT_ID, SLACK_ALLOWED_CHANNEL_IDS (worker.py:1815-1866). So UI exists even self-hosted (local mode, no Clerk; configuration.md:17).
Q3 identity: Slack user is NOT mapped to a product user. Every Slack request runs as the installer: `user_id=installation.user_id` (worker.py:1794) / `SLACK_POC_USER_ID` (1825); `_analysis_user_id` returns config.user_id (1259-1263). Requester slack id is only recorded as metadata (worker.py:1493). Shared service-account style. (Per-user mapping: none found.)
Q4 per-channel: yes, mild. `allowed_channel_ids` allow-list per installation (worker.py:520; models/slack.py:25) and per-channel project override via env JSON `SLACK_POC_CHANNEL_DEFAULTS` (worker.py:1920-1940, used 1265-1272) -- env-only, not exposed in self-serve UI/dispatch (1781-1800 passes no channel_defaults). Threads/DMs map to a "discussion id" -> analysis trail (worker.py:2035-2039); watched threads continue without re-mention (1092-1136).
Q5 state: Postgres (docker-compose.yml:61 DATABASE_URL); bot token encrypted (store/slack.py:131 _encrypt) with key rotation (:16-30). Config read per event from DB (`_dispatch_self_serve_payload`, worker.py:1765-1800) => live, no restart. Env seed config needs restart (read at boot, load_config_from_env).
Q6 minimal: env-only PoC path works with no UI/OAuth: SLACK_BOT_TOKEN + SLACK_SIGNING_SECRET (http) or SLACK_APP_TOKEN (socket, SLACK_DELIVERY_MODE) + default project + allowed channels (.env.example:27-47; main.py:359-366 mounts /slack/events). Full: OAuth install, multi-tenant per org, UI provision, audit/chats pages.

## nao (apps/backend/src)
Q1 scopes: project is THE scope. Slack settings are a JSON column on `project` (db/sqlite-schema.ts:279 slackSettings). Fields: bot token, signing secret, app token, transport mode, model provider/id, reply mode, autoCreateUsers(+domains) (queries/project-slack-config.queries.ts:33-47). One Slack bot per project (services/slack.ts _bots map keyed by projectId :1777). Model override per integration (trpc/project.routes.ts:408 updateSlackModelConfig). Knowledge/tools/data = the project dir (nao_config.yaml + RULES.md etc., example/nao_config.yaml:8-25; has `slack: null` key and cli SlackConfig bot_token/signing_secret cli/nao_core/config/slack/__init__.py:7-14, legacy). Slack excludes some tools (SLACK_EXCLUDED_TOOLS = str_replace, slack.ts:81,1233). Per-user budgets: projectMemberBudget, group budget (sqlite-schema.ts:639-690). Roles per project: admin / user / context_admin (slack.ts:928-930).
Q2 who edits: admin web UI Settings > Slack (adminProtectedProcedure: upsertSlackConfig, updateSlackModelConfig, updateSlackReplyMode, updateSlackAutoCreateUsers, deleteSlackConfig; trpc/project.routes.ts:355-446) OR declaratively via env SLACK_BOT_TOKEN/SIGNING_SECRET/APP_TOKEN/TRANSPORT_MODE seeded on boot (services/slack-env-seed.ts:1-60; env.ts:208-224; app.ts:491). Rule: UI-saved config is never overwritten by env (seed.ts:25-31). Project context itself comes from a local mount or a git repo cloned at container start (NAO_CONTEXT_SOURCE=git, NAO_CONTEXT_GIT_URL/BRANCH/SUBPATH, NAO_REFRESH_SCHEDULE: docker-compose.yml:55-73; docker/entrypoint.sh:84-124).
Q3 identity: Slack user -> email via Slack API (users.info profile.email) -> nao user by email (slack.ts:908-933 `_resolveAuthorizedUser`); must exist and have a project role, else "No user found / no permission" ephemeral reply (:880-905). Optional auto-create users for allowed email domains (`_canAutoProvision`, ensureMessagingProviderUser :917-924). Agent runs as that user (`agentService.create({... userId: ctx.user.id, projectId})`, slack.ts:1230-1232), so per-user usage, budgets, roles apply. Warehouse credentials: shared from project config (per-user DB creds not found; unverified).
Q4 per-channel: no channel->project/agent binding. Binding is bot(workspace) = project. Chats keyed by slack thread id `slack:<channel>:<ts>` (slack.ts:1789-1791); `/new` slash command resets the channel's main thread (:845); replyMode thread vs channel (:1808).
Q5 state: DB (sqlite default, Postgres via DB_URI: docker-compose.yml:50; schemas db/sqlite-schema.ts, pg-schema.ts). Config change live: `syncProjectSocketMode` recreates the bot on config diff (slack.ts:1731-1760, 1777-1818); webhook route reads config per request (routes/slack.ts:15-20, path /api/webhooks/slack/:projectId). Context files: restart or scheduled git refresh.
Q6 minimal: set 3-4 env vars; Slack works without touching UI (seed). Web login and project-roles still required for identity (users need accounts or domain auto-create); full product adds Settings UI, budgets, automations, usage, backoffice.

## dash (dash/)
Q1: single deployment-wide config, no scopes. Slack = agno `Slack(team=dash, streaming=True, token, signing_secret, resolve_user_identity=True)` (app/main.py:33-44). Model hard-coded in code dash/settings.py:20 (MODEL=...); knowledge = files in knowledge/{business,queries,tables} loaded into pgvector (settings.py:26-35; main.py:52-63 daily reload schedule) plus agentic "learnings" (settings.py:30-36). Quick prompts in app/config.yaml.
Q2: code + env only. Env: SLACK_TOKEN, SLACK_SIGNING_SECRET, OPENAI_API_KEY (example.env:9-16, compose.yaml:43-45). Slack app created from manifest in docs/SLACK_CONNECT.md (README.md:213-223). No admin UI for config (AgentOS web UI is a chat/observability client, not config; unverified beyond main.py).
Q3: `resolve_user_identity=True` (main.py:42) resolves the Slack user id (agno feature; internals unverified, not in repo); README.md:207: Slack user id maps to sessions via thread ts; API uses JWT (`authorization=runtime_env=="prd"` main.py:80). Data access is one shared read-only DB engine (db/session.py:94-103), no per-user credentials.
Q4: none; each Slack thread = one session id (docs/SLACK_CONNECT.md:7; README.md:223).
Q5: Postgres for sessions/knowledge/learnings/schedules (compose.yaml, main.py:75-87); knowledge files re-indexed on cron or POST /knowledge/reload (main.py:96-102); code/env changes need restart.
Q6: this IS the minimal: 2 env vars + manifest + public URL.

## Recurring patterns
1. Slack credentials are env/deployment secrets; non-secret binding lives elsewhere. SignalPilot (.env.example:27-47), nao (slack-env-seed.ts), dash (example.env:9-16).
2. Env can declaratively seed the product so the admin UI is optional; UI-saved wins over env. nao (seed.ts:25-31), SignalPilot PoC env path (worker.py:1815+).
3. Workspace-level binding to ONE project/knowledge set; per-channel is only an allow-list (SignalPilot allowed_channel_ids) or thread-as-session (all three). No product binds a channel to a different agent in the shipped UI.
4. Context/knowledge is files (dbt project, nao project dir, dash knowledge/), optionally git-cloned at start (nao entrypoint.sh), not edited in Slack.
5. State in a database (all three use Postgres/sqlite for sessions, threads, config rows); config rows are read per event/request so changes are live; env/context files need restart or scheduled refresh.
6. Admin config is behind a role gate (SignalPilot OrgAdmin, nao adminProtectedProcedure); chat members cannot change it.
7. Identity is the weak/variable part: SignalPilot and dash = shared service identity, requester id only logged; nao = Slack email -> product user (with optional domain auto-create) so roles/budgets apply.
8. Thread id = conversation id (`slack:<channel>:<ts>` nao; discussion id SignalPilot; session id dash); follow-ups without re-mention via watched threads (SignalPilot).
9. Socket Mode vs HTTP webhook both supported (SignalPilot SLACK_DELIVERY_MODE, nao transportMode) -- socket avoids public URL on self-host.

## Minimal version for a solo self-hosted project
- Secrets in env only: bot token + signing secret (HTTP) or app token (Socket Mode).
- One workspace = one deployment = one config.toml; no org/user/project tables, no admin UI, no OAuth install (single-workspace bot token like dash/nao seed).
- Optional small allow-list: allowed channel ids (+ maybe allowed Slack user ids); thread = session; `/new` to reset (nao).
- Shared service identity for data access (dash, SignalPilot); log Slack user id on each run for audit. Defer Slack-email -> user mapping and budgets (nao) until multi-user need.
- Context from files/git, refreshed on restart or schedule; sessions/threads state in sqlite file.
- Keep config file as source of truth; add an admin UI only if non-technical admins must edit it.
