# 126 — `evals improve`：候选生成与应用、分支与提交、经 `da-remote` 初筛、`accept.py` 初筛模式判定、draft PR、报告、预算

Status: ready-for-agent
Blocked by: 125（done）
Spec: ../spec.md
Claimed by: —
Branch: —

## Goal

实现 125 留下的空壳 `evals/improve.py`，加上 `python -m evals improve` 子命令和 `evals/accept.py` 的初筛模式。一轮做完这些事：

1. 读一次开发集运行（一个或几个 job），对失败做归因；
2. 按失败模式分组，取前 3 组，每组生成一个增量修改的候选；
3. 每个候选一个 `skill-improve/<日期>-<n>` 分支、一个提交；
4. 推送分支，经 `da-remote` 跑 `ablate --split dev` 初筛；
5. 用 `accept.py` 的初筛模式判定；
6. 通过的开 draft PR，没通过的删掉远端分支；
7. 写报告，总花费不超过 $25。

完成标准：125 合入的 `TICKET_126` 验收测试全部转绿；最终回复给出主会话合入后要跑的完整命令（Context 第 8 项）。

## Authority

- May edit：
  - `evals/improve.py`：实现空壳。
    - 125 的类型、常量和 `improve` 的签名不改（`evals/improve.py:105-219`），契约 1–10 照原文实现（`:8-94`）。
    - 可以加私有函数、改写提示词、建 SDK judge、加 `add_parser` / `run`。
  - `evals/__main__.py`：只在 `modules`（`evals/__main__.py:117-130`）里加上 `improve`。
  - `evals/accept.py`：只加初筛模式（Context 第 4 项）。
    - 已有的 `evaluate`、`run`、`render` 和 CLI 的行为不变。
    - `tests/test_eval_accept.py` 一行不改，必须照样通过。
  - `tests/acceptance/test_skill_improve.py`：**只**删 `TICKET_126` 标记，以及因此没用的 `TICKET_126 = pytest.mark.xfail(...)` 定义（`:42-44`）和 import（AGENTS.md 硬规矩的唯一例外）。断言、夹具、输入一字不改。
  - `tests/` 下新建的单元测试文件：初筛模式、CLI、SDK edit judge 的提示和回复解析，以及验收测试没覆盖的分支。
