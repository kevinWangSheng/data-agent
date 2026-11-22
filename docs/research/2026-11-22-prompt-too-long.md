# trial 为什么撞上 "Prompt is too long"，压缩为什么从没触发（工单 109）

日期：2026-11-22。只诊断，不改 `src/`、`evals/`、`skills/`。没有调用模型，花费 $0。

## 结论

**"Prompt is too long" 不是 DeepSeek 返回的，是 Claude Code CLI 在发请求之前自己拦下的。原因有两个，叠在一起：**

1. **窗口算小了。** CLI 2.1.301 不认识 `deepseek-flash` 这个模型名，按 200,000 token 的上下文窗口算（不是 1M）。会话估算值达到 200,000 − 20,000 − 3,000 = **177,000** token 时，CLI 不发请求，直接以 `terminal_reason: blocking_limit` 结束（§2）。DeepSeek 官方文档写的 `deepseek-flash` 上下文长度是 1M（§3）。
2. **压缩被关了。** 评测服务器的 Claude 用户设置里有 `autoCompactEnabled: false`，我们的运行又用 `setting_sources=["user"]` 读用户设置（`src/data_agent/agent.py:1643`）。自动压缩本来会在 167,000 token 处触发，早于 177,000 的拦截线；关掉之后，会话一路涨到拦截线（§4）。

所以这**是**上下文管理的配置问题，不是接口偶发错误。修法只改设置，不用自己写压缩或截断（§5）。

四个问题的答案：

| # | 问题 | 答案 | 状态 |
|---|---|---|---|
| 1 | dbt 那边有没有 | 没有。服务器上 dbt 的 2,042 个事件文件里 0 次；DAB 的 2,830 个里 46 次（去掉 `f19*` 提交包里的副本是 37 次） | 已核实 |
| 2 | 失败时请求多大、谁拒的 | 38 个失败会话里，最后一次被接受的请求是 141,532–180,212 输入 token（中位数 171,472）；下一次请求被 CLI 在本地拦下，没有发给 DeepSeek | 已核实 |
| 3 | `deepseek-flash` 的真实窗口 | 官方价格页写 1M；我们实际被接受过的最大请求是 180,212 token。180k 到 1M 之间没有实测 | 文档已核实，没有实测 |
| 4 | 为什么从没压缩 | 服务器用户设置把自动压缩关了；2,004 个 transcript 里 0 次压缩 | 已核实；谁在 2026-10-20 写的这个设置未确认 |

## 1. dbt 那边有没有（问题 1）

统计脚本遍历服务器 `runs/` 下所有事件文件：DAB 的 `events.jsonl`，以及 dbt（harbor）trial 的 `agent/events/*.jsonl`（`evals/harbor.py:72-74`）。按运行、配置数 `result` 事件的 `terminal_reason`，以及 `text` 事件里的 "Prompt is too long"。只输出汇总，不输出题目名。

命令（脚本在 scratchpad，不提交；通过 `python3 -c` 传到服务器，不在服务器上写文件）：

```bash
scripts/da-remote exec -- python3 -c "$(cat count_events.py)" runs
```

输出（汇总部分原文）：

```text
--- totals by kind (all runs, including rows not printed above)
dab   files=2830  results=2830  blocking_limit=46  files_with_blocking=46  prompt_too_long_reason=0  ptl_text=46
dbt   files=2042  results=2036  blocking_limit=0   files_with_blocking=0   prompt_too_long_reason=0  ptl_text=0
--- dab runs with zero hits (count): 178
--- terminal_reason by kind
dab {'completed': 2742, 'blocking_limit': 46, 'max_turns': 27, 'None': 9, 'budget_exhausted': 6}
dbt {'completed': 1794, 'api_error': 240, 'max_turns': 1, 'deadline': 1}
```

dbt 按配置（开发集、留出集、data-eng-bench 的 `f17` 都在内，共 75 个"运行 × 配置"；留出集只有这里的汇总数字）：

| 配置 | 运行数 | 事件文件 | `blocking_limit` | "Prompt is too long" |
|---|---|---|---|---|
| full | 45 | 703 | 0 | 0 |
| no-verifiers | 10 | 604 | 0 | 0 |
| baseline | 12 | 373 | 0 | 0 |
| no-context | 4 | 181 | 0 | 0 |
| no-skill | 4 | 181 | 0 | 0 |

DAB 有命中的运行（模型都是 `deepseek-flash`，CLI 都是 2.1.301）：

