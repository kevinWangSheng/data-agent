# 29 — 一条命令判断一个改动是否按 ADR-0008 过关

Status: done
Blocked by: 27（运行记录）
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 29-change-acceptance-check)
Branch: worktree-29-change-acceptance-check

## Goal

给定改动前、改动后两份开发集结果、本改动的目标题和回归集文件，一条命令按 ADR-0008 给出"过关 / 不过 / 需补跑哪几题"，并说明旧版结果能不能复用；主会话验收改动时不再手算。

## Authority

- May edit: `evals/`（`compare` 或新子命令）、`tests/`（除 `tests/acceptance/`）、`docs/setup.md`
- Must not edit: `tests/acceptance/` 下的验收测试、`src/`、`skills/`、`scripts/`、`evals/regression.json`（回归集由主会话在工单 31 维护）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- ADR-0008 的"改动过关"三条、"回归集"、"复用旧版结果"原文就是规格；这里不重述，以 ADR 为准。
- 回归集文件 `evals/regression.json`（题名列表，只含开发集题）由工单 31 生成；本工单用测试夹具，文件格式自己定一个最简单的并写进 `docs/setup.md`。
- 补跑：某回归题一轮低于 3/3，命令列出它，补跑结果作为额外输入再判一次；补跑仍 ≤1/3 才算退步。
- 复用：读两份结果的 version 头和工单 27 的运行记录；字段不一致、benchmark commit 或机器标签不同、超过两周、该轮有基础设施报错，任一条就判"旧版需重跑"。
- 只用于开发集：输入里出现非开发集题就拒绝（ADR-0004）。
- 本地已有 `runs/dev-2/out/jobs/{full,baseline}`（不同 commit，k=1）可作真实运行。

## Acceptance

- [ ] 三条过关条件各有通过与不通过的测试：点估计、目标题成功次数、回归集（含补跑后转为通过与判退步两种）
- [ ] 复用条件每条不满足时都判"旧版需重跑"；有测试
- [ ] 输入含非开发集题时拒绝；有测试
- [ ] 真实运行：对 `runs/dev-2` 的两份 job 跑一次（随意指定 2 道目标题、3 道回归题的夹具文件），贴输出
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令与输出。

## Stop conditions

