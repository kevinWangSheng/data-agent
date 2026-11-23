# 升级 claude-agent-sdk 0.2.168 → 0.2.187：8 处 CLI 2.1.301 行为在 2.1.330 上是否仍成立

日期 2026-11-23。只读取证：没有调模型，没有改代码，没有提交；没有读 `.env*`、`~/.config/data-agent/`、`~/.ssh`。

## 0. 取证来源与方法

- Claude Code 官方 CHANGELOG：`https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md`，取 2.1.302 到 2.1.330 全部条目（该范围内文件里共 25 个版本段、约 2450 条）。2.1.313、2.1.315、2.1.320、2.1.324 在该文件里没有段落，未确认它们是否发布过。下文引用的原文都是英文原句，版本号在前。
- SDK 的 CHANGELOG（`claude-agent-sdk-python` main 分支）只写到 0.2.178。GitHub Releases 页 0.2.179 到 0.2.187 的正文是空的（只有 PyPI 链接）。所以 0.2.179 到 0.2.187 没有官方文字说明，改为对比两个已安装的包源码（旧 `data-agent/.venv`，新 `data-agent-spike-otel/.venv`，除 `_bundled/` 外 `diff`）。这是原文级证据，但看不到变更动机。
- 两个 bundle：旧 CLI `--version` 输出 2.1.301，新输出 2.1.330（只执行了 `--version`）。字符串取证用 `python3 -I` 的 mmap 正则取 ≤330 字符的片段（`ugrep` 对长上下文正则报复杂度错误）。旧 bundle 里不少常量在字节码块里、可读 JS 在另一处，所以"旧里搜不到"不等于"旧里没有"。
- 额外证据：本机 `~/.claude/projects/*/*/subagents/agent-*.jsonl` 里有大量 2.1.330 写的真实子 agent transcript（只取了结构字段：type、message.id、content 块类型、stop_reason、output_tokens，没有看内容），用于第 3 条。

## 1. 结论表

| # | 位置 | 结论 | 一句话 |
|---|---|---|---|
| 1 | agent.py:127-129 effort 默认 | 无法判断，需实测 | changelog 无相反条目，解析链函数结构新旧相同，但"最后默认 high"这个常量没在 bundle 里定位到 |
| 2 | agent.py:132-135 环境变量优先 | 仍成立 | 新 bundle 里 `CLAUDE_CODE_EFFORT_LEVEL` 仍压过其它来源，2.1.323 changelog 明说"overrides" |
| 3 | agent.py:428-440 子 agent transcript | 有变化（解析逻辑不用改，注释和夹具过时） | 2.1.307 起每个 content 块行都带最终 usage 和 stop_reason，不再有"前几行 null/0" |
| 4 | agent.py:944-950 steered turn 的 result | 仍成立（有新增字段） | `total_cost_usd`/`modelUsage` 仍是累计；result 合并代码逐字相同；新增 @internal 的 `model_usage_since_last_result` |
| 5 | agent.py:1234-1242 窗口和压缩 | 有变化（环境变量仍有效，但注释里三个说法过时） | 不认识的模型默认窗口从 200k 变 1M；`AUTO_COMPACT_WINDOW` 现在认 `k`/`M`；`MAX_CONTEXT_TOKENS` 仍对 deepseek 类名生效；"窗口减 33,000"公式不变 |
| 6 | agent.py:1455-1470 `continue: false` | 仍成立（resume 路径有新代码，需实测一次） | `terminal_reason: "hook_stopped"` 仍在，被归为非错误；2.1.323 修了 `continue: false` 被 resume 重跑；2.1.330 bundle 新增 hook-stopped 批次的 resume 判断 |
| 7 | agent.py:1911-1920 cost-state | 仍成立 | `cost-state` 的 schema、last-wins、恢复函数、`--max-budget-usd` 检查在新旧 bundle 里相同 |
| 8 | test_turn_budget 开头：回合计数 | 仍成立（结构证据，不是实测） | 新旧 bundle 的"工具跑完后 turnCount+1，超过 maxTurns 就 max_turns_reached"代码相同 |

