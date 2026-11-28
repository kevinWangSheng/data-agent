# 业务文档进 CLI 运行的位置；"行为不变"快照守门怎么对待 Skill、何时可重录

日期：2026-11-27。只取证，不做决定。起因：141 验收测试审查里提出的两个设计问题（spec `.scratch/product-config/spec.md`、`decisions.md`、ADR-0011）。代码行号以 main `32b7cb6` 和 141 worktree `e7d9233` 为准。

## 结论摘要

- **问题 1 大部分是我们自己的选择造成的，只有一小块是固有的。** 固有的部分：agent 的文件工具（Read/Glob/Grep）跑在宿主机上，cwd 就是使用者的目录。宿主机上没有"每次运行一个文件系统视图"，所以在宿主机这一侧，"只读挂进工作区"做不到，只能复制。我们的选择有四个：
  1. spec 写的是"进工作区"，不是"让 agent 读得到"。
  2. 141 的测试把这句话钉成"在 `options.cwd` 下能 rglob 到"。
  3. `workspace_guard` 只放行工作区，外加按文件列出的 `readable`。
  4. CLI 运行没有一个归自己的工作区。

  同类工具都不把参考文档复制进使用者的目录。常见做法有三种：放在 cwd 之外、加进允许读的目录（Claude Code `--add-dir`）；写进提示词（CLAUDE.md `@import`、AGENTS.md）；用工具提供（SignalPilot 知识库 MCP、ktx CLI/MCP、dbt-mcp）。nao 的做法是把上下文目录复制进沙箱，不复制进使用者的目录。
- **问题 2 的三条里：(a)、(c) 是我们的实现选择，(b) 是全文快照固有的，但"不许重录"是我们的流程规则把它放大了。**
  - (a) 的根因是 `agent.skill_version` 把 `__pycache__` 也算进哈希。Harbor 的内容哈希用 gitignore 和默认忽略表解决了同一问题。
  - (c) 是普通的子串替换不认路径边界。
  - (b)：成熟的快照工具都把"重录 + 审 diff"当作正常流程。我们的守门同时混了"新旧机制对拍"和"冻结金样"两个目的。缺的是授权的重录路径和守门的失效时间，不一定要重新设计。

## 问题 1：`[sources.<名字>].docs` 在 CLI 运行里放哪

### 已核实（本仓库）

- spec `:42`：`docs` 是"目录，复制或只读挂进工作区"。工单 148 写"两种由本工单选、写明理由"（`148-prompts-and-context.md` Context "docs 的做法"）。
- 141 的测试 `tests/acceptance/test_config_prompts.py:110-128`（worktree）：`capture.probe` 在 `Path(options.cwd or workspace)` 下用 `rglob` 找文档文件，找不到就失败。
  - 能通过的做法：复制进工作区，或者让 cwd 指向一个归本次运行的目录。
  - 通不过、要人改测试的做法：放在 cwd 外的可读目录、只在 Docker 里只读挂载、MCP 工具、写进提示词。
  - 测试里的工作区是 `tmp_path/run`，看不出写进使用者目录的问题。
- CLI 的 `--workspace` 是必填参数，就是使用者给的目录（`cli.py:296`、`:484`）。`build_options` 设 `cwd=task.workspace`（`agent.py:1705`）。
- 网页查数运行自己建一个空工作区（`web/launcher.py:436-438`）。dbt 运行把项目复制过去，复制时跳过 profile、DuckDB 文件和凭据文件（`:439-466`）。
- 文件工具由 `workspace_guard` 限制在工作区内。工作区外只能 Read 按文件列出的 `readable` 路径，Glob 和 Grep 没有例外（`workspace_guard.py:12-14`、`:448-457`）。现有先例是任务文件：它存在工作区外的只读临时文件里（`agent.py:336-344`，`saved_task`），只放行给 Read。
- 沙箱：
  - Docker 后端：工作区可写挂到 `/workspace`，Skill 目录只读挂到 `/skills`（`sandbox.py:277-278`）。这是"参考材料放在工作区外、只读给沙箱"的现成先例。
  - local 后端：在宿主机上直接跑，只读做不到（`sandbox.py:205-223`）。
