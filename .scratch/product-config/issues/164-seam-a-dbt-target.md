# 164 — 修复 seam A 的 dbt 集成测试：显式传 `--dbt-target task`（140 漏改）

Status: in-review
Blocked by: 143
Spec: ../spec.md
Claimed by: claude (opus-5-5) 2026-11-28
Branch: worktree-164-seam-a-dbt-target

## Goal

`tests/acceptance/test_seam_a_run_cli.py::test_dbt_model_built_with_correct_values` 在真实模型下重新通过。原因：140 把 `data-agent run` 的 `--dbt-target` 默认改成 `scratch`（ADR-0010，写到每次运行的临时 schema），并给 Harbor、jaffle 两个评测入口显式加了 `--dbt-target task`，但漏了这个测试的 `run_agent`。这个测试要在任务库里查模型，照 140 对评测入口的做法，`--type dbt` 时显式传 `--dbt-target task`。

## Authority

- May edit：`tests/acceptance/test_seam_a_run_cli.py` 的 `run_agent`（`:61-77`）：`task_type == "dbt"` 时加 `--dbt-target task`；别的参数、断言、夹具、输入一字不改。这是 140 定下的规矩（产品默认 `scratch`，要写任务库的入口显式传 `task`）在漏掉的那一处的落实，不是为了让测试变绿而改断言。
- Must not edit：本文件的其他部分；其他验收测试；`src/`、`evals/`；其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access：none（真实运行由主会话在服务器上做）。
- Spend cap：工单 agent $0。

## Context

- 失败现象（143 合并前主会话在服务器上跑集成测试，`322d38d`）：agent 退出 0，建好了 `customer_ltv`，但在 `da_run_events_0bb466cc.customer_ltv`；测试在 `:161` 查 `main` 下的 `customer_ltv`，报 `Catalog Error: Table with name customer_ltv does not exist! Did you mean "da_run_events_0bb466cc.customer_ltv"?`。临时 schema 里的值和测试的期望一致（12 个客户）。
- 140 的改法与理由：`140-product-safety-defaults.md` 第 1 项（Harbor `evals/harbor_agent.py`、jaffle `evals/jaffle.py` 显式传 `task`）。这个测试是集成测试，`make check` 不跑，141 又标了 `TICKET_143`（strict xfail），所以一直没暴露。
- 归类：140 的遗漏（建票时没扫到这个入口），不是 143 的问题。

## Acceptance

- [ ] `run_agent` 在 `--type dbt` 时传 `--dbt-target task`，查数题不传。
- [ ] 这个文件的其他行没有改动（`git diff` 只有 `run_agent` 里这一处）。
- [ ] `make check` 通过。

## Evidence required

- `git diff main...HEAD` 全文。
- `make check` 的结果。
- 主会话在服务器上跑 `pytest -m integration tests/acceptance/test_seam_a_run_cli.py`，5 个都通过（合并前）。

## Stop conditions

- 发现要改断言或别的行才能通过：停下交回。

## Failure handling

照 AGENTS.md 工作流程。

## Ledger

- Attempts: 2 (first start was interrupted when the machine ran out of memory; the change it left uncommitted was checked and committed)
- Review rounds: 1 (da-review: no findings; eng:code-review Standards/Spec: no findings)
- Model spend: $0 (no real model run)
- Outcome: 7dc568c, tests/acceptance/test_seam_a_run_cli.py +3 lines (all inside run_agent)
- Human review minutes:

## Comments

- 2026-11-28（主会话建票）：143 合并前的集成测试发现；140 的遗漏，照 140 已定的规矩修。
- 2026-11-28 (ticket agent): run_agent now adds `*(("--dbt-target", "task") if task_type == "dbt" else ())`, same as evals/jaffle.py:364 and evals/harbor_agent.py:397. `git diff main...HEAD --stat`: 1 file, 3 insertions. `make check` exit 0; ruff, ruff format and basedpyright clean on the changed file; `data-agent run --help` shows `--dbt-target {scratch,task}`. da-review main: no findings (mocked runs: dbt gets the flag, query doesn't). eng:code-review main: Standards no violations; Spec all acceptance items met, nothing out of scope. Not fixed: the module docstring's interface list (:8-9) leaves out --dbt-target and :14 still says TICKET_143; Authority forbids editing outside run_agent, so it goes to a follow-up if needed. Pending (main session): `pytest -m integration tests/acceptance/test_seam_a_run_cli.py` on the server, 5 passing.
