# 11 — 评测 CLI：scrub 与累计花费

Status: ready-for-agent
Blocked by: 01
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

上传前的 scrub 命令能移除 API key、私有端点、IP、主机名；批量运行累计花费到上限 80% 时停止并提示找人。

## Authority

- May edit: `evals/`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

plan §7（上榜轨迹公开、清理脚本强制运行、80% 护栏）；`.gitleaks.toml` 里已有的 IP 与 key 规则可参考。

## Acceptance

- [ ] 接缝 C 中 scrub 与累计花费的验收测试转绿
- [ ] `make check` 通过

## Evidence required

对夹具轨迹跑 scrub 前后的差异（用假数据）。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0
- Outcome: —
- Human review minutes: —

## Comments
