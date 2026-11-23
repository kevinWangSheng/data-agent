# 外部 MCP 接入：agent 调用用户配置的外部工具

日期：2026-11-23。决策来自当天的 grilling（Q1–Q10，人逐条确认）。取证笔记：

- `docs/research/2026-11-23-external-mcp.md`：本仓库现状、SDK 配置格式、上游做法、安全冲突点、要改的文件
- `docs/research/2026-11-23-mcp-demo-and-timeouts.md`：MongoDB、GitHub 等官方 MCP server，客户端超时
- `docs/research/2026-11-23-trace-content-defaults.md`：trace 内容的业界默认（Q8 的依据）

## Problem Statement

agent 只能用我们自己写的工具（数据工具、`dbt_execute`、`sandbox_exec`、文件工具），清单是写死的。用户想让它多会一样东西，比如查 MongoDB、读 GitHub issue，就得我们写一个新工具。很多产品已经有现成的 MCP server，Claude Code、Cursor 填一份配置就能用；data-agent 现在用 `strict_mcp_config=True` 挡住了一切外部配置（`src/data_agent/agent.py:1650`）。

## Solution

- `data-agent run` 和 `data-agent web` 新增 `--mcp-servers <文件>`：读一份 Claude Code / Cursor 通用的 `mcpServers` JSON，把里面的 server 挂给 agent。
- 每个 server 必须写放行清单，只有列出的工具能用。
- 只在产品入口挂；评测入口一律不挂。
- 网页把外部工具显示成"server · tool"。

不做：把 data-agent 暴露成 MCP server 给 Claude Code 等调用（Q1，人定不做）。IM 入口（在 Slack、Telegram、Discord 里和 agent 对话）是下一个单独的 feature。

## Implementation Decisions

### 配置文件（Q3、Q4、Q6）

```json
{
  "mcpServers": {
    "mongo": {
      "type": "stdio",
      "command": "docker",
      "args": ["run", "--rm", "-i", "-e", "MDB_MCP_CONNECTION_STRING", "-e", "MDB_MCP_READ_ONLY",
               "mongodb/mongodb-mcp-server:latest"],
      "env": {"MDB_MCP_CONNECTION_STRING": "${DATA_AGENT_MCP_MONGO_URL}", "MDB_MCP_READ_ONLY": "true"},
      "allowedTools": ["find", "aggregate", "count", "list-collections", "collection-schema"]
    },
    "github": {
      "type": "http",
      "url": "https://api.githubcopilot.com/mcp/readonly",
      "headers": {"Authorization": "Bearer ${DATA_AGENT_MCP_GITHUB_TOKEN}"},
      "allowedTools": ["*"]
    }
  }
}
```

（示例里的工具名、GitHub 只读端点的写法实现时按官方文档核实。）

- 格式：Claude Code / Cursor 的 `mcpServers`，外加我们自己的 `allowedTools` 字段（每个 server 一个）。用户的现有配置拷过来，加上 `allowedTools` 就能用。
- 文件在仓库外，由 `--mcp-servers` 传入；没有默认路径（和 `--data-sources` 一样，`src/data_agent/web/sources.py`）。
- 传输：只支持 `stdio`（缺省 `type` 按 stdio）和 `http`（streamable HTTP）。`sse`、OAuth、其他类型一律报错并指出是哪个 server。
- 凭据：文件里写 `${VAR}`，运行时由我们从环境变量展开（SDK 是否展开 Python 配置里的 `${VAR}` 未确认，所以自己展开）。变量名必须以 `DATA_AGENT_MCP_` 开头；引用了没设的变量、或者不是这个前缀的变量，启动前报错。展开后的值不写进事件文件、命令行参数和日志。
- 放行：`allowedTools` 列出的工具变成 SDK 的 `allowed_tools` 里的 `mcp__<server>__<tool>`；`["*"]` 变成 `mcp__<server>__*`；没写或为空，这个 server 的工具一个都不放行（启动时给一条警告）。没列出的工具由现有的 `permission_mode="dontAsk"` 拒绝（`agent.py:1648` 附近），不新写权限代码。工具注解只用于显示，不参与放行（Q4）。
- 写入类工具（Q5）：用户列进清单就能用，不做调用前确认。README 写明风险。
- server 名：`data` 保留给我们自己的服务器（`MCP_SERVER`），配置里用 `data` 报错；名字只允许字母、数字、`-`、`_`。