| 运行 | 配置 | trial 数 | `blocking_limit` |
|---|---|---|---|
| `f19c-dab` / `f19b-dab` / `f19-dab`（提交包，trace 是 `f18*` 的副本） | no-context | 270 / 270 / 270 | 5 / 3 / 1 |
| `f18c-no-context-agnews`、`-stockmarket` | no-context | 20、25 | 3、2 |
| `f18b-baseline-agnews` | baseline | 20 | 3 |
| `f18b-no-context-agnews`、`-stockmarket` | no-context | 20、25 | 2、1 |
| `f18-baseline-agnews`、`-patents`、`-stockmarket` | baseline | 20、15、25 | 1、1、2 |
| `f18-no-context-agnews` | no-context | 20 | 1 |
| `e104-default-agnews-f`、`e104-max-agnews-f` | no-context | 12、12 | 3、3 |
| `e104-max-crmarenapro-f`、`-deps_dev_v1-f`、`-stockmarket-f` | no-context | 12、6、9 | 1、1、1 |
| `dabtrial2-baseline`、`dabtrial2-no-context`、`dabtrial-no-context` | baseline、no-context | 17、17、17 | 2、1、1 |
| `f18t-baseline-cve`、`f18t-no-context-cve`、`f18t-no-context-krama` | baseline、no-context | 1、1、1 | 1、1、1 |
| `t100-agnews` | no-context-t100 | 3 | 2 |
| `t61-krama`、`v105-default-agnews-f`、`v18u-no-context-agnews` | no-context | 1、12、6 | 1、1、1 |

合计 46；去掉 `f19*` 三个提交包里的 9 个副本是 37。数据集集中在 agnews、stockmarket、cve、krama。

两点说明：

- **事件文件看不出有没有压缩。** 事件流只记录 SDK 的 `init` 系统消息（`src/data_agent/agent.py:875`、`:1745`、`:1798`），压缩边界消息被丢掉了。"没有一次压缩"的证据来自 CLI transcript（§2），而 transcript 只有 DAB 的。
- **dbt 为什么是 0，只能推断。** dbt trial 在容器里跑，容器里的 Claude 配置目录不是服务器的 `/root/.claude`（`evals/harbor_agent.py` 里没有任何对 `.claude` 或 `CLAUDE_CONFIG_DIR` 的处理）。容器里有没有 `autoCompactEnabled: false`、dbt trial 里有没有发生过压缩，都**未确认**：容器销毁后 transcript 不在了。dbt 的 240 次 `api_error` 和本工单无关，没有查。

## 2. 失败时的请求多大、谁拒的（问题 2）

### 2.1 是 CLI 拦的，不是 DeepSeek 拒的（已核实）

打包的 CLI：主 checkout 的 `.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude`（`claude-agent-sdk` 0.2.168，CLI 2.1.301；JS 以文本内嵌，下面的数字是字节偏移）。

**(a) 拦截点**（偏移 212710131，主循环里，在发请求之前）：

```js
let gi=zp()&&CL(),eu=gi&&(!hue(Tt,y.options.autoCompactWindow)||yHn(h,Tt,y.options.autoCompactWindow));
if(At.kind!=="compacted"&&h!=="compact"&&!eu){
  if(yue(Sf(P,Tt.bytesPerToken)-Tn,Tt,y.options.autoCompactWindow).level==="blocked"){
    let I=yBe(h),se=At.kind==="failed"?At.compactFailure:void 0;
    if(!I&&gi&&ja(Tt,y.options.autoCompactWindow,h,se));
    else{ ...
      let X=Hs({content:Edo(se)??K$,error:"invalid_request",now:A.now,uuid:A.uuid});
      return yield X,go(y,h,X),{reason:"blocking_limit"}}}}
```

`K$="Prompt is too long"`（偏移 205217401）。这段代码自己造了一条 `error:"invalid_request"`、正文为 "Prompt is too long" 的消息并返回 `blocking_limit`，没有网络请求。

**(b) 事件里的字段对得上。** 例子 `runs/f19c-dab/pkg/traces/agnews/query3/run1/events.jsonl`：

```json
{"seq": 171, "type": "error", "source": "model", "error": "invalid_request"}
{"seq": 173, "type": "result", "subtype": "success", "is_error": true, "num_turns": 42, "terminal_reason": "blocking_limit", "api_error_status": null, "api_error": null, ...}
```

