# 同一轮发出的多个 MCP 调用为什么串行执行（工单 43）

日期：2026-11-04。只诊断，不改 `src/`。

## 结论

**根因：Claude Code（CLI 2.1.301，SDK 0.2.168）的工具调度器只并行执行"concurrency-safe"的工具。对我们这种用户配置的 stdio MCP 服务器，一个 MCP 工具是否 concurrency-safe 由 `readOnlyHint` 注解决定（判定式是 `mayOverlap || readOnlyHint`，`mayOverlap` 只对 CLI 内部登记的服务器为真，对我们恒为假）。我们的 11 个工具都没有注解，所以 CLI 把它们一个接一个地发给服务器。** 服务器端（mcp 2.3.0 的 stdio 分发、`anyio` 线程池、DuckDB 网关）本身是并行的，已用本地复现排除。

四条假设的判定：

| # | 假设 | 判定 | 证据 |
|---|---|---|---|
| 1 | CLI 只并行执行标了 `readOnlyHint` 的 MCP 工具 | **证实（根因；对本配置成立，见 §2.1(a) 的 `mayOverlap` 例外）** | CLI 二进制源码 §2.1；复现 B、C 的 A/B 对照 §3.2、§3.3 |
| 2 | MCP Python 服务端按顺序处理 stdio 请求 | 排除 | `tools/call` 每个请求 `start_soon` 一个任务，只有 `initialize` 是 inline §2.2；复现 A 4 个并发调用 0.43 s 完成 §3.1 |
| 3 | `anyio.to_thread.run_sync` 的线程上限或全局锁让同步工具排队 | 排除 | 默认 limiter 40 个线程，没有全局锁 §2.3；复现 A |
| 4 | 网关的锁 | DuckDB 排除；Postgres 是次级限制（只看了源码，没有测） | DuckDB 每条语句开关一个连接，没有锁；Postgres 网关一把 `threading.Lock` 串行化所有语句 §2.4 |

修法：给所有工具加 `ToolAnnotations(readOnlyHint=True)`（草稿见 §4）。本地用真实服务器验证：4 个 `query_database` 从 7.5 s 串行变成 2.7 s 并行（§3.3）。

## 1. 现象回顾

`runs/accept-2/reading/design-audit/audit-harness.md` 第 2 条：`pos-operations__JfH8WAC` 里 21 个 `verify_model_values` 在 5 s 内发出，每 13–20 s 完成一个，共 340 s；4 个 `query_database` 一批时每个在前一个结束后约 1 s 结束；内置 Read/Glob 一批同时完成。

## 2. 源码证据

### 2.1 Claude Code CLI（根因）

打包的 CLI 在 `.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude`（`claude-agent-sdk` 0.2.168，CLI 2.1.301，Mach-O 二进制，JS 以文本内嵌）。用 `.scratch/43-serial-mcp/ctx.py`（按字节偏移打印上下文）查到三段：

**(a) MCP 工具的 concurrency-safe 判定**（偏移 234049957，另一处 234319151 相同）：

```js
isConcurrencySafe(){return Oe.mayOverlap||(B.annotations?.readOnlyHint??!1)},
isReadOnly(){return B.annotations?.readOnlyHint??!1},
readOnlyHint:B.annotations?.readOnlyHint,
isDestructive(){return B.annotations?.destructiveHint??!1},
```

没有注解时 `readOnlyHint ?? false` → `false`。`mayOverlap` 来自一个按 host 索引的内部 map（偏移 233929489：`xkn(e,n,o){let r=he().get(e);if(r===void 0||!r.toolNames.has(n))return{mayOverlap:!1,...}`），和 `builtinToolsServedByMcp`（内置工具由 MCP 代理的模式）相关；对我们这种用户配置的 stdio 服务器没有登记，所以为 `false`（登记路径细节未确认，但复现 B/C 证明它对我们不生效）。

**(b) 调度器**（偏移 212610365）：

```js
canExecuteTool(e){let o=this.tools.filter((n)=>n.status==="executing");
  return o.length===0||e&&o.every((n)=>n.isConcurrencySafe)}
async processQueue(){for(let e of this.tools){if(e.status!=="queued")continue;
  if(this.canExecuteTool(e.isConcurrencySafe))this.applyEndedRunLayers(),await this.executeTool(e);
  else if(!e.isConcurrencySafe)break}}
```

