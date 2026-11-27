# 调研：配置怎么分层（本地与云端）、怎么跑榜

日期：2026-11-27。只取证，不做设计决定。三路只读子 agent：本地上游仓库（`~/dev/AI/data-agent-refs/`：SignalPilot、signalpilot-plugin、nao、dash、ktx、harbor）、同类产品官方文档（Claude Code / Agent SDK、LibreChat、Open WebUI、Onyx、Dify）、跑榜做法（SWE-agent、Aider、Harbor + Claude Code、SignalPilot benchmark）。主会话抽查：SignalPilot `docker-compose.yml:63` `SP_DEPLOYMENT_MODE: local`；nao `apps/backend/src/env.ts:388` `DISABLED_PROVIDERS`；nao 测试里 `NAO_MODE`、`NAO_CONTEXT_SOURCE`。未核实处在附录里标 (unverified)；Wren AI、Vanna 没查；OpenHands、Terminal-bench 文档页 404。

起因：审计（`docs/research/2026-11-27-product-config-audit.md`）发现产品路径上大量写死；人要求配置也能用于云端部署（IM bot 一般跑在服务器上），产品默认全开，跑榜像使用者那样配置。

## 反复出现的做法

1. **分层、固定优先级，越具体的层越优先。** 常见顺序：命令行参数 > 数据库/管理界面 > 配置文件 > 环境变量 > 代码默认值。nao（模型提供方：数据库 → `nao_config.yaml` → 环境变量，`apps/backend/src/utils/llm.ts:374`）、harbor（命令行 > 后面的 `--config` > 前面的）、Claude Code（managed > `--settings` > local > project > user；MCP：local > project > user > plugin，整条替换不合并字段）、Dify（`docker/.env` > `.env` > `.defaults` > 应用默认）。
2. **环境变量只放密钥、基础设施（库地址、端口、模式）和启动参数；行为放进有类型的配置文件或数据库。** nao、SignalPilot（pydantic-settings）、ktx、dash、Onyx。
3. **配置文件里写密钥引用，不写明文。** nao `{{ env() / aws() / k8s() }}`、ktx `env:`/`file:`、harbor `${KEY}`（保存时脱敏）、Claude Code `.mcp.json` `${VAR}`、LibreChat `${VAR}`；SignalPilot、Dify 在库里加密存，密钥来自环境变量或 KMS。
4. **云端分两层：部署层（环境变量、挂载文件）和应用层（管理界面 + 数据库）。** 环境变量在首次启动时填入，之后以界面/数据库为准、不覆盖（nao `slack-env-seed.ts`、SignalPilot `load_settings`、Open WebUI PersistentConfig——它文档里写了"库里的旧值不再跟着环境变量变"这个坑和 `ENABLE_PERSISTENT_CONFIG=False`、Onyx EE `ENV_SEED_CONFIGURATION`）。
5. **一个部署模式开关改默认值，并对危险项默认关闭。** SignalPilot `SP_DEPLOYMENT_MODE=local|cloud`（cloud 要求鉴权和工作区根目录、拒绝自定义端点）、nao `NAO_MODE=self-hosted|cloud`、dash `RUNTIME_ENV=prd` 开 RBAC。自部署的默认几乎不用配。
6. **运维级锁定，使用者覆盖不了。** Claude Code managed settings（`availableModels`、`managedMcpServers`、`strictPluginOnlyCustomization`）、nao `DISABLED_PROVIDERS`（界面和 yaml 里配的也被禁）、LibreChat `modelSpecs.enforce`、Dify `APP_MAX_ACTIVE_REQUESTS`。
7. **扩展是约定目录里的普通文件，加一个打包的插件。** 规则文件（`RULES.md`）、`skills/*/SKILL.md`、`mcp.json`、知识目录；作用域有全局、项目、连接、用户；插件把多种组件包在一个开关后面、按名字空间防冲突（Claude Code `plugin:skill`、signalpilot-plugin、ktx `setup --agents --global`、harbor `skills:` 列表、Onyx 按频道配 Agent 和文档集）。
8. **云端的上下文来自 git 仓库或 API，定时刷新。** nao `NAO_CONTEXT_SOURCE=local|git|api` + `NAO_REFRESH_SCHEDULE`、dash 每天重建知识索引、ktx 项目目录 + git。
9. **模型配置是一张有名字的列表。** 每个提供方：端点、密钥引用；每个模型：显示名、单价、effort/temperature、是否默认；未知模型给安全默认值；按角色（主模型、小模型）指定。nao `ProviderConfig`/`ModelConfig`（库表 `project_llm_config`）、ktx `llm.models`、LibreChat `endpoints.custom`/`modelSpecs`、Claude Code `modelSettings` + `effortLevel`。
10. **默认：核心功能开、能关；新的、有风险的、管理类功能默认关。** LibreChat `interface`、Open WebUI、Claude Code SDK（默认加载全部设置来源和全部 Skill，插件 `defaultEnabled: true`）、nao `BETA_*`、SignalPilot `SP_FEATURE_*`。反例：ktx `AGENTS.md` 不许无具体需要加开关。
11. **能看到生效的配置。** Claude Code `/status`、`claude doctor`、SDK `resolveSettings()`（每个键标来源）；harbor `--print-config`；ktx `status`；Dify `docker compose config`。有类型的 schema 拒绝未知键（harbor `extra="forbid"`、pydantic/zod）。
12. **跑榜走产品自己的入口和配置机制。** SWE-agent `--config` YAML + 覆盖参数；Aider 参数"和平时用 aider 一样"、`--read-model-settings`；Harbor 跑真的 `claude`，用 `--settings`、`--allowedTools`/`--disallowedTools`、`CLAUDE_CODE_EFFORT_LEVEL`，MCP 和 Skill 也走 Claude Code 自己的机制（`harbor/src/harbor/agents/installed/claude_code.py` 约 55-105、377-408、1692-1743、1920-1956 行）；nao `nao test` 走真的后端和项目配置；dash 评测直接导入生产的 team 对象。跑榜关功能也用产品自己的开关（Harbor 关 WebSearch/WebFetch）。榜单专属代码只在外围：选题、沙箱、送题、收结果。反例：SignalPilot `benchmark/` 有自己的 runner 和提示词，但 Skill 从产品插件加载。
13. **跑榜固定的东西：** 模型名（有时带日期）、数据集版本、agent/CLI 版本、容器镜像、题目顺序、轮数和花费上限；harbor 每次运行写 `lock.json`（任务、Skill、指令、版本的 sha256）；Aider 记 git 哈希和是否有未提交改动；每题新容器新卷、固定时钟、重复次数写在配置里。
14. **嵌入式/库模式默认加载全部本地配置，隔离要显式打开。** Agent SDK 默认读全部设置来源；文档写明隔离或多租户运行要传 `settingSources: []`、`strictMcpConfig`、每个租户单独的文件系统、`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`。这最接近"跑榜就是另一份配置"。