`api_error_status` 和 `api_error` 都是 `null`：没有 HTTP 状态码。接口真的拒绝时，CLI 用的是另一个 `terminal_reason`，`prompt_too_long`（枚举在偏移 197142525）；服务器全部事件里它出现 0 次（§1 输出的 `prompt_too_long_reason=0`）。

**(c) 拦截线怎么算**（偏移 205192500–205201400）：

```js
function xze(e,n,r,s=n,h){let y=h??w8(n,r),b=r.enabled?y:n, ... q=M!==void 0&&!isNaN(M)&&M>0?M:s-3000, ...
  if(e>=q)return{level:"blocked",pctLeft:V};
  if(r.enabled&&e>=y)return{level:"compact",pctLeft:V}; ...}
function Dmr(e){let n=Math.min(e.maxOutputTokens,Mmr);return e.contextWindow-n}   // Mmr=20000
function w8(e,n){let r=e-13000, ...}
```

拦截线 = `contextWindow − min(maxOutputTokens, 20000) − 3000`。压缩线（开着压缩时）= `窗口 − min(maxOutputTokens, 20000) − 13000`。

**(d) 不认识的模型，窗口是 200,000**（偏移 198277130–198279600）：

```js
var CXe=200000,QG=200000,Rv=32000,CU=128000;
function RU(e){let n=MHr(e);if(n!==void 0)return Man(e)??n.believed;if(Bk(e))return 1e6;
  let r=Man(e);if(r!==null)return r;
  let s=a.CLAUDE_CODE_MAX_CONTEXT_TOKENS;if(s!==void 0&&s>0&&xv(e))return s;
  return CXe}
```

`deepseek-flash` 不在 CLI 的模型表里（`MHr` 返回 `undefined`），名字没有 `[1m]` 后缀，我们也没设 `CLAUDE_CODE_MAX_CONTEXT_TOKENS`，所以落到最后一行 `CXe` = 200,000。`maxOutputTokens` 对不认识的模型默认 32,000（`Vae`，偏移 198280188：`else n=Rv,r=CU`）。

代入：拦截线 = 200,000 − 20,000 − 3,000 = **177,000**；压缩线 = 200,000 − 20,000 − 13,000 = **167,000**。

**(e) 估算值怎么来**（偏移 205184342）：

```js
function Sf(e,n){let r=kze(e);if(!r)return Hg(e,n);return r.tokens+Hg(e.slice(r.anchorIndex+1),n)}
```

最近一次接口返回的 usage，加上之后新增消息按"字符数 ÷ `bytesPerToken`"的估算。`bytesPerToken` 对不在名单里的模型是 3（`uh`，偏移 198267112），所以新增的工具结果按 3 个字符 1 token 算，偏保守。

### 2.2 失败前最后一次请求的大小（已核实，来自 CLI transcript）

