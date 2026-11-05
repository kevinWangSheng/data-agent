# 57 — 重派只列需要重验的模型，compare_by_key 不再每次重算 model_diff

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: Claude Code (claude-opus-5-5), 2026-11-05
Branch: worktree-57-redispatch-scope

## Goal

第二次及以后的验证器派单，模板里只列需要重验的模型：没报告过的、报告后文件改过的、上次报告里有 FAIL 或 NEEDS_INFO 的，再加上这些模型在本次改动范围内的下游。第一次派单和 Stop 的三条规则不变。compare_by_key 先取证，确认重算工作区 `model_diff` 占单次耗时的大头后，再按文件指纹缓存。

## 已和用户定下的（2026-11-05）

- 依据：`docs/research/2026-11-05-verifier-wall-root-cause.md` §2、§3、§5 杠杆 2 和 4。pos-operations 每次重派都把 21 个模型全验一遍，派单原文写着"只有 fct_payment_analysis 变了"也照样全验。上游 SignalPilot 只对改过的模型重派（`data-agent-refs/SignalPilot/benchmark/signalpilot-plugin/skills/dbt-workflow/SKILL.md:270`）。
- 拆分：本工单只改 `verifier_v2/` 和 `verifier_tools.py`。验证器提示词里"只验派单列出的模型"那句，交给工单 47 第 4 轮退回改。两边改的文件不重叠，可以并行做；本工单先合入，47 再合并 main，然后重跑过线初筛。
- **下游闭包**：`model_hashes` 只对模型自己的 SQL 和配置取指纹（`stop.py:26-38`），上游模型 A 改了，下游 B 的指纹不变。所以重派列表要把重验集合在本次改动模型里的下游也带上（按 `ref()` 传递）。Stop 规则 2 不扩展：A 改了就会被规则 2 拦下，重派时 B 跟着进列表，就会被重验。
- 重派列表为空时（例如主 agent 在没有 FAIL、也没有改动的情况下又派了一次），退回全量列表（`changed_models`），不发空派单。

## Authority

