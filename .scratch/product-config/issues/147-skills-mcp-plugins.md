# 147 — Skill、MCP、插件：插件打包、按需加载与 `preload`、使用者 Skill 目录、`[mcp]` 与 `claude_code_config`、`[plugins]`、Skill 工具事件、插件清单不计入 Skill 版本哈希

Status: ready-for-agent
Blocked by: 142、143、146
Spec: ../spec.md（"Skill、MCP、插件（方向 1、3，Q3）"）
Claimed by: —
Branch: —

## Goal

产品运行打开 Claude Code 的默认能力：内置 `skills/` 作为本地插件 `data-agent` 交给 SDK（`plugins=[{"type": "local", "path": ...}]`），`[skills].dirs` 每个目录运行时包成一个本地插件；SDK `skills` = 发现的全部减 `[skills].disabled`，模型经 Skill 工具按需读全文；`[skills].preload` 的全文照旧进系统提示词，默认只有本任务类型的工作流 Skill。`[mcp].servers` 和（`claude_code_config = true` 时）使用者的 Claude Code MCP 配置挂给 agent；`[plugins].dirs` 作为本地插件加载。Skill 工具每次调用写一条事件。插件清单文件不计入 Skill 版本哈希（`decisions.md` Q8 人定第 2 条）。Skill 文本一字不改：dbt-workflow 第 2 步的改写和任何 Skill 文本改动推到迁移第二步（主会话做，和"评测改用产品新默认"一起在开发集上量）。评测配置文件（146 的 `[skills].tool = false`）的运行参数不变。141 合入的 `TICKET_147` 验收测试全部转绿（只删标记）。

## Authority

- May edit：
  - `skills/` 下新增插件清单（格式照 142 的结论，例如 `skills/.claude-plugin/plugin.json`）。只加清单，不改任何 `SKILL.md`。
  - `src/data_agent/agent.py` 的 `skill_version`（`agent.py:1289-1296`）：插件清单文件不计入哈希（评测和 `ablate` 的 `conditions`、`dab.py` 都经它算 `skill_version`）。
  - `src/data_agent/config.py`：只补 `[skills]`、`[mcp]`、`[plugins]` 的校验，不改 143 的加载与合并。
  - `src/data_agent/agent.py`：只改 Skill、MCP、插件相关部分：`QUERY_SKILLS`/`DBT_SKILLS` 与注释（`agent.py:111-115`；`:112` 是代码注释，不是 Skill 文本）、`load_skills`（`:1316-1326`）、`skill_names`/`injected_skills`（`:1329-1341`）、`system_prompt` 的 Skill 段（`:1360-1383`）、`build_options` 的 `mcp_servers`/`strict_mcp_config`/`setting_sources`/`skills`/新增 `plugins`（`:1713-1721`）、`record_message`（`:1810`）里记 Skill 工具调用的事件、`run` 里生成插件临时目录。
  - `src/data_agent/external_mcp.py`：加读使用者 Claude Code MCP 配置的路径（spec"已定"1 的放行与环境变量规则和 116 的不同，分开写）。
  - `src/data_agent/sandbox.py`：只在 142 推荐"Skill 目录按原路径挂进 Docker 沙箱"时改 `DockerSandbox` 的挂载（`sandbox.py:278`）。
  - `src/data_agent/web/conversations.py`：只在对话一轮的 `CLAUDE_CONFIG_DIR` 下插件发现需要额外设置时改（142 问题 2 的结论）。
  - `evals/profiles/*.toml`：只在 146 写的 `[skills]` 键名需要随本工单对齐时改，评测行为不变。
  - `tests/acceptance/` 里 141 标了 `TICKET_147` 的文件（`test_config_skills.py` 等，清单见 141 最终回复）：**只删** `TICKET_147` 标记，以及因此没用的定义和 import。
  - `tests/`（除 `tests/acceptance/`）；`docs/setup.md`（`[skills]`、`[mcp]`、`[plugins]`、`[instructions]` 之外）；`README.md`（使用者的 MCP 不在只读保证之内，spec"验收"）。