服务器的 `/root/.claude/projects/` 下有 2,002 个目录，都是 DAB trial 的工作目录。脚本读每个 transcript 里主线程 assistant 消息的 `usage`（按消息 id 去重），输入合计 = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`。

```bash
scripts/da-remote exec -- python3 -c "$(cat transcripts.py)"
```

```text
sessions=2004 with_compaction=0 with_prompt_too_long=38
assistant message models: {'deepseek-flash': 105102}
max per-call input tokens across sessions:  {50: 39061, 90: 114695, 99: 172293} max 180212
sessions whose largest accepted request >= 100000: 274
sessions whose largest accepted request >= 150000: 72
sessions whose largest accepted request >= 167000: 36
sessions whose largest accepted request >= 177000: 2
sessions whose largest accepted request >= 180000: 1
sessions whose largest accepted request >= 200000: 0
failing sessions: ...
last accepted input: min 141532 median 171472 max 180212
last input+output: min 163484 median 177480 max 192277
```

38 个失败会话的文本全部是不带后缀的 "Prompt is too long"。几个例子（DAB 没有留出集）：

| 会话 | 接口调用次数 | 最后一次被接受的输入 token | 那次的输出 token | 之后新增字符 |
|---|---|---|---|---|
| `f18c-no-context-agnews` query3 run1（工单里的例子） | 16 | 170,209 | 13,326 | 49,898 |
| `f18c-no-context-agnews` query3 run2 | 14 | 141,532 | 21,952 | 41,034 |
| `f18-baseline-patents` query1 run4 | 40 | 176,471 | 547 | 2,817 |
| `v105-default-agnews-f` query3 run0 | 33 | 180,212 | 11,629 | 24,302 |

拿工单里的例子核对：170,209（输入）+ 13,326（输出）= 183,535，已经超过 177,000；再加上两个约 8,400 字符的工具结果。下一次请求被拦。最小的一例（141,532 + 21,952 = 163,484）加上 41,034 个字符 ÷ 3 ≈ 13,700，约 177,200，也刚过线。

对齐会话的"对不上"（42 轮累计 95 万 token，最后一次只有几万）现在对上了：`num_turns: 42` 不是接口调用次数，这个 trial 只有 16 次接口调用；每次的输入从几千涨到 170,209，累计 950,830。

38（transcript）和 37（事件，去掉副本）差 1：有的运行目录被同名重跑覆盖，transcript 还留着（如 `v105-default-agnews-f` 事件里 1 次、transcript 里 2 次）。

## 3. `deepseek-flash` 的真实窗口（问题 3）

**文档原文**（2026-11-22 由 web-fetch 子 agent 读取；DeepSeek 页面的引文是短引文加转述）：

- https://api-docs.deepseek.com/quick_start/pricing ，"Model Details" 表有两列，`deepseek-flash` 和 `deepseek-v4-pro`。"CONTEXT LENGTH" 一行只有一个值 **"1M"**，跨两列合并；"MAX OUTPUT" 一行是 "MAXIMUM: 384K"，同样合并。脚注 (1)：模型名用 `deepseek-flash`，当前由 DeepSeek-V4.1-Flash-1017 提供服务。
- https://api-docs.deepseek.com/guides/anthropic_api ：没有上下文长度的数字，没有"输入过长"的错误码。
- https://api-docs.deepseek.com/quick_start/error_codes ：没有"上下文过长"的条目（只有 400 格式错误、422 参数错误；413 是上传文件过大）。

**我们自己的数据**（已核实）：105,102 次接口调用里，DeepSeek 接受过的最大请求是 180,212 输入 token；没有一次被接口以"过长"拒绝（`prompt_too_long` 0 次）。

**判断**：文档写的是 1M，对 `deepseek-flash` 适用（表里那一行跨两列）。Anthropic 兼容接口是否同样给到 1M，文档没有单独写，180k 以上我们没有数据，这一段**未实测**。按工单的停止条件，这一点不需要花钱实测就能给出方案：方案里把自动压缩留作兜底，接口如果在 1M 之前拒绝，CLI 会收到真正的 `prompt_too_long` 并走被动压缩（§5 的风险 2）。

## 4. 为什么从没压缩（问题 4）

**(a) 服务器的用户设置把自动压缩关了**（已核实）：

```bash
scripts/da-remote exec -- bash -c 'cd ~/.claude; grep -o "\"autoCompact[A-Za-z]*\"[^,}]*" settings.json ~/.claude.json'
```

```text
settings.json:"autoCompactEnabled": false
/root/.claude.json:"autoCompactEnabled": false
```

`settings.json` 只有 `autoCompactEnabled` 和 `theme` 两个键，修改时间 2026-10-20 19:40（UTC），和 `.claude.json`、`.credentials.json` 同一分钟。仓库里没有任何地方写这个键（`grep -rn autoCompact .` 无结果）。**谁写的、为什么写，未确认**；时间上看是那天在服务器上装 Claude Code 时设的。本机的 `~/.claude/settings.json` 和 `~/.claude.json` 里没有这个键。

**(b) 这个设置对我们的运行生效**（已核实）：`src/data_agent/agent.py:1643` 是 `setting_sources=["user"]`，评测不设 `CLAUDE_CONFIG_DIR`，所以 CLI 读 `/root/.claude/settings.json`。

**(c) CLI 里这个开关管什么**（偏移 199628926、199160294）：

```js
function ih(){return Boolean(Fe(process.env.DISABLE_COMPACT)||a.DISABLE_AUTO_COMPACT)}
function zp(){if(ih())return!1;return ks("autoCompactEnabled",!0).value}
```

`ks` 从优先级高到低查各个设置来源，都没有时再看 `~/.claude.json`（`legacyGlobalConfig`），最后才用默认值 `true`。

`zp()` 为假时：

- 主动压缩不跑。`O1r`（偏移 206968649）：`if(!zp())return!1`，返回 `not_needed`。
- 被动压缩（接口报过长后再压）也不跑。`vdo`（偏移 206889582）的条件里有 `zp()&&CL()`。
- 拦截照常。§2.1(a) 里 `gi=zp()&&CL()` 为假，`eu` 为假，进入 `blocked` 判断；`!I&&gi&&ja(...)` 这个放行分支也因为 `gi` 为假而走不到。

**(d) 如果压缩开着会怎样**（源码推断，没有运行验证）：对不认识的模型，窗口来源是 `unknown-model`、窗口 200,000（`dio`，偏移 205197495：`zp()&&!a.CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT&&fqt(e,b)`）。估算值到 167,000 就压缩，早于 177,000 的拦截线。也就是说，默认设置下这 46 次不会以 `blocking_limit` 结束，而是被压缩一次后继续。

**(e) 文档和源码一致**。https://code.claude.com/docs/en/model-config （"Default auto-compact thresholds"；这一段来自 web-fetch 的摘要，不是逐字核对过的原文）："Sessions on a model ID Claude Code doesn't recognize, such as an LLM gateway alias, compact at the context window Claude Code assumes for the ID"。同页 "Pin models for third-party deployments"（摘要）："Before v2.1.323, Claude Code assumed 200K by default, so on those versions append `[1m]` to the ID if the model accepts 1M." 我们是 2.1.301，在 2.1.323 之前，和源码里的 `CXe=200000` 一致。工单 Context 里"不认识的模型名按 1M 上下文算"说的是 2.1.323 及以后的版本，对我们的版本不成立。

## 5. 修复方案（草稿，不提交到 `src/`）

### 5.1 推荐

在 `sdk_env()`（`src/data_agent/agent.py:1220-1237`）里加两个变量，再把服务器用户设置里的 `autoCompactEnabled: false` 去掉：

| 改动 | 取值 | 改哪里 | 作用 |
|---|---|---|---|
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | `1000000` | `sdk_env()` 返回的字典，`agent.py:1222-1237` | 把 CLI 认为的窗口从 200,000 改成 DeepSeek 文档写的 1M。拦截线从 177,000 变成 977,000 |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | 待和人对齐，建议 `400000` | 同上 | 压缩在 `窗口 − 33,000` 处触发（400,000 时是 367,000），不等到 967,000 |
| 打开自动压缩 | 删掉 `autoCompactEnabled: false` | 服务器 `/root/.claude/settings.json` 和 `/root/.claude.json`（环境改动，不在仓库里） | 压缩和被动压缩都恢复，作为兜底 |

依据：

- `CLAUDE_CODE_MAX_CONTEXT_TOKENS` 对我们生效的路径是 §2.1(d) 的 `RU`：模型不认识、变量大于 0、`xv(e)` 为真（名字不以 `claude-` 开头，偏移 198277672）时直接返回它。这条路径不看 `zp()`，所以**单是这一个变量就能消掉这 46 次拦截**，哪怕压缩仍然关着。文档（https://code.claude.com/docs/en/env-vars ，摘要）："Override the context window size Claude Code assumes for the active model... Use this when routing to a model through `ANTHROPIC_BASE_URL` whose context window does not match the built-in size for its name"。
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` 的原文（同页，逐字）："Set the auto-compact window, which is how full the context gets before compaction, to a token count of `100000` or more. Suffixed values such as `200k` require v2.1.304 or later." 我们是 2.1.301，**必须写纯数字**，不能写 `400k`。源码（`TS`，偏移 205196840）：取值范围 100,000–1,000,000，再和模型窗口取小。它只在压缩开着时有用。
- 400,000 这个数是建议，不是结论：2,004 个会话里单次请求最大值的 P99 是 172,293，超过 150,000 的有 72 个（3.6%）。窗口放到 400,000，现有 trial 在 50 轮上限内基本碰不到压缩，压缩只兜住失控的会话。