没找到证据的：产品默认"全部功能开"在哪个产品上都没被明确写成原则（只有第 10 条的"核心开、风险关"）；SWE-agent、Aider 是否把完整配置快照进运行结果。

---

## 附录 A：本地上游仓库（逐产品）

# Upstream configuration research: SignalPilot, signalpilot-plugin, nao, dash, ktx, harbor

Sources: local clones under /Users/shenghuikevin/dev/AI/data-agent-refs/ (paths below relative to it). Read 2026-11-27. No web pages used. "(unverified)" = not confirmed in code.

## 1. SignalPilot (SignalPilot/, gateway = signalpilot/gateway/gateway)

Q1 layers/precedence
- Env typed via pydantic-settings: `config/_base.py` (no env_file, process env only, case-sensitive); per-domain classes `config/{auth,governance,storage,network,mcp,evals,...}.py` with `lru_cache` accessors. `config/__init__.py` comment: some vars intentionally stay raw `os.getenv` because tests monkeypatch them.
- Org-scoped settings in DB: `store/settings.py:11-20` `load_settings` reads `GatewaySetting.settings_json`, then env fills only keys absent ("Environment variables provide defaults — user-saved settings take priority"). Model `models/settings.py` (`GatewaySettings`: default_row_limit, default_budget_usd, default_timeout_seconds, blocked_tables, improvement_runs_enabled=True ...).
- So precedence: DB/admin-UI value > env > code default. Per-org secrets in DB encrypted (`store/org_secrets.py`, `store/crypto.py`; `SP_ENCRYPTION_KEY` env). BYOK key providers local/aws_kms/gcp_kms/azure_kv (`models/byok.py:27`).
- Per-connection settings (store_connections.py), per-project knowledge (store_knowledge.py), per-user secrets (user_secrets.py).

Q2 local vs cloud
- One switch `SP_DEPLOYMENT_MODE=local|cloud` (`runtime/mode.py:11`), default local. Cloud: JWT secret from env (`auth/jwt_secret.py`), Clerk auth required (`auth/user.py:31`), fail-closed `SP_WORKSPACE_ROOT` (`mcp/tools/model_map.py:43-47`), `SP_ALLOWED_ORIGINS` validated (`runtime/mode.py` `_validate_cloud_allowed_origins`), BYOK custom endpoint denied unless opt-in (`runtime/mode.py:27-40`), eval backend Vercel sandboxes not Docker socket (`config/evals.py` docstring). Local mode: requests with no API key get all scopes (`docker-compose.yml` comment, "Do not expose local mode to a network").
- Secrets: `.env` (gitignored; `.env.example` all commented out, core stack starts with none), compose env_file entries `required: false`; secrets in DB encrypted. Compose ships a dev-only encryption key literal (docker-compose.yml, SP_ENCRYPTION_KEY) - a counter-example.

Q3 extension points
- Skills/agents: separate repo signalpilot-plugin (Claude Code plugin, `.claude-plugin/plugin.json`, marketplace.json), installed via `claude plugin install signalpilot-dbt@signalpilot`; MCP server added separately via `claude mcp add --transport http signalpilot <gateway>/mcp` (signalpilot-plugin/README.md "Install"). Skills are SKILL.md dirs (`skills/<name>/SKILL.md`), agents are `agents/*.md` (verifier, value-verifier, notion-verify). Domain skills (ecommerce, financial...) are plain skill dirs.
- Business context: Notion integration set in web UI (/integrations), token stored in gateway; knowledge base entries per project (`store/knowledge.py`, "Evaluate Change" on entries).
- Integrations (Slack, Notion, GitHub, Tableau) configured through env (`SLACK_*` in `.env.example`) and OAuth flows stored in DB.

Q4 model settings
- Mostly env: `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`, `SIGNALPILOT_DELIVERY_MODEL`, `SLACK_PROGRESS_MODEL(_PROVIDER)`, `SP_EVAL_CLAUDE_TOKEN` (`.env.example`, docker-compose.yml). Org Anthropic key in DB (`store/org_secrets.py:get_org_anthropic_key`). Model ids not catalog-driven in what I read (unverified for web chat model picker).

Q5 benchmarks
- Two paths. (a) `benchmark/` (Spider2-DBT): own runner `benchmark/runners/direct.py` + `agent/sdk_runner.py` using Claude Agent SDK, `bypassPermissions`, fixed `thinking budget 20000` (sdk_runner.py:52-58), `--model` flag, own system prompts in `benchmark/prompts/*.md`, per-suite skill list hardcoded in `core/suite.py:48-110`, skills copied into `<workdir>/.claude/skills`, task CLAUDE.md written by `core/workdir.py`. It uses the same MCP server and the product plugin ("Skills are provided by the signalpilot-plugin (installed at user scope)", direct.py:56-57), but with benchmark-specific prompts and quick-fix/verifier agent loops - i.e. a special code path around the shared MCP/plugin. (b) Product "Evaluate Change" (`gateway/evals/`, `config/evals.py`) runs the `claude` CLI in a container against the gateway MCP URL (`SP_EVAL_MCP_URL`) - same product path.
- Pinned for reproducibility: Docker image + fresh volume per task, libfaketime date per task derived from gold DB (README "Determinism"), DO_NOT_TRACK/DBT_NO_VERSION_CHECK, no eval config exposed to agent.