- CLI 运行本来就往使用者的工作区写东西：`data/`、`outputs/`、`sandbox_logs/`（`sandbox.py:430-432`）。所以问题不在"能不能写进去"，而在复制带来的这些后果：
  - 复制件是旧的、跟源目录不同步；
  - 和使用者自己的文件重名；
  - 运行结束后谁来清理；
  - 凭据文件筛查：工作区里有凭据文件，`check_no_credentials` 会直接拒绝运行（`agent.py:2110-2121`）；
  - dbt 运行的工作区就是 dbt 项目本身，文档会落进项目里；
  - 复制件在可写的工作区里，agent 能改。
- ADR-0010 讲的是写库凭据和守卫的两条规则，不讲文档只读。"只读"在这里只是指"别改使用者的源文档"。
- SDK 有 `add_dirs`（`.venv/.../claude_agent_sdk/types.py:2577`），本仓库现在没用。
- 评测：
  - DAB 的库说明由评测脚本读出，写进任务提示词（`evals/dab.py:120-122`、`:196`）。这是"写进提示词"的仓库内先例。
  - 评测配置文件不配 `docs`，所以无论选哪种做法，评测都不受影响（推断，依据 spec"评测配置文件"一节）。
- 云端：工单 154 让 `docs` 可以是 git 地址，浅克隆到 `<runs_dir>/.cache/git`，供多次运行共享读取（`.scratch/cloud-deploy/issues/154-git-context.md`）。

### A. 冲突是我们自己的还是固有的

- **固有**：文件工具在宿主机上跑，cwd 是使用者的目录。在宿主机上，"进 cwd"就只能是复制。软链接不行：守卫会解析链接，指到工作区外就拒绝（`workspace_guard.py:1-5`）。
- **我们的选择**：
  1. spec 用了"进工作区"这个说法；
  2. 141 的测试钉死了"在 cwd 下"，148 原本可以在两种做法里选，现在只剩复制；
  3. 守卫对工作区外只放行单个文件；
  4. CLI 运行没有一个归本次运行的工作区（网页运行有）。
- 第 3、4 条都能改，而且已有先例可以照着改：`readable` 机制和 `/skills` 只读挂载。

### B. 同类做法（已核实的标出处，见"来源"）

| 工具 | 业务/参考文档怎么到 agent | 是否复制进使用者目录 | 只读 |
|---|---|---|---|
| Claude Code | 两种。CLAUDE.md 用 `@path` 导入，展开进上下文，可以导入工作区外的文件（第一次会弹确认框）。`--add-dir` / `additionalDirectories` / SDK `add_dirs` 把额外目录加成可访问目录；这些目录里的 CLAUDE.md 默认不加载，要设 `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` | 否 | 否：额外目录"可读可编辑"，编辑照当前权限模式。要只读得另写 deny 规则（未确认） |
| Codex CLI | AGENTS.md 从项目根往下走到 cwd，逐层拼接，默认上限 32 KiB（`project_doc_max_bytes`）；可配备用文件名 | 否 | `--add-dir` 给的是写权限。workspace 外能不能读，文档没写（未确认） |
| nao | 上下文目录就是 nao 项目目录（`nao sync` 在使用者自己的项目里生成 `databases/`、`docs/` 等）。文件工具限定在 `projectFolder` 内（`read.ts:74-80`）。代码沙箱把项目目录**复制进沙箱**的 `<WORKING_DIR>/context`，复制时按权限和 `.naoignore` 过滤（`execute-sandboxed-code.ts:178-251`） | 不复制进使用者目录。上下文目录本身就是 agent 的根目录 | 复制件在沙箱里，源目录不受影响 |
| ktx | 上下文是 ktx 项目里的普通文件（wiki 和语义层 YAML），agent 通过 `ktx sl` / `ktx wiki` 命令或 `ktx mcp` 读；不在 ktx 项目目录里时用 `--project-dir`。`ktx setup --agents` 往 agent 项目里写一个 SKILL/规则文件，这是使用者显式执行的、有清单记录 | 只写一份说明文件，由使用者主动执行 | 通过工具读 |
| SignalPilot | 知识库存在网关数据库里，用 MCP 工具 `get_knowledge`（"context" 类每次都注入）、`search_knowledge`、`read_knowledge` 读（`gateway/mcp/tools/knowledge.py:18-19`、`:96-281`）。插件里 `notion-context` 按需读 Notion | 否 | 是，通过工具 |
| Wren AI | 全局指令"每次查询都用"，问题匹配的指令按示例问题触发。检索和拼进提示词的方式未确认（源码页 404） | 否（推断） | 未确认 |
| dbt-mcp | 模型描述、血缘通过工具读（`get_all_models`、`get_node_details`；`_dev` 版本读本地 `manifest.json`） | 否 | 是，通过工具 |