### 挂到哪些入口（Q2）

| 入口 | 挂不挂 |
|---|---|
| `data-agent run --mcp-servers F` | 挂 |
| `data-agent web --mcp-servers F`：表单运行和对话的每一轮 | 挂，由 `web/launcher.py`、`web/conversations.py` 拼进子进程命令行（和 `--network` 的传法一样，`launcher.py:168`、`conversations.py:143`） |
| `evals/harbor_agent.py`、`evals/dab.py`、jaffle-shop | 不挂：`build_options` 默认不挂任何外部 server，评测入口不传配置；有测试锁住 |

### stdio server 的环境（Q7）

- stdio server 跑在宿主机上，不进沙箱，网络不受 `--network` 控制。写进 README 和已知限制。
- 它们拿不到我们的凭据：模型密钥（`DATA_AGENT_*`、传给 CLI 的 `ANTHROPIC_*`）、数据库连接串（`--data-sources` 里 `env` 指向的变量、`--postgres-env`）、Langfuse 密钥。
- 做法：配置里的 stdio server 都经一个包装命令启动（如 `python -m data_agent mcp-launch -- <原命令>`），包装从一个干净的环境开始，只带 `PATH`、`HOME`、`LANG` 等基本变量和配置里 `env` 展开后的变量，再 `exec` 原命令。CLI 本身会不会把自己的环境传给 stdio server 实现时核实，但不论结果如何都走包装。

### 事件、可观测性（Q8）

- 外部工具的调用照常走现有的 `tool_start`、`tool_end` 事件（`agent.py:1776-1806` 对任何工具都生成），入参和输出照常记录。
- 导出到 Langfuse 时照可观测性 spec 的开发集档完整记录（`.scratch/observability/spec.md` 的 Update 2026-11-23），外部工具不单独处理。评测入口不挂外部 MCP，所以留出集档不涉及。
- `version` 事件记下这次运行挂了哪些外部 server 和放行清单（只有名字，不带 URL 里的查询串、不带 env 的值），方便事后知道答案用到了什么。

### 网页（Q9）

- 运行页和对话页：`mcp__<server>__<tool>` 显示成"server · tool"，归入新类别"外部工具"；结果按文本显示（`web/src/run-page/tools.ts:20-37` 的 `shortTool`、`toolKind`；`web/src/run-view/model.ts:183` 的表格解析只对 `mcp__data__` 工具，不变）。
- 不在界面上增删改 MCP 配置（和"不在界面上增删改数据源"一致）。

## 工单

| 工单 | 内容 | 依赖 |
|---|---|---|
| 115 | 验收测试：在 Testing Decisions 的接缝上写测试，标 `xfail`；人审后合入 main | 无 |
| 116 | 后端：配置解析与校验、`${VAR}` 展开、放行清单、`--mcp-servers`（`run`、`web` 和两条子进程命令行）、stdio 包装、`version` 事件、评测入口不挂、README 与 `docs/setup.md`（含 MongoDB、GitHub 示例配置） | 115 |
| 117 | 前端：外部工具的显示和类别 | 116 |

和可观测性的工单（111–114）互不依赖，可以并行；都改 `agent.py` 的 `build_options`，后合入的 rebase。

## Testing Decisions

单元测试不起 MCP server、不联网、不调模型：测的是"配置文件 → SDK 选项"的翻译（Q10）。

