# 工单 142 探针：Skill 工具、本地插件、Skill 脚本在沙箱里的路径

结论与实测输出见 `docs/research/2026-11-27-skill-tool-spike.md`。

- `plugin/`：最小本地插件 `spike-probe`（`.claude-plugin/plugin.json` + `skills/`）。`skill-probe` 正文里有金丝雀、`${CLAUDE_SKILL_DIR}`、`${CLAUDE_PLUGIN_ROOT}` 和脚本 `where.py`；`hidden-probe` 故意不放进 `skills=[...]`，用来看列表过滤。
- `probe.py`：一条命令跑三段会话（默认配置目录、对话那种自己的 `CLAUDE_CONFIG_DIR`、运行时符号链接拼成的插件），再按 sandbox_exec 的路径（`execute_tool`）在本机和 Docker 沙箱里跑 `where.py`，打印三个问题的是/否和证据。

```bash
# 不调模型（$0）：本机起一个按脚本调 Skill 工具的假端点，看 CLI 这边的行为
uv run python scripts/spikes/skill_tool/probe.py --endpoint stub --sandbox docker

# 真调模型（主会话跑；DATA_AGENT_* 从环境来）
set -a && . ~/.config/data-agent/env && set +a && \
  uv run python scripts/spikes/skill_tool/probe.py --endpoint model --sandbox docker
```

`--sandbox docker` 要先有镜像（`make sandbox-image`）；`local` 只跑本机后端，`none` 跳过。
