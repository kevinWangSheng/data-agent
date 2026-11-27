# 实测：DeepSeek 下的 Skill 工具、本地插件里的 Skill、Skill 脚本在沙箱里的路径（工单 142）

日期：2026-11-27。分支 `worktree-142-spike-skill-tool`。claude-agent-sdk 0.2.187，CLI 2.1.330；Docker 29.2.1（colima），镜像 `data-agent-sandbox`。

探针：`scripts/spikes/skill_tool/probe.py`（说明见同目录 `README.md`）。下文 `<wt>` = 本 worktree 根目录，`<tmp>` = 本次运行的临时目录（`/var/folders/.../T/spike-142-*`）。

## 0. 结论

| 问题 | CLI / SDK 这边（假端点，$0，已实测） | DeepSeek（`deepseek-flash`） |
|---|---|---|
| 1. 设 `skills=[...]` 后模型看到 Skill 列表、能调 Skill 工具、拿到 SKILL.md 全文 | **是**，但 `tools` 必须写上 `"Skill"`（见 1.1） | 待主会话实测（第 4 节） |
| 2. `plugins=[{"type": "local"}]` 里的 Skill 被发现，名字是 `插件名:Skill名`；对话自己的 `CLAUDE_CONFIG_DIR` 下也一样 | **是**（默认配置目录、新建的 `CLAUDE_CONFIG_DIR`、运行时符号链接拼的插件三种都是） | 与模型无关 |
| 3. `${CLAUDE_SKILL_DIR}` 填什么；Docker 沙箱能不能跑到脚本 | 填**宿主路径**（插件路径原样拼接，不解析符号链接）；现状的 Docker 沙箱**跑不到**；推荐**方案 A：Skill 目录按原路径挂进沙箱** | 与模型无关 |

问题 1、2 在 SDK 层面可行，没有触发停止条件。

## 1. 问题 1：Skill 工具

### 1.1 `skills=` 不会把 Skill 工具加进 `tools`

`build_options` 传的是显式的 `tools=[...]`（`src/data_agent/agent.py:1636`、`:1706`）。SDK 的 `skills` 只改 `--allowedTools`：列表时加 `Skill(<名字>)`，`"all"` 时加不带名字的 `Skill`（`claude_agent_sdk/_internal/transport/subprocess_cli.py:476-517`），不动 `--tools`。CLI 以 `--tools` 为准，所以只设 `skills` 时会话里没有 Skill 工具。

探针每次都跑一段只读初始化消息的会话（假端点，`tools` 是 `build_options` 的内置工具、不含 `Skill`，`skills=["spike-probe:skill-probe"]`），输出：

```text
## init only: skills set, tools without Skill (stub)
init tools: ['Edit', 'Glob', 'Grep', 'Read', 'Write']; error: None
Q1 precondition — Skill tool in the session when `tools` omits it: 否
```

之前另用一次性脚本（没入库：端点指向关着的本机端口，读到初始化消息就断开）跑过整个矩阵，结论相同：

| `tools` | `skills` | 初始化消息 `tools` 里有 `Skill` |
|---|---|---|
| `["Read"]` | `"all"` | 否 |
| `["Read"]` | `["spike-probe:skill-probe"]` | 否 |
| `["Read", "Skill"]` | `"all"` | 是 |
| `["Read", "Skill"]` | `["spike-probe:skill-probe"]` | 是 |

（默认配置目录和新建 `CLAUDE_CONFIG_DIR` 结果相同。）单元测试 `tests/test_spike_skill_tool.py::test_skills_alone_leaves_the_skill_tool_out_of_an_explicit_tools_list` 钉住 SDK 这一步的 argv。

### 1.2 调用与返回（假端点，`--endpoint stub`）

假端点按脚本先调 `Skill(skill="spike-probe:skill-probe")`，再调没放进列表的 `spike-probe:hidden-probe`，并记下 CLI 发出的每个请求。三段会话结果一样：

- 发给模型的工具（探针输出 `tools sent to the model`）：`['Edit', 'Glob', 'Grep', 'Read', 'Skill', 'DeferredToolPlaceholder', 'Write']`，内置工具都在，`DeferredToolPlaceholder` 是 CLI 自己加的；Skill 工具的入参 `{"skill": string, "args"?: string}`。
- 列表在第一条 user 消息的 system-reminder 里，不在系统提示词里：`The following skills are available for use with the Skill tool:` 下面只有 `- spike-probe:skill-probe: Spike probe. Load this Skill when asked to run the skill probe.`。
- 工具结果只是一句确认：`Launching skill: spike-probe:skill-probe`（`tool_use_result={"success": true, "commandName": "spike-probe:skill-probe"}`）。
- **SKILL.md 全文另作一条 user 消息注入**（SDK 流里是 `UserMessage` 的 `TextBlock`），去掉了 frontmatter，开头一行是目录，占位符已替换：