- **解析与校验**：合法的 stdio、http 配置被接受；`sse`、未知类型、名为 `data` 的 server、非法名字报错并点名是哪个 server；缺 `allowedTools` 时这个 server 的工具不在放行清单里，并有警告。
- **`${VAR}` 展开**：`DATA_AGENT_MCP_*` 变量被展开进 `env` 和 `headers`；引用没设的变量、非 `DATA_AGENT_MCP_` 前缀的变量，启动前报错；展开后的值不出现在事件文件、`version` 事件、子进程命令行里（网页拼的命令行只带文件路径）。
- **放行清单**：`["find", "aggregate"]` 生成 `mcp__mongo__find`、`mcp__mongo__aggregate`；`["*"]` 生成 `mcp__github__*`；原有的 `mcp__data` 和内置工具不变；`permission_mode` 仍是 `dontAsk`。
- **入口**：`run` 和网页两条子进程命令行带上 `--mcp-servers`；不带时 `build_options` 的 `mcp_servers` 只有 `data`；`evals/harbor_agent.py` 拼的命令行没有 `--mcp-servers`；`evals/dab.py` 拿到的选项里只有它自己的服务器。
- **stdio 包装**：生成的 stdio 配置的命令是包装命令；包装启动的子进程环境里只有基本变量和配置展开的变量（用一个打印自身环境的小脚本当"原命令"，在临时目录里真实执行）：没有 `DATA_AGENT_*`、`ANTHROPIC_*`、数据库连接串变量、Langfuse 变量。
- **`version` 事件**：记有 server 名和放行清单，没有 env 的值、请求头的值。
- **网页**（117）：`mcp__mongo__find` 显示为"mongo · find"、类别"外部工具"；`mcp__data__*` 的显示和表格解析不变。

## 验收

- `make check` 通过。
- **MongoDB**（stdio，本机 docker，小的演示库）：
  - 开 `--readOnly`：一个需要同时查 MongoDB 和 DuckDB（仓库夹具）的问题，agent 调用了外部工具，并给出正确答案。
  - 不开只读模式、只放行查询工具：让 agent 往集合里插入一条数据，调用被拒，演示库没有变化。
- **GitHub**（http、只读、token 走请求头）：问一个需要读公开仓库 issue 的问题，agent 调用了外部工具。人需要准备一个只读 token，写进 `~/.config/data-agent/env`（变量名 `DATA_AGENT_MCP_GITHUB_TOKEN`）。
- 网页对话里各问一次，运行页显示"mongo · find"这类名字。
- 评测入口不受影响：`make check` 里的入口测试；不需要跑分。
- 集成测试（`make test-integration`）加 MongoDB 一个用例；本机先 `colima start`。

## Out of Scope

- 把 data-agent 暴露成 MCP server 给 Claude Code、Cursor 调用（Q1，人定不做）。
- IM 入口（Slack、Telegram、Discord）：下一个单独的 feature。
- OAuth 登录、SSE 传输。
- 写入类外部工具的调用前确认（Q5，记为已知限制）。
- 把 stdio server 放进 Docker 沙箱、限制它们的网络（Q7）。
- 在网页上管理 MCP 配置。
- 评测里使用外部 MCP。
- 外部工具结果的表格解析、图表。

## Further Notes

- 上游：SignalPilot 的外部 MCP 连接器按注解设默认开关（`SignalPilot/.../mcp_connectors/tools.py:1-52`），我们更保守，只认用户写的清单；nao 用 `mcp.json`，管理员可以禁用 server 或工具。
- MCP 规范原文："Clients should never make tool use decisions based on ToolAnnotations received from untrusted servers"（取证笔记 Q4）。
- 原计划里的"MongoDB 产品连接器"由本 feature 加 MongoDB 官方 MCP server 顶替，不再单独做。
- 已知限制：写入类工具没有调用前确认；stdio server 跑在宿主机、网络不受控；外部工具的入参和输出会进事件文件和（打开导出时）用户自己的 Langfuse。