- Must not edit：
  - `tests/acceptance/skill_improve_support.py`、`tests/fixtures/skill_improve/`、`tests/acceptance/` 里除删标记以外的任何内容；
  - `evals/attribute.py`、`evals/ablate.py`、`evals/report.py`、`evals/spend.py`、`evals/regression.json`、`evals/manifest.json` 和 `evals/` 下其他已有文件；
  - `scripts/da-remote`、`skills/`、`src/`；
  - 已有的 `tests/` 文件；
  - `docs/adr/`（ADR-0008 的 Update 由主会话在真实运行后写）；
  - `Makefile`、`pyproject.toml`、`uv.lock`、`.github/workflows/`、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`、其他工单的文件。
- Server access：工单 agent 没有。主会话合入后用 `scripts/da-remote` 跑（Context 第 8 项）。
- Git 与 GitHub：
  - 工单 agent 不推送，不调用真实的 `gh`，不在主 checkout 建 `skill-improve/*` 分支。
  - 测试和工单 agent 的真实运行只碰临时仓库和本地 bare origin。
  - spec 授权的推送、删除远端分支和开 draft PR，只在主会话合入后真实跑 `evals improve` 时发生，只限 `skill-improve/` 分支（spec "交给人"）。
- Spend cap：
  - 工单 agent $0：加载不了模型环境，也连不上服务器。
  - 主会话合入后的两次运行都已由人批准（2026-11-24）：
    - 开发集 30 题 × 3 `full` 一次，不计入 $25；
    - `evals improve` 第 1 轮，含归因在内总花费不超过 $25。
  - 都是已批准的运行，不再问；范围不能再扩大。

## Context

### 1. 行为来源

- spec `.scratch/skill-improve/spec.md`：
  - "Implementation Decisions" 各节，含全部 "Update 2026-11-24"：输入与旧版（人定）、多个 job、分组、初筛改用 `ablate`、`accept.py` 的初筛模式、推送与删除远端分支（人定）、花费口径、DAB 被拒；
  - "Testing Decisions"、"验收"。
- 125 的契约 1–10：
  - 2026-11-24 人审通过，原样合入（PR #135，`5a8bdfc`），已经定了，不再讨论。
  - 原文在 `evals/improve.py:8-94` 的 docstring，逐条清单在 `.scratch/skill-improve/issues/125-skill-improve-acceptance-tests.md:258-287` 的 Comments。
  - 契约之外 125 定的两点也已一并通过：丢弃的候选不由下一组补上；判定没通过的候选，本地分支保留。
- 125 的 agent 写过一份没提交的参考实现，在已删掉的 worktree 里，已经没有了。只能按契约和测试实现。
- ADR：
  - `docs/adr/0008-change-acceptance.md`：改动过关、补跑规则、复用旧版、预检；
  - ADR-0004：只用开发集，留出集不点名；
  - ADR-0005：Skill 里不写针对题目的内容。
- 取证笔记：`docs/research/2026-11-23-skill-self-improvement.md`（只参考上游的设计，不复制代码）。

### 2. 要转绿的验收测试（只删 `TICKET_126`）

- `tests/acceptance/test_skill_improve.py`：22 个带标记的测试函数，参数化展开后 27 个用例。文件里 24 处 `TICKET_126`：22 个标记、`:42` 的定义、模块 docstring 里 1 处（docstring 不动）。
- 分组：输入范围、候选生成、分支与提交、初筛、判定、预算（spec "Testing Decisions"）。
- 替身都在 `tests/acceptance/skill_improve_support.py`：
  - 假 judge：`FakeJudge`，`:411`；
  - 假 `da-remote`：`fake_da_remote`，`:311`。`pull` 用 cwd 的 `git rev-parse --show-toplevel` 定位，所以 `da-remote` 必须以 `repo` 为工作目录调用（契约 1）；
  - 假 `gh`：`fake_gh`，`:373`；
  - 临时仓库加 bare origin：`World.make_repo`、`advance_main`，`:500-525`。main 会越过输入 commit 往前走，所以分支只能基于版本头里的 `git_commit` 建，不能基于 HEAD；
  - `World.run`（`:575`）把 `NotImplementedError` 转成 `AssertionError`，所以今天全是 XFAIL。
- 测试里看得到、要照着做的几处：
  - 预算用例（`test_spend_over_the_budget_stops_the_remaining_screenings`）里，没初筛的候选也有分支，而且不在 origin 上。也就是说，先给所有候选建好本地分支，再一个一个推送、初筛。
  - 旧版超过 14 天的用例（`test_an_old_version_too_old_to_reuse_leaves_the_candidate_undecided`）要求照样初筛一次（`len(world.starts()) == 1`），之后判 `rerun-old`。因此不能在初筛前按年龄预先跳过。
  - spec "之后的轮次……不再满足时，这一轮在报告里写明需要新跑一次开发集，自己不开跑"：按测试做，先照常初筛、判成 `rerun-old`、记为未判定，报告里写明需要新跑开发集。

### 3. 归因：在 Authority 内拼起来，不改 `attribute.py`

- `attribute()`（`evals/attribute.py:501`）不能直接用：
  - 它只收一个 job（`:518` `load_trials(job_dir)`）；
  - 它会把自己的 `attribution.json` 和 `report.md` 写进 `out_dir`（`:549-552`），和 `improve` 的 `report.md` 撞名。
- 用这几块公开函数拼：
  - `evals.report.load_jobs`（`evals/report.py:218`）：按配置合并多个 job，合并不了就报错；
  - `dev_failures`（`evals/attribute.py:493-498`）：只取开发集失败，留出题只计数；
  - `attribute_one`（`:418-490`）：一个 trial 一次 judge 调用，带一次重试，校验引用和点名；
  - `Meter`（`:392-408`）：每次调用的上限是剩下的额度，没额度就不再调用。
- `Judge` 的形状、`JudgeReply(text, cost_usd)`、`skill_sets()`：`:137`、`:131-134`、`:145-149`。
- 输出一个失败一条。`failure_mode` 为 None 的是没归因成功的，不进分组（契约 2）。点名题目数据的建议会被扣下，`suggestion=None`（`:486`）。
- 引用的 Skill 版本在 `skills` 里找不到时，`attribute_one` 记错误、不归因（`:436-441`）。契约 10 要求在任何 judge 调用之前就以 `InputRefused` 拒绝，所以 `improve` 要先自己查一遍版本头。
- SDK judge：
  - `sdk_judge`（`:610-645`）的系统提示写死为归因用的 `JUDGE_SYSTEM`（`:68-71`）。
  - 归因 judge 直接用它。
  - edit judge 在 `improve.py` 里照它另建，换成改写用的系统提示。
  - 两者都照 `attribute.run`（`:662`）的做法在第一次用到时才建，这样被拒的输入和没有失败的输入不需要模型环境。
- `secrets` 用 `data_agent.events.known_secrets(os.environ)`（`src/data_agent/events.py:30`），和 `attribute.run`（`evals/attribute.py:680`）一样。

### 4. `accept.py` 的初筛模式

- 今天的 `evaluate`（`evals/accept.py:112-181`）：
  - 要求新 job 覆盖整个开发集：`:134` 的 `split_trials`（`evals/report.py:348-363`），加上 `:135` 的 `check_complete`；
  - 检查整个开发集的点估计：`:149-154`。
- 初筛模式只查 spec 规定的三项，门槛不改：
  1. 目标题：成功合计严格高于旧版，即 `:155-159` 的条件；
  2. 回归集：不退步，含补跑规则，即 `regression_check`（`:184-210`）。补跑的版本头必须和新 job 的一致（`:193`）；
  3. 复用条件：`reuse_problems`（`:242-291`），含 `MAX_AGE` 14 天（`:51`）、`CHANGED_BY_DESIGN`（`:56`）、run record 里的 benchmark commit 和机器（`:61`）。
- 判定的先后照 `:162-172`：回归退步是 `fail`，复用不了是 `rerun-old`，其余条件没过是 `fail`，还有要补跑的是 `rerun-regression`，都没问题才是 `pass`。
- 旧版：照今天，整个开发集、每题 `trials` 次，没有基础设施报错。
- 新 job：只要求覆盖候选的题（目标题加回归集），每题 `trials` 次，没有基础设施报错。
- 注意 `had_infra_errors`（`:294-301`）：`OUT/dropped/<config>/` 非空就算旧版有基础设施报错，见第 8 项。
- 函数名、签名、是否加 CLI 选项由本工单定。不改 `evaluate` 的签名和行为。

### 5. 初筛命令、`ablate` 和 `da-remote`（逐条核对过当前 main）

- `ablate` 的选项在 `evals/ablate.py:159-202`：
  - `--tasks-dir`、`--manifest`、`--out`、`--budget` 必填（`:161-164`）；
  - `--split dev`（`:171`）；
  - `--task` 可重复（`:172-174`），处理在 `:851-859`：不在 manifest 开发集里的题直接退出码 2；
  - `--n-concurrent` 是可选的（`:169`）。契约 7 没列它，不传。
- 退出码（`:67`）：0 是出表；1 是花费守卫停了这批；2 是要人看的问题（`Stop` 默认 `code=2`，`:140-144`）。
- `--budget` 不是硬上限。`guard_spend`（`:387-395`）只在每个 job 开始前查一次，80% 就停（`:671`）。每次初筛只有一个 job，所以一次初筛跑起来之后，不会因为预算中途停下。契约 9 的"每次初筛前检查"是唯一的闸。
- 预检都是 `ablate` 自己做的，在服务器上：
  - `check_balance`（`:471-491`，`:880` 调用）：账户余额要不低于 `--budget` 剩下的部分，否则 `Stop`，退出码 2；
  - `readiness.check_environment`（`:882-885`）；
  - 冒烟 1 个 trial（`:893`，`dev[:1]`，写在 `OUT/smoke/`），也算进 `evals spend` 的花费。
- run record：`make_record`（`:583-594`）记下 benchmark commit（`:588`，来自 tasks checkout）和机器（`DATA_AGENT_MACHINE`，`da-remote start` 设成主机别名）。初筛和开发集运行都用 `ablate`、都在同一台服务器上，所以能复用旧版。`board` 记的是 `DATASET_VERSION`（`evals/board.py:256`），复用不了，而且 `ZERO_TASK` 在 v1.0 上每次都得 0（`:78`）。
- 花费：用 `evals.walk.walk_files` 加 `evals.spend.file_spend(p).billed` 汇总拉回的运行目录，和 `ablate.spent`（`evals/ablate.py:460-468`）同一口径。闲时半价见 `evals/spend.py:35`。
- `da-remote`（`scripts/da-remote`）：
  - `setup [<commit>]`：
    - 先 `git fetch origin`，再 `checkout --detach`，最后 `uv sync`（`:306-342`）；
    - 服务器上有任何运行还活着就拒绝，退出码 1（`:309-318`）；
    - commit 不在 origin 上也拒绝（`:333-336`）。
  - `start <name> -- <evals args>`：
    - 在后台跑 `python -m evals <args>`（`:476-554`）；
    - 名字只能是 `[A-Za-z0-9._-]+`（`:71-77`）；
    - `--budget` 记进 `runs/<name>/budget`。
  - `status <name>`：第一行是 `<name>: <state>`（`:576`）。state 有三种（`:196-204`）：`running (pid N)`、`finished, exit N`、`stopped without an exit code`。后两种都是终态，轮询不能只认 `finished`。
  - `pull <name>`：拉进 `<checkout 根>/runs/<name>/`（`:623-815`，根由 `git rev-parse --show-toplevel` 定，`:99-104`）。ablate 运行只有证明全是开发集题时，才整个拉回（`:793`），否则只拉汇总。
- **装在 `~/.local/bin/da-remote` 的那份是旧的**（10 月 20 日的版本，和 `scripts/da-remote` 不一样）。它的 `pull` 对所有 ablate 运行都只拉汇总（它的 `:596`），拉回来没有 `jobs/full`，判定和花费都算不了。
  - 契约 1 要求按裸名从 `PATH` 调 `da-remote`，代码照契约写，不写死路径。
  - 主会话真实运行时把 `scripts/` 放到 `PATH` 最前面（第 8 项）。`scripts/` 里只有 `da-remote` 和 `da-review`，不会挡住 `git`、`gh`。
- 以前在服务器上跑 ablate 的写法（例如 `.scratch/verifier-v2/issues/47-verifier-prompt-skill-config.md:263`）：`--tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest evals/manifest.json`，和测试里的 `REMOTE_TASKS_DIR`、`REMOTE_MANIFEST`（`tests/acceptance/skill_improve_support.py:63-64`）一致。

### 6. 分支、`git_commit`、`skill_version`

- 版本头的 `git_commit` 来自 `git describe --always --dirty --abbrev=40`（`src/data_agent/agent.py:1299-1311`）。
  - 本仓库的 tag 都是轻量 tag，所以 describe 得到的就是 40 位 hash。b7c6071 的事件文件里就是完整 hash。
  - 带 `-dirty` 的值不是 commit，不能用来建分支。
- `skill_version`：`src/data_agent/agent.py:1289-1296`，对 `skills/` 下所有文件求哈希。125 的 Context 写的是 `:1288-1295`，已经偏了一行。
- 真实运行时 `repo` 是主 checkout。所有会话都共用它的工作区和 HEAD（AGENTS.md "Git 与 worktree"）。
- 每次推送都会触发 CI（`.github/workflows/check.yml:3-5`）。

### 7. 现有数据与花费估计

- 开发集 30 题，留出集 30 题（`evals/manifest.json`）；回归集 12 题（`evals/regression.json`）。
- b7c6071 的 `full` 分在三个 OUT 里：`runs/u1-target`、`runs/u1-full-rest`、`runs/u2-rest`，`jobs/full` 下分别有 15、25、50 个 trial，合起来 90。
- 它的 Skill 版本 `e075718e1fd7` 不在当前的 `skill_sets()` 里，所以作为输入一定被拒（spec "输入与旧版"）。工单 agent 可以拿它做被拒的真实运行（第 8 项）。
- 花费估计（推断，按 b7c6071 的 $8.83 / 90 trial，约 $0.10 一个 trial）：
  - 一个 15 题的候选是 45 个 trial 加 1 个冒烟，约 $4.5；
  - 3 个候选加归因，约 $15–18。
  - 一次初筛开始后不会中途停（第 5 项），最坏情况下最后一次初筛会超出剩下的额度。
- 用时估计（推断）：`t47-screen3` 跑 5 题 × 3 用了约 1.5 小时。一个 15 题的候选要几个小时，一轮可能十几个小时。

### 8. 真实运行：谁跑什么

工单 agent 不能加载模型环境，也连不上服务器（AGENTS.md，memory "worktree agent cannot source env"）。分成下面两部分。缺了主会话那部分，不是停下交回的理由。

**工单 agent 自己跑（AGENTS.md 工作流程第 3 步），都不调模型、不连服务器、不推送：**

1. `uv run python -m evals improve --help`：退出码 0。
2. 被拒的真实输入：`evals improve`，输入是主 checkout 里 b7c6071 的三个 job，用绝对路径：`/Users/shenghuikevin/dev/AI/data-agent/runs/{u1-target,u1-full-rest,u2-rest}/jobs/full`。
   - 应该以 Skill 版本 `e075718e1fd7` 不认识为由拒绝，退出码非零；
   - 没有 judge 调用，没有分支，没有 `da-remote` 调用；
   - 环境里没有模型变量也能跑完。
3. 初筛模式跑在真实 job 上：
   - 旧版是上面合并的 b7c6071 `full`；
   - 新 job 是 `/Users/shenghuikevin/dev/AI/data-agent/runs/t47-screen3/jobs/full`（5 题 × 3，`full`）；
   - 目标题和回归集从那 5 题里分，回归集写进 worktree 的 `runs/126/` 下的临时文件；
   - 预期是 `rerun-old`，因为超过 14 天、commit 也不同。贴出判定和 `reuse_problems`。
4. CLI 全流程，在临时仓库里跑：
   - 照工单 104、112 的做法，把 `DATA_AGENT_BASE_URL` 指向一个本地假模型端点，它按请求返回归因 JSON 和改写 JSON；
   - 把 `tests/acceptance/skill_improve_support.py` 的假 `da-remote`、假 `gh` 放在 `PATH` 最前面；
   - origin 是本地 bare 仓库；
   - 跑完一轮，贴出 `report.md` 和 `improve.json` 的摘录，以及 `gh pr create` 的参数。
   - 脚本放在 worktree 的 `runs/126/` 下，不提交。
   - 这一步验证真实的 SDK judge 接线和 CLI，单元测试覆盖不到这两处。
   - 假端点接不上 SDK 的话，写明原因和卡在哪里，不停下；主会话的第 1 轮会第一次真正碰到这条路径。
- 前 3 项读主 checkout 的 `runs/` 时被拦住的话，记下来，交给主会话跑。

**主会话在 126 合入后跑（工单 agent 在最终回复里写成能直接复制的命令，用本工单实际实现的参数名）：**

1. 开发集运行（人已批准；不计入 $25）。前提：
   - 本地 main 等于 origin/main；
   - 服务器上没有活着的运行（`setup` 会拒绝，第 5 项）。

   ```
   scripts/da-remote setup <main 的完整 hash>
   scripts/da-remote start dev-<YYYYMMDD> -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest evals/manifest.json --out runs/dev-<YYYYMMDD> --split dev --config full --trials 3 --budget <USD>
   scripts/da-remote status dev-<YYYYMMDD>     # 直到 finished, exit 0
   scripts/da-remote pull dev-<YYYYMMDD>
   ```

   - 拉回之后核对：
     - `runs/dev-<YYYYMMDD>/jobs/full` 有 90 个 trial，基础设施报错为 0；
     - `runs/dev-<YYYYMMDD>/dropped/` 不存在或为空。
   - 有基础设施报错时**不要**用同一个 `--out` 续跑。续跑会把报错的 trial 挪进 `dropped/`，于是 `had_infra_errors` 为真（`evals/accept.py:294-301`），每个候选都会判成 `rerun-old`。这时停下问人，因为再跑一次超出了已批准的范围。
2. `evals improve` 第 1 轮。前提：
   - 在主 checkout 里跑，HEAD 是第 1 步的 commit，`skills/` 和那次运行的一样，否则 `skill_sets()` 认不出这个版本，输入会被拒；
   - 模型账户余额不低于 $25（`check_balance`，第 5 项）；
   - 这一轮期间服务器上不跑别的；
   - 本地和 origin 上没有当天的 `skill-improve/*` 分支。

   ```
   set -a; . ~/.config/data-agent/env; set +a
   PATH="$PWD/scripts:$PATH" uv run python -m evals improve runs/dev-<YYYYMMDD>/jobs/full --manifest evals/manifest.json --tasks-dir <本地 data-eng-bench tasks> … 2>&1 | tee runs/improve-<YYYY-MM-DD>.log
   ```

   - 本地 tasks checkout：`../data-agent-refs/data-eng-bench/tasks`，在 a3278ad，和 `da-remote` 钉住的 `deb_commit` 一致（`scripts/da-remote:49`）。
   - 要跑几个小时：用后台运行，等它结束，不要用前台超时去等。
3. 跑完之后：
   - 主会话把 `report.md` 的结论、花费和 PR 链接抄进本工单的 Comments；
   - 结论照实写进 ADR-0008 的 Update，作为文档提交；
   - 人看 draft PR 是否可读、修改是否通用。
   - 一个都没通过也算验收通过，只要报告完整、花费在上限内（spec "验收"）。
   - 结论要求改代码的话，按工作流程第 7 步退回本工单。

### 9. 审查

不是前端工单，照 AGENTS.md 工作流程第 5 步：先 `da-review main`（125 在 2026-11-24 跑通过），再开新上下文跑 `eng:code-review main`。第 7 步照常等 Codex GitHub app。

### 10. 主会话 2026-11-24 定的点（实现时照做，不是停下的理由）

都由 spec、人已批准的 125 契约和现有代码推出来，主会话对照代码定：

1. 建分支不碰主 checkout 的 HEAD、index 和工作区：用 git plumbing（`hash-object` / `mktree` / `commit-tree` / `update-ref`）造提交。只改 `skills/`，跳过 pre-commit 可以接受。
2. `skill: "none"` 或只有被扣下建议的组：照契约 2 留在分组里。`none` 组把全部 Skill 文件交给修改 judge，让它选 `insert_after` 的锚点；没有可用建议的组也照样做一次修改调用（带失败模式和引用）。
3. 修改文字里点名题目或数据：用 `attribute.named_in` 查，带反馈重试一次，还不行按 `bad-reply` 丢弃并写明原因；`Discard` 类型不变。
4. 修改 judge 的重试：`bad-reply`、`no-match`、`ambiguous-match`、`net-lines` 各带反馈重试一次（同 `attribute` 的 `MAX_ATTEMPTS = 2`）。
5. `git push`、`setup`、`start`、`pull` 失败，`ablate` 非零退出，或状态是 `stopped without an exit code`：该候选判未判定并写原因、删远端分支、停下后面的初筛、写报告；不自动续跑。
6. 某个候选 `rerun-old` 之后，其余候选照常初筛（契约里候选互相独立）。
7. 同一天第二轮：`skill-improve/<date>-*` 分支或 `runs/improve-<date>*` 已存在就一开始拒绝（否则服务器上的 ablate 会悄悄复用旧的 OUT）；加可选 `--date`。
8. 不加 `--dry-run`：假端点的整轮运行已覆盖接线，dry run 会让归因花两次钱。
9. 输入 commit 是 `-dirty`、`unknown` 或本地没有：抛 `InputRefused`。
10. 初筛模式：单独的 `evaluate_screening(...)`，返回和 `evaluate` 同形的 dict、去掉 `point_estimate`；`accept` 加 `--screening`，方便主会话手动重判。旧版 job 仍按整个开发集检查。
11. 主会话跑开发集那次的 `--budget` 是 15 USD（b7c6071 同规模标价 $8.83）。
12. 其余：不传 `--n-concurrent`；CLI 默认值 `--regression evals/regression.json`、`--out runs/improve-<date>`、远端路径照测试、`--repo` 是仓库根；PR 标题 `skill-improve <date>-<n>: <FM> <skill>`；每个候选做完就写一次 `report.md`，不只在最后写。
13. 后面几轮旧版不能复用时：`improve` 自己不开跑新的开发集；候选照常初筛，判 `rerun-old`，报告写明需要新跑一次开发集（spec 那句和已合入的验收测试一致，主会话已在 spec 里补明）。
14. `--budget` 不是硬上限（`ablate` 只在每个 job 开始前查，`ablate.py:387-395`）：一次初筛可能让本轮总花费略超 $25。照 spec "超过就停止剩下的初筛"做，报告写明实际花费；估计 3 个候选约 $15–18（推断）。
15. 归因在 Authority 内用 `load_jobs`、`dev_failures`、`attribute_one`、`Meter` 拼起来，修改 judge 写在 `improve.py` 里；不改 `attribute.py`。
16. 已装的 `~/.local/bin/da-remote` 是 10-20 的旧版，和 `scripts/da-remote` 不同（主会话已核实）；主会话真实运行时一律 `PATH="$PWD/scripts:$PATH"`。

## Acceptance

- [ ] `tests/acceptance/test_skill_improve.py` 的 27 个用例全部 PASSED。`git diff main -- tests/acceptance/` 只有删掉的 `TICKET_126` 标记行、它的定义和因此没用的 import
- [ ] `tests/test_eval_accept.py` 一行没改，全部通过；初筛模式有自己的单元测试，至少覆盖三项条件各自不过、补跑、复用不了，以及新 job 少了候选的某道题时被拒
- [ ] `python -m evals improve --help` 退出码 0；子命令只是在 `evals/__main__.py` 的 `modules` 里加了一项
- [ ] 代码里调用 `git`、`da-remote`、`gh` 都按裸名、以 `repo` 为工作目录；推送和删除只针对 `skill-improve/` 开头的 ref（单元测试断言：拒绝推送其他 ref）
- [ ] 跑测试和工单 agent 的真实运行时，主 checkout 里 `git branch --list 'skill-improve/*'` 和 `git ls-remote origin 'refs/heads/skill-improve/*'` 都是空
- [ ] Context 第 8 项工单 agent 那 4 次真实运行有命令和真实输出（第 4 次接不上时写明原因）
- [ ] 最终回复里有主会话要跑的完整命令（Context 第 8 项），参数名和实现一致
- [ ] 主会话做（不阻塞本工单交回）：开发集运行和 `evals improve` 第 1 轮。至少生成 1 个候选并跑完初筛，实际花费不超过 $25；结论写进报告和 ADR-0008 的 Update
- [ ] `make check` 通过

## Evidence required

- 改动前的基线：`make check` 的 pytest 汇总。
- `make check` 的完整输出。
- `uv run pytest tests/acceptance/test_skill_improve.py -v`：每个用例都是 PASSED。
- `git diff main -- tests/acceptance/`。
- 第 8 项 4 次真实运行的命令和输出。
- 主 checkout 里那两条 `skill-improve/*` 查询的输出。
- 交给主会话的命令清单。
- 实现时自己定下的细节（见 Stop conditions 最后一条）。

## Stop conditions

- 要改 125 的验收测试、支撑文件或夹具（除了删 `TICKET_126`）才能转绿，或者觉得某条验收测试错了。
- 要改 125 的类型、常量、`improve` 的签名或契约 1–10 的行为才能实现。
- 要改 Authority 以外的文件，例如 `evals/attribute.py` 或 `evals/ablate.py`。
- 初筛模式要改 `evaluate` 已有的行为，或者让 `tests/test_eval_accept.py` 失败。
- 第二轮审查修复后仍不过。

其余情况不停。spec、契约和 Context 第 10 项都没定的细节，选不多花钱、不多推送、不多删东西的做法，集中在一个常量或一个函数里，方便改。最终回复的"待人定"标题下逐条列出：选了什么、依据、改起来要动哪里。中途不问。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 0
- Review rounds: 0
- Model spend: $0 (data-agent model calls only, read from run event files; Claude Code / Codex subscription usage is not recorded)
- Outcome: — (accepted | returned)
- Human review minutes: — (filled by the human at merge, optional)

## Comments
