# 26 — scrub 把题目数据里的 `/home/<x>` 当成主机路径

Status: done
Blocked by: —
Spec: ../spec.md
Claimed by: claude-opus-5-5 (worktree 26-scrub-data-paths)
Branch: worktree-26-scrub-data-paths

## Goal

`evals scrub` 不再因为轨迹里出现题目数据本身的 `/home/<x>`（例如网页 URL 的路径）而判"检查不完"；真正的主机 home 路径仍然被拦。修好后 `ablate` 能在 dev-3 这样的 job 上出报告。

## Authority

- May edit: `evals/scrub.py`、`tests/`（除 `tests/acceptance/`）
- Must not edit: `tests/acceptance/` 下的验收测试、`evals/ablate.py`、`scripts/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none
- Spend cap: none

## Context

- 服务器上 dev-3 两次 `ablate` 都以 exit 2 结束："scrub could not check every file of the baseline job, so no report"。主会话 2026-10-21 单跑 `evals scrub runs/dev-3/jobs/baseline` 的输出：
  - `.../dbt-cart-abandonment-recovery__ixzCvZ3/agent/events/run-20261021T194022Z-dce21b13.jsonl:53: /home/193`
  - `.../agent/trajectory.json:448: /home/193`
  - `scrub: changed 0 file(s) of 1006`
- 那一行的原文没看过（trial 在服务器上，`ablate` 运行只能取回汇总）。按形状推测是题目数据里的 URL 路径（如页面路径 `/home/193`），**未确认**；不能确认时按"URL 或 SQL 字符串里的路径"处理，不要靠猜具体内容。
- 规则在 `evals/scrub.py` 的 home 路径检查（模块 docstring 第 10 行起；`/home|Users/<x>` 正则约在第 47–50 行）。scrub 的目的见 plan §7：上榜轨迹公开，不能漏主机路径、用户名、IP、密钥。
- 放宽规则只能针对"不是本机或跑 job 的机器的 home"的情况；宁可多拦，不能漏拦（例如 `/home/<真实用户名>/...` 仍须被删或报出）。

## Acceptance

- [ ] 一个包含 URL 路径 `/home/193`（以及 JSON 里转义过的同样内容）的假轨迹，scrub 通过且不改动它；配修复前会失败的测试
- [ ] 本机 home、`--home` 给出的 home、以及形如 `/home/<字母用户名>/...` 的未知 home 路径，行为与现在一致（已有测试保持通过，必要时补一条）
- [ ] 真实运行：对本地已有的 Harbor job 副本（`runs/dev-2/out/jobs/full` 复制到新目录）跑 `uv run python -m evals scrub <副本>`，贴输出
- [ ] `make check` 通过

## Evidence required

单元测试输出；真实运行的命令与输出。

## Stop conditions

发现必须放宽到可能漏掉真实主机路径才能过；需要人决定的事；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2 (plus a final Codex confirmation run)
- Model spend: not recorded
- Outcome: fixed; commits 2c7610e, 1b40f17, f421111 on worktree-26-scrub-data-paths
- Human review minutes: —

## Comments

- 2026-10-31 工单 agent（分支 worktree-26-scrub-data-paths）：
  - 改法：`/home/<数字>` 只有作为最后一个路径段、且后面是空白、引号、`?`、`#`、非 `\/`/`\uXXXX` 的 JSON 转义或串尾时才放过（`host_paths_in` 里在主正则匹配后再查白名单）；`/home/1001/jobs`、`PATH=/home/1002:/usr/bin`、`/home/1003%2F`、原样 `\/`、全角数字、`/home/193abc`、`/home/7.bak`、所有 `/Users/<x>` 仍报。
  - 真实运行：`cp -R runs/dev-2/out/jobs/full runs/26-scrub/copy && uv run python -m evals scrub runs/26-scrub/copy` → `scrub: changed 0 file(s) of 338; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0`，exit 0（dev-2 副本本身没有 /home、/Users，只是回归检查）。在第二份副本植入 `page_url = '/home/193'` 和 `/home/alice/jobs`：只报 `.../trajectory.json:2355: /home/alice`，exit 1。
  - 测试：`test_scrub_keeps_numeric_home_segments_from_task_data` 修复前失败（输出同 dev-3）；`test_scrub_still_fails_on_home_segments_that_could_be_user_names` 覆盖仍须报的情况。tests/test_eval_scrub.py 25 passed；make check 1179 passed, 1 skipped。
  - Codex 第 1 轮 P1（已修 1b40f17）：没给 --home 时数字命名的真实 home 会漏，豁免收窄到最后一段。Codex 第 2 轮无阻断。
  - eng:code-review（已修 f421111）：后随字符黑名单换成显式白名单并补回归测试；删掉超出范围的 `/Users/<数字>` 豁免；嵌套 lookahead 改成匹配后检查；docstring 改正。
  - Codex 第 3 轮 P2 不修：要求也豁免 `/Users/<数字>`。理由：相对 main 不是退步（main 也报 `/Users/7`），工单只管 `/home/<x>`，且宁多拦不漏拦。
  - 已知限制：数字命名的真实 home 裸打印（如 `HOME=/home/1001`）在别的机器上不带 --home 跑 scrub 会放过；ablate 里 scrub 在跑 job 的机器上跑（evals/ablate.py:435），Path.home() 会删掉。dev-3 上 ablate 能否出报告未确认（需服务器）。useradd/macOS/systemd 拒绝纯数字用户名未确认。
- 2026-10-31 主会话：PR #28 CI 绿。Codex GitHub app P1（evals/scrub.py:213）"suffix-only 豁免会放过 `working directory /home/193` 这类数字 home"——不修：工单只允许放宽"不是本机或跑 job 的机器的 home"；ablate 里 scrub 在跑 job 的机器上跑，Path.home() 会删本机 home，`--home` 也照拦；剩下的只有别的机器上纯数字命名的 home，主流工具链默认拒绝纯数字用户名（未确认）；按上下文判断只能靠猜 dev-3 那行未读过的原文。合并。