- May edit：`src/data_agent/verifier_v2/dispatch.py`、`src/data_agent/verifier_v2/hooks.py`、`src/data_agent/verifier_v2/stop.py`（只加计算重派集合的函数，三条规则不改）、`src/data_agent/verifier_v2/project.py`（下游需要的 `ref()` 解析，或者缓存）、`src/data_agent/verifier_tools.py`（只改 `compare_by_key` 怎么拿已改模型列表）、`tests/`（`tests/acceptance/` 除外）
- Must not edit：`tests/acceptance/`、`src/data_agent/agents/verifier.md`、`src/data_agent/verifiers.py`、`skills/`、`src/data_agent/configs/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access：none（过线初筛由主会话在 47 合入前跑）
- Spend cap：$0（只有单元测试和不调模型的钩子模拟）

## Context

- 派单改写：`dispatch.py:16-22`（`TEMPLATE`）、`hooks.py:114-130`（`models=changed_models(workspace, snapshot)`）。spec「派单」：第一次派单只用模板，再次派单附上主 agent 的原文。
- 状态：`hooks.py` 里的 `state` 由 `record_checks` 按模型合并（`stop.py:58-69`），每个模型带 `checks` 和派单时的 `sha256`。重派集合用这份状态算，和 `stop_decision`（`stop.py:72-89`）用同一套口径：规则 1 对应没报告过，规则 2 对应指纹变了，规则 3 对应 FAIL；另外加上 NEEDS_INFO（它不拦 Stop，但主 agent 会为它重派）。
- `BLOCK_INSTRUCTION`（`hooks.py:44-48`）已经写着 "for every model named here"，不用改。
- 验收测试的接缝：`rewrite_dispatch(..., models=...)` 的签名不变（`tests/acceptance/test_verifier_v2.py:40,478-517`），变的只是 `hooks.py` 传进去的 `models`。
- compare_by_key：`verifier_tools.py:1574` 每次调用都重跑 `v2.model_diff(workspace, snapshot, dialect)`。工单 52 只缓存了快照侧（`model_diff.py:55`）和文件遍历（`project.py:199-209`）。服务器上单次 p50 10.5 s，本地 pos-operations 1.8 s（工单 50）。model_diff 在其中占多少，还没确认。
- 相关：工单 56（容器里 DuckDB 的线程数和内存）管 compare_by_key 并发时 CPU 打满的问题，本工单不碰。

## Acceptance

- [ ] 第一次派单列出全部 `changed_models`（和现在一样）。
- [ ] 第二次派单只列：没在 `state` 里的、`sha256` 和当前不同的、上次 checks 里有 FAIL 或 NEEDS_INFO 的，以及这些模型在 `changed_models` 范围内按 `ref()` 传递的下游。测试覆盖每一类，外加一个"A 改了、B ref A、B 上次 PASS"时 B 也在列表里的用例。
- [ ] 重派集合为空时，列表退回全量 `changed_models`。
- [ ] Stop 的三条规则行为不变：现有的 `test_stop_*`（验收测试和单元测试）全绿，一字不改。
- [ ] compare_by_key：先用本地 cProfile（pos-operations 或者 21 个模型的 fixture）给出单次调用的耗时拆分，写进 Comments。重算工作区 `model_diff` 占单次 ≥30% 才做缓存：按工作区模型文件指纹失效，文件一变就重算；配一个"改了文件之后拒绝名单跟着变"的测试。低于 30% 就不改，在 Comments 里写明数字。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出；一段不调模型的钩子模拟（照 `tests/test_verifier_v2_hooks.py` 的写法，在临时 workspace 里：派单 → 报告 → 改一个模型 → 再派单），贴出两次派单里拼出的 prompt；cProfile 的拆分。

## Stop conditions

- 重派范围的改动必须改验收测试才能通过：停下交回。
- 拿不到可靠的 `ref()` 依赖（比如模型用宏间接引用，正则抓不到），而且影响到正确性：停下交回，说明是哪种写法。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2（da-review，34b4173；eng:code-review，0152faf）
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: done（PR #55 → 40d77ad）；重派范围已实现，compare_by_key 不改（非首次调用 model_diff 占 26–28% < 30%）；branch `worktree-57-redispatch-scope`（475889f、34b4173、0152faf）
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（worktree 57-redispatch-scope，commits 475889f、34b4173、0152faf）

`-w` 会话写不了主 checkout，认领字段由主会话补填。

- 实现（`verifier_v2/{stop,project,hooks,dispatch}.py`，新增 `tests/test_verifier_v2_redispatch.py`）：
  - `stop.redispatch_models`：从 `stop_decision` 点名的模型（规则 1/2/3）出发，加上次 checks 有 NEEDS_INFO 的，再加这些模型按 `ref()` 传递的、在已改模型里的下游。下游按整个工作区的 `ref()` 走（`downstream_of`），只保留已改模型，所以 A（改）→ C（未改）→ B（改）仍会列出 B。
  - 钩子从第二次派单起调用它；集合为空或读文件出错时退回全量 `changed_models`。
  - 模板：再次派单的模型行改为 "Models to verify in this dispatch"（原 "Models model_diff lists as new or changed" 对子集不准，也是工单 47 交来的那条）；第一次派单不变。
  - `project.referenced_models` 解析 `ref('m')`、`ref("m")`、`ref('pkg','m')`、`ref('m', v=2)`；模型 Jinja 里调了字面 ref/source/var、`config`、`is_incremental` 以外的东西就返回 None，None 视作所有重验模型的下游（可能多列，不会漏）。所以没触发"ref 抓不到"的停止条件。
  - Stop 三条规则、`stop_decision`、`tests/acceptance/`、既有 `test_stop_*` 都没动。
- 验收测试：第一次派单全列 `test_first_dispatch_lists_every_changed_model`；重派每类一个（没报告过、文件改过及其下游 [a, b, d]、FAIL 及下游、NEEDS_INFO、改回快照、宏藏 ref、穿过未改模型）；空集合退回全量 `test_redispatch_with_nothing_to_verify_again_lists_every_changed_model`。穿过未改模型那条在第 1 轮的 `stop.py` 上失败，现在通过。
- 钩子模拟（`uv run python runs/t57/simulate.py`，不调模型；报告 1 除 `dim_staff` FAIL 外全 PASS，然后改 `stg_orders`）：
  ```
  dispatch 1: Models model_diff lists as new or changed: dim_staff, dim_store, fct_payment_analysis, rpt_payments, stg_orders
  Stop: block — dim_staff: rule 3 ...
  dispatch 2: Models to verify in this dispatch: dim_staff, fct_payment_analysis, rpt_payments, stg_orders
              ## 主 agent 提供的反证或补充信息（未经核实，只能当作需要复查的线索）
              Only stg_orders changed; dim_staff fixed.
  ```
- 真实 pos-operations 工作区（2377 个模型文件，21 个已改；`runs/t57/pos_refs.py`）：全 PASS 之后一个模型 FAIL，重派列出 21 个里的 3–5 个，每次约 0.6–0.7 s。21 个里有 1 个 ref 未知（`int_pos__order_timing` 调了列宏 `safe_ts('col')`），它和两个下游（`fct_order_fulfillment`、`fct_pos_orders`）每次重派都在列表里。
- compare_by_key 耗时拆分（`runs/t57/prof57.py`、`prof57b.py`，同一工作区、工单 50 的参考 SQL）：进程内第一次调用 model_diff 0.6–1.1 s、比较 0.5–0.8 s，model_diff 占 54–68%；之后的调用（15 次取中位数，三轮）model_diff 0.20 s、比较 0.54–0.57 s，占 28% / 26% / 27%（早期单次热样本 41%、37%、22%，噪声较大）。单次 profile：model_diff 0.46 s（`model_config` ×2377 约 0.24 s，sqlglot 解析 21 个模型约 0.11 s），查询 0.89 s（3 次 `duckdb.connect` 0.36 s、execute 0.43 s），`_forbidden` 0.25 s。**结论：不加缓存**：MCP server 整个 run 只起一次（`cli.py:97`），除第一次外都是后续调用，占比 < 30%。服务器上单次 p50 10.5 s 的拆分可能和本地不同，未测。
- `make check`：全过，basedpyright 0 errors，1832 passed、1 skipped、1 xfailed（`runs/t57/check3.txt`）。
- 审查：第 1 轮 `da-review main`（Codex）P1：宏或 `ref(var(...))` 里的 ref 漏抓，下游可能不被重验 → 34b4173 用 None 回退修。第 2 轮 eng:code-review 已修（0152faf）：下游遍历在中间的未改模型处中断（Spec）；模板标签对子集不准（Spec）；遍历拆成 `downstream_of`、测试共用一个钩子构造器（Standards）。不修：验证器/子 agent 判断在 `hooks.py` 和 `rewrite_dispatch` 各有一份（`rewrite_dispatch` 对其他工具返回 `{}` 是验收测试约定的契约，钩子那份避免每次 Agent 调用都读文件）；项目模型遍历又出现一次（抽公共函数要改 Authority 外的模块）；`project.py` 变大（拆分超出本工单）；被轮数上限截断的报告保留已写的 checks，已写 checks 全 PASS 的模型不再列出（与 `stop_decision` 和验收措辞一致）；宏回退会多列（数字见上；后续可读宏定义，把不调 `ref` 的宏如 `safe_ts` 视作已知）。第 2 轮修复后还没有模型审查过，PR 上的 Codex 审查是第一次。

### 2026-11-05 主会话（合入）

- PR #55：CI 绿（两次 check 都 pass）；Codex GitHub app 审查 0152faf，"has_any_findings": false。squash 合入 main → 40d77ad。
- compare_by_key 不加缓存的判断按"除第一次外的调用"算（26–28% < 30%）；按第一次调用算是 54–68%，服务器上的拆分没测。记为已知限制，交人看。