规则：一个工具能启动，当且仅当没有工具在执行，或者它和所有正在执行的工具都 concurrency-safe。不 safe 的工具排队时还会 `break`，把它后面的全部堵住。入队时 `isConcurrencySafe` 按工具的 `isConcurrencySafe(input)` 算一次（偏移 212610151）。

**(c) 注解从服务器到 CLI 的通路**：CLI 用 MCP SDK 的 schema 解析 `tools/list` 里的 `annotations:{title,readOnlyHint,destructiveHint,idempotentHint,openWorldHint}`，直接挂在工具对象上。另外 `readOnlyHint` 还进入 `isReadOnly()`：它在 `plan` 模式和 auto 模式分类里用来自动放行只读工具；我们的运行用 `permission_mode="dontAsk"` 加显式 `allowed_tools=[..., "mcp__data-agent"]`（`src/data_agent/agent.py`），加注解不改变权限结果。

### 2.2 MCP Python 服务端的分发（假设 2，排除）

`mcp/shared/jsonrpc_dispatcher.py`（mcp 2.3.0）：

- `run()` 在一个 task group 里循环读 stdio，每条消息 `await self._dispatch(...)`（`:508-525`）。
- `_dispatch_request()`：方法在 `_inline_methods` 里才阻塞读循环等 handler 完成；否则 `self._spawn(self._handle_request, ...)`，即 `tg.start_soon`（`:609-622`，`:699-707`）。
- `_inline_methods` 只有 `{"initialize"}`（`mcp/server/runner.py:496,733`）。`tools/call` 不在其中。

所以服务器对每个 `tools/call` 起一个独立任务，不串行。

### 2.3 同步工具的线程池（假设 3，排除）

- `mcp/server/mcpserver/utilities/func_metadata.py:164`：`return await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))`，没有自定义 limiter。
- `anyio.to_thread.current_default_thread_limiter().total_tokens` 在本环境里输出 `40`（anyio 4.15.1）。远高于一批 21 个调用。

### 2.4 网关（假设 4）

- DuckDB：`src/data_agent/gateway.py:82-84` 每条语句 `duckdb.connect(..., read_only=True)`，`finally` 里 `con.close()`（`:102`），没有锁。多个只读连接可以并存（复现 A、C 证明）。
- Postgres：`gateway.py:117,121,126` 一把 `threading.Lock` 包住 `query()` 和 `explain()`，一个连接。即使 CLI 并行发出，Postgres 上的语句仍会在网关里排队。这是修完根因之后的次级限制，只影响 Postgres 运行。本工单只从源码判断，没有对 Postgres 跑复现（推断）。

## 3. 复现

脚本在 `.scratch/43-serial-mcp/`（按工单约定不提交；本节写明每个脚本做什么，照着能重写）。都在本 worktree 根目录运行，用 `.venv/bin/python -I`。没有调模型，花费 $0。§2.1 的字节偏移用 `ctx.py` 得到：把二进制整个读进内存，`re.finditer` 找模式，打印前后各 300 字节和 `m.start()`；等价一行：`python -c "d=open(CLI,'rb').read();i=d.find(b'isConcurrencySafe(){return');print(i,d[i-300:i+300])"`。

### 3.1 复现 A：服务器端是否并行（排除假设 2、3 和 4 的 DuckDB 部分）

`repro_server.py`：用 `mcp` 的 `stdio_client` 起真实的 `python -m data_agent mcp-server --config full --duckdb shop.duckdb`（和 `agent.py` 一样的启动方式；`shop.duckdb` 由 `tests/fixtures/shop.sql` 生成），`initialize` 后先单独调一次 `query_database`，再 `asyncio.gather` 并发调 4 次，SQL 是约 0.5 s 的 `range(40000000)` 聚合。

