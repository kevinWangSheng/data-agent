# 57 — 重派只列需要重验的模型，compare_by_key 不再每次重算 model_diff

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

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

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
