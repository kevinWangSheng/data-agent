# 44 — 验证层 v2 的验收测试与夹具

Status: ready-for-agent
Blocked by: —
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

把 spec「Testing Decisions」里的每一条写成验收测试，放在 `tests/acceptance/test_verifier_v2.py`，夹具放在 `tests/fixtures/verifier_v2/`。测试里定下各个接缝的接口：模块名、函数签名、返回结构。全部标 `xfail(strict=True)`，`make check` 保持绿。人审后合入，之后 45–47 都不能改这些测试。

## 已和用户定下的（2026-11-04）

- 测试内容以 spec「Testing Decisions」为准，一条不少；不加 spec 里没有的行为。
- 接口由这张工单提出，人审时确认。约定如下：
  - 快照、4 个工具、报告解析、两个钩子，各自有一个**纯函数**入口，返回结构化结果（dataclass 或 dict）；MCP 和钩子只是很薄的一层包装。测试直接测纯函数，钩子测试用构造好的 hook 输入，不调模型。
  - 新代码放在 `src/data_agent/verifier_v2/` 包里（模块拆分由本工单提出）。测试只从这个包 import。
- spec 2026-11-04 修订后新增的测试，必须包含：
  - Stop 钩子按模型分别记录状态：先验证 A、B，B 报 FAIL；之后只重新验证 A 并通过，B 仍然拦截；
  - 4 个开着验证器的配置（full、no-skill、no-context、full-old-skill）都只注册一个 `verifier`；
  - `compare_by_key` 的相等规则：DECIMAL、浮点、其他类型、NULL，各一个测试；
  - 集成测试：`updatedInput` 整体替换 `Agent` 的 `prompt`。这条标 `integration`，不进 `make check`，测试体照 `docs/research/2026-11-04-agent-updatedinput-spike.md` 写。
- 夹具：
  - 一个小 dbt 项目，带"改动前"和"改动后"两个版本，覆盖 spec 列出的全部改动类型；
  - 一个小 DuckDB 文件，包含粒度键有 NULL、有重复、窗口排序不唯一的数据；
  - 一份 subagent transcript 样例，用来测报告解析和轮数统计。可以参考现有的 `tests/fixtures/subagent_transcript.jsonl`。

## Authority

- May edit: `tests/acceptance/test_verifier_v2.py`（新建）、`tests/fixtures/verifier_v2/`（新建）、`src/data_agent/verifier_v2/__init__.py`（只放让 import 成功所需的空桩，抛 `NotImplementedError`）
- Must not edit: 其他 `tests/acceptance/` 文件、`src/` 下其余文件、`skills/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- spec：`.scratch/verifier-v2/spec.md`（Implementation Decisions、Testing Decisions）；ADR-0009。
- 现有可复用的东西：报告解析的正则 `CHECK_RE`、`MODEL_RE`（`src/data_agent/verifiers.py`）；workspace 守卫（`src/data_agent/workspace_guard.py`），测试"agent 用 Read 读快照会被拒绝"时用它。
- 派单改写的实测结论：`updatedInput` 是整体替换，必须带上 `subagent_type` 等其他字段（2026-11-04 本地实测，见 spec「派单」）。
- 工单 01 的写法可以参考：`.scratch/phase1/issues/01-acceptance-tests.md`。

## Acceptance

- [ ] spec「Testing Decisions」的每一条都有对应的测试函数；在 Comments 里贴一张"spec 条目 → 测试函数名"对照表。
- [ ] 全部测试标 `xfail(strict=True)`，并且确实因为未实现（`NotImplementedError`）而失败，不是因为夹具错误。
- [ ] 夹具很小：dbt 项目少于 10 个模型，DuckDB 文件小于 1 MB。
- [ ] `make check` 通过。

## Evidence required

`uv run pytest tests/acceptance/test_verifier_v2.py -q` 的输出，显示全部 xfailed；对照表。

## Stop conditions

- spec 的某条测试写不出来，或者两条互相矛盾：停下，写明是哪一条。
- 要测某条行为就必须调模型。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: —
- Human review minutes: —

## Comments