```text
Base directory for this skill: <wt>/scripts/spikes/skill_tool/plugin/skills/skill-probe

# Skill probe

The canary for this Skill is CANARY-142-plum-orbit.
...
SKILL_DIR: <wt>/scripts/spikes/skill_tool/plugin/skills/skill-probe
PLUGIN_ROOT: <wt>/scripts/spikes/skill_tool/plugin

The probe script is `python3 "<wt>/scripts/spikes/skill_tool/plugin/skills/skill-probe/where.py"`.
```

金丝雀只在正文里（frontmatter 和 description 里没有，单元测试钉住），所以看到它就说明全文进了上下文。模型能否从列表里自己找到 Skill、是否照着做，只有真实运行能回答（第 4 节；真实运行的提示词只说"run the skill probe"，不给 Skill 名，模型要从列表里挑）。

## 2. 问题 2：本地插件

插件目录：`.claude-plugin/plugin.json`（`{"name": "spike-probe", "version": "0.1.0", "description": ...}`，格式照 SignalPilot 插件）+ `skills/<名字>/SKILL.md`。

| 会话 | 初始化消息 `plugins`（只列探针插件） | `plugin_errors` | Skill 名 |
|---|---|---|---|
| 默认配置目录 | `{"name": "spike-probe", "path": "<wt>/scripts/spikes/skill_tool/plugin", "source": "spike-probe@inline", "version": "0.1.0"}` | 键缺失（输出 `null`） | `spike-probe:skill-probe`、`spike-probe:hidden-probe` |
| 新建 `CLAUDE_CONFIG_DIR`（同对话的 `ConversationTurn.config_dir`） | 同上 | 键缺失 | 同上 |
| 符号链接插件（`<tmp>/linked-plugin`，清单复制、每个 Skill 是指向原目录的链接） | `path` 是 `<tmp>/linked-plugin` | 键缺失 | 同上 |

- `setting_sources=["user"]` 不影响：插件走 `--plugin-dir`，与设置来源无关；SDK 只在 `setting_sources is None` 时才补 `["user", "project"]`，我们显式传了就保持不读本仓库 `.claude/`。
- **初始化消息的 `skills` 不按 `skills=[...]` 过滤**：没列进去的 `hidden-probe` 也在里面。过滤发生在发给模型的列表和 Skill 工具上：列表里没有它，调用被拒，`is_error=True`：`<tool_use_error>Skill spike-probe:hidden-probe is not in this session's skills allowlist</tool_use_error>`，全文没有注入（隐藏金丝雀没出现）。
- 初始化消息 `skills` 里还有 CLI 自带的 Skill：新建的 `CLAUDE_CONFIG_DIR` 下共 18 个（探针输出 `skills_total`）（如 `dataviz`、`deep-research`、`claude-api`），默认配置目录下 64 个（加上使用者 `~/.claude` 里的 Skill 和插件）。用 `skills=[...]` 列表时它们都不进模型的列表；用 `"all"` 会全部进。

## 3. 问题 3：`${CLAUDE_SKILL_DIR}` 与沙箱

### 3.1 SDK 填的值

宿主机上的 Skill 目录，就是插件路径 + `skills/<名字>`，原样拼接：符号链接插件填的是 `<tmp>/linked-plugin/skills/skill-probe`，**不是**链接指向的真实路径。`${CLAUDE_PLUGIN_ROOT}` 填插件目录。

### 3.2 经 sandbox_exec 跑脚本

探针用 `execute_tool`（sandbox_exec 的实现）跑 `where.py`，它打印自己的路径：

| 沙箱 | 命令里的路径 | 结果 |
|---|---|---|
| 本机后端 | SDK 填的路径（两种） | 跑到：`WHERE-142 ran <wt>/.../skill-probe/where.py` |
| 本机后端 | `/skills/skill-probe/where.py`（方案 B） | 跑不到：`can't open file '/skills/skill-probe/where.py'` |
| Docker，现状（只挂 `/skills`） | SDK 填的路径（两种） | 跑不到：`can't open file '<wt>/.../skill-probe/where.py': [Errno 2]` |
| Docker，现状 | `/skills/skill-probe/where.py`（方案 B） | 跑到：`WHERE-142 ran /skills/skill-probe/where.py` |
| Docker + 按原路径挂（方案 A） | SDK 填的路径（两种） | 跑到：`WHERE-142 ran <wt>/...` 和 `WHERE-142 ran <tmp>/linked-plugin/skills/skill-probe/where.py` |

方案 A 的容器（`SamePathDockerSandbox`）在 `DockerSandbox.start` 的参数之外，对每个填出来的路径加一条 `--mount type=bind,source=<真实路径>,target=<SDK 填的路径>,readonly`。