```
$ .venv/bin/python -I .scratch/43-serial-mcp/repro_server.py .scratch/43-serial-mcp/shop.duckdb 4
tools and annotations:
  query_database: annotations=None
  list_tables: annotations=None
  describe_table: annotations=None
  explain_query: annotations=None
  discover: annotations=None
  hydrate: annotations=None
  dictionary_search: annotations=None
  map_columns: annotations=None
  check_model_schema: annotations=None
  audit_model_sources: annotations=None
  verify_model_values: annotations=None
solo query_database ... 0.57s
  call 0: start +0.00s end +0.39s
  call 1: start +0.00s end +0.40s
  call 2: start +0.00s end +0.43s
  call 3: start +0.00s end +0.41s
4 concurrent calls: wall 0.43s; serial would be ~2.28s
```

4 个并发调用的墙钟时间等于 1 个的时间：服务器端并行。顺带确认了 `tools/list` 里 11 个工具的 `annotations` 都是 `None`。

### 3.2 复现 B：CLI 端，假工具 A/B 对照（证实假设 1）

- `fake_mcp.py`：一个 stdio MCP 服务器，两个工具：`sleep_plain(seconds)` 无注解（和我们的一样），`sleep_ro(seconds)` 带 `ToolAnnotations(readOnlyHint=True)`。每次调用把 `tool,start,end` 追加到日志。
- `fake_api.py`：本地 HTTP 服务器冒充 Messages API（SSE 流式）：第一轮回 N 个 `tool_use` 块（同一条 assistant 消息），收到 `tool_result` 后回文本 `end_turn`。这样同一轮多调用是确定的，不用真模型。
- `run_cli.sh TOOL N SECONDS`：用 venv 里打包的 CLI 跑 `claude -p go --mcp-config ... --strict-mcp-config --allowedTools mcp__fake__TOOL --permission-mode dontAsk --setting-sources "" --output-format stream-json`，`ANTHROPIC_BASE_URL` 指向假 API。

无注解：

```
$ bash .scratch/43-serial-mcp/run_cli.sh sleep_plain 3 3
cli exit 0, wall 12s
--- tool log: tool,start,end (s since server start)
sleep_plain,0.36,3.37
sleep_plain,3.45,6.45
sleep_plain,6.47,9.48
--- api log
api: /v1/messages?beta=true tools=29 -> tool_use x3
api: /v1/messages?beta=true tools=29 -> end_turn x1
```

`readOnlyHint=True`：

```
$ bash .scratch/43-serial-mcp/run_cli.sh sleep_ro 3 3
cli exit 0, wall 6s
--- tool log: tool,start,end (s since server start)
sleep_ro,0.21,3.21
sleep_ro,0.21,3.21
sleep_ro,0.21,3.21
```

同一个 CLI、同一个服务器、同一条含 3 个 `tool_use` 的消息：无注解时第 k 个在第 k−1 个结束后才开始（间隔 0.02–0.08 s，就是调度器本身的开销），有注解时三个同时开始。由此推断线上观察到的"13–20 s 一个"是 `verify_model_values` 自身的耗时，调度器只是不让它们重叠；本工单没有测 `verify_model_values` 的单次耗时（推断，未测）。

### 3.3 复现 C：CLI 端，真实服务器，注解只在内存里打上（验证修法）

`real_server.py DUCKDB plain|ro`：调 `build_server(DuckDBGateway(db), load_config("full"))` 得到真实服务器，`ro` 模式下遍历 `server._tool_manager._tools` 把每个工具的 `annotations` 设成 `ToolAnnotations(readOnlyHint=True)`（就是修法草稿会在 `src/` 里做的事，这里在 `src/` 之外做），并给 `query_database` 包一层计时。`run_cli_real.sh plain|ro N` 让假 API 回 4 个 `query_database`，SQL 是约 1.5–2.5 s 的 `range(150000000)` 聚合。

```
$ bash .scratch/43-serial-mcp/run_cli_real.sh plain 4
cli exit 0, wall 10s
--- query_database log: tool,start,end (s since server start)
query_database,0.30,2.87
query_database,2.93,4.39
query_database,4.40,6.04
query_database,6.06,7.45

$ bash .scratch/43-serial-mcp/run_cli_real.sh ro 4
cli exit 0, wall 4s
--- query_database log: tool,start,end (s since server start)
query_database,0.14,2.50
query_database,0.14,2.50
query_database,0.14,2.70
query_database,0.14,2.70
```

