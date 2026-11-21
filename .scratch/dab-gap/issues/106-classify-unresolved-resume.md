# 106 — classify_text：未定结果、只重跑没标上的行、多列文本

Status: in-progress
Blocked by: —
Spec: ../spec.md（"分类工具（106）"）
Claimed by: claude (worktree 106-classify-unresolved-resume)
Branch: worktree-106-classify-unresolved-resume

## Goal

`classify_text` 能把拿不准的行记成 `unresolved`；能只对上一次没标上的行再调模型并合并结果；能一次给几列文本。产品和 DAB 两个工具服务器行为一致。

## Authority

- May edit: `src/data_agent/classify.py`、`src/data_agent/mcp_server.py` 和 `evals/dab_server.py`（只限这个工具的参数和注册）、`tests/test_classify.py` 和新测试、`tests/fixtures/`（新的非基准小样本）
- Must not edit: `skills/`、提示词（`agent.py` 里的任务提示）、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- 现状：`src/data_agent/classify.py`。结果类型 `LABELED`、`OUTSIDE`、`NO_LABEL`、`FAILED`、`EMPTY`（`:45-49`）；`ROW_LIMIT` 20,000，`BATCH_ROWS` 50，并发 8；模型调用关思考（工单 71）；预算和花费计入（工单 70、72）。
- 工单 70 的规则全部沿用：取数走只读网关、超限报错不截断、预算用完停下并报告、密钥不出现在事件和输出里、工具返回事实不返回猜测。
- 思路来源（只读，不复制原文）：Stela `semantic-analysis` 的 unresolved、`resume=`；摘要在 `docs/research/2026-11-21-dab-same-model-gap.md`。
- 设计要点见 spec：`unresolved` 用固定标记，写进系统提示，单独计数；重跑参数指向上次存下的结果，只对 `failed`、`no_label`（和可选的 `unresolved`）再调模型；多列按列名拼接。工具描述（`DESCRIPTION`）同步说明新参数。

## Acceptance

- [ ] 假模型对某行回固定的"拿不准"标记：该行 `unresolved`，不进任何类别计数，返回里单独计数（测试）。
- [ ] 重跑：上次结果里有 `failed`、`no_label` 的行，假模型只收到这些行；已标上的行原样保留；返回写明新调的行数（测试）。
- [ ] 重跑途中预算用完：照工单 70 停下，已有标签保留（测试）。
- [ ] 多列：两列文本按列名拼进同一行的提示（测试）。
- [ ] 不给新参数时，行为、提示和返回格式和现在一致（测试）。
- [ ] 产品和 DAB 两个服务器都注册了新参数（测试）。
- [ ] `make check` 通过。

## Evidence required

`make check` 输出。最终回复写好主会话用的命令：`make test-integration` 里分类相关的那条，加一次带重跑参数的本地运行。

## Stop conditions

- 重跑没法从上次存下的结果可靠地找到没标上的行（例如结果没存键）。
- 需要改 Skill 或提示才能让新参数被用上（另开讨论）。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —

## Comments