Q6 defaults
- Feature flags `SP_FEATURE_*` default via compose: mixed (CHAT_STRUCTURED_RESULTS=true, SIZE_ROUTER=true, RUNTIME_*=true; QUERY_APPROVAL, DATASET_REFS, ORG_SHARING, FORKING=false) (docker-compose.yml:61-72+). DB setting `improvement_runs_enabled=True` default "org admins turn it off". Core stack starts with zero config.

## 2. signalpilot-plugin (signalpilot-plugin/)
Distribution unit only: `plugin.json` + `skills/` + `agents/`; no config schema of its own. Config is the MCP URL + `Authorization: Bearer sp_...` header (README). Same plugin serves cloud (`https://gateway.signalpilot.ai/mcp` + API key) and self-hosted (`http://localhost:3300/mcp`, no key) - only the endpoint/header differs. Separate marketplace install, version in plugin.json (1.2.0). Business context optional via Notion skill that is "load on request".

## 3. nao (nao/)

Q1 layers/precedence
- Project folder `nao_config.yaml` (pydantic `cli/nao_core/config/base.py:73-95`, all integration blocks optional), plus context files in the project folder: `RULES.md` (agent rules, read in `apps/backend/src/agents/user-rules.ts`), `agent/skills/*.md`, `agent/mcps/mcp.json`, `semantics/`, `docs/`, `databases/` (example/ tree).
- Backend env schema zod `apps/backend/src/env.ts`; DB (sqlite/postgres) holds project settings edited in UI.
- Model providers: explicit order in `apps/backend/src/utils/llm.ts:374-413` "each provider from the first source that declares it: the database, then nao_config.yaml, then the environment". `DISABLED_PROVIDERS` env filters all sources (utils/llm.ts:412; env.ts:388).
- Slack: env seeds DB only when absent or previously env-seeded; UI-saved config never overwritten (`services/slack-env-seed.ts:7-13,28-36`).
- Per-user: user groups, per-user/ group model access, budgets (sqlite-schema.ts `userGroupMember`, `projectProviderBudget`, `member-budget.service.ts`); API keys, MCP endpoints per project.

Q2 local vs cloud
- `NAO_MODE=self-hosted|cloud` (env.ts:234); cloud forbids `NAO_DEFAULT_PROJECT_PATH` and `NAO_CONTEXT_SOURCE=git` (env.ts:510-518), entrypoint sets context source `api` (docker/entrypoint.sh:7-9). Self-hosted container: `NAO_CONTEXT_SOURCE=local|git`; git source clones context repo with token or SSH deploy key from env (docker-compose.yml, entrypoint.sh) and optional `NAO_REFRESH_SCHEDULE` cron pull. Helm chart `helm/values.yaml` (config.* values -> env). Postgres via `DB_URI` (sqlite default).
- Secrets out of yaml: `{{ env('X') }}`, `{{ aws('secret/path') }}`, `{{ k8s('ns/secret/field') }}` templates substituted before YAML parse (`cli/nao_core/config/secrets.py:1-25`). `.env.example` for keys; ambient cloud credentials (IRSA, ECS role) auto-register Bedrock unless in `DISABLED_PROVIDERS`.
- Managed policy: `DISABLED_PROVIDERS` env (cannot be overridden from UI/yaml, env.ts:388, .env.example comment); license/billing flags only in cloud.

Q3 extension points
- Project-level files: RULES.md, skills dir (`skills.folder_path`), MCP json (`example/agent/mcps/mcp.json`, streamable-http servers), custom charts, semantic layer, notion/confluence/obsidian/repos/metabase blocks syncing docs. UI-level: Settings > Project > Models for custom models, per-project. Context can be edited via PR flow (`context-pr.service.ts`) (unverified details).
- Coding-agent skills for working on the product live in `skills/` (add-semantic-layer, audit-context...).

Q4 model settings
- `LLMConfig.providers[]` -> `ProviderConfig{provider,api_key,base_url,aws_*,gcp_*,models[]}`; `ModelConfig{id,name,default,costs{input,cacheRead,cacheWrite,output per M tokens},settings{temperature,reasoning_effort}}` (`cli/nao_core/config/llm/__init__.py:226-266,441-451`). DB mirror `project_llm_config` (api_key, enabledModels, customModels with costPerM, modelSettings, baseUrl; sqlite-schema.ts:563-602). Built-in catalogue + capabilities per model (`agents/provider-meta.ts`: reasoning effort surfaces per model; unknown models assume 200k context, providers.ts `DEFAULT_CONTEXT_WINDOW`). Default chat model pinned per project; per-integration default (slack/teams/telegram...) in `default-models.service.ts`. OpenAI-compatible endpoints named `openaiCompatible/<name>`.

Q5 benchmarks
- `nao test`: yaml test cases in project `tests/*.yml` (name, prompt, sql) run through the real backend HTTP API (`cli/nao_core/commands/test/client.py`, `BACKEND_URL`), comparing result to expected SQL result with rtol/atol/decimals; defaults in `nao_config.yaml` `test:` block (models "provider:model_id", threads, k for pass@k/pass^k) overridable by CLI flags (config/test/__init__.py:12-33). So evals use the same project config and server as users; no special agent path.

Q6 defaults
- Core features on; newer ones behind env flags: BETA_AUTOMATIONS / CONTEXT_RECOMMENDATIONS / STORY_FILTERS default true, BETA_CUSTOM_STORIES / BETA_SUBAGENTS default false (env.ts:415-443). Provider auto-enable from env keys present.

## 4. dash (dash/)
- Q1/Q2: almost no config layer. `dash/settings.py` hardcodes model (`OpenAIResponses(id="gpt-5.6-sol")`, comment "Change class + ID together when switching providers"); env only for secrets/infra (`example.env`: OPENAI_API_KEY, SLACK_TOKEN, SLACK_SIGNING_SECRET, DB_*, RUNTIME_ENV, AGENTOS_URL, JWT_VERIFICATION_KEY). `RUNTIME_ENV=prd` turns on RBAC (`app/main.py:26,80` `authorization=runtime_env == "prd"`; default "prd"; compose sets dev). Docker compose for local, Railway scripts for cloud (`scripts/railway_*.sh`, `.env.production` synced). Slack interface only mounted if both token and secret set (app/main.py:31-42).
- Q3: business context = files in `knowledge/{tables,queries,business}` JSON/SQL loaded into pgvector (`scripts/load_knowledge.py`), daily re-index schedule (app/main.py:_register_schedules); learned knowledge written at runtime (LearningMachine). UI quick prompts in `app/config.yaml`. No skills/MCP user extension.
- Q4: hardcoded in two places (settings.py, evals/__init__.py JUDGE_MODEL). No prices/context settings.
- Q5: evals (`evals/run.py`) call the same `dash` team object the app uses (`from dash.team import dash`), categories accuracy/routing/security/governance/boundaries; judge model hardcoded.
- Q6: everything on (team of analyst+engineer, learning, scheduler, tracing=True hardcoded in app/main.py).