另外 `tests/test_harbor_agent.py:626`（`claude-agent-sdk==0.2.168`）、`tests/test_eval_atif.py:16-17`（`sdk_version`、`cli_version`）以及 `pyproject.toml:9`、`uv.lock` 要随升级改；wheel 内置 CLI 从约 250 MB 涨到约 364 MB（未压缩 bundle 文件大小，新 `claude` 363,855,808 字节，旧 250,540,840 字节），工单 20 的离线安装（wheel 114 MB 时下载就卡过）会受影响，未确认新 wheel 大小。

## 2. 逐条证据

### 2.1 第 1 条：未设 effort 时 CLI 发什么

changelog（2.1.302 到 2.1.330）里与 effort 相关的条目：

- 2.1.317："Fixed a project's `effortLevel` setting being ignored by sessions that an app starts ahead of time and then opens in the project, such as new sessions in the desktop app's Code tab"。我们 `setting_sources=["user"]`，不读项目设置，无影响。
- 2.1.317："Changed subagents and teammates on a different model from the main conversation to use that model's saved effort level or its default, instead of the session's effort level"。我们所有 agent 都用同一个 DeepSeek 模型（`CLAUDE_CODE_SUBAGENT_MODEL` 同值），按字面不触发。是否真不触发未实测。
- 2.1.316："Added `CLAUDE_CODE_SUBAGENT_EFFORT` to set the effort level subagents run at when nothing else names one, instead of inheriting the session's"。新变量，我们没设，所以子 agent 仍继承。
- 2.1.314："Fixed sessions failing turn after turn with "Invalid effort level" when an organization's admin caps a model's effort: Claude Code now lowers the effort, retries, and says which level it uses"。管理员上限场景，与我们无关。
- 2.1.312："Fixed the spinner, OpenTelemetry `effort` values and transcripts naming a newly picked effort level during a task before the model applied it; they now show the level in force"。展示层。
- 2.1.310："Fixed requests failing behind some gateways and compatible servers that reject the `effort-2025-11-24` beta value or mid-conversation system messages; Claude Code now retries without them"。**值得注意**：网关拒绝 effort beta 值时 CLI 改为去掉后重试。DeepSeek 端点是否被这条触发，未确认。

bundle 对比：

- 旧：`function Mzn(e,n){let r=X(e);return(r===void 0?n:void 0)??aBe(e)??r??Ye(e)??Ge(e)??ze(e)}`；新：`function dcn(e,o){let r=kX(e),f=q(r);return(f===void 0?o:void 0)??gvt(r)??f??Qe(r)??Je(e)??Ze(r)}`。链的形状相同（显式参数 → 环境/标志 → 设置 → 模型 `default_effort` → 末位兜底），变量名被压缩器改了。
- 末位兜底函数（旧 `ze`、新 `Ze`）在可读 JS 里取不到（压缩后的短名在 bundle 里重名，抓到的是别的函数）。所以"没设就是 `high`"这个常量新旧是否一致，没有直接证据。
- 两边都有 `max` 降级规则：`if(r==="max"&&!BRe(o))r="high"`（新）、`if(r==="max"&&!Jie(n))r="high"`（旧）。新增 `unknown --effort value ... ignoring it and using the default effort` 的提示串。我们只传 `low`/`high`/`max`，对非 Claude 模型名 `BRe` 的结果两边都没跑过。

结论：无法判断，需要实测。最小实测法（不花钱）：照工单 104 的做法，让 `data-agent run` 指向本地 HTTP 假端点，抓请求体看 `output_config.effort`（未设、设了 user settings 的 `effortLevel`、传 `--effort max` 三种）。`tests/test_effort_option.py` 的断言依赖这个常量。

### 2.2 第 2 条：`CLAUDE_CODE_EFFORT_LEVEL` 优先于 `--effort`

- 新 bundle：`function rk(){let e=s.CLAUDE_CODE_EFFORT_LEVEL;return e?.toLowerCase()==="unset"||e?.toLowerCase()==="auto"?null:AZ(e)}`，`function _we(e){let o=X();return o===null?void 0:o??e}`：环境变量有值就用它，否则才用传入值。旧 bundle 的 `hx()` 同构（`unset`/`auto` 返回 null）。
- 新 bundle 的提示串 `effort stays ... (set by CLAUDE_CODE_EFFORT_LEVEL)`，旧 bundle 的 `/effort` 路径同样有 "CLAUDE_CODE_EFFORT_LEVEL=... overrides effort this session"。
- 2.1.323："Fixed the model picker and `/model` showing a picked effort level that `CLAUDE_CODE_EFFORT_LEVEL` overrides"。
- 2.1.304："Fixed `/effort status`, `/effort ultracode` and the prompt's effort indicator showing a guessed effort level when `CLAUDE_CODE_EFFORT_LEVEL` is `auto` or `unset` and no level is sent"。说明 `auto`/`unset` 取值是"不发 effort"。我们的做法是"有 effort 就把变量设成同值"，不受影响。