### 3.3 推荐方案 A：Skill 目录按原路径挂进沙箱

理由：

1. **两个后端用同一份文字**。SDK 填宿主路径；本机后端本来就在宿主机上，方案 A 只需要 Docker 多挂载。方案 B 在本机后端跑不到（上表第 2 行），要么两种后端写不同文字，要么本机后端也造一个 `/skills`。
2. **按需加载和预加载一致**。预加载走 `load_skills` 自己替换，可以同样替换成宿主路径（去掉 `skills_root` 分支），两条路径给模型的是同一个路径。方案 B 要么让 SDK 填的值失效（Skill 里不写占位符，写死 `/skills/...`），要么预加载与按需不一致。
3. **使用者和第三方的 Skill 改不了**。`[skills].dirs`、`[plugins].dirs` 里的 Skill 按 Claude Code 的约定写 `${CLAUDE_SKILL_DIR}`；方案 B 要求它们改写成我们的挂载路径。
4. **多个目录**。方案 A 每个目录挂在自己的路径上，没有重名；方案 B 要在 `/skills` 下给多个插件、多个目录定命名规则，还要处理同名 Skill。

代价与限制：

- 容器里能看到宿主机的目录结构（如家目录名）。只读挂载，不含凭据；沙箱本来就挂了工作区。
- 挂载的 `source` 要在 Docker 能共享的目录里：colima 默认只共享家目录，所以 `source` 用真实路径（`Path.resolve()`），`target` 用 SDK 填的路径（可以是 `/var/folders/...`，只在容器里存在）。本次符号链接插件在 `/var/folders` 下也跑通了，就是因为 `source` 解析到了家目录下的真实路径。Linux 服务器没有这个限制。
- `--mount` 的值以逗号分隔，路径里有逗号会出错；`-v` 以冒号分隔，也有同类问题；147 应拒绝含逗号的路径，或在文档里写明。
- sandbox_exec 的工具说明里"Skill 目录在 `/skills` 下"（`src/data_agent/sandbox.py:689-690`）要改成按原路径。

## 4. 待主会话真实运行（DeepSeek）

```bash
set -a && . ~/.config/data-agent/env && set +a && \
  uv run python scripts/spikes/skill_tool/probe.py --endpoint model --sandbox docker
```

预计 < $0.05（三段短会话，每段 3 次请求）。看输出末尾 `## verdicts`：

- `Q1 ... model follows it (model): 是` = 三段会话里模型都从列表里找到了 `spike-probe:skill-probe`（提示词不给名字），回答里都有金丝雀 `CANARY-142-plum-orbit`；每段的 `answer:` 里 `SKILL_DIR:` 应是宿主路径。
- `HIDDEN:` 一行应是 allowlist 的拒绝信息；`unlisted Skill kept out` 全是 `是`。模型没调隐藏 Skill 时这一项是 `未实测`（不算通过）；`listed to the model` 只有假端点能看到请求，真实运行恒为 `未实测`，以第 1.2 节为准。
- 任一项 `否`：按 decisions.md Q3 停下回对齐。

实测输出：（待主会话补）

## 5. 给工单 147 的注意事项

1. `tools` 要加 `"Skill"`（1.1）；`skills` 照 spec 传显式列表（`插件名:Skill名`），不用 `"all"`：`"all"` 会把 CLI 自带的 Skill 和使用者 `~/.claude` 里的 Skill 都列给模型（第 2 节）。
2. "发现的全部"要从我们自己的插件目录算（`skills/*/SKILL.md` 加清单里的 `name`），不能从初始化消息的 `skills` 取：那里不过滤，还混着 CLI 自带的和使用者的。
3. Skill 工具事件：`ToolUseBlock(name="Skill")` 的 `input["skill"]` 是名字，`:` 前是来源插件；结果是 `Launching skill: <名字>`，被拒时 `is_error=True`；全文另有一条以 `Base directory for this skill: ` 开头的 user 消息，要记"加载了哪个目录"可以从这里取。
4. `preload` 的 Skill 已在系统提示词里；要不要同时留在 `skills` 列表里（模型可能再调一次，把全文重复注入一遍）由 147 定，本次没测重复加载。
5. 问题 3 照方案 A：`DockerSandbox` 为每个 Skill 目录加一条按原路径的只读挂载（`source` 取真实路径，`target` 取传给 SDK 的路径）；`load_skills` 统一替换成宿主路径；改 sandbox_exec 的工具说明。符号链接拼插件时，`target` 用链接路径，因为 SDK 不解析链接。
6. 本次三段会话加载都成功，初始化消息里都没有 `plugin_errors` 键（SDK 文档：CLI 只在失败时带这个键，但缺键本身不证明加载干净，`types.py` `SystemInitData.plugin_errors`）；147 加载失败时应读它报错，失败项带 `path`。
7. 本次没测：SDK 自带的 Skill 与我们的同名时谁优先；`skills=[]` 下插件是否仍加载（评测入口要关掉时，应同时不传 `plugins`）。


