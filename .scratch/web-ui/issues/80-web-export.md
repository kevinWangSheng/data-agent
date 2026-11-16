# 80 — 导出命令：把选定的运行转成在线版的静态数据

Status: ready-for-agent
Blocked by: 73, 84（done）
Spec: ../spec.md
Claimed by: claude-code (opus)
Branch: worktree-80-web-export

## Goal

一条命令把选定的运行导出成在线版读的静态数据（每条运行的事件文件和运行说明、索引、评测页的汇总），强制脱敏，拒绝留出集的运行。导出命令的验收测试全绿。

## Authority

- May edit: `evals/`（新增导出模块和子命令）；`tests/`（新增的单元测试）
- Must not edit: 验收测试（`web/src/**/acceptance/`、`tests/acceptance/test_web_api.py`、`tests/acceptance/test_web_export.py`、`tests/fixtures/web/`，由工单 73 合入；唯一例外是本工单让某个测试转绿时删掉它的 `xfail` 标记）、`evals/scrub.py` 的脱敏规则（只调用，不改）、`src/data_agent/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- spec "导出命令和在线版数据"和"评测页"两节。
- 输入：精选运行的事件目录和各自的说明（标题、失败原因等）；开发集统一评估的 Harbor 任务目录（每个 trial 的事件文件和评分结果）；评测清单。
- 输出目录的结构已由工单 73 的验收测试定（`tests/acceptance/test_web_export.py:15-16, 146-151`，2026-11-15 人确认）：`DIR/index.json` = `{"runs": [notes, ...]}`；每条运行 `DIR/runs/<id>/events.jsonl` 和 `DIR/runs/<id>/meta.json`（与索引里那条 notes 相同）。本工单只定评测页汇总文件放在哪里（在上面的结构上增加，不改它），写进最终回复，工单 76、81 的静态数据来源照它读。
- 命令行：`evals` 按 `args.run` 分派子命令，所以导出命令的 `--run` 参数必须写成 `dest="runs"`，否则会冲突。
- 运行说明的字段：id、标题、任务、类型、数据源名字、评分结果、失败原因、是否精选。事件文件里有 `task` 事件时从它取，没有时从 Harbor 的题目目录取。
- 评测页的汇总：两个榜单的总分和误差、各配置的通过率和误差、开发集每个 trial 一行。这些数字从现有的评测代码算（`evals/report.py`、`evals/metrics.py`、`evals/dab_score.py`），不另写一套算法。
- **精选运行的留出集检查**（退回第 1 轮加，2026-11-15 人定，spec 同名一条，契约在工单 84）：`notes.json` 必须有 `task_id` 或 `local_data: true` 之一，两个都没有就拒绝；有 `task_id` 的和 trial 走同一套开发集/留出集检查；拒绝时只报数量。这两个字段只是输入，输出的运行说明字段不变。
- **留出集**：对照清单，任何属于留出集的 trial 都拒绝导出并以非零退出；留出集和全量只输出汇总数字。实现和测试都不读、不打印留出集的逐题失败细节（ADR-0004）；测试用假清单。
- 脱敏不能关：没有跳过脱敏的参数。
- 只用 uv。

## Acceptance

- [ ] 导出命令的验收测试全部通过，`xfail` 标记已删，测试内容一字未改
- [ ] 没有任何参数或环境变量能跳过脱敏或留出集检查
- [ ] （退回第 1 轮加）精选运行检查的单元测试覆盖开发集、留出集、清单里没有的题、`local_data: true`、两个都没有五种情况，另加 `local_data` 是非布尔真值（如 `"yes"`、`1`）时按没有处理、拒绝
- [ ] `make check` 通过

## Evidence required

`make check` 输出；真实运行一次：对 `/Users/shenghuikevin/dev/AI/data-agent/runs/web-prototypes/data/runs/` 的三条运行导出到一个临时目录，贴出命令、输出目录的文件列表和索引文件的内容。

## Stop conditions

- 汇总数字没法从现有评测代码得到，需要新的统计口径。
- spec 自相矛盾或缺一个需要人定的决定。
- 第二轮审查修复后仍不过。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 2
- Review rounds: 4 (round 1: da-review 1 fix, eng:code-review 1 fix round; return round 1: da-review 1 fix, eng:code-review 1 fix round)
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: returned ×2
- Human review minutes: — (filled by the human at merge, optional)

## Comments

- 2026-11-15 claude-code (opus)，第 1 次实现（worktree-80-web-export：aac399c、298f87f、6a16e5a）：新增 `evals/export.py`（注册在 `evals/__main__.py`），`tests/test_eval_export.py` 14 条单元测试；4 条导出验收测试去掉 `xfail` 后通过。
- 评测页汇总的位置（工单 76、81 照它读）：`DIR/eval.json` = `{"dev": {"reference": "full", "configs": [...], "contributions": [...], "trials": [...]}, "data_eng_bench": {...} | null, "dab": {...} | null}`；结构写在 `evals/export.py` 的模块文档里。
  - `configs`：每个配置一行，通过率和误差来自 `report.summary`。
  - `contributions`：full 减各配置，`compare.paired_bootstrap`（seed 0、10000 次，和 `ablate` 一样），每条带 `significant`。
  - `trials`：开发集每个 trial 一行 `{id, task, config, attempt, passed, reward, cost_usd, duration_seconds}`。
  - `data_eng_bench` 来自 `--board board.json`，只抄榜单分数和误差、题数和 trial 数、留出集汇总；`dab` 来自 `--dab dab_score.json`，只抄每个输出和每个数据集的 Pass@1 及对比。"为什么只有汇总"那句由页面自己写。
- 只导出开发集 trial：题目不在清单开发集里，或被清单或仓库 `evals/manifest.json` 留出的，都拒绝，报错只给数量。
- 旧事件文件（没有 `task` 事件）：精选运行从 `notes.json` 取 task、类型、数据源；trial 从 `--tasks-dir/<task>/instruction.md` 取任务（没有就用题目 id），类型 `dbt`，数据源 null。270 个真实 trial 都没有 `task` 事件，不给 `--tasks-dir` 时显示题目 id（工单 81、82 的导出命令要带 `--tasks-dir`）。
- 输出安全：输出目录必须是新的或空的；先在暂存目录里建好，用 `evals scrub` 的规则脱敏，干净了才移过去。
- 真实运行：三条 `runs/web-prototypes/data/runs/` 的原型运行，meta.json 转成各自的 notes.json（暂存脚本 /tmp/80-prepare.py）。`uv run python -m evals export --manifest evals/manifest.json --out /tmp/80-export4/site --run …/query-shop --run …/dbt-cart-abandonment --run …/dbt-division-by-zero` → "scrub: changed 0 file(s) of 8; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0" / "export: 3 run(s) -> /tmp/80-export4/site"，exit 0。文件：eval.json、index.json、runs/{query-shop,dbt-cart-abandonment,dbt-division-by-zero}/{events.jsonl,meta.json}，目录 755。index.json：query-shop（query，grade null，featured true）；dbt-cart-abandonment（dbt，passed true "benchmark tests passed (reward 1)"）；dbt-division-by-zero（dbt，passed false "14 of 15 graded components passed; …"，failure_reason 是失败归因）。eval.json：dev 为空，data_eng_bench、dab 为 null。
- 真实开发集数据的额外核对：导出 `runs/u2-rest/jobs/{full,baseline}`（100 个 trial），eval.json 的数和 `ablate` 自己的报告一致：full 64.0±2.83，baseline 66.0±2.0，贡献 −2.0 [−12, 10]，不显著。
- make check：ruff 和格式通过；basedpyright 0 errors；pytest "2214 passed, 1 skipped, 6 xfailed"；web typecheck 和测试通过。
- 验收测试文件还删了因去掉 `xfail` 而没用的 `import pytest`（否则 ruff F401），其余没改。人 2026-11-15 认可，AGENTS.md 的例外已扩成"因此没用的 import 或标记定义"。
- da-review（Codex）：P2 非 UTF-8 事件文件导致 traceback、exit 1 → 298f87f 修，带测试。
- eng:code-review，6a16e5a 修：清单开发集以外的题也拒绝（原来只拒留出集）；事件行只按 \n 分，字符串里的 U+2028 不再丢 task 事件；每个 trial 的 result.json 只读一次；trial 按名字跨 job 列出；站点目录 0755；held_out 改成两句；改名 summary_。
- 不修（理由）：精选运行不查留出集（说明里没有题目 id，已写进模块文档）——**已被人退回，见下**；留出集读取和 `report.check_dev_list` 重复（错误类型不同，改 report.py 超出本工单）；trial_notes 里三处 `if task else` 保留；模块 450 行偏长，保留；没有 DAB 运行总数（页面可按数据集加）；pass@k 和 cost_usd 保留；没有 `task` 事件的 trial 数据源为 null（Harbor 题目目录不记数据源名字）。
- **退回：精选运行没过留出集检查，fail-open**（2026-11-15 人核实）。trial 按题目 id 检查，但 `featured_run()` 没有题目 id，`export.py:30-36` 明说精选运行不查留出集；今天的精选运行恰好都是开发集题，只是现状，不是边界。修法（契约由工单 84 先改，人审合入后再修）：
  - 来自榜单题目的精选运行，`notes.json` 带 `task_id`；真正的本地运行带 `local_data: true`；至少有一个，两个都没有就拒绝，不当成本地。
  - 带 `task_id` 的和 trial 走同一套开发集/留出集检查。
  - 拒绝只报数量，不打印题目 id、失败原因或结果。
  - 单元测试覆盖开发集、留出集、清单里没有的题、本地、缺元数据五种情况（验收测试由 84 加）。
  - 84 合入后把本分支 rebase 到新 main，删 84 新测试的 `xfail` 标记（按 AGENTS.md 例外），修完重跑 `make check` 和两道审查。
  - 人的基线：工单 80 的测试 19 passed。
- 2026-11-15 claude-code (opus)，退回第 1 轮修复（rebase 到 origin/main 8bb1c01，无冲突；新提交 c61f1c1、013fb0d、aac5c01）：
  - 精选运行检查：`featured_allowed` 只放行 `task_id` 是字符串且在清单开发集里（减去两份清单的留出集，和 trial 同一个 `dev` 集合）的；没有 `task_id` 时 `local_data` 必须是布尔 `true`。`task_id` 优先于 `local_data`，留出集的题不能冒充本地。拒绝：两个都没有、`local_data` 为 false/`"yes"`/`1`、`task_id` 不是字符串、`notes.json` 读不了。`check_featured` 在读 trial 和写任何东西之前跑，每个 `notes.json` 只读一次，整次导出拒绝、只报数量（`N featured run(s) refused: …`），exit 2。`task_id`、`local_data` 不抄进输出。
  - 单元测试：11 种情况的参数化测试（开发集、留出集、清单外、`local_data: true`、两个都没有、留出集 + `local_data: true`、`local_data` false、`"yes"`、`1`、非字符串 `task_id` 两种）；一条 CLI 测试查拒绝只报数量、不出现 run id、目录或失败原因。现有精选运行单元测试只在输入里加 `local_data: true`。
  - 验收：`tests/acceptance/test_web_export.py` 9 passed；修之前 84 的 3 条拒绝测试 exit 0 失败。主会话核对验收文件和 main 相比只删了 `import pytest` 和 `pytestmark = pytest.mark.xfail(...)` 两行。
  - make check（aac5c01）：ruff 通过；basedpyright 0 errors；pytest "2244 passed, 1 skipped, 39 deselected, 6 xfailed"；web 测试通过；exit 0。
  - 真实运行：agent 在 c61f1c1 跑过；最后一个提交上主会话重跑：`uv run python -m evals export --manifest evals/manifest.json --out /tmp/80-r3/site --run /tmp/80-r2/in/query-shop --run /tmp/80-r2/in/dbt-cart-abandonment --run /tmp/80-r2/in/dbt-division-by-zero`（query-shop `local_data: true`；另两条 `task_id` 为 `dbt-cart-abandonment-recovery`、`dbt-fix-division-by-zero`，都在开发集）→ "scrub: changed 0 file(s) of 8; …" / "export: 3 run(s) -> /tmp/80-r3/site"，exit 0。文件：eval.json、index.json、runs/{query-shop,dbt-cart-abandonment,dbt-division-by-zero}/{events.jsonl,meta.json}（59、473、1057 行事件）；输出里没有 `task_id`、`local_data`。agent 的反向真实运行：同样三条不带这两个字段 → "export: 3 featured run(s) refused: …"，exit 2，没有输出目录。
  - da-review（Codex）：P2 格式坏但已放行的精选运行（事件文件数不对、没标题、grade 坏、事件读不了）报错带目录 → 013fb0d 改成 `--run #N`，5 种情况参数化测试。
  - eng:code-review：Spec 无缺口；Standards 无硬违规。aac5c01 修：留出集 trial 的 `result.json` 读不了时报错带 trial 路径 → 只报 `strerror`（新测试修前失败、修后通过）；`notes.json` 读两次 → 读一次；事件文件读错不再一律说 UTF-8。
  - 不修（理由）：留出集检查信任自报的 `task_id`/`local_data`，不和 `task` 事件交叉核对（84 契约的已知限制）；notes 的类型化记录、notes 和事件文件的元组属写法判断；开发集检查之后的错误路径（trial 没有事件文件、instruction 读不了）仍带路径，只会是开发集题，ADR-0004 允许；模块约 500 行，同第 1 轮。
- **退回（PR #88 第 1 轮，Codex GitHub app 对 aac5c01 的 P2，主会话 2026-11-16 核实属实；只是落实"强制脱敏"，由主会话直接定）**：脱敏只改文件内容，不改路径。运行目录名（精选运行的 id）或 trial 名里有可脱敏的值（主机名、IP、home 路径、用户名等）时，`index.json`/`meta.json`/`eval.json` 里的 id 被换成 `[redacted-…]`，`runs/<原名>/` 目录名却没变：IP 留在公开的路径里，网页按 `runs/<id>/events.jsonl` 也找不到文件。修法：
  - 建目录之前，用 `evals scrub` 同一套规则检查每个要导出的 id（精选运行和 trial）；脱敏会改动的 id → 整次导出拒绝、非零退出、什么都不写，报错只给数量（不打印 id 本身，它可能就是要藏的值），提示改目录名后重来。不自动改名。
  - 单元测试：精选运行 id 带 `--hostname` 指定的主机名 → 拒绝且输出里没有它；trial 名带 IP → 拒绝；正常 id 不受影响。验收测试不改。
  - 修完重跑 `make check`、真实导出（三条原型运行）和两道审查。