结论：仍成立。

### 2.3 第 3 条：子 agent transcript 格式

- changelog 2.1.307："Fixed subagent transcripts recording placeholder token usage and an empty stop reason for streamed responses"。这正是 agent.py:432-436 注释描述的旧行为（"earlier ones have no output yet and a null stop_reason"）被修掉了。
- 2.1.323："Fixed subagent transcripts and large tool results being saved apart from the session's transcript after entering a worktree created by a `WorktreeCreate` hook or belonging to a nested repository"。我们的 CLI 不进 worktree，不触发；文件路径 `subagents/agent-<id>.jsonl` 在 2.1.330 的真实样本里仍是这个名字。
- 2.1.311："Fixed `claude -p` and SDK sessions saving transcript messages out of order or keeping messages that were taken back"。顺序问题，对"最后一行为准"的解析是利好。
- 实测样本（本机 2.1.330 真实 transcript，`entrypoint` 为 `cli` 和 `sdk-cli` 两种都看了）：同一个 `message.id` 仍然是"每个 content 块一行 assistant"（例如 `['thinking']` 一行、`['tool_use']` 一行），但**每一行**都带 `stop_reason: tool_use` 和相同的最终 `output_tokens`（例如 1161/1161、405/405）。仓库夹具 `tests/fixtures/subagent_transcript.jsonl`（2.1.301，`sdk-py`）里则是前几行 `stop_reason: None`、`output_tokens: 0`，只有最后一两行是最终值。
- 对 `transcript_usage`（agent.py:427-462）的影响：它按 `message.id` 取最后一行，再看最后一行的 `stop_reason` 是否非空，新旧格式下都给出相同结果；`TranscriptUnfinished` 重试分支在新格式下只会在 CLI 还没写完最后一行时触发。代码不用改，但 docstring（432-436 行）和夹具不再代表 2.1.330 的真实输出。
- 局限：样本不是 `sdk-py` 入口、也不是 DeepSeek 模型；`include_partial_messages` 打开时的流式路径是否与 2.1.307 的修复同一条路，没有直接证据。agent.py:451-456 的 `Spend` 注释说的是 SDK 的 `AssistantMessage`（流里的），不是 transcript，不受这条修复影响，但它是否变化未验证。

结论：有变化。解析逻辑仍正确；建议升级后用一次真实验证器子 agent 的 transcript 换掉夹具，并改注释。

### 2.4 第 4 条：steered turn 的 result

- changelog 里没有改 result 的 `usage`、`total_cost_usd`、`modelUsage`、`duration_api_ms` 语义的条目。相近条目：2.1.328 "Fixed the token counts on the spinner and for subagents counting a retried reply twice, missing replies that were not streamed, and standing still while Claude thinks"（只说界面里的计数）；2.1.325 "Changed `/cost`, `$.session.usage()`, `--max-budget-usd` and the cost and token metrics to count a mod's `$.model.complete` and `$.model.classify` calls"（mod 才有，我们没有 mod）。
- 新旧 bundle 里 result 的 schema 描述文字：`total_cost_usd` 仍写 "cumulative across turns in streaming-input sessions — each result carries the running total so far, so read the latest result rather than summing across results"；`modelUsage` 同样写 "Cumulative across turns in streaming-input sessions"。新版多了 "billed permission classifier calls and stopped or cut calls included, which maxBudgetUsd / --max-budget-usd therefore counts"。我们 `permission_mode="dontAsk"`，没有分类器调用，影响不大。
- result 合并函数新旧逐字相同：旧 `tdn(e,s)`、新 `TGr(e,r)`：`if(e.total_cost_usd!==void 0&&s.totalCostUsd<e.total_cost_usd)return{...e,...};return{...e,total_cost_usd:s.totalCostUsd,duration_api_ms:...,modelUsage:s.modelUsage,...s.usage!==void 0&&{usage:s.usage}...}`。
- 新增 schema 字段（`@internal`）：`model_usage_since_last_result`，说明写 "Per-model usage and cost of the calls this process counted since it wrote its previous result ... Sum it over a conversation's results instead of subtracting modelUsage totals"；同时有 `result_index`。Python SDK 的 `ResultMessage` 类型里没有这两个字段，要从 `ResultMessage.data` 之类的原始字段取，且标了 `@internal`，不建议依赖。
- 另一个新东西（SDK 侧 types.py）：`ResultMessage.user_message_uuid`、`user_message_uuids`，文档写 "a prompt sent while an injected turn is running can be answered inside that turn, and the result then carries the injected turn's origin ... Read with `receive_messages()`: `receive_response()` stops at the first result, which can be an injected turn's"。和 steering 有关，见 §4。