ADR-0008 的规则有歧义、需要人定；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 3
- Review rounds: 4
- Model spend: $0
- Outcome: merged (PR #30), worktree-29-change-acceptance-check (616452c, d5a0c7e, 1e02636, ec48f52, b40ba25, c6daaf3, 170c8d7)
- Human review minutes: —

## Comments

- 2026-11-01 工单 agent：
  - 实现：`python -m evals accept`（`evals/accept.py`），结论 pass / fail / rerun-regression / rerun-old；规则与回归集格式（开发集题名的 JSON 列表）写进 `docs/setup.md`。另改 `ablate.keep_spend`：续跑前把被删 trial 的 result.json 存进 `dropped/`，没写事件文件的基础设施报错也查得出（dev-2、dev-3 这类旧 OUT 查不出，setup.md 写明）。
  - 解读（主会话接受，用户可推翻）："两周内"按旧版最早开始的 trial 到新版最晚结束的 trial ≤14 天，不依赖跑命令的时间；"version 字段一致"指除 git_commit、skill_version 外每个字段两份 job 都只有同一个值，每个非基础设施报错 trial 都要有 version 头；回归题判退步时一律 fail。
  - 测试：tests/test_eval_accept.py 31 个，覆盖三条过关条件的通过/不通过（回归含补跑转通过与判退步）、每条复用条件、非开发集输入；后补测试部分做了 8 个变异，全被抓到。make check：1264 passed, 1 skipped。
  - 真实运行：`uv run python -m evals accept --old <main>/runs/dev-2/out/jobs/baseline --new <main>/runs/dev-2/out/jobs/full --manifest evals/manifest.json --target dbt-price-elasticity --target fifo-inventory-cogs --regression runs/29/regression.json --trials 1` → exit 1：
    ```
    verdict: rerun-old
    old results: rerun the old version
      - version field config: ['"baseline"'] old, ['"full"'] new
      - version field layers: [... false ...] old, [... true ...] new
      - the old job has no run record
      - the new job has no run record
    point_estimate: FAIL, old 20/30, new 18/30 (-6.7 points)
    targets: FAIL, old 1/2, new 1/2
    regression: rerun needed
      dbt-daily-order-summary: 1/1 -> ok
      dbt-test-orders-filter: 0/1 -> rerun
      pos-operations: 1/1 -> ok
    rerun these tasks on the new version: dbt-test-orders-filter
    ```
  - da-review 第 1 轮：修了每个 trial 要有 version 头、dropped/ 存 result.json、dropped/ 有报错时前两条不计分；不修 `--trials` 只能为 3（真实运行要 k=1，compare/report 同有此参数，默认 3，setup.md 写明正式验收用 3）。
  - eng:code-review 第 2 轮：补了若干测试与文档；不修 dropped/ 有报错但旧 job 已续跑完整时仍报前两条（与 Codex 第 1 轮意见冲突，结论反正是 rerun-old）、OUT 布局三处描述、dict 不用类型、主观改名。
- 2026-11-01 主会话：PR #30 CI 绿。退回（第 1 轮）：Codex GitHub app（1e02636）P1 属实——`evals/accept.py` 的 `fields()`（约 186–194 行）只收集出现过的值；某个 trial 的 version 头缺少一个非豁免字段（如 `model`）而其他 trial 都有同一个值时，缺失不留痕迹，旧版被判可复用。要求：按 trial 记下字段是否存在（或比较完整的键集合），任何 trial 缺字段即判"旧版需重跑"并写明原因；配修复前会失败的测试。
- 2026-11-01 工单 agent（退回第 1 轮；ec48f52、b40ba25）：
  - 修复（ec48f52）：`fields()` 逐个事件文件看 header，任何 header 缺某个字段就给该字段加一个 `(missing)` 值，于是判 rerun-old 并写明，如 `version field model: ['"m1"', '(missing)'] old, ['"m1"'] new`；setup.md 写明每个 trial 须带全部字段。测试 `test_reuse_needs_every_version_field_on_every_trial[old|new]` 修复前 exit 0、pass。
  - 审查修复（b40ba25）：`fields()` 跳过基础设施报错 trial（空事件文件会给每个字段加 `(missing)`）；新测试 `test_an_infrastructure_error_in_a_rerun_is_reported_as_one`、`test_an_old_infrastructure_error_without_a_header_adds_no_field_problems` 修复前失败。
  - make check：1268 passed, 1 skipped。真实运行同上一轮，输出不变（dev-2 header 没有缺字段）。
  - da-review（ec48f52）两条未修，交主会话：P1 `times()` 跳过缺 started_at/finished_at 的 trial，两周检查可能在不知道全部时间时通过（accept.py 约 239–242 行）；P2 运行记录的 benchmark commit 或机器只在 None 时拒绝，两边都是 "" 会判一致（约 234–237 行）。
  - eng:code-review 不修：versions()/header 位置、命名、Literal 类型等判断题；`--target X --target X` 重复计数（结论不变，只是显示总数偏大）。
  - b40ba25 未再跑 da-review。
- 2026-11-01 主会话：PR #30 CI 绿；Codex GitHub app 对 b40ba25 无意见（`@codex review` 触发，has_any_findings false）。退回（第 2 轮，最后一轮）：工单 agent 交来的两条 da-review 意见主会话核实属实，与第 1 轮同类（复用检查对缺失信息静默放行）：
  1. `times()`（evals/accept.py 约 206–212 行）跳过没有 started_at/finished_at 的 trial；非基础设施报错 trial 缺时间戳时应判"旧版需重跑"并写明。
  2. 运行记录的 benchmark commit 或机器标签为空串（或非字符串）时也应判不可复用，不只 None。
  两条各配修复前会失败的测试。
- 2026-11-01 工单 agent（退回第 2 轮；c6daaf3、170c8d7）：
  - 修 1：`times()` 返回时间与缺时间的 trial 数（不计基础设施报错）；缺时 `reuse_problems` 写 `1 old trial(s) have no started_at, so the old results' age is unknown`，判 rerun-old。测试 `test_reuse_needs_the_times_of_every_trial[old-started_at|new-finished_at]`。
  - 修 2：运行记录的 benchmark commit 或机器不是非空字符串就不能复用（如 `machine: '' old, '' new`）。测试 `test_reuse_needs_a_non_empty_record_value`（"" 与 7，各两个键）。6 个新测试修复前都 exit 0、pass。setup.md:38 同步。
  - make check（c6daaf3）：1274 passed, 1 skipped。
  - da-review（c6daaf3）P2 已修（170c8d7）：两边所有 trial 都缺某必需字段时从不比较——加 `REQUIRED = {config, layers, model, sdk_version, cli_version}`（agent.py:785-794 每次都写），两边都缺也算缺。测试 `test_reuse_needs_the_version_fields_every_run_writes` 修复前失败。
  - eng:code-review：Standards 修了 times() docstring 与重复 guard；不修消息形状与 `(list, int)` 返回值。Spec 无遗漏无越界。已知限制：时间值存在但格式不对（数字、非法日期、naive/aware 混用）会在 fromisoformat 或相减处崩溃，不是给出 rerun-old（早先代码）。
  - make check（170c8d7）：1267 passed, 8 failed, 1 skipped；8 个失败是 test_harbor_agent.py 3 个、test_packaging.py 5 个，都卡在 `uv build` 连不上 PyPI（会话网络），本分支未碰这些文件，c6daaf3 上通过；以 CI 为准。
  - 真实运行（170c8d7）：输出同前，`verdict: rerun-old`。
  - 170c8d7 未再送审（修复次数用完），之后的意见交主会话。
- 2026-11-01 主会话：PR #30 Codex GitHub app 对 170c8d7 无意见。工单 28 先合并后，主会话把 origin/main 合进本分支（9039e93）：唯一冲突 evals/__main__.py 两边各加一个子命令，两个都注册（accept、diverge）；ablate.keep_spend 存的 `dropped/<config>/<trial>/result.json` 与工单 28 dev-only pull 只认 trial 目录的检查兼容。合并后 make check 1324 passed, 1 skipped；CI 绿；Codex 对 9039e93 无意见。合并。170c8d7 本地 8 个 wheel 构建测试失败是会话连不上 PyPI，CI 通过。
