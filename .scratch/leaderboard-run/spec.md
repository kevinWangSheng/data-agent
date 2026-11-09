# Spec: data-eng-bench 上榜用的全量运行

人定（2026-11-08，plan §10 第 17 项；ADR-0004、ADR-0008 Update 2026-11-08）。

## 问题

榜单 CI 只认这样的 trial：来源是 `snowflake-labs/data-eng-bench`，每题的 `task.ref` 等于登记处 v1.0 的摘要（`data-eng-bench/leaderboard/src/leaderboard/core/hub.py:26-27,94`，`ci/static_analysis.py:148-176`），并且没有超时、倍率或资源覆盖（`ci/static_analysis.py:64-71,122-131`）。

`evals ablate` 用本地 `--path` 跑（`evals/ablate.py:415`），trial 的 `config.task` 只有 `path` 和 `"source": "tasks"`，第一步筛选就会被过滤掉。榜单又按（agent、agent 版本、模型、推理强度）分组，agent 版本就是 git commit（`evals/harbor_agent.py:233`），所以不同 commit 的 trial 也拼不到同一行。

## 决定

- 新增一种运行方式：只跑一个配置，题目从登记处的 `snowflake-labs/data-eng-bench@sha256:de31878dba7e5a9aad85dc8276820b0a20ab593aed46ff93dfbbbdb6007ec995` 取，可以用 Harbor 的 `-i` 拆批，同一个 OUT 里的几批合并计分。
- 第 17 项用它在一个新 commit 上全新跑 103 题 × 3，只跑 no-verifiers，约 309 个 trial、约 $15，只在闲时跑。
- `src/`、`skills/` 不动，agent 的行为和 b7c6071 一致（代码冻结的例外，ADR-0008 Update 2026-11-08）。
- 上传到 Hub 和清理轨迹不在这里，属于 plan §10 第 19 项，是对外动作，要人单独确认。

## 测试接缝

用 `tests/test_eval_ablate.py` 里现成的 FakeHarbor 接缝，加上 CLI 的 acceptance 接缝（`tests/acceptance/test_seam_c_eval_cli.py`）。不调真模型、不连登记处。只有一个工单，所以测试和实现放在同一个工单里用 TDD 完成，不单独开验收测试工单。

## Out of scope

- 改 `ablate` 的消融语义（split、参照配置、消融表）。
- 上传、清理轨迹、`lb submit`。
- DAB。