"input/cache/output tokens 只算本步"这半句，bundle 里没有找到直接字符串依据（只有 `usage:s.usage` 被覆盖的合并代码，代码相同）。

结论：仍成立（累计字段的语义没改，合并代码相同）；"本步 usage"那半句没有新证据，可以靠 `tests/test_conversation_turn.py` 之外再抓一次真实 steered 的 result 对比。

### 2.5 第 5 条：窗口、停止点、压缩

changelog：

- 2.1.323（最重要）："Changed the context window assumed for a model ID that Claude Code doesn't recognize from 200K to 1M; use `/autocompact` to compact earlier, or `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` to keep 200K"。
- 2.1.323："Changed sessions on other vendors' models with a smaller window: once full, they can fail every turn until `/clear`, so set `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` before they fill"。
- 2.1.325："Changed `CLAUDE_CODE_MAX_CONTEXT_TOKENS` to also set the context window of unrecognized model IDs that start with `claude-`, such as a gateway's `claude-sonnet-latest`"。
- 2.1.304："Fixed `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200k` silently setting a 100K window: the variable now accepts `k` and `M` suffixes like `--autocompact`, and warns when a value is out of range or ignored"。
- 2.1.316："Fixed `/model <id>` not warning when the model isn't recognized by this version and auto-compact will assume a 200K context window"（2.1.316 的这句仍说 200K，与 2.1.323 的 1M 默认矛盾，应以 2.1.323 为准；bundle 见下）。
- 2.1.312："Changed a subagent's `autoCompactWindow` to lower the window set by `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, where it had no effect before; it still never raises that window"。我们的验证器子 agent 如果定义了 `autoCompactWindow`，现在会生效；我们没设，未确认。
- 2.1.325："Fixed sessions on many third-party gateways and Foundry stopping with a context length error on every turn instead of compacting"。
- 2.1.311："Fixed standard-context sessions stopping with "Usage credits required for 1M context" when a request ran over 200K tokens; Claude Code now compacts the conversation and continues"。
- 2.1.310："Fixed compaction after a "Prompt is too long" error keeping too much of a conversation with dense text, such as base64 or SVG, so the next request could fail again"。
- 2.1.307："Fixed some "Autocompact is thrashing" errors in turns that read many PDF pages, images or other large tool results"。
- 2.1.306："Fixed an API error being replaced by "prompt is too long", and an unneeded compaction, in sessions with many MCP tools"。

bundle 对比：

- 窗口函数。旧（2.1.301）：
  `function RU(e){...if(Bk(e))return 1e6;let r=Man(e);if(r!==null)return r;let s=a.CLAUDE_CODE_MAX_CONTEXT_TOKENS;if(s!==void 0&&s>0&&xv(e))return s;return CXe}`，`xv(e)` 对"不是 `claude-` 开头的名字"返回真，所以 deepseek 类名吃 `MAX_CONTEXT_TOKENS`，否则落到 `CXe`（200k）。
  新（2.1.330）：
  `...if(!Ew(e))return bCt;let y=s.CLAUDE_CODE_MAX_CONTEXT_TOKENS;if(y!==void 0&&y>0)return y;return H1()?bCt:NW}`，其中 `spn=200000`，`H1()` 返回 `CLAUDE_CODE_DISABLE_1M_CONTEXT`，`NW=1e6`。即：不认识的模型，设了 `MAX_CONTEXT_TOKENS` 就用它（对 `claude-` 前缀的也生效了）；没设则 1M，除非 `DISABLE_1M_CONTEXT`。
  我们设了 `MAX_CONTEXT_TOKENS=1000000`，所以窗口数值仍是 1M，没有变化；agent.py:1237-1239 注释里"CLI 不认识模型、假定 200,000"这句在 2.1.330 上不再是默认行为（不设这个变量时，窗口变成 1M，而不是 200k）。