预期效果：这 46 个 trial 不再以 `blocking_limit` 结束，会继续跑到给出答案、`max_turns` 或预算上限。**它们会不会答对，不知道**；现在这些 trial 的答案栏是报错文字，必然算错。

代价：这些 trial 会多跑若干轮，每轮带 17 万以上的上下文。按 DeepSeek 价格页，`deepseek-flash` 缓存命中每百万 token $0.003–$0.006，未命中 $0.15–$0.3；上下文大部分命中缓存，单个 trial 多出的钱在几美分量级（推断，没有实测）。

### 5.2 不推荐的做法

- **只打开压缩，窗口留在 200,000**：能消掉拦截，但会在 167,000 处用 `deepseek-flash` 做摘要，而模型的真实窗口还有 80% 没用。按现有数据会有约 36 个会话（1.8%）被压缩，丢任务细节的风险白担了。
- **`CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1`**：只是不按假定窗口主动压缩，CLI 认为的窗口还是 200,000；压缩关着时拦截线不变，解决不了问题。
- **`CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE`**：源码里有（`whe`，偏移 205200951，变量名 `testBlockingOverride`），官方文档里没找到，是测试用的，不用。
- **模型名加 `[1m]` 后缀**：文档给 2.1.323 之前版本的建议，但模型名会原样发给 DeepSeek；DeepSeek 对不以 `claude` 开头的未知模型名返回 400（anthropic_api 页）。CLI 发请求前会不会去掉后缀，未确认。不如环境变量直接。

