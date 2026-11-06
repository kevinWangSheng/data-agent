# 60 — 项目自己的宏不再让模型"依赖未知"，重派列表不再退成全量

Status: ready-for-agent
Blocked by: —
Spec: ../../../docs/adr/0008-change-acceptance.md（Update 2026-11-06）
Claimed by: —
Branch: —

## Goal

主 agent 在项目 `macros/` 里写的、不会引用其他模型的宏，不再让调用它的模型被当成"refs 未知、是所有改动模型的下游"。重派只验真正需要再验的模型。

## 已和用户定下的（2026-11-06，grilling 第 2 轮 Q2）

- 只看项目自己的 `macros/` 目录（`dbt_project.yml` 的 `macro-paths`，默认 `macros`），不看 `dbt_packages/`。
- 一个项目宏算"不会引用模型"，要同时满足：
  - 宏体去掉 Jinja 注释后不含 `ref`、`adapter`、`dispatch`、`run_query`、`graph`；
  - 宏体里调用的其他宏也都满足这两条（不动点迭代；调用了包宏或未知宏就不满足）。
- 模型的 Jinja 里调用的只有这样的宏（加上现在已允许的 ref/source/var/config/is_incremental），`referenced_models` 返回它的 ref 集合而不是 None。
- 包宏（如 `dbt_utils`）仍算未知；`downstream_of` 对 None 的保守处理不变。
- 宏解析按工作区读，跟模型文件一样受工单 52 的缓存约束（目录 mtime）或每次重读，由实现定，但不能让单次 Stop/派单判定明显变慢。

## Authority

- May edit: `src/data_agent/verifier_v2/project.py`、`src/data_agent/verifier_v2/stop.py`、`tests/test_verifier_v2_redispatch.py`、新的测试文件
- Must not edit: `tests/acceptance/`、`hooks.py`、`dispatch.py`、`src/data_agent/agents/`、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: $0

## Context

- 证据（2026-11-06，读 `runs/t47-screen3/jobs/full/pos-operations__*`）：pos 每次重派都是 21 个模型，单次验证调用 535–684 s。按事件里的 Write/Edit 重建 SQL 后重算：20/21 个模型调用了主 agent 自己写的宏（`parse_pos_amount`、`pos_clean_money` 等），`project.py:299-306` 遇到宏就返回 None，`stop.py:131-133` 把 None 当成每个种子的下游，所以种子 1 个、6 个、8 个时列表都是 21。复算脚本：`/private/tmp/claude-501/-Users-shenghuikevin-dev-AI-data-agent/2ea30d9e-8846-4eab-b489-eb8d7ee12b9f/scratchpad/refs.py`（本机临时目录，可能已清理；思路是按事件顺序回放 Write/Edit 得到最终模型 SQL）。
- 现有代码：`referenced_models`（`project.py:299`）、`LITERAL_CALL_RE`、`redispatch_models` 与 `downstream_of`（`stop.py:100-137`）。

## Acceptance

- [ ] 新测试：模型调用一个纯 SQL 项目宏，`referenced_models` 返回它的 ref 集合（修复前返回 None，会失败）。
- [ ] 新测试：宏体含 `ref`、`adapter.dispatch`、`run_query`，或调用包宏/未知宏、或间接调用这样的宏，仍返回 None。
- [ ] 新测试：`redispatch_models` 在"种子 1 个 + 其余模型都只调纯 SQL 项目宏"的工作区上，只返回种子和真正 ref 它的下游。
- [ ] 离线复算：用 t47-screen3 的 pos trial（`HGcLuCF` 或 `qpLnwaW`）回放出最终工作区，重派列表少于 21 个模型；把前后数字贴进 Comments。
- [ ] `make check` 通过。

## Evidence required

测试输出和离线复算的前后数字。

## Stop conditions

- 必须改 `downstream_of` 对 None 的保守语义，或包宏的处理。
- 离线复算后列表仍是 21 且原因不在宏（交回并写明原因）。
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
