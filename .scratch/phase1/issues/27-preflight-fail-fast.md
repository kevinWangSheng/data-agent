# 27 — 批量评测的预检、快速失败与运行记录

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

`ablate` 在花钱、花时间之前先确认这批能跑完并出报告：账户余额够、每个配置冒烟 1 题能跑通、冒烟结果过得了 scrub；跑的过程中账户类错误或连续的基础设施报错会让整批停下找人；每次运行留一份记录（benchmark commit、机器标签），供 ADR-0008 判断旧结果能否复用。

## Authority

- May edit: `evals/ablate.py`、`evals/report.py`（只限错误分类）、`evals/` 下新模块、`scripts/da-remote`（`check` 的余额项、`start` 传机器标签）、`docs/setup.md`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试、`evals/scrub.py`（工单 26）、`src/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（服务器实测由主会话在合并后做）
- Spend cap: $2（冒烟实测）

## Context

- ADR-0008"跑之前预检、跑的过程中快速失败"与"复用旧版结果"两条。
- **已核实的 bug**：`ApiUsageLimitError` 在 `evals/report.py` 的 `NOT_RETRIED` 里（照 Harbor 默认排除列表），所以 `is_infra_error` 判它为否，报告把它当 agent 失败记 0；它也不在 `evals/ablate.py` 的 `CONFIG_ERRORS` 里，`ablate` 不停。dev-3 里 no-skill 60/90、no-verifiers 90/90 都是它，`ablate` 照常往下跑了约 5 小时；只因为 scrub 另有问题（工单 26）才没出表，否则 no-verifiers 0% 会被当成真结果。它表示账户额度或余额用完，和 `CONFIG_ERRORS` 一样是"要人处理"的错误。
- `da-remote check` 现在只测 `https://api.deepseek.com/` 能连上（`scripts/da-remote` 约第 272 行，HTTP 401 也算 ok），看不出余额。DeepSeek 有查余额的接口（官方 API 文档 "Get User Balance"，用之前核对路径与返回字段）；key 只经环境变量传，不打印、不写文件。
- 事件文件的 version 头已有 `git_commit`、`skill_version`、`config`、`layers`、`model`、`sdk_version`、`cli_version`（`src/data_agent/agent.py:786-794`）；Harbor 由 `uv.lock` 锁定，随 commit 确定。缺的是 benchmark commit（tasks 目录所在 clone 的 HEAD）和机器标签。机器标签用人起的名字（如环境变量给出），不写主机名或 IP（scrub 会删它们，plan §7）。
- 停 Harbor 时用它自己的停止方式；如果只能发信号，只对本次启动的那个进程，按 `~/.claude/rules` 的进程规则来。
- 快速失败的阈值（连续几个）自己定一个默认值并写明理由。

## Acceptance

- [ ] `ApiUsageLimitError` 让 `ablate` 停下找人（exit 2，说明是账户问题），不出报告；配修复前会失败的测试。续跑时这类 trial 会被重跑
- [ ] 跑某个配置的过程中，连续 N 个基础设施报错就停整批、exit 2；有测试
- [ ] 预检：`ablate` 在第一个 job 之前查余额（低于本批预算的剩余部分就停）、每个配置冒烟 1 题、对冒烟结果跑 scrub；任何一项不过就停，不起正式 job；有测试。可以用参数跳过冒烟（续跑时）
- [ ] `da-remote check` 报出余额（只报是否足够和数额，不打印 key）
- [ ] 每次 `ablate` 在输出目录写运行记录：benchmark commit、机器标签、开始时间；有测试
- [ ] 真实运行（Mac colima，开发集 1 题，`full` + `baseline`，k=1）：贴预检输出与运行记录内容
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令与输出（遮掉余额以外的账户信息）。

## Stop conditions

余额接口与文档不符或需要额外权限；花费到上限；需要人决定的事；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