### 5.3 风险

1. **压缩后丢任务细节。** 压缩用 `deepseek-flash` 把历史写成摘要，表名、已经查到的中间结果、题目里的约束都可能丢。窗口设到 400,000 就是为了让它尽量不触发。dbt 任务的影响：dbt trial 现在一次都没撞线，容器里的设置和是否压缩过都未确认；`sdk_env()` 是 DAB 和 dbt 共用的，改它会同时改 dbt 的窗口。dbt 的会话如果现在在 167,000 处被默默压缩过，改完之后会变成不压缩，行为会变，要在验收里看。
2. **DeepSeek 在 1M 之前就拒绝。** Anthropic 兼容接口的上限文档没有单独写。那时 CLI 收到真正的过长错误，走被动压缩（要求压缩开着，所以方案里一起打开）；压缩也失败时以 `prompt_too_long` 结束，不比现在差。
3. **上下文长了之后模型变差或变慢。** 17 万 token 以上的会话，`deepseek-flash` 的表现没有数据。这些 trial 多数是在反复读大段文本（agnews 的例子里，一半上下文是 8,000 多字符一条的工具结果），继续跑未必收敛，可能只是把 `blocking_limit` 换成 `max_turns`。
4. **依赖服务器环境。** 压缩开关在仓库外面，换一台机器或重装就可能变。更稳的做法是让运行自己声明这个设置（SDK 的 `settings` 选项优先级高于用户设置，`ks` 按来源优先级查找），但那是改 `ClaudeAgentOptions`，不是只改 `sdk_env`，而且 SDK 模式下来源的实际顺序没有验证，留给修复工单定。
5. **会改变已发布分数的口径。** `f19c-dab` 是已提交的结果，里面 5 个 trial 因此算错。修复后重跑的分数不能和它直接比，要写明。

### 5.4 什么证据会推翻这个方案

- 加了 `CLAUDE_CODE_MAX_CONTEXT_TOKENS=1000000` 之后，同一批题仍然出现 `blocking_limit`：说明 §2.1(d) 的路径读错了（例如 `UBt()` 那个账户锁存把窗口压回 200,000，偏移 198278323 的 `Zf`；我们不用 Anthropic 账户，推断它为假，没有验证）。
- 出现 `terminal_reason: prompt_too_long`、带 HTTP 状态码：说明 DeepSeek 的 Anthropic 接口上限低于 1M，窗口要按实际拒绝点下调。
- 这批 trial 修复后正确率没有变化、只是从撞线变成跑满轮数：说明问题主要不在窗口，而在 agent 反复读大段文本的行为，该查的是工具返回的大小和 Skill 的读法。

### 5.5 验收时要看的（给修复工单）

- 事件流现在不记录压缩（§1）。要判断修复后有没有压缩、压了几次，得让事件流记下压缩边界消息，或者读 transcript。
- 用 §1、§2 的两个脚本对重跑结果再数一遍：`blocking_limit` 应为 0，`with_compaction` 和单次请求最大值的分布要一起报。

## 6. 没有查清的

- 服务器上的 `autoCompactEnabled: false` 是谁、为什么设的。
- dbt 容器里的 Claude 设置，以及 dbt trial 有没有发生过压缩。
- DeepSeek 的 Anthropic 兼容接口在 180k–1M 之间是否真的都接受（没有实测，要花钱）。
- 官方文档里 `CLAUDE_CODE_MAX_CONTEXT_TOKENS`、`CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT` 和 model-config 页"Correct the window for a gateway or custom model ID"一节的引文来自 web-fetch 的摘要，页面太长没有逐字读到；`CLAUDE_CODE_AUTO_COMPACT_WINDOW`、`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`、`DISABLE_AUTO_COMPACT`、`DISABLE_COMPACT` 四条是逐字原文。结论以源码为准，文档只作旁证。
