# 142 — 实测：DeepSeek 下的 Skill 工具、本地插件里的 Skill、Skill 脚本在沙箱里的路径

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md（"Skill、MCP、插件"；decisions.md Q3 最后一条）
Claimed by: —
Branch: —

## Goal

回答三个是/否问题，各带证据，供工单 147 照着实现：

1. 经 DeepSeek 的 Anthropic 兼容端点（`deepseek-flash`），SDK 设 `skills=[...]` 后，模型能看到 Skill 列表、能调 Skill 工具、调了之后拿到 SKILL.md 全文。
2. `plugins=[{"type": "local", "path": <目录>}]` 指向一个带插件清单和 `skills/` 的目录时，里面的 Skill 被发现，名字是 `插件名:Skill名`；对话界面那种每段对话自己的 `CLAUDE_CONFIG_DIR` 下也一样。
3. 按需加载的 Skill 里写 `${CLAUDE_SKILL_DIR}`（如 `skills/dbt-workflow/scan_project.py`）时，SDK 填的是什么路径；经 `sandbox_exec`（Docker 后端，`SKILLS_MOUNT`）能不能跑到脚本；不能的话，两个候选方案（Skill 目录按原路径挂进沙箱 / Skill 里统一写挂载路径）哪个可行。

## Authority

- May edit：新建 `scripts/spikes/skill_tool/`（探针脚本、最小插件目录、说明）和 `tests/test_spike_skill_tool.py`（不调模型的部分：插件清单格式、选项装配）；`docs/research/2026-11-27-skill-tool-spike.md`（结论）。
- Must not edit：`src/`、`skills/`、`evals/`、`tests/acceptance/`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access：none。
- Spend cap：工单 agent $0（不能 source 模型的 env 文件）。调模型的探针由主会话跑，预计 < $0.05。

## Context

- SDK 文档（`.venv` 里 `claude_agent_sdk/types.py`）：`skills: list[str] | "all" | None`——设了它 SDK 自动开 Skill 工具和设置来源，`[]` 是全关，`None` 交给 CLI 默认；`plugins: list[SdkPluginConfig]`，只支持 `{"type": "local", "path"}`；初始化消息里有 `skills`、`plugins`、`plugin_errors`。
- 现状：`src/data_agent/agent.py:1721` `skills=[]`；`:1316-1324` `load_skills` 把 `${CLAUDE_SKILL_DIR}` 换成沙箱挂载路径或真实路径；`:112` 注释"as there is no Skill tool"；对话的 `CLAUDE_CONFIG_DIR` 见 ADR-0001 Update 2026-11-17。
- ADR-0001 Update 2026-10-05 写过"Skill 与子 agent 由 SDK 在本地处理，待 01 实测"，没找到实测结果。
- 上游：SignalPilot 产品聊天 `plugins=[{type: local}]`、`skills="all"`（`docs/research/2026-10-11-upstream-agent-design.md:11`）；插件清单格式见 `~/dev/AI/data-agent-refs/signalpilot-plugin/.claude-plugin/plugin.json`。

## Acceptance

- [ ] 探针脚本：一条命令跑完三个问题，打印每个问题的是/否和关键证据（初始化消息里的 `skills`/`plugins`/`plugin_errors`、Skill 工具调用和结果、`${CLAUDE_SKILL_DIR}` 的值、沙箱里执行脚本的输出）。不调模型的部分（插件目录、选项装配）有单元测试。
- [ ] `docs/research/2026-11-27-skill-tool-spike.md` 写清：三个问题的答案（待主会话真实运行后补实测输出）、问题 3 推荐哪个方案及理由、给 147 的注意事项。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出；探针在不调模型模式下的输出；给主会话的真实运行命令（`DATA_AGENT_*` 从环境来、`--sandbox docker`）。

## Stop conditions

- 要改 `src/` 才能探到。
- 问题 1 或 2 在 SDK 层面就不可能（文档或源码证明），写明证据后交回，不必等真实运行。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments

- 2026-11-27（主会话建票）：product-config spec 的第一张实测工单；任一问题答"否"就停下回对齐（decisions.md Q3）。