## 4. 修法草稿（不提交到 `src/`）

所有工具都只读（SQL 全走只读网关；`map_columns` 只读 workspace 里的文件），所以全部标 `readOnlyHint=True` 是如实的。mcp 2.3.0 的 `MCPServer.tool()` 接受 `annotations: ToolAnnotations | None`（`mcp/server/mcpserver/server.py:669-674`，`:730` 传给 `add_tool`）；`ToolAnnotations` 从 `mcp.types` 导入（定义在 `mcp_types/_types.py:1365`）。

`READ_ONLY` 不能放在 `mcp_server.py`：它导入 `context_tools` 和 `verifier_tools`，反向导入会循环。所以放一个新的小模块，三个文件都从它导入：

```diff
--- /dev/null
+++ b/src/data_agent/tool_annotations.py
@@
+"""Annotations the MCP tools declare to the client."""
+
+from mcp.types import ToolAnnotations
+
+# Every tool is read-only (SQL goes through the read-only gateway; map_columns only reads
+# workspace files). Claude Code runs an MCP tool concurrently with others in the same turn
+# only when it carries readOnlyHint; without it the calls of one turn run one at a time.
+READ_ONLY = ToolAnnotations(readOnlyHint=True)
```

然后 11 处装饰器都是同一行替换，加三处 import（`mcp_server.py:88,104,124,137`；`context_tools.py:180,202,218`；`verifier_tools.py:1380,1396,1442,1453`），以 `query_database` 为例：

```diff
--- a/src/data_agent/mcp_server.py
+++ b/src/data_agent/mcp_server.py
@@
 from data_agent.run_config import RunConfig
+from data_agent.tool_annotations import READ_ONLY
 from data_agent.verifier_tools import register_verifier_tools
@@
-    @server.tool()
+    @server.tool(annotations=READ_ONLY)
     def query_database(sql: str, row_limit: int = 1000) -> str:
```

配套测试：`build_server(...)` 后 `tools/list` 里每个工具 `annotations.readOnlyHint is True`（复现 A 的 `list_tools` 就是这个检查；现在输出全是 `None`，改完应全为 `True`）。

**预期效果。** 同一轮的一批 MCP 调用并发执行，墙钟从"每个耗时之和"降到"最慢一个的耗时加排队"。按审计里的样本：21 个 `verify_model_values` 各 13–20 s 共 340 s，并发受限于服务器 40 个线程和机器 CPU，估计降到几十秒（估算，未测）；4 个 `query_database` 一批从 4 倍降到约 1 倍（复现 C 测得）。`audit-harness.md` 第 2 条的 578 s 验证器墙钟里有多少是这个，要在一个任务上重测才知道。

**代价与风险。**

- 并发查询会同时打开多个 DuckDB 只读连接，CPU 和内存同时被多条查询占用；单条查询变慢（DuckDB 本身多线程，N 条并发时各自分到的核变少）。总墙钟仍应下降。复现 C 里 4 条并发每条 2.4–2.6 s，比串行时的单条 1.4–2.6 s 略慢。
- Postgres 运行不受益（§2.4 的锁）。要并行需要连接池或每语句一连接，这是另一个改动，不在本工单内。
- `dictionary_search` 这类长扫描（审计第 1 条）并发后会同时占住多个连接，`dbt run` 等写锁的概率上升；第 1 条的修复（有界扫描、可取消）应先做或同时做。
- `readOnlyHint` 同时影响权限（§2.1(c)）：换成 `plan`/auto 权限模式时只读工具会被自动放行；现在的 `dontAsk` 加显式允许不受影响。
- 不 safe 的工具会堵住队列里它后面的所有工具（§2.1(b) 的 `break`）。只要有一个工具漏标注解，同一批里它之后的调用都会等它。所以要全部标，并用测试锁住。

## 5. 未确认

- `mayOverlap` 的登记路径（`builtinToolsServedByMcp`）具体在哪种模式下打开；对我们不生效已由复现 B/C 证明。
- 线上 Linux 容器里 Claude Code 的调度规则和本地 macOS 二进制是否完全一致：同一版本 2.1.301 的同一份 JS，假定一致。
- 修后在真实任务上的实际提速数字。