- Must not edit：`tests/acceptance/` 里除 `TICKET_147` 标记以外的一切；`tests/fixtures/product_config/`；`skills/` 里任何 `SKILL.md` 和脚本（Skill 文本改动推到迁移第二步）；`skills-frozen/`；`agent.py` 的提示词文字（148）、模型设置（145）、上限（144）；`evals/` 里除 `evals/profiles/` 的 `[skills]` 键名以外的文件；`web/src/`；其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none。
- Spend cap：工单 agent $0（不能 source 模型的 env 文件）。

## Context

行号以 main `e494e49` 为准。

- **142 的结论**：`docs/research/2026-11-27-skill-tool-spike.md`（142 的交付物，建票时还不存在）。本工单开工前先读它：三个问题的答案、`${CLAUDE_SKILL_DIR}` 推荐哪个方案、给 147 的注意事项。插件清单格式、Skill 名的写法（`data-agent:sql-workflow`）、对话 `CLAUDE_CONFIG_DIR` 下的发现行为都以它为准。
- **现状**：
  - Skill 全文写进系统提示词，`skills=[]` 关掉 Skill 工具（`agent.py:1721`）；`load_skills` 把 `${CLAUDE_SKILL_DIR}` 换成沙箱挂载路径或真实路径（`:1316-1326`，`SKILLS_MOUNT = "/skills"` 在 `sandbox.py:42`，挂载 `:278`）。
  - `DBT_SKILLS` 的注释"its Step 2 Skills are preloaded with it, as there is no Skill tool"（`agent.py:112`）；`dbt-workflow` 第 2 步"loaded below this one"（`skills/dbt-workflow/SKILL.md:125-131`）。
  - `strict_mcp_config=True`（`agent.py:1714`）、`setting_sources=["user"]`（`:1716`，ADR-0001：不读本仓库 `.claude/`）、`Bash` 在 `DENIED_TOOLS`（`:131`，ADR-0010）。spec：产品运行不再设 `strict_mcp_config`，但只加载 `[mcp].servers` 和 `claude_code_config` 两处来源，不让 SDK 去找本仓库的 `.mcp.json`；`setting_sources` 不含 `project`、禁用 Bash 不变。
  - 外部 MCP（116）：`external_mcp.load`（`external_mcp.py:189`）、`sdk_servers`（`:109`）、`allowed_tools`（`:125`）、`summary`（`:138`）。`[mcp].servers` 照 116 的规矩（放行清单、`DATA_AGENT_MCP_` 前缀）。
  - 使用者 Claude Code MCP 配置（spec"已定"1）：默认放行它的全部工具，可在 `[mcp]` 按服务器名补 `allowedTools` 收窄；环境变量照 Claude Code 原样展开，不加前缀限制；`config show` 和运行开头的事件列出这些服务器和工具。Claude Code 把用户级 MCP 配置放在哪个文件、什么格式：照官方文档核实，附链接和访问日期，没核实到的标 `未确认`。
- **SDK**：`claude-agent-sdk==0.2.187`（`uv.lock:620-621`）。`ClaudeAgentOptions.skills: list[str] | "all" | None`、`plugins: list[SdkPluginConfig]`（`.venv` 里 `claude_agent_sdk/types.py:2772`、`:2806`）；初始化消息带 `skills`、`plugins`（`types.py:1571-1572`）。
- **评测不变**：146 的评测配置文件 `[skills].tool = false`、`preload` = 现在注入的全部 Skill、`[mcp]`/`[plugins]`/`[skills].dirs` 为空。141 的快照守门组逐项比系统提示词全文和 `skill_version`；`skill_version` 哈希 Skill 目录下的每个文件（`agent.py:1289-1296`），所以插件清单要排除在哈希外（Q8），Skill 文本不改。
- **按需加载与现有 Skill 文本**：dbt-workflow 第 2 步仍写"loaded below this one"（`skills/dbt-workflow/SKILL.md:125-131`），产品默认只常驻 `dbt-workflow` 后这句和实际不符；Q8 定这类文本改动推到迁移第二步，本工单不改，在最终回复里列出受影响的句子供第二步用。
- **`${CLAUDE_SKILL_DIR}`**：Skill 里的脚本路径（`skills/dbt-workflow/SKILL.md:50`、`:61`、`:108`、`:134`、`:391`）不改，所以 142 的两个候选里本工单只能用"Skill 目录按原路径挂进 Docker 沙箱"（改 `sandbox.py`）；142 推荐另一个时见 Stop conditions。
- **Skill 工具事件**：字段（Skill 名、来源插件）由本工单定，写进 `record_message` 附近；不在 141 的截停接缝上（没有模型调用），用单元测试（喂一条手写的 Skill 工具调用消息）覆盖，真实运行由主会话确认。
- **并行工单**：148 改系统提示词的其他段、145 改模型设置、144 改上限，都在 `agent.py`；后合入的一方 rebase。
- **沙箱里的 Skill**（人 2026-11-27 定，`decisions.md`"调研后定"第 1 条）：Docker 后端改成每次运行用 tar 流把 Skill 复制进沙箱，归新工单 cloud-deploy 163，不在本工单。本工单照原计划让使用者 Skill 目录在本机模式下能用，不另做容器内的方案。