推断：上游没有一家把参考文档复制进使用者的工作目录。写进 cwd 的只有 ktx 的一份说明文件，而且是使用者主动执行的。

### C. 选项

1. **复制进工作区的一个固定子目录**，例如 `.data-agent/docs/<源名>/`，结束时删掉。
   - 适合：网页运行（工作区本来就归运行）。两种沙箱后端都能用，测试原样通过。
   - 代价：在 CLI 运行里写使用者目录，可能重名、残留（运行中途崩了就清不掉）；dbt 项目里多一个目录；复制件可写；要筛掉凭据文件（148 验收已要求）。
   - 上游：没有一家对使用者目录这样做。nao 对沙箱是这样做的。
2. **CLI 也给每次运行建自己的工作区**（像网页那样复制，或者叠一层）。
   - 适合：隔离最干净。
   - 代价：CLI 的语义变了。使用者期望 agent 直接改 `--workspace` 里的 dbt 项目，所以不可行或代价很大。评测（Harbor）的工作区就是题目目录，评分器读它，不能换。
3. **放在工作区外，并扩展守卫和沙箱**：
   - 守卫加"只读根目录"（Read/Glob/Grep 可读，Write/Edit 不行），沿用 `readable` 的思路；
   - Docker 后端照 `/skills` 的做法只读挂到 `/docs/<源名>`；local 后端用宿主路径；
   - 提示词写明路径。
   - 适合：CLI 和网页一致，不碰使用者目录。154 的 git 缓存可以直接指过去，不用每次复制。只读在 Docker 后端成立，local 后端不成立。local 后端只在评测容器里用，而评测不配 `docs`。
   - 代价：要改守卫和沙箱（148 的 Authority 里没有 `workspace_guard.py`、`sandbox.py`）；141 的测试要改成"agent 读得到"，例如探针换成按提示词里的路径读，这要人审。
   - 上游：Claude Code `--add-dir`（它不是只读的，我们可以更严）、Docker 只读挂载的先例是我们自己的 `/skills`。
4. **写进提示词**（像 CLAUDE.md `@import`、DAB 的库说明）。
   - 适合：文档小的时候。
   - 代价：占上下文，大目录放不下。148 已经让 `description` 和 `[instructions].files` 进提示词，`docs` 是给大目录的，所以只能作为补充。
5. **用 MCP 工具读或搜**（SignalPilot、ktx、dbt-mcp）。
   - 适合：文档大、需要检索时。只读天然成立，沙箱后端无关。
   - 代价：要新做工具；沙箱里的代码读不到（要读得另写进 `data/`）。

推断（不是决定）：选项 3 和"CLI 用户目录不写、上游不复制"这两点最一致，可以和 4 组合（小文件直接进提示词）。选它的前提是人同意改 141 的测试，并把守卫和沙箱加进 148 的 Authority。如果不改测试，就只能选 1。选 1 的话，要写明复制件放的子目录、结束时清理、凭据文件筛查、重名时怎么报错。

## 问题 2：快照守门怎么对待 Skill、何时可以重录