### 主会话真实运行（DeepSeek，2026-11-27）

命令：`uv run python scripts/spikes/skill_tool/probe.py --endpoint model --sandbox docker`（`deepseek-flash`，标价 cost_usd 0.046348）。模型模式下"listed to the model"记作未实测，是因为真实端点的请求体不由探针记录；列表这一项由 stub 模式核实（是 ×3）。没有阻塞性的"否"：`tools` 不含 Skill 时没有 Skill 工具（147 要显式加 `"Skill"`）；现在的 Docker 沙箱找不到 SDK 填的宿主路径（推荐方案 A，实测可用）。

```
## verdicts
Q1 Skill tool, list, full SKILL.md — CLI side: 是; model follows it (model): 是; listed to the model: default-config-dir=未实测, conversation-config-dir=未实测, linked-plugin=未实测
Q1 precondition — Skill tool in the session when `tools` omits it: 否
Q2 local plugin found as spike-probe:skill-probe, per config dir: default-config-dir=是, conversation-config-dir=是, linked-plugin=是; unlisted Skill kept out: default-config-dir=是, conversation-config-dir=是, linked-plugin=是
Q3 ${CLAUDE_SKILL_DIR} as filled: {"default-config-dir": "~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe", "conversation-config-dir": "~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe", "linked-plugin": "/var/folders/nh/02mxl3td7rxcyj70_y5t1xqr0000gn/T/spike-142-grsx_ygv/linked-plugin/skills/skill-probe"}
   sandbox local, SDK path ~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe: 是 — `python3 "~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe/where.py"` -> 'exit_code: 0\nstdout:\nWHERE-142 ran ~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe/where.py'
   sandbox local, SDK path /var/folders/nh/02mxl3td7rxcyj70_y5t1xqr0000gn/T/spike-142-grsx_ygv/linked-plugin/skills/skill-probe: 是 — `python3 "/var/folders/nh/02mxl3td7rxcyj70_y5t1xqr0000gn/T/spike-142-grsx_ygv/linked-plugin/skills/skill-probe/where.py"` -> 'exit_code: 0\nstdout:\nWHERE-142 ran ~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe/where.py'
   sandbox local, mount path (B): 否 — `python3 "/skills/skill-probe/where.py"` -> "exit_code: 2\nstderr:\npython3: can't open file '/skills/skill-probe/where.py': [Errno 2] No such file or directory"
   sandbox docker as today, SDK path ~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe: 否 — `python3 "~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe/where.py"` -> "exit_code: 2\nstderr:\npython3: can't open file '~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe/where.py': [Errno 2] No such file or directory"
   sandbox docker as today, SDK path /var/folders/nh/02mxl3td7rxcyj70_y5t1xqr0000gn/T/spike-142-grsx_ygv/linked-plugin/skills/skill-probe: 否 — `python3 "/var/folders/nh/02mxl3td7rxcyj70_y5t1xqr0000gn/T/spike-142-grsx_ygv/linked-plugin/skills/skill-probe/where.py"` -> "exit_code: 2\nstderr:\npython3: can't open file '/var/folders/nh/02mxl3td7rxcyj70_y5t1xqr0000gn/T/spike-142-grsx_ygv/linked-plugin/skills/skill-probe/where.py': [Errno 2] No such file or directory"
   sandbox docker as today, mount path (B): 是 — `python3 "/skills/skill-probe/where.py"` -> 'exit_code: 0\nstdout:\nWHERE-142 ran /skills/skill-probe/where.py'
   sandbox docker + same-path mount (A), ~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe: 是 — `python3 "~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe/where.py"` -> 'exit_code: 0\nstdout:\nWHERE-142 ran ~/dev/AI/data-agent/.claude/worktrees/142-spike-skill-tool/scripts/spikes/skill_tool/plugin/skills/skill-probe/where.py'
   sandbox docker + same-path mount (A), /var/folders/nh/02mxl3td7rxcyj70_y5t1xqr0000gn/T/spike-142-grsx_ygv/linked-plugin/skills/skill-probe: 是 — `python3 "/var/folders/nh/02mxl3td7rxcyj70_y5t1xqr0000gn/T/spike-142-grsx_ygv/linked-plugin/skills/skill-probe/where.py"` -> 'exit_code: 0\nstdout:\nWHERE-142 ran /var/folders/nh/02mxl3td7rxcyj70_y5t1xqr0000gn/T/spike-142-grsx_ygv/linked-plugin/skills/skill-probe/where.py'
```