- 压缩阈值公式相同：旧 `function w8(e,n){let r=e-13000,...}`、新 `function SY(e,o){let r=e-13000,...}`；有效窗口都是 `window - min(maxOutputTokens, 20000)`（旧 `Mmr=20000`，新 `ZQr=20000`）。所以"窗口减 33,000 开始压缩"仍成立（假设 `maxOutputTokens>=20000`；本次没验证 DeepSeek 的 `maxOutputTokens`）。注意新 bundle 里另有 `Be=33000,lr=2*Be` 常量，但上下文是 `or=120000,Te=3,Ft=600000,Ae=1e4` 一串超时/重试常量，与压缩无关，不要误读。
- `AUTO_COMPACT_WINDOW` 解析：旧 bundle 里 `--autocompact`/设置用的 `V8t` 本来就认 `k`、`m`（`n.endsWith("k")?parseFloat(n)*1000`），2.1.301 的环境变量路径只吃纯数字；新 bundle 出现专门的校验提示串 `CLAUDE_CODE_AUTO_COMPACT_WINDOW="…"`、`Set it to a size of at least …, such as 200k or 200000, or unset it.`、`reads as N tokens, so Claude Code treats it as the … minimum`、`isn't a usable size, so Claude Code ignores it`，对应 2.1.304 的 changelog。结论：纯数字 `400000` 仍然有效，"只接受纯数字/不认 400k"的说法在 2.1.330 上不成立，但我们写纯数字是安全的。新增下限：低于最小值会被当最小值（"treats it as the … minimum"），最小值常量（提示串里的 `uF/1000` k）未取到，我们 400000 不会撞下限。
- 新增上下文：`CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT` 出现在两个 bundle 里（"restores the previous wait-for-the-API behavior"），属于"窗口强制"（我们 docs 里说的"估算到 177,000 就不发请求"）那套逻辑的开关；这个开关两边都在，177k 的停止点在 2.1.330 是否仍按 `window-23000` 之类计算，没有取到新代码，需实测。
- 新增 `tengu_auto_compact_put_off`、"autocompact: not compacting at … : no prompt since the last summary was under … tokens"（新 bundle 新增的串），说明压缩有了新的"推迟"规则；是否影响我们的 400k 窗口下的压缩时机，未确认。

结论：有变化。`MAX_CONTEXT_TOKENS=1000000` 与 `AUTO_COMPACT_WINDOW=400000` 仍然有效、数值语义不变；但 agent.py:1234-1242 注释里（a）"不认识就 200,000"（b）"177,000 停"（c）"只接受纯数字（拒 400k）"三个说法按 2.1.330 都过时或未验证，docs/research/2026-11-22-prompt-too-long.md 与 tests/test_context_window.py 里同样依赖 2.1.301 的数字，升级后需要用探针再确认一次（可参考工单 110 的做法）。另外 `CLAUDE_CODE_DISABLE_1M_CONTEXT` 不要在评测环境里被继承为 1：它在 `MAX_CONTEXT_TOKENS` 之前不起作用（我们设了值就直接用），但会影响其它路径；未确认。

### 2.6 第 6 条：hook `continue: false`

changelog：