### 已核实（本仓库）

- 快照内容：系统提示词全文（含注入的 SKILL.md 全文）、任务提示词、工具清单、MCP 参数、环境变量、`version` 里的 `skill_version` 等（`test_config_snapshot.py:1-60`）。`data-eng-bench-full.json` 的系统提示词有 56,000 字符。
- `skill_version_label`（`product_config_support.py:484-500`）把哈希换成"现在对哪个目录算哈希能得到同一个值"的目录标签。效果：快照只比"哈希的是哪个目录"。
  - 脚本内容改了，标签不变，看不出来（审查意见 (a)）；
  - SKILL.md 改了，系统提示词全文变了，守门失败（审查意见 (b)）。
  - 两者对同一类改动（Skill 改动）给出相反的结果。
- 不录哈希值的理由写在 docstring 里：`agent.skill_version` 对目录下所有文件做哈希（`agent.py:1289-1296`，`rglob("*")`），被忽略的文件也算在内。本机确实有 `skills/dbt-workflow/__pycache__`，而 `git ls-files skills | grep -c pycache` 是 0。理由属实，但根因在哈希函数本身。
- 141 的审查记录里"不修"(a) 的理由是：录成固定摘要的话，之后任何正当的 Skill 修改都会让守门永久失败（`141-config-acceptance-tests.md:237`）。这个理由同样适用于 (b)，因为全文已经在快照里了。
- 规则：
  - AGENTS.md："MUST NOT 改 spec 约定的验收测试来让它变绿"；
  - `test_config_snapshot.py:4-5`："no ticket edits them or re-records their snapshots"，守门要撑过 143-148。
  - spec 迁移第二步（`:79-80`）和 Update（`:127`）：Skill 文本的改动推到第二步，由主会话做。所以第一步期间 Skill 文本按决定冻结，冲突出在第二步，以及第一步期间别的工作流改 `skills/` 的时候。
- 别的工作流会改 `skills/`：skill-improve 的候选"每个候选一个分支、一个提交、只改 `skills/`"（`.scratch/skill-improve/spec.md:101`）。这样的候选在 143-148 期间合进 main，守门就会失败（推断）。
- (c) 的机制：`normalize_text` 按长度从长到短依次做 `str.replace`（`product_config_support.py:445-452`），`REPO_ROOT` 换成 `<repo>`，不看路径边界。checkout 在 `/work` 时，`/workspace` 变成 `<repo>space`。
  - 同类风险还有：Linux 上 `gettempdir()` 是 `/tmp`，会命中任何以 `/tmp` 开头的字符串。现有快照 JSON 里没有字面 `/tmp`（已 grep）。
  - 占位符和 Skill 原文也可能撞：快照里本来就有 Skill 原文的 `<project>`、`<path>`。如果 Skill 原文里出现字面 `<repo>`，会被误认成占位符，目前没出现。
  - 141 验收要求在 `python:3.12-slim` 里重跑一次，工单里记录这一步还没做（`141-...md:313`）。跨机器是否相等：未确认。

### A. 哪些是我们的选择

- (a) 我们的选择：哈希函数不过滤被忽略的文件，于是测试只能退成目录标签。
- (b) 全文快照本身就会这样（任何内容变化都会让它失败，这正是它的用途）。被放大是因为我们的流程规则：不许重录，也没有失效时间。另外快照同时承担了两个目的：
  - 新旧机制对拍：同一份 checkout 上新旧两套机制生成的参数相同，Skill 内容和它无关；
  - 冻结金样：任何变化都要失败。
- (c) 我们的实现：普通子串替换。成熟工具的做法是用正则或带边界的替换，或者在结构化字段上做替换。

### B. 成熟做法