## Acceptance

- [ ] 开工前读了 142 的结论文档，最终回复写明照它的哪个方案做的。
- [ ] `TICKET_147` 标记全部删掉；`git diff main -- tests/acceptance tests/fixtures` 只有这些删除行。
- [ ] `uv run pytest tests/acceptance/test_config_skills.py -v` 全部 PASSED，0 个 xfail。
- [ ] 141 的快照守门组和评测隔离组照旧通过（`skill_version` 不因插件清单改变）；其他已合入的验收测试照旧通过。
- [ ] Skill 工具调用事件有单元测试；`[skills].dirs` 包成插件的临时目录在运行结束后的去留写明并有测试；使用者 Claude Code MCP 配置的读取有单元测试（假家目录）。
- [ ] `docs/setup.md` 写了 `[skills]`、`[mcp]`、`[plugins]`；README 写明产品默认按需加载 Skill、使用者的 MCP 不在只读保证之内。
- [ ] `make check` 通过。
- [ ] spec"已定"第 1 条的列出要求有单元测试：`config show` 和运行开头的事件都列出 Claude Code 的 MCP 服务器和它们的工具（141 合并前审查第 11 条，验收测试没钉）。

## Evidence required

`make check` 输出；`test_config_skills.py` 的 `-v` 输出；`git diff main -- tests/acceptance | grep '^[-+]' | grep -v '^[-+][-+]'`。不调模型的真实运行（$0）：一次产品查数运行截停前的选项摘要（`skills`、`plugins`、`mcp_servers` 的键、系统提示词里有哪些 Skill 全文）。

交给主会话（写出命令）：
- 一次产品默认的查数运行（`--sandbox docker`）：事件里有 Skill 工具调用（例如模型读了 `data-agent:duckdb-sql`），记下调了哪些 Skill。
- 一次产品默认的 dbt 运行（`--sandbox docker`，临时 schema）：`dbt-workflow` 的脚本（`scan_project.py`）在沙箱里跑通。
- 一份带 `[skills].dirs`（一个自写的小 Skill）的配置跑一次，确认 Skill 被发现、被调用。
- 142 探针若需要在本工单的实现上重跑一次，写出命令。

## Stop conditions

- 142 的结论文档不存在，或三个问题任一答"否"（decisions.md Q3：停下回对齐）。
- 142 的结论是只有"Skill 里统一写挂载路径"可行（要改 Skill 文本，Q8 推到迁移第二步），或推荐的方案要改 Authority 以外的文件。
- 产品默认按需加载不改 Skill 文本就跑不通（例如主会话的 dbt 冒烟里 agent 因第 2 步的旧说法卡住）：写明现象交回，不改 Skill。
- 产品运行不设 `strict_mcp_config` 时，实测 SDK 仍会去读本仓库的 `.mcp.json` 或 `.claude/`。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：方向 1、3 和 Q3 的实现；照 142 的结论（`docs/research/2026-11-27-skill-tool-spike.md`，建票时还没有）。
- 2026-11-27（主会话）：按 `decisions.md` Q8 改票：Skill 文本（含 dbt-workflow 第 2 步）不改，推到迁移第二步；插件清单不计入 `skill_version`，由本工单做；去掉"加清单或改 Skill 让快照失败"的停止条件。
- 2026-11-27（主会话）：按调研后的决定补 Context：沙箱里 Skill 的复制方案归 cloud-deploy 163。