## 5. ktx (ktx/)
- Q1: single `ktx.yaml` at project root ("single source of truth"; every block optional with documented defaults; docs-site/content/docs/configuration/ktx-yaml.mdx). Top-level blocks: connections, setup, storage, llm, ingest, scan, memory etc. Project dir found from cwd, `--project-dir`, or `KTX_PROJECT_DIR`. State in `.ktx/` (gitignored parts), context as plain files in git (git author configured in `storage.git.author`). CLI flags override per command (e.g. `ktx mcp --host --port --token`). Env vars are few: KTX_PROJECT_DIR, KTX_MCP_TOKEN, KTX_PROFILE_INGEST.
- Q2: no cloud mode; local-first. For serving: `ktx mcp` binds 127.0.0.1:7878; non-loopback requires bearer token (`--token`/`KTX_MCP_TOKEN`) plus allowed host/origin lists (cli-reference/ktx-mcp.mdx:29-33,60-62). Storage backend switchable `sqlite` -> `postgres` (`storage.state`, `storage.search`). Secrets: `env:VAR` and `file:/path` references in yaml so the file can stay in git; literal accepted for non-secrets (ktx-yaml.mdx "Secret references"). Unknown whether anything stops literal secrets (unverified).
- Q3: agent integration = `ktx setup --agents --target claude-code|codex|cursor|opencode [--global]` writes a SKILL/rule file into project (`.claude/skills/ktx/SKILL.md`) or user-global scope; recorded in `.ktx/agents/install-manifest.json` (guides/serving-agents.mdx:20-41). Business context = wiki markdown + semantic-layer YAML in repo, connectors list `ingest.adapters`.
- Q4: `llm.provider.backend` none|anthropic|vertex|gateway|claude-code|codex; `llm.models` fixed role keys (default, triage, candidateExtraction, curator, reconcile, repair) -> model id; `promptCaching` TTLs; embeddings block separate with explicit dimensions (guides/llm-configuration.mdx; ktx-yaml.mdx `llm`). No price/context-window fields (not seen). Default backend `none` (LLM features off until set up).
- Q5: no benchmark found (unverified). Of note: `AGENTS.md` "Opinionated Product Defaults": prefer one canonical behavior; new flag/env/config needs explicit justification; no boolean switches creating two runtime paths. Counterweight to "configure everything".
- Q6: configuration blocks optional and defaulted; features generally on; LLM/embeddings off (`none`) until configured.

## 6. harbor (harbor/)
Not a product agent; shows how a run-harness configures an agent.
- Q1: JobConfig (`src/harbor/models/job/config.py:399-454`) YAML/JSON; `--config` repeatable, layers merged in order with list-append paths (`cli/jobs.py:378-390,1466-1487` `merge_config`), then individual CLI flags override (`jobs.py:1490+`: job_name, n_attempts, timeouts, `--agent`, `--model`, `--ak key=value` agent kwargs, `--ae` agent env, `--mcp-config`). Env: shell, then `.env.local` (override=False), then `--env-file` (override=True) (jobs.py:1441-1458). Precedence: CLI flag > later config > earlier config > code default.
- Q2/secrets: agent env values persisted into job config go through `templatize_sensitive_env` -> `${KEY}` templates or redacted, so configs/results can be shared (`trial/config.py:~140 _serialize_env`, `utils/env.py:58-75`).
- Q3: `AgentConfig` (trial/config.py:63-160): `name`/`import_path` (custom agents by Python import path), `model_name`, `skills` (dirs, git URLs, org/name@ref), `kwargs`, `env`, `mcp_servers`, `extra_allowed_hosts`, timeouts overrides, `extra_instructions` at job level. Per-agent options typed with `extra="forbid"` pydantic models and `Cli(flag, fallback=ENV)` / `Env(name)` annotations compile an option to a CLI flag or env var (`agents/options.py:15-80`; ClaudeCode options in `agents/installed/claude_code.py:53-105`: max_turns, reasoning_effort, max_budget_usd, append_system_prompt, allowed/disallowed tools, permission mode).
- Q4: `model_name` string plus agent kwargs (reasoning_effort etc.); no price table in config seen (unverified).
- Q5: the whole harness IS a config-driven profile: agent+model+kwargs+skills+MCP in YAML, task via dataset/registry. Pins: `lock.json` per job/trial (`models/job/lock.py`) with sha256 digests of tasks, skills (`AgentSkillLock` name+digest+git commit), extra instructions, verifier, harbor version/git hash; resume validates equality.
- Q6: agents/features opt in through config; defaults minimal (`AgentConfig` default name oracle, kwargs empty).

## Patterns that recur across products