- 2.1.323："Fixed `CLAUDE_CODE_RESUME_INTERRUPTED_TURN` re-running a turn that a hook's `continue: false` had stopped"。说明 `continue: false` 的语义（结束本轮）保留，且 resume 时不会重跑被它停下的轮次。
- 2.1.328："Changed managed `PostToolUse` hooks to no longer fire for calls refused before they ran: no log entry, rewrite, block or stop there; `PostToolBatch` gets most, not marked as refused"。限定 managed 设置里的 hook；我们的 hook 是 SDK 回调，不是 managed，按字面不受影响，但"被拒的调用不触发 PostToolUse"这个方向与第 6 条"被拒调用走 PostToolUseFailure"一致。
- 2.1.326："Changed a hook's `"decision": "block"` to hold in more cases, so a hook that always prints one may start blocking calls"。我们用的是 `continue_`，不用 `decision`，仓库里没有 `"decision"` 字段（grep 确认）。
- 2.1.319："Changed PostToolUse hooks to run, with up to 10 seconds to finish, on tool calls that finish after a new message interrupts the turn"。steering 场景下的新行为：新消息打断一轮后，在途工具的 PostToolUse 钩子仍会跑。对澄清工具的"一轮只问一次"的 `asked` 标志有影响的可能性：低，但未验证。
- 2.1.317："Added `onFailure: "block"` for Agent SDK hook callbacks"；SDK 0.2.177 增加 `HookMatcher.on_failure`，默认 `"continue"`，不设就和以前一致。

bundle：`terminal_reason` 枚举新旧都含 `"hook_stopped"`、`"stop_hook_prevented"`；`case"hook_stopped": ... return!1`（非错误）新旧相同。新 bundle 有新增的 resume 判断函数：`function eTt(e,o){...if(h.type==="user"&&y5(h)){if(r)return a("tengu_resume_hook_stopped_batch",{}),!0}...r||=h.type==="attachment"&&h.attachment.type==="hook_stopped_continuation"}`，以及 `Vhs`（只看 `hookEvent==="Stop"` 的 `hook_stopped_continuation`）。旧 bundle 没有 `tengu_resume_hook_stopped_batch`。作用推断：resume 时识别"被 hook 停下的批次"，避免当成被打断的轮次重跑。这与我们"澄清后用户回答再 resume"（工单 91、90）的流程直接相关，应实测一次。SDK 侧 `continue_` → `continue` 的转换没有在 diff 中出现，未变。

结论：仍成立；resume 一个以 `hook_stopped` 结束的会话这条路径，2.1.330 有新代码，建议升级后跑一次澄清 → resume 的真实流程验证。

### 2.7 第 7 条：resume 时恢复花费

- changelog 2.1.302："Fixed a cloud session's cost and usage restarting from zero after the session resumed in a new process"。云会话的事，与本地文件无关；说明"恢复成本"是被维护的行为。2.1.328 "a session that resumes in a new Claude config directory no longer loses its task list"（只针对 self-hosted runner）。
- bundle 里 `cost-state` 记录新旧相同：schema `{type:"cost-state",sessionId,totalCostUSD,totalAPIDuration,totalAPIDurationWithoutRetries,totalToolDuration,totalLinesAdded,totalLinesRemoved,totalDuration,startTime,modelUsage:{model:{inputTokens,outputTokens,thinkingTokens?,cacheReadInputTokens,...}}}`；合并规则 `"cost-state":"last-wins"`；不合 schema 的条目被丢弃（`it.type==="cost-state"&&!AQt().safeParse(it).success)z.add(o)`，旧 `_ae()`）；恢复函数 `function $be(e){let n=e.costState;if(!n)return rWo(),!1;return oWo({totalCostUSD:...})}`（旧）与 `aHe`（新）一致；预算检查 `rys`/`T6a`：`Reached maximum budget ($…)` 与 `Session cost is not a number ... refusing to continue under --max-budget-usd` 新旧相同；result 的 `total_cost_usd` 描述新旧都含 "a resumed or forked session continues from the total its transcript saved, when it has one"。
- 新增：result 多了 `modelCallCostUSD`（mod 的 model 调用，我们没有）。
- 注意：新 bundle 里 `cost-state` 在"保留哪些行"的表里是 `"cost-state":"ends"`（压缩/截断时保留尾部的规则），旧 bundle 里没找到同一张表（旧 bundle 的同类表里 `cost-state` 只出现 `last-wins`、`always`）。含义没读出来，可能影响很长会话压缩后文件里 `cost-state` 条目是否还在最后；我们的 `restored_cost`（agent.py:1911）取"最后一个"，仍应成立，但未实测。

结论：仍成立。

### 2.8 第 8 条：回合计数

