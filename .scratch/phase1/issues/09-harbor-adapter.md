# 09 — Harbor 适配器

Status: ready-for-agent
Blocked by: 04
Spec: ../spec.md
Claimed by: claude-code (opus-5-5)
Branch: worktree-09-harbor-adapter

## Goal

data-eng-bench 的题能经 Harbor 用我们的 agent 跑，结果与 token、花费回填进 Harbor 结果；顺带用 5 题真实运行测出单次成本。

## Authority

- May edit: `evals/`（Harbor 适配器）、`src/data_agent/`（只限适配需要的入口）、`tests/`（除 `tests/acceptance/`）、`pyproject.toml`、`uv.lock`
- Must not edit: `tests/acceptance/` 下的验收测试（唯一例外：本工单让某个测试转绿时，删掉该测试的 `xfail` 标记，其余一字不改）、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（本地 colima；批量留到服务器）
- Spend cap: $5

## Context

plan §5 Harbor 与 dbt 版本、§7 运行时注意事项（报错记 0、只对临时错误重试、关网页搜索、版本头）；ADR-0001（`BaseInstalledAgent` 的 `install()`/`run()`）；上游 `~/dev/AI/data-agent-refs/harbor`、`data-eng-bench/configs/`。

## Acceptance

- [ ] 单题经 Harbor 跑通并出分
- [ ] 网页搜索与抓取关闭
- [ ] 只对模型接口临时错误重试
- [ ] token 与花费回填
- [ ] 上传的轨迹与事件文件不含本机绝对路径和用户名（工单 03 的 workspace guard 拒绝消息会写入绝对路径）
- [ ] workspace guard 拒绝读 workspace 内的 `.env*` 等凭据文件（工单 03 已知限制）
- [ ] 没装 `git` 的环境里也能构建 wheel：`hatch_build.py` 捕获 `OSError` 回落 `unknown`（工单 03 已知限制）
- [ ] 5 题真实运行的单次平均成本写进 Comments，并更新 plan §7 的推算
- [ ] 5 题每题的峰值内存（`docker stats`）与用时写进 Comments，并更新 plan §7"服务器与并发"的并发推算（不改 `task.toml` 资源）

## Evidence required

Harbor 命令与结果摘要；5 题的成本统计。

## Stop conditions

Spec 自相矛盾或验收测试看起来有错（接口猜错）；需要人做决定；花费到上限；第 2 轮审查仍不过。

## Failure handling

`Status: ready-for-human`，Comment 写清试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0.28
- Outcome: 适配器可用，5 题实测完成；预算上限的问题待决定
- Human review minutes: —

## Comments

### 2026-10-16 工单 agent 交回（commits `c208e3c`…`62b838f`）

- 2026-10-16 实测（第 3 步）：`PYTHONPATH=. uv run harbor run --config evals/configs/data-eng-bench-duckdb.data-agent.yaml --path <data-eng-bench>/tasks -i dbt-daily-order-summary -i dbt-fix-division-by-zero -i dbt-fix-daily-cohorts -i dbt-rfm-customer-segmentation -i dbt-session-attribution -k 1 -n 2`（DATA_AGENT_* 照 `make test-integration` 的方式加载）。Harbor 汇总：Trials 4、Exceptions 1、Mean 0.400，用时 13m55s。session-attribution 装 agent 时 PyPI 超时，修复后（`400c6c2`）单独重跑：reward 0，无报错。
- 5 题（开发集）：每次 $0.0349–0.0375，平均 $0.036；约 78 万输入 token（93% 缓存）+ 1.2 万输出。峰值内存 373–772 MB（`docker stats` 每 3 秒采样）。用时：装 agent 62–135 秒，agent 90–363 秒，验证 1–23 秒。reward 2/5。plan §7 已更新。
- **待人决定**：`max_budget_usd=$1` 由 SDK 按 Claude 单价判断（SDK 记约 $1，按价目表约 $0.035），5 次里 4 次在 36–48 轮被截停（terminal_reason budget_exhausted）。上限怎么改需要决定，建议另开工单。
- 第一轮实测发现的适配器问题已修：workspace 从 `dbt_models_duckdb` 改为 `/app`（任务会在 `/app/dbt_project` 等处建项目，这一轮 0/3）；容器里从 astral.sh/GitHub 下载 uv 两次 TLS 失败，改成经 PyPI 安装，并加大 uv 的重试与超时。装 agent 时的网络错误不在重试列表里，记为报错，按基础设施报错重跑。
- 上传前清理：复制真实 job 目录跑 `scrub`，18/47 个文件被改（home=47），之后本机 home 路径与用户名 0 处，result.json 都能解析。agent 自己的事件文件只有容器路径。
- 审查：da-review 第 1 轮无问题；eng:code-review 的 Standards 与 Spec 意见已修（截断的事件行会盖住真实错误、断连不重试、NOTICE、测试清理）。不修：scrub 汇总固定列出全部类别（没有对应规则时也输出 0）；YAML 重试列表与 `TRANSIENT_ERRORS` 重复，有测试保持一致。da-review 第 2 轮 P2（用户名与 JSON 键同名时会改坏键）已修，加了回归测试。`make check`：742 passed，8 xfailed。
- 超出 Authority 的改动：`hatch_build.py`（验收要求）、`NOTICE`（job config 改自 data-eng-bench 的 claude-code 配置）、`docs/plan.md`（验收要求）。

### 2026-10-16 第 7 步：PR #13 退回第 1 轮（主会话）

`62b838f` CI 两次全绿。Codex 3 条 P1，主会话已核实。在原分支 `worktree-09-harbor-adapter` 上修，修完提交，不 push、不 rebase（需要同步 main 时用 `git merge origin/main`）；各配修复前会失败的测试。

1. **[P1] `evals/harbor_agent.py:185`**：容器里用 `python -m pip install uv` 引导 uv，违反"只用 uv、不用 pip"。改成不经 pip 的方式：优先把本机 uv 的 Linux 发行包（与容器架构一致、版本 `UV_VERSION`、核对 sha256）经 Harbor 的上传接口放进容器；或用 astral 官方安装脚本加 `curl --retry-all-errors` 重试。两种都走不通就停下交回 `ready-for-human`，写清卡在哪。
2. **[P1] `evals/scrub.py:89`**：只按运行 scrub 的这台机器的 home 和用户名清理；在服务器上产出、在本机清理的 job，服务器路径（`/home/<user>/...`）会留下来，scrub 还报成功。改为：可通过参数传入产出机器的 home 与用户名（可多个）；清理后扫描残留的主机绝对路径（`/home/<x>/`、`/Users/<x>/`、`/root/`），有残留就退出码非 0 并列出位置；容器内路径（如 `/app`、`/logs`）不算。
3. **[P1] 越权改 `docs/plan.md`、`NOTICE`、`hatch_build.py`：不退回**。三处都是验收标准或许可规则要求的改动；本工单 Authority 视为包含它们。

修完 `make check`；用改后的安装方式经 Harbor 真实跑 1 题；对一份复制的真实 job 用"别的 home/用户名"参数跑一次 scrub 并展示残留检查。再跑一轮 `da-review main`。最终回复给出 Comments 与 Ledger（Attempts 2）和 `Status: in-review`。