1. Layered precedence with the most specific/runtime layer winning: CLI flag > (DB/admin UI) > config file > env > code default, with the order stated in code or docs. Shown by: nao (DB > yaml > env for model providers, `utils/llm.ts:374`), SignalPilot (DB setting > env default, `store/settings.py`), harbor (flags > later config > earlier config, shell env < `--env-file`), ktx (yaml with documented defaults, flags per command).
2. Env vars are for secrets, infrastructure (DB URL, ports, mode) and bootstrap, not for the full feature/behavior surface; behavior lives in a typed config file or DB. Shown by: nao, SignalPilot (typed pydantic-settings), ktx, dash (only secrets/infra via env).
3. Env seeds, UI/DB owns afterwards: env values fill an absent setting once and a UI-saved value is never overwritten, with a source marker. Shown by: nao Slack seed (`slack-env-seed.ts`), SignalPilot `load_settings`.
4. One deployment-mode switch (`local`/`self-hosted` vs `cloud`) that flips defaults and fail-closes risky options (auth required, workspace root required, custom endpoints denied, git context forbidden); self-hosted default needs near-zero config. Shown by: SignalPilot `SP_DEPLOYMENT_MODE`, nao `NAO_MODE`, dash `RUNTIME_ENV=prd` enabling RBAC.
5. Secret references in config files instead of literals: `{{ env() / aws() / k8s() }}`, `env:` / `file:` prefixes, `${KEY}` templates, encrypted DB columns with a key from env or KMS. Shown by: nao, ktx, harbor, SignalPilot.
6. Cloud context delivery = git repo or API as source of the context folder, with periodic pull; containers take env and a mounted project folder. Shown by: nao (`NAO_CONTEXT_SOURCE=local|git|api`, `NAO_REFRESH_SCHEDULE`), dash (knowledge files re-indexed on schedule), ktx (project dir + git).
7. Managed/admin lockdown via env that overrides every other layer: `DISABLED_PROVIDERS`, mode-gated features, org plan gating. Shown by: nao, SignalPilot.
8. Extension via plain files in conventional folders at project scope (RULES.md, skills/*.md or SKILL.md dirs, mcp.json, semantic/wiki files) plus a packaged plugin/marketplace for shared skills; a client-install command writes the skill file into project or global scope. Shown by: nao, signalpilot-plugin, ktx (`setup --agents`), dash (knowledge/ dirs), harbor (`skills:` list of dirs/git refs).
9. Model config is a structured list: provider (+endpoint, key ref) with model entries carrying display name, per-token prices, per-model inference settings (effort/temperature), a default flag; unknown models get safe defaults; roles map to models (ktx role keys, nao per-integration defaults). Shown by: nao (ModelConfig/ProviderConfig, DB custom models), ktx (`llm.models` roles), harbor (model_name + typed kwargs). dash/SignalPilot hardcode or env-only model ids.
10. Benchmarks reuse the product's own interface when the product is a server/CLI: nao `nao test` goes through the same backend and config, dash evals import the production team object, SignalPilot "Evaluate Change" runs the CLI against the product MCP; harbor expresses a run as a config file with a lock file of digests. The counter-example is SignalPilot `benchmark/` which wraps the product MCP/plugin in its own runner, prompts and per-suite skill lists. Reproducibility pins seen: container image/fresh volume per task, lock.json digests (tasks, skills, instructions, versions), fixed clock, k-repeat pass@k settings in config.
11. Defaults: feature on by default with explicit off-switch for stable features, and experimental ones off behind `BETA_*`/`SP_FEATURE_*` flags; first-run needs no config (compose without .env, optional blocks everywhere). LLM-dependent parts are off until a provider is configured (ktx `backend: none`, nao no models until key). Shown by: nao, SignalPilot, ktx, dash (all on). Opposing stance: ktx AGENTS.md discourages adding flags/booleans without a concrete need.
12. Options validated by typed schemas that reject unknown keys and print the effective merged config (harbor `extra="forbid"`, `--print-config`; nao/ktx pydantic and zod with explicit error messages and `ktx status`/doctor readiness checks).


---

## 附录 B：同类产品官方文档（逐产品）

# Product config research (web docs, accessed 2026-11-27)

Pages were read through a summarizing fetch tool, so quotes are paraphrases of the page text. Anything not shown on a page is marked (unverified).

## 1. Claude Code (CLI) and Claude Agent SDK

Sources:
- https://code.claude.com/docs/en/settings
- https://code.claude.com/docs/en/mcp
- https://code.claude.com/docs/en/skills
- https://code.claude.com/docs/en/plugins-reference
- https://code.claude.com/docs/en/agent-sdk/claude-code-features
- https://code.claude.com/docs/en/agent-sdk/typescript

**Q1 layers and precedence.** Settings scopes are User (`~/.claude/settings.json`), shared Project (`.claude/settings.json`), Project local (`.claude/settings.local.json`) and Managed (`managed-settings.json`, MDM, or server-managed from the admin console). Precedence, highest first: managed, command line (`--settings`), project local, shared project, user. Environment variables are not a level. Each variable/key pair decides for itself; for example `ANTHROPIC_MODEL` beats the `model` key from any file. List keys such as `permissions.allow` merge across files. A few keys are exceptions: `fallbackModel` and `modelPicker` take the whole value from the highest source, and `availableModels` from managed settings is applied as-is. A short list of security keys honor the stricter value from any scope, even over managed.
- Skills: enterprise (managed dir) > personal (`~/.claude/skills`) > project (`.claude/skills`). Plugin skills are namespaced, so they never collide. Nested `.claude/skills` in subdirectories load on demand.
- MCP: local > project (`.mcp.json`) > user > plugin > claude.ai connectors. `managedMcpServers` (managed setting) beats all of them. Fields are not merged: the whole entry from the winning scope is used.
- CLAUDE.md: levels are additive with no hard precedence.

**Q2 local vs cloud.** A cloud session reads only part of the config:
- It reads the project `.claude/settings.json` from the clone.
- It does not read user or local settings.
- It does not read a device `managed-settings.json`; only server-managed settings reach it.
- Cloud env vars are set on the environment instead.

Managed settings are the policy tier: nothing in user, project or `--settings` overrides them. They can be delivered as a file, an MDM policy, or fetched from the admin console or a gateway. An embedding host can pass `managedSettings`, but admin-deployed managed settings ignore it unless `parentSettingsBehavior: 'merge'`.

Secrets:
- `.mcp.json` takes `${VAR}` and `${VAR:-default}` in `command`, `args`, `env`, `url` and `headers`, so the committed file stays secret-free.
- Credential variables read as empty in remote `url`/`headers`.
- Plugin `userConfig` fields marked `sensitive: true` go to the OS credential store, not settings.json.
- Settings files are strict JSON with `$schema`. They hot-reload. `/status` and `claude doctor` show what loaded.

**Q3 extension points.**
- Skills, CLAUDE.md and rules (`.claude/rules/*.md`), subagents, hooks and commands are files under `~/.claude/` (user) or `.claude/` (project).
- MCP servers have three scopes (local, project, user).
- Plugins bundle skills, agents, hooks, MCP, LSP and output styles through `.claude-plugin/plugin.json`, with `enabledPlugins` in settings. Their `userConfig` is prompted once at enable time.
- `skillOverrides` sets per-skill state (`on`, `name-only`, `user-invocable-only`, `off`).
- Admins can restrict customization with `strictPluginOnlyCustomization`.

**Q4 model settings.** The `model` key, `--model` and `ANTHROPIC_MODEL` are layered as described above. `effortLevel` and `modelSettings` (per model, resolved together with effort) are keys with their own flag (`--effort`). Admins use `availableModels` and `deniedModels` as locks. `modelPicker` and `modelOverrides` also exist (the docs name them but this research did not read their schemas). Per-model price and context window: (unverified).

**Q5 benchmarks.** Not documented in the pages read (unverified).

**Q6 defaults.** Features are on by default; configuration restricts them.
- The SDK with `settingSources` omitted equals `["user","project","local"]`.
- `skills` omitted means all discovered skills are on.
- A plugin's `defaultEnabled` defaults to `true`.
- Turn-off controls exist: `skillOverrides: off`, `disableBundledSkills`, `settingSources: []`, `strictMcpConfig`.

**Agent SDK specifics.**
- `settingSources`: omitted loads all of user, project and local. `[]` turns off filesystem settings, but managed policy, `~/.claude.json` and auto memory are still read.
- `settings` takes an inline object, JSON or a path, and sits in the flag-settings layer.
- `managedSettings` is host-supplied policy-tier settings.
- `skills` is `'all'` or a list of names. There is no API to register a skill programmatically; skills must be files.
- `plugins` takes `[{type:'local', path}]`.
- `mcpServers` takes a map. `strictMcpConfig` uses only the servers passed in `mcpServers` and ignores `.mcp.json`, user settings, plugin servers and claude.ai connectors.
- `systemPrompt`, `model`, `env` (replaces the subprocess environment) and `extraArgs` are also options.
- `resolveSettings()` is an alpha function that returns effective settings plus per-key provenance.
- The docs warn that the default options are not multi-tenant isolation. For multi-tenant use: a separate filesystem per tenant, `settingSources: []`, and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.

## 2. LibreChat (librechat.yaml)

Sources:
- https://www.librechat.ai/docs/configuration/librechat_yaml
- https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/config
- https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/model_specs
- https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/interface

**Q1.** There are two files. `.env` holds secrets and infrastructure. `librechat.yaml` sits in the project root, and `CONFIG_PATH` can point elsewhere. The YAML is optional and defaults apply when it is absent. A unique case: many YAML `interface`/permission fields only seed permissions at first startup, and the page recommends the Admin Panel for ongoing management. The `version` key is required. Validation errors exit with code 1, and `CONFIG_BYPASS_VALIDATION=true` skips validation (documented as temporary use only). Precedence between `.env` and YAML is (unverified).

**Q2.**
- Docker needs a bind mount of `./librechat.yaml` to `/app/librechat.yaml`, and a restart after changes.
- Secrets stay in `.env`. The YAML references them as `${VAR}`.
- Endpoints can be marked `user_provided`, so each user supplies their own key. The page names this but does not document it (unverified detail).
- MCP `customUserVars` let users set per-user variables, which the server config references as `{{VAR}}` in `headers` and `env`.

**Q3.** `endpoints.custom` adds OpenAI-compatible providers. `mcpServers` (types stdio, websocket, sse, streamable-http; `serverInstructions`; `timeout`) are global in the YAML, and server changes need a restart. `modelSpecs` bundle a model, a system prompt (`promptPrefix`) and tool flags (`webSearch`, `executeCode`, `mcpServers`, `skills`, ...) into named picker entries. `skills` is an interface/spec key. Specs are server-wide, and agent-based specs are filtered by each user's permissions.

**Q4.** A custom endpoint has `name`, `apiKey`, `baseURL`, `models` (`default` list, `fetch` to pull the list from the provider), `titleModel`, `dropParams` and `modelDisplayLabel`. A `modelSpecs` preset adds `temperature`, `maxContextTokens`, reasoning options and similar per spec. Prices: (unverified).

**Q5.** Not documented (unverified).

**Q6.** The `interface` toggles default to on: `presets`, `parameters`, `prompts`, `bookmarks`, `memories`, `multiConvo`, `agents`, `temporaryChat`, `runCode`, `webSearch`, `fileSearch`, `modelSelect` and others. Off by default: `contextCost`, `marketplace`, `remoteAgents`, and `schedules` (omitted means off). So it is mostly on by default, and the YAML turns things off. `modelSpecs.prioritize` defaults to true and `enforce` to false.

## 3. Open WebUI

Source: https://docs.openwebui.com/reference/env-configuration (only the first part of a very long page was read).

**Q1.** Variables marked `ConfigVar` (PersistentConfig) are read from the environment only on first launch. After that the value stored in the database is used and the env var is ignored. Examples are `ENABLE_SIGNUP`, `DEFAULT_MODELS` and `WEBUI_URL`. Plain variables (`HOST`, `PORT`) are applied from the environment on every start. With `ENABLE_PERSISTENT_CONFIG=False` (default is True), env vars win, and admin UI edits last only until restart. OAuth settings are read-only in the admin panel unless `ENABLE_OAUTH_PERSISTENT_CONFIG` is set. A stale DB value is documented as the usual reason a setting seems ignored.

**Q2.** Container deployment is env-first, and the admin UI plus DB becomes the long-term source of truth after first boot. Secrets: not covered in the part read (unverified).

**Q3.** Global defaults for per-user settings and models are JSON env vars: `DEFAULT_INTERFACE_SETTINGS`, `DEFAULT_MODEL_PARAMS`, `DEFAULT_PINNED_MODELS`. Provider endpoints (`OPENAI_API_BASE_URLS`) were not in the text read (unverified).

**Q4.** `DEFAULT_MODEL_PARAMS` and `DEFAULT_MODELS` are env/DB defaults. Per-model editing in the admin UI is plausible but was not verified.

**Q5.** Not documented (unverified).

**Q6.** Most `ENABLE_*` toggles are ConfigVars. On by default: signup, login form, memories, notes, folders. Off by default: channels, apps, comments, user webhooks. The mix is on for the core features and off for the newer or riskier ones.

## 4. Onyx (Danswer)

Sources:
- https://docs.onyx.app/deployment/configuration/configuration
- https://docs.onyx.app/admins/getting_started/slack_bot_setup.md

**Q1.** Two layers. The deployment layer is env vars (Docker Compose `.env`, Helm `values.yaml`) and covers databases, caches, file storage and auth backends. The application layer is the Admin Panel (stored by the app). SSO and connectors are configured there. Slack bot behavior has two levels: a Default Configuration for all channels and DMs, and a per-channel Channel Configuration that overrides it.

**Q2.** Helm puts env vars in `configMap` and secrets in `auth.secrets`. `ENCRYPTION_KEY_SECRET` encrypts connector credentials and API keys at rest (changing it makes stored credentials unreadable). `MASK_CREDENTIAL_PREFIX` masks credentials in the admin UI. Passwords come from env vars. The docs do not say where the per-channel Slack config or tokens are stored (unverified). The Slack bot connects over Socket Mode, so no public URL is needed. Tokens (bot, app, optional user) are entered in the Admin Panel. Slack bot tuning on a self-hosted install is via env vars on the background worker (for example `ONYX_BOT_MAX_QPM`).

**Q3.** Per-channel settings: the Agent (persona), Document Sets that limit search scope, respond-to-tags-only, only-respond-if-citations-found, private (ephemeral) replies, allowed users or groups, and follow-up tags. This is a per-connection layer (per Slack channel). Connectors and LLM providers are added in the UI.

**Q4.** The page does not say how to add LLM providers in the UI. Enterprise Edition has `ENV_SEED_CONFIGURATION`, which takes an `llms` list (provider, API key, API base, default model names) and `admin_user_emails`, and persists across restarts. That is a seed-from-env path into UI-managed state. Prices and context windows: (unverified).

**Q5.** Not documented (unverified).

**Q6.** Out of the box the Slack bot responds only when tagged or in a DM. Disabling the default configuration limits it to configured channels and stops DM replies. Other default feature sets: (unverified).

## 5. Dify (self-host)

Source: https://docs.dify.ai/en/self-host/configuration/environments

**Q1.** Files and precedence, first match wins: `docker/.env`, then `urls.env`, then the service's local `.env`, then its `.defaults`, then the application default. Shell exports and a Compose `environment:` entry can override these. `docker/envs/**/*.defaults` are shipped defaults that you do not edit. Local `.env` files under `docker/envs/` are your overrides, git-ignored so they survive upgrades. Env vs UI: some env vars act as defaults when the UI sets nothing (`APP_DEFAULT_ACTIVE_REQUESTS`), and some are caps over UI values (`APP_MAX_ACTIVE_REQUESTS`). Whether the admin UI overrides env values for model providers is not stated (unverified).

**Q2.** `SECRET_KEY` signs tokens and encrypts system OAuth credentials. Stored credentials, including model provider API keys, are encrypted with per-workspace RSA keys. `KEY_PROVIDER_TYPE` chooses where the private key lives (`local` storage backend, or `azure-keyvault`), and must be chosen before the first workspace. Credentials and providers are managed per workspace in the UI, not in a config file.

**Q3.** `NEW_USER_DEFAULT_PLUGIN_IDS` auto-installs marketplace plugins in new workspaces. Model providers come in as plugins, set per workspace (inferred from the env names; the page does not describe the UI flow).

**Q4.** `NEW_USER_DEFAULT_MODELS` sets default models for new workspaces. Per-model price and context window: (unverified).

**Q5.** Not documented (unverified).

**Q6.** Feature switches in the env include `ENABLE_COLLABORATION_MODE`, `ENABLE_SKILL`, `OPENAPI_ENABLED`, `ALLOW_REGISTER` and `FILE_SCAN_ENABLED`. Their defaults were not stated on the page (unverified).

## Not covered

Wren AI and Vanna were not researched. Q5 (benchmark through the same config path) is not documented in any of the product pages read. This is a gap in the evidence, not evidence that no product does it.

## Patterns that recur across products

1. **Secrets in env, structure in a file or the DB, with a reference syntax.** `${VAR}` placeholders in `.mcp.json` and librechat.yaml; Onyx `auth.secrets` and `ENCRYPTION_KEY_SECRET`; Dify `SECRET_KEY` plus per-workspace RSA encryption; Claude Code plugin `sensitive` fields go to the OS credential store. (Claude Code, LibreChat, Onyx, Dify)
2. **A cloud or container deployment splits config in two: a deployment layer (env, mounted files) and an application layer (admin UI and DB).** Onyx states this split explicitly. Open WebUI's PersistentConfig and Dify's workspace-level providers show it too. (Onyx, Open WebUI, Dify, LibreChat)
3. **Env seeds, then the DB owns it.** Open WebUI reads PersistentConfig vars from env only on first launch. LibreChat seeds interface permissions at first startup. Onyx EE `ENV_SEED_CONFIGURATION` seeds LLMs. This breeds a "stale value" trap, which Open WebUI documents. Each has a switch or a procedure to make env win (`ENABLE_PERSISTENT_CONFIG=False`). (Open WebUI, LibreChat, Onyx EE)
4. **Fixed layer order with the most specific or most controlled layer winning, and lists that merge.** Claude Code: managed > CLI > local > project > user, with permission lists merging and a few model keys taking the whole value. Dify: `docker/.env` > local `.env` > `.defaults` > app default. Onyx Slack: channel config overrides the default config. Whole-entry replacement for MCP servers (no field merge). (Claude Code, Dify, Onyx)
5. **A policy tier that users cannot override, plus a few "stricter wins" exceptions.** Claude Code managed settings (`availableModels`, `managedMcpServers`, `strictPluginOnlyCustomization`). LibreChat `modelSpecs.enforce`. Dify `APP_MAX_ACTIVE_REQUESTS` as a cap over UI values. (Claude Code, LibreChat, Dify)
6. **Shipped defaults are separate files or code the user never edits; user overrides live in git-ignored or separate files.** Dify `.defaults` vs local `.env`; Claude Code `settings.local.json` kept out of git. (Dify, Claude Code)
7. **Extensions are files or entries at several scopes, with namespacing to avoid collisions.** Claude Code skills (enterprise/personal/project/nested/plugin, with `plugin:skill` namespacing) and MCP (local/project/user/plugin); LibreChat `mcpServers` and `modelSpecs` bundles; Dify marketplace plugins; Onyx per-channel Agent and Document Sets. A plugin or bundle packages several component types behind one enable switch. (Claude Code, LibreChat, Dify, Onyx)
8. **Per-user variables for per-user credentials.** LibreChat `user_provided` keys and MCP `customUserVars`; Claude Code plugin `userConfig`. (LibreChat, Claude Code)
9. **Per-model settings are a named list of entries, not scattered keys.** LibreChat `endpoints.custom` and `modelSpecs.preset`; Claude Code `modelSettings` per model and `effortLevel`; Onyx EE `llms` seed list; Dify default-model env vars. (LibreChat, Claude Code, Onyx, Dify)
10. **Core features default on, with off switches; newer, risky or admin-oriented features default off.** LibreChat `interface` (most true; `marketplace`, `schedules`, `remoteAgents` off); Open WebUI (core on; channels, apps, comments off); Claude Code SDK (all sources and all skills on by default, plugin `defaultEnabled: true`); Onyx Slack bot (answers only when tagged or in a DM by default). (LibreChat, Open WebUI, Claude Code, Onyx)
11. **A way to see the effective config.** Claude Code `/status`, `claude doctor`, SDK `resolveSettings()` with per-key provenance; Dify `docker compose config`. (Claude Code, Dify)
12. **A library or embedded mode defaults to the full local config; isolation is opt-in.** The Agent SDK defaults to loading every settings source, and the docs say to pass `settingSources: []` plus `strictMcpConfig` for isolated or multi-tenant runs. This is the closest documented analogue to a benchmark run set up as "another configuration". No product page read documents benchmark runs. (Claude Code only)


---

## 附录 C：跑榜做法

# Q5/Q6: benchmarks vs product configuration (access date 2026-11-27)

## Evidence
- SWE-agent batch mode: YAML via `--config config/default.yaml`; per-run overrides on CLI (`--agent.model.name`, `--agent.model.per_instance_cost_limit`); instance selection deterministic. Model pinned only by the name passed; tools/templates/version not stated on page. https://swe-agent.com/latest/usage/batch_mode/
- Aider benchmark: runs the cloned aider code in docker (`pip install -e .[dev]`); `--model`, `--edit-format` "same as you would pass directly to aider"; `--read-model-settings=<yml>`; no pinned commit, but the report records git hash (marked dirty). https://raw.githubusercontent.com/Aider-AI/aider/main/benchmark/README.md
- Harbor README: `harbor run -d terminal-bench@2.0 -a claude-code -m anthropic/...`; dataset version pinned via `@version`. Agent-kwargs / job YAML not covered on that page (unverified there). https://raw.githubusercontent.com/harbor-framework/harbor/main/README.md
- Harbor local clone, Claude Code agent: /Users/shenghuikevin/dev/AI/data-agent-refs/harbor/src/harbor/agents/installed/claude_code.py
  - user-supplied settings JSON loaded+validated (L377-397), uploaded to /tmp/claude-code-settings/settings.json (L399-408) and passed as the real product flag `--settings` (L1924-1925, 1956); real `claude` CLI is invoked with `--print --output-format=stream-json`.
  - knobs map to product flags/env: `--max-turns`/CLAUDE_CODE_MAX_TURNS, `--effort`/CLAUDE_CODE_EFFORT_LEVEL, `--allowedTools`/`--disallowedTools`, MAX_THINKING_TOKENS (L55-105); skills copied to $CLAUDE_CONFIG_DIR/skills, MCP servers registered via product `claude mcp` command (L1692-1743, 1920).
  - benchmark-specific tweak: web tools disallowed (L87-88, appears conditional; condition not read).
  - version pin: base.py `version` option (L646-720); `get_version_command` runs `claude --version`.
- SignalPilot benchmark: /Users/shenghuikevin/dev/AI/data-agent-refs/SignalPilot/benchmark/runners/direct.py: own runner (not via product CLI); skills come from the same `signalpilot-plugin` installed at user scope (L49-50); MCP via `load_mcp_servers()` (L621) and `benchmark/mcp_config.json`; benchmark-only system prompt `prompts/dbt_local_system.md` (L38) and benchmark-only Dockerfiles; model default `claude-sonnet-4-6` via `--model` (L664).
- OpenHands config.toml/eval docs: fetches 404'd; (unverified) not checked this session.
- Terminal-Bench agents docs URL 404'd; (unverified).

## Recurring patterns
1. Benchmark = the product's own entry point plus its own config surface (a config file and CLI overrides). Seen in SWE-agent (YAML + `--agent.*` overrides), Aider (same flags as normal `aider`, `--read-model-settings`), Harbor+Claude Code (real `claude` binary, real `--settings` file, real env vars/flags).
2. Harness-specific code is limited to the outside: dataset/task selection, sandbox/container, instruction delivery, result collection. Agent behaviour knobs reuse product knobs. Exception: SignalPilot uses a separate runner plus a benchmark-only system prompt (though skills/plugin are the product's).
3. Per-run variation goes in CLI overrides on top of a config file, not new code paths (SWE-agent, Aider, Harbor).
4. Reproducibility pins: model name (sometimes dated id), dataset version (`terminal-bench@2.0`), agent/CLI version (Harbor `version` option; Aider records git hash incl. dirty flag), container image, deterministic instance order/slice (SWE-agent), cost/turn limits. Full config snapshot is not always saved (SWE-agent page and Aider doc do not say) (unverified beyond those pages).
5. Benchmarks typically disable features that cannot work or are unfair in the sandbox (e.g. web tools disallowed in Harbor Claude Code) via the product's own disallow setting; no evidence found of products defaulting to "off" and benchmarks turning features on. Whether product defaults are "all on" was not directly verified for SWE-agent/OpenHands (unverified).