- changelog 里没有改计数规则的条目。相关：2.1.304 "Fixed a plugin keeping a headless run going past `--max-turns`, Stop, or ended input by spawning subagents or re-running a finished one"（plugin 才触发）。
- bundle：旧 `let Ce=Jo+1,...;if(M&&Ce>M)...yield en({type:"max_turns_reached",maxTurns:M,turnCount:Ce},A)...{reason:"max_turns",turnCount:Ce}`（工具执行后的路径），新 `let Ve=tr+1,...;if(P&&Ve>P)...yield Ut({type:"max_turns_reached",maxTurns:P,turnCount:Ve},G)...{reason:"max_turns",turnCount:Ve}`；工具中断路径 `let C=Jo+1;if(M&&C>M)yield ...max_turns_reached` 与 `let v=tr+1;if(P&&v>P)yield ...` 同构；result 的 `error_max_turns` 文案 `Reached maximum number of turns (…)` 新旧相同；`CLAUDE_CODE_MAX_TURNS` 环境变量名两边都在。
- 这是对压缩代码的结构对比，不是对"有工具调用的主线程回复算一轮"的行为实测。子 agent 内的回合是否并入主线程计数，没有读。

结论：仍成立（结构相同），不是实测。

## 3. SDK 0.2.169 到 0.2.187（Python 包）

官方 CHANGELOG 到 0.2.178 为止；0.2.169 到 0.2.178 每个版本就是升 bundled CLI（2.1.305 到 2.1.318），外加：

- 0.2.170："Fixed parallel tool calls missing from session messages: `get_session_messages` and `get_session_messages_from_store` now return every tool call and result from turns that ran tools in parallel"。我们不读这两个函数的结果。（未确认：grep 了 src，没有看到使用。）
- 0.2.177：新增 `HookMatcher.on_failure`（`"continue"` 默认；设 `"block"` 才改变行为，且要求 CLI 支持，否则抛 `CLIConnectionError`）。
- 0.2.169：文档澄清 `include_hook_events` 不抑制 `SessionStart`/`Setup`。

0.2.179 到 0.2.187：没有官方文字。包源码 diff（只列有行为影响的）：

1. `ResultMessage`/`AssistantMessage` 新增 `user_message_uuid`、`user_message_uuids`（可选，缺省 None）；`origin` 的文档改成"只描述这一轮由什么启动"，并要求用 `receive_messages()` 配合每条消息的 `uuid` 来匹配 result。破坏性：无（新字段可选）。对 steering 工单 92 是新信息。
2. `ClaudeAgentOptions.settings` 的构造（`SubprocessCLITransport._build_settings_value`）改为：settings 文件读不出或 JSON 不合法时抛 `CLIConnectionError("Failed to load settings: ...")`，不再 warning 后继续；JSON 字符串路径照常。我们传的是合法 JSON 字符串（agent.py:1656），不受影响。但这是行为变化：以前读不出会静默丢弃。
3. 版本检查从"启动前另起 `claude -v` 子进程"改成"读 init 消息里的 `claude_code_version`"；`CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK` 仍有。无影响。
4. `PermissionUpdate.to_dict()` 对缺字段会抛 `ValueError`；`can_use_tool` 的 `updated_permissions` 转换失败时整批丢弃并 warning。我们没用 `can_use_tool` 更新权限（未全面 grep，只查了 `settings=`/`sandbox=`/`origin`）。
5. `PermissionUpdate` 的 `rule_content=None` 现在不发 `ruleContent`；`destination=None` 发 `"session"`。同上。
6. 新增 `_client_app.py` 与 `CLAUDE_AGENT_SDK_CLIENT_APP`/`CLAUDE_AGENT_SDK_DERIVED_CLIENT_APP` 环境变量；`DERIVED_CLIENT_APP_MINIMUM_CLAUDE_CODE_VERSION = "999.0.0"`，即当前不会派生任何标签；只有显式 `options.client_app` 才会设变量。对我们无影响。
7. Windows 下 CLI 查找逻辑重写（只影响 Windows）。
8. Requires-Dist 与旧版相同（anyio、jsonschema、`mcp>=1.23,<3`、sniffio），`uv.lock` 的第三方依赖应不动；`opentelemetry` 只是 `otel` extra。

结论：未发现破坏性变更。最需要看的是 #1（steering）和 #2（settings 加载失败变抛错）。

## 4. 8 条之外、同样依赖 2.1.301 的东西（升级时顺带看）