- **重录是正常流程，靠审 diff 把关**：
  - Jest：`-u` 重新生成；"snapshot 应与代码一起提交，并在代码审查里审"；CI 里不带 `--updateSnapshot` 就不写新快照。
  - insta：`.snap.new` 加 `cargo insta review` 逐条接受；CI 里不写新快照（`INSTA_UPDATE=auto` 在 CI 里等于 `no`）。
  - syrupy：`--snapshot-update` 更新并删除没用到的快照；快照缺失直接失败。
  - pytest-snapshot：`--snapshot-update`，删除另外要 `--allow-snapshot-deletion`；文档要求核对新快照后和代码改动一起提交。
  - ApprovalTests：`received` 对 `approved`，打开 diff 工具由人批准。
- **不稳定的值用替换器，而不是字符串替换**：
  - insta 的 filters 是正则（例子 `\b[[:xdigit:]]{32}\b → [UID]`，带词边界）；redactions 按选择器替换字段；
  - syrupy 的 `path_type` / `path_value` 按字段路径替换或排除；
  - Jest 用属性匹配器，`expect.any(Date)` 存成 `Any<Date>`；
  - ApprovalTests 的 DateScrubber 把值换成 `<date0>`。
  - insta 文档里没有路径或临时目录的例子，路径要自己写正则（未确认是否有官方建议）。
- **评测框架怎么钉 agent 配置和版本**：
  - Harbor：`lock.json` 存完整的结构化 `AgentConfig`；任务、额外说明、Skill 各存 `sha256` 摘要（`AgentSkillLock.digest`）；续跑时配置对不上就拒绝（本地 `job.py:390`）。内容哈希先按 gitignore 或默认忽略表（`__pycache__/`、`*.pyc` 等）过滤（本地 `publisher/packager.py:13-20`、`:83-95`）。
  - inspect_ai：日志记 `task_version`、solver 名和参数、`revision`（git commit 加 `dirty`）、`packages`。不记提示词全文。
  - OpenAI evals：名字是 `<eval>.<split>.<version>`，"改了 eval 就升版本号"。
  - SWE-bench：只记 `model_name_or_path`、`run_id`，提交时附 `metadata.yaml`、轨迹。没有哈希。
- 推断：业界对 Skill 这类内容要么记内容哈希（Harbor），要么记 git commit 加 dirty（inspect）。"全文进金样"在快照测试里常见，但都配了审过的重录流程。

### C. 选项

1. **修哈希函数，录真正的内容哈希**：`skill_version` 跳过 `__pycache__`、`*.pyc` 等，或者只算 `git ls-files` 里的文件（照 Harbor）。快照录哈希值，不录目录标签。
   - 效果：脚本改了也能发现。
   - 代价：任何 Skill 改动都要重录。它改的是运行记录里的 `skill_version`，影响历史运行的可比性：旧运行的哈希会变，必须一次性说明。
2. **快照里 Skill 部分换成结构化占位**：系统提示词里注入的每段 SKILL.md 换成 `<skill:名字 sha256:…>`；另加一条断言，确认这一段等于当前文件经 `load_skills` 处理后的结果。
   - 效果：守门只看机制（注入了哪些 Skill、什么顺序、`${CLAUDE_SKILL_DIR}` 怎么替换），Skill 内容改了不影响它；内容由哈希或另一条测试单独看。
   - 代价：规范化逻辑变复杂；要改 141 的测试（人审）。
3. **对拍不录金样**：同一次测试里分别用旧入口和新入口生成参数，互相比较。
   - 效果：Skill 改了也照样通过。
   - 代价：146 删掉旧入口之后就没法对拍了，所以只适合做到 146，之后还得有金样。
4. **保留全文金样，补流程**：
   - 写明重录的授权路径：一个只改 `tests/fixtures/product_config/snapshots/` 和引起变化的 Skill 文件的提交，加上 diff 摘要，由人审，照 Jest 和 insta 的做法；
   - 写明守门在哪个时间点失效：146 合入并且第二步开始后，改成普通回归快照，第二步本身也要重录。
   - 代价：每次 Skill 改动多一次人审；skill-improve 的候选要附带重录。
5. **(c) 不论选哪个都要修**：
   - 只在路径边界上替换：后面跟 `/`、引号、空白或字符串结尾才替换，可以用正则 `re.escape(path) + r"(?=/|\W|$)"`；或者只在已知是路径的字段和参数上替换；
   - 占位符用不会在原文里出现的形式，例如 `⟨repo⟩`；
   - 补在 `python:3.12-slim`、`/work`、`/tmp` 下的测试。
   - 141 的守门本来就按"规范化可以修，断言不能动"处理，所以这一条属于修实现缺陷（推断，要人确认它算不算"改验收测试"）。

推断（不是决定）：(c) 和"哈希过滤忽略文件"是实现缺陷，可以直接修。(a)/(b) 实质上是一个问题："守门要不要对 Skill 内容敏感"。它取决于人怎么定守门的目的。如果目的是"只证明机制迁移不变"，选 2 或 3 加 4 的失效时间最贴；如果要同时冻结 Skill，就选 1 加 4 的重录流程。

## 来源

本地代码由本会话直接读取：

- 本仓库：
  - main `32b7cb6`：`src/data_agent/sandbox.py`、`agent.py`、`workspace_guard.py`、`cli.py`、`web/launcher.py`、`evals/dab.py`
  - 141 worktree `e7d9233`：`tests/acceptance/test_config_prompts.py`、`test_config_snapshot.py`、`product_config_support.py`、`tests/fixtures/product_config/snapshots/*.json`
  - 工单与规划：工单 141、147、148、154，`.scratch/skill-improve/spec.md`，ADR-0010
- `~/dev/AI/data-agent-refs/`：
  - nao `5bde830`：`apps/backend/src/agents/tools/read.ts`、`execute-sandboxed-code.ts`、`cli/nao_core/commands/sync/__init__.py`
  - ktx `49a4ae6`：`docs-site/content/docs/guides/serving-agents.mdx`、`cli-reference/ktx-mcp.mdx`
  - SignalPilot `f5eee22`：`gateway/mcp/tools/knowledge.py`
  - signalpilot-plugin `f7b0c09`：`skills/` 目录清单、`knowledge-base/SKILL.md`
  - harbor `dc933be`：`models/trial/config.py`、`models/trial/result.py`、`job.py`、`publisher/packager.py`

网页由一个只读子 agent（Sonnet）通过 WebFetch 读取，本会话没有逐页复核。WebFetch 返回的是小模型的摘要，所以"引文"不一定逐字：

- Claude Code：https://code.claude.com/docs/en/memory 、 /cli-reference 、 /permissions 、 /agent-sdk/python
- Codex：https://learn.chatgpt.com/docs/agent-configuration/agents-md （从 developers.openai.com 跳转过去）、 /docs/cli/reference 、 /docs/config-file/config-reference 、 /docs/agent-approvals-security
- Wren AI：https://docs.getwren.ai/oss/guide/knowledge/instructions （只有使用说明；源码页 404，未确认）
- dbt-mcp：https://raw.githubusercontent.com/dbt-labs/dbt-mcp/main/README.md
- syrupy README；pytest-snapshot GitHub 页（README 原文 404）
- insta：https://insta.rs/docs/quickstart/ 、 /advanced/ 、 /redactions/ 、 /filters/
- Jest：https://jestjs.io/docs/snapshot-testing
- ApprovalTests.Python README、`docs/how_to/scrub_dates.md`
- inspect_ai：`src/inspect_ai/log/_log.py`（GitHub main 分支）
- Harbor：GitHub main 分支的 `models/trial/result.py`、`models/job/config.py`、`models/job/lock.py`（`JobLock` 用的是 main 分支，比本地 `dc933be` 新）
- OpenAI evals：`docs/build-eval.md`
- SWE-bench：`docs/guides/evaluation.md`；SWE-bench/experiments README

未确认：

- Claude Code 额外目录能否靠 deny 规则做成只读；
- Codex workspace 外能不能读；
- Wren AI 的检索方式；
- Jest 过期快照和 `--ci` 的细节；
- 141 快照的跨机器相等（`python:3.12-slim` 那次还没跑）。