这些是从 changelog 里挑出来的、对我们当前配置有潜在影响的条目，不在原 8 条里，没有实测：

- 2.1.302："Changed fork subagents (`subagent_type: "fork"`) to be on by default in `claude -p`, the Agent SDK, the desktop app and VS Code ... `CLAUDE_CODE_FORK_SUBAGENT=0` turns them off"；同版本："a subagent's own Agent calls in `claude -p`, the Agent SDK, the desktop app and VS Code now run in the foreground by default"；2.1.328："Changed subagents in `claude -p`, the Agent SDK, desktop and VS Code to wait for their own subagents by default when `CLAUDE_CODE_FORK_SUBAGENT=0` is set"。我们设了 `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`，验证器子 agent 的形态（agent.py:1234-1236 的注释）和 transcript 路径要在升级后核一次，尤其是 fork 子 agent 是否会出现在 `subagents/` 里被 `transcript_usage` 读到。
- 2.1.302："Changed `claude -p` and Agent SDK sessions with no permission mode to start in `default` mode on third-party providers"。我们显式 `permission_mode="dontAsk"`，不受影响。
- 2.1.328："Changed Claude Desktop and Agent SDK sessions on third-party providers or with telemetry or feature flags off to end the turn when closed with a question open, instead of continuing without an answer"。针对 `AskUserQuestion` 类场景；我们的 clarification 是自定义 MCP 工具 + PostToolUse hook，不一定走这条，未验证。
- 2.1.317："Fixed `claude -p` run inside a cloud or self-hosted runner session sharing that session's ID ..." 与新变量 `CLAUDE_CODE_DISABLE_NESTED_SESSION_ID`：如果在另一个 Claude Code 会话里启动评测，子进程现在用自己的 session id（以前是共享）。我们的 worktree 工单 agent 就是在 Claude Code 里跑 `data-agent`，所以**第 7 条的 session 文件路径和 resume** 需要在这种嵌套环境下核一次。2.1.317 的描述限定 cloud/self-hosted runner 会话，本地普通会话是否也受影响，未确认。
- 工单 43（串行 MCP 调用）的结论依赖 "Claude Code 2.1.301 的工具队列只在……都并发安全时才并行"；changelog 里没有找到相关条目，bundle 没查，未确认。
- 工单 92（steer-stdin）里的"race probe""CLI 行为的取证"都是 2.1.301 上做的，且 SDK 新增了 `user_message_uuid(s)`，与 `receive_response()` 的说明相关；建议升级后重跑这两个探针。

## 5. 未确认项清单

1. 第 1 条：未设 effort 时最终兜底值是否仍为 `high`（需要抓请求体）。
2. 第 3 条：`sdk-py` 入口 + DeepSeek + 流式时的 transcript 是否和本机 `cli`/`sdk-cli` 样本一致；`Spend`（agent.py:451）注释里的"逐块 AssistantMessage 用量从响应开始、无输出"是否变。
3. 第 4 条："input/cache/output 只算本步"的新证据没有，需要抓一次 steered result。
4. 第 5 条：177,000 停止点、DeepSeek 的 `maxOutputTokens`（决定是 33,000 还是更小）、新的"压缩推迟"规则；最小窗口常量。
5. 第 6 条：`hook_stopped` 结束的会话 resume 的新代码路径。
6. 第 7 条：嵌套会话下的 session 文件路径；`"cost-state":"ends"` 的含义。
7. 第 8 条：行为实测（目前只有代码结构对比）。
8. SDK 0.2.179 到 0.2.187 没有官方 changelog，上面第 3 节只来自本机两个已安装包的源码 diff。
9. 新 wheel 的下载大小和 `uv.lock` 哈希（未取）。

## 6. 升级时需要改的明确位置

- `pyproject.toml:9`、`uv.lock`、`tests/test_harbor_agent.py:626`、`tests/test_eval_atif.py:16-17`（`sdk_version`、`cli_version`）。
- 注释里写了 2.1.301 的位置：agent.py:129、132、432、946、1237-1239、1913；以及 `tests/test_turn_budget.py` 开头；按第 2 节的结论逐条改。
- 夹具 `tests/fixtures/subagent_transcript.jsonl`：升级后用 2.1.330 的真实输出替换，并同步改 docstring。
