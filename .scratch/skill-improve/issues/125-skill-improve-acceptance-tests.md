# 125 — 小范围自动改写 Skill：验收测试（输入范围、候选生成、分支与提交、初筛、判定、预算；全部不调模型）

Status: in-review
Blocked by: —
Spec: ../spec.md
Claimed by: claude (worktree 125-skill-improve-acceptance-tests)
Branch: worktree-125-skill-improve-acceptance-tests

## Goal

spec "Testing Decisions"（`.scratch/skill-improve/spec.md` 的 "## Testing Decisions" 一节）列了六组行为：输入范围、候选生成、分支与提交、初筛、判定、预算。每组都写成验收测试，落在一个只有签名的 `evals/improve.py` 空壳上。

- 测试全部标 `TICKET_126`（`xfail(strict=True, raises=AssertionError)`），由 126 转绿。
- 测试不调模型、不连服务器、不推到 GitHub、不联网，在 `make check` 里跑。judge、`da-remote`、`gh` 都用替身。
- 契约（Context "要写进契约的细节"）写进空壳的签名、文档字符串和测试，最终回复逐项列出，合并时人审。
- 本工单由人审过才合并，之后的工单不能改这些测试。

## Authority

- May edit：
  - `evals/improve.py`（新建）：只放类型、常量和签名，函数体 `raise NotImplementedError("ticket 126")`。
    - 不加进 `evals/__main__.py` 的子命令表，那是 126 的事。
    - 不加新依赖。
  - `tests/acceptance/test_skill_improve.py`（新建）。行为组多的话可以拆成几个 `test_skill_improve_*.py`。
  - `tests/acceptance/skill_improve_support.py`（新建），放这些替身和构造函数：
    - 假 judge；
    - 放在 `PATH` 最前面的假 `da-remote`、假 `gh`；
    - 临时 git 仓库，带一个本地 bare origin；
    - 构造 Harbor job、DAB 运行目录的函数。
  - `tests/fixtures/skill_improve/`（新建）：手写的小 Skill 目录、judge 回复、清单和回归集文件。
- Must not edit（参考实现例外：为验证测试，可以临时改，但不提交，验证完撤掉）：
  - `src/` 下的任何文件。
  - `evals/` 下已有的文件，包括 `accept.py`、`attribute.py`、`ablate.py`、`__main__.py`、`regression.json`、`manifest.json`。`accept.py` 的初筛模式由 126 加。
  - `scripts/da-remote`、`skills/`。
  - 已有的 `tests/` 文件和 `tests/fixtures/` 里已有的文件。要用其中的辅助函数，就照抄进 `skill_improve_support.py`，115 就是这么做的。
  - `Makefile`、`.github/workflows/`、`pyproject.toml`、`uv.lock`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`、`AGENTS.md`。
- Server access: none
- Spend cap: $0（不调模型）

## Context

### spec

`.scratch/skill-improve/spec.md`：

- "Implementation Decisions" 各节，含 Update 2026-11-24 的各条：
  - 输入与旧版（人定）；
  - 多个 job（更正）；
  - 分组（更正）；
  - 初筛命令改用 `ablate`（更正）；
  - `accept.py` 的初筛模式（更正）；
  - 推送与删除远端分支（人定）；
  - 花费口径（更正）。
- "Testing Decisions"。

取证笔记：`docs/research/2026-11-23-skill-self-improvement.md`。ADR：

- `docs/adr/0008-change-acceptance.md`：改动过关、复用旧版结果；
- ADR-0004：开发集与留出集；
- ADR-0005：不写针对题目的内容。

### 代码现状（HEAD 6b7ada6，逐条核对过）

- **还没有 `evals improve`**：
  - `evals/__main__.py:117-130` 的子命令表里没有它，`evals/improve.py` 不存在。
  - 现在跑 `uv run python -m evals improve x`，argparse 报 `invalid choice: 'improve' (choose from sample, score, …, attribute, dab, …, accept, …)`，退出码 2（2026-11-24 实测）。
- **注意 XPASS**：
  - 测试经空壳调用时，今天的失败是 `NotImplementedError("ticket 126")`。这不是 `AssertionError`，加了 `raises=AssertionError` 的标记会把它记成失败，不是 XFAIL。所以每个用例开头先断言入口已实现，再调用。做法任选：
    - 断言模块上的标志；
    - 捕获 `NotImplementedError` 后 `pytest.fail` 掉，用 `assert` 写，失败信息是 "ticket 126: improve is not implemented yet"。
  - 走 CLI 的用例要注意：上面那条 argparse 报错里有 `dab`、`accept` 这些字样，退出码也非零，所以"DAB 被拒绝"这类断言今天就成立。要先断言 `python -m evals improve --help` 退出码为 0。
  - 所有否定断言（没调 `gh`、没推送、没初筛）前面，都要先有一条正向断言。
- **归因**（`evals/attribute.py`）：
  - 入口 `attribute(job_dir, manifest, out_dir, judge, skills, tasks_dir, budget_usd, secrets)` 在 `:501`，只收一个 job 目录（`:518`）。spec 的更正要求多个 job，按配置合并，和 `evals/report.py:218` 的 `load_jobs` 一样。
  - judge 的类型是 `Judge = Callable[[str, float], Awaitable[JudgeReply]]`（`:137`）；`JudgeReply(text, cost_usd)` 在 `:131`。
  - 留出题只计数、不读：`dev_failures`（`:493`）。
  - 输出是一个失败一条：`failure_mode`、`skill`、`skill_quote`、`suggestion`（`:540`）。
  - 引用的 Skill 版本在 `skill_sets()`（`:145`）里找不到时，不归因（`:436-441`）。
  - 引用比较前先把空白归一（`:367`、`_normalize` `:335`）。
  - 测试先例在 `tests/test_eval_attribute.py`：`write_trial`（`:37`）、`reply`（`:68`）、`FakeJudge`（`:80`）。
- **判定**（`evals/accept.py`）：
  - `evaluate` 在 `:112`。
  - 条件：`point_estimate`（`:149-154`）、`targets`（`:155-159`）、`regression`（`:160`，补跑规则在 `:184-210`）。
  - 判定结果：`pass`、`fail`、`rerun-regression`、`rerun-old`（`:162-172`）。
  - 复用条件在 `:242-291`，`MAX_AGE` 14 天在 `:51`。
  - 今天的 `accept.py` 要求新 job 覆盖整个开发集（`:134`；`evals/report.py:348-363`；测试 `tests/test_eval_accept.py:487`）。126 加的初筛模式去掉这一条和点估计（spec 的更正）。
  - 测试只看 `improve` 的结果：判定、推送、PR、报告。不直接调初筛模式，它的签名由 126 定。
  - 构造 job 的先例：`tests/test_eval_accept.py:23` 的 `write_job`，写 `OUT/jobs/full/<task>__<i>/`、版本头和 `OUT/records/`。
- **初筛命令**：
  - `ablate --split dev --config full --trials 3 --task …`：参数在 `evals/ablate.py:161-200`，`--task` 的处理在 `:851-859`，`--budget` 必填（`:164`），预检多跑 1 个冒烟 trial（`:893`）。
  - 经 `da-remote start <name> -- <evals args>` 跑（`scripts/da-remote:15`），用 `pull <name>` 取回（`:29`）。
  - `setup [<commit>]` 只从 origin fetch，再 checkout（`:8`、`:306-341`）。所以分支要在初筛前推送。
- **`da-remote` 和 `gh` 没有 Python 封装**：`evals/`、`src/` 里只有注释提到 `da-remote`，`gh` 一处也没有。
  - 先例：`tests/test_da_remote.py:17-47` 把假 `ssh`、`rsync`、`uv` 放进 `PATH`；`:90-109` 的 `make_origin` 建本地 bare origin。
  - 假 `da-remote`、假 `gh` 照这个做法写，记下每次调用的参数。`gh` 的 PR 正文可能来自 `--body` 或 `--body-file`，也要记下。
  - 假 `da-remote pull` 把夹具 job 放进 `runs/<name>/`。
- **`skill_version`**：`src/data_agent/agent.py:1288-1295`。
- **回归集**：`evals/regression.json` 有 12 道真实的开发集题。`tests/fixtures/eval_manifest.json` 只有 3 道 dev 题。测试通过注入的清单和回归集文件，只用假题名。
- **DAB 的运行目录**：形如 `OUT/<dataset>/query<id>/run<k>/events.jsonl` 加 `OUT/submission.json`（`evals/dab.py:8-12`），没有 `<trial>/result.json`。`tests/fixtures/fake_dab/` 是 DAB 的脚手架，不是运行产出。
- **花费**：`evals spend` 对 DeepSeek 按价目表计价的运行，闲时记半价（`evals/spend.py:35`，`:78-90`）。预算用例的夹具花费，按全价、按半价算都超过 $25。
- **标记写法**：
  - `TICKET_126 = pytest.mark.xfail(strict=True, raises=AssertionError, reason="ticket 126: …")`。
  - 118 的写法见 `git show d42c579:tests/acceptance/test_charts_switch.py` 第 43-45 行；`raises=AssertionError` 照 115（`tests/acceptance/mcp_support.py:16`）。
- **类型检查**：basedpyright 也检查 `tests/`（`pyproject.toml:70`）。空壳要有完整的类型签名，测试才能 `from evals.improve import …`。

### 要写进契约的细节（本工单定，写进空壳和测试，最终回复逐项列出供人审）

1. **入口与注入**：
   - 入口函数要注入：judge（`attribute.Judge` 的形状；可以分归因和改写两个，也可以合成一个）、仓库根、清单、回归集文件、输出目录。
   - 运行 `da-remote`、`gh`、`git` 的命令名或路径，靠 `PATH` 替身换掉。
   - 入口接受多个输入 job 目录，按配置合并。
   - 其余参数名由本工单定。
2. **分组与挑选**：
   - 分组键是 FM 标签加 `(skill, skill_quote)`；
   - 按组里的失败次数取前 3 个；
   - 同一份 Skill 的同一段只出一个候选。
3. **改写的 judge 回复**：第二次 judge 调用，给出"原文片段 → 新片段"的替换，或者在某条后插入。
   - 回复格式（字段名、文件路径放在哪）的空壳签名和文档字符串由本工单写，126 照着实现。
   - 应用时按字节逐字匹配。匹配不到、改到 `skills/` 以外的文件，都不生成候选，报告里写原因。
4. **净增行数**：加的行数减删的行数，不超过 10 行。正好 10 行算通过，11 行不通过。测试包括这个边界。
5. **题数**：目标题和回归集取并集。并集超过 30 题就丢弃这个候选，写进报告。
6. **分支**：
   - `skill-improve/<日期>-<n>`，基于输入运行版本头里的 `git_commit`，只多一个提交，只改 `skills/`；
   - 初筛前推送到 origin；
   - 判定没通过的，初筛后删掉远端分支；
   - 推送、删除都只限 `skill-improve/` 分支。
7. **初筛参数**：
   - 传给 `da-remote start` 的 evals 参数是 `ablate --split dev --config full --trials 3`，`--task` 是并集里的每道题；
   - `--budget` 等于剩下的额度。
8. **判定之后**：
   - `pass`：推送的分支留着，`gh pr create --draft`，正文含 spec 那句固定说明，一字不改："开发集噪声大（单配置标准误约 ±2.2–2.5 分），通过初筛不等于显著提升"。
   - `fail`：删掉远端分支，不开 PR，只写进报告。
   - `rerun-regression`：在预算内自动补跑一次，只补跑要补的回归题，补跑的题算进 30 题上限；之后按补跑结果判。
   - `rerun-old`：报告里记为"未判定"，删掉远端分支，不开 PR。
9. **花费**：
   - 花费 = `evals spend` 算的实付花费加上 judge 的花费；
   - 每次初筛开始前检查，累计超过 $25 就停止剩下的初筛，报告写明停在哪个候选。
10. **输入范围**：
    - 留出题不进归因，也不进目标题；
    - DAB 的运行目录被拒绝，judge 不被调用；
    - 输入运行的 Skill 版本要能在 `skill_sets()` 里找到。

### 各组要写的测试（spec "Testing Decisions"，每条至少一个用例）

- **输入范围**：
  - 输入 job 里有失败的留出题 trial 时，judge 收到的提示里不出现这道题，所有候选的目标题都不含它。
  - 输入是 DAB 运行目录时被拒绝：judge 没被调用，没有分支，没有初筛。
  - 给两个 job 目录时，两个 job 里的失败都进了归因。
- **候选生成**：
  - 替换片段和原文字节完全相同才应用。
  - 下面几种情况都不生成候选，报告里写明原因，各一个用例：
    - 匹配不到（用词不同，不只是空白不同）；
    - 净增 11 行（另有一个净增正好 10 行的用例，能生成候选）；
    - 改到 `skills/` 以外的文件；
    - 并集超过 30 题。
  - 两组的 FM 标签不同、`skill_quote` 一字不差：只出一个候选。
  - 4 个组、失败次数各不相同：只取次数最多的 3 个。
- **分支与提交**（临时仓库）：
  - 每个候选一个分支，名字照契约 6，父提交是输入运行的 `git_commit`，只多一个提交，只改 `skills/`；
  - 用提交里的 Skill 目录算出的 `skill_version` 和输入运行的不同；
  - 初筛开始时，分支已经在 bare origin 上。
- **初筛**：每个候选传给假 `da-remote start` 的参数符合契约 7，题数不超过 30。
- **判定**：
  - `pass`：分支还在 origin 上；假 `gh` 收到 `pr create` 和 `--draft`；正文里有固定说明。
  - `fail`：先断言这个候选跑了初筛、报告里有它的判定，再断言 origin 上没有这个分支、`gh` 没被调用。
  - `rerun-regression`：回归题第一轮低于 3/3，补跑高于 1/3。断言补跑只跑了那道题，之后按 `pass` 处理。
  - `rerun-old`：旧版 job 超过 14 天。断言报告记为未判定，不开 PR。
  - 至少一个用例有两个以上候选，并且判定结果不同。
- **预算**：第 1 个候选初筛后累计超过 $25。断言后面的候选没有再调 `da-remote start`，报告写明停在哪里。

## Acceptance

- [ ] "各组要写的测试"的每一条都有断言，全部标 `TICKET_126`
- [ ] 当前全部以预期失败通过：没有 XPASS，没有被跳过的
- [ ] 每个用例今天的失败原因都是功能还没做（"ticket 126: improve is not implemented yet"），不是夹具、git、假命令或导入出错。最终回复每组贴一个用 `--runxfail` 跑出的真实失败原因
- [ ] 临时写过参考实现（不提交，含 `accept.py` 的初筛模式）：
  - 去掉标记后全部通过；
  - 每组至少一个反向检查会失败。例如：
    - 留出题进了 judge 的提示；
    - 只按空白归一的匹配也应用了替换；
    - 净增 10 行被拒；
    - 改了 `skills/` 以外的文件；
    - 取了前 4 个组；
    - 一个分支两个提交；
    - 初筛前没推送；
    - `fail` 的远端分支没删；
    - 初筛少了一道回归题；
    - `fail` 也开了 PR；
    - PR 正文没有噪声说明；
    - 超预算后还在初筛。
- [ ] 测试运行中没有碰到真实的 `gh`、真实的 `da-remote`、网络，也没有在主 checkout 里建分支或提交。最终回复写明怎么保证的。跑完在主 checkout 里执行 `git branch --list 'skill-improve/*'`，输出为空
- [ ] 夹具里没有密钥、地址、主机名：`uv run python -m evals scrub` 对 `tests/fixtures/skill_improve/` 的一份副本是 0 处改动
- [ ] 契约 1–10 都写在空壳、测试或夹具里，最终回复逐项列出
- [ ] `make check` 通过

## Evidence required

- `make check` 的完整输出。
- `uv run pytest tests/acceptance/test_skill_improve*.py -v` 的输出：每个用例的名字和 XFAIL。
- 每组一条 `--runxfail` 的失败原因。
- 参考实现和反向检查的结果。
- 主 checkout 里 `git branch --list 'skill-improve/*'` 的输出。
- 脱敏命令的输出。
- 契约清单。

## Stop conditions

- 第二轮审查修复后仍不过。

其余情况都不停：spec 没写到的细节由本工单定，写进契约供人审。测试有可能碰到真实的 `gh`、`da-remote`、网络或主 checkout 的分支时，先把替身和仓库根改对再跑，不要让测试真的碰到。

## Failure handling

停下时：最终回复写明 `Status: ready-for-human`，Comment 写试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 2
- Model spend: $0 (no data-agent model calls)
- Outcome: 27 acceptance tests, all xfail; reference implementation 27/27; 22/22 mutants caught; `make check` green
- Human review minutes: —

## Comments

- 2026-11-24（工单 agent 最终回复，主会话照抄）：

**What's on the branch** (`worktree-125-skill-improve-acceptance-tests`, 4 commits: 391c1ac, 013c8ac, 52b49f2, a441ce1)
- New files only, all on the Authority "May edit" list:
  - `evals/improve.py`: the shell. Types, constants and the `improve` signature; the body raises `NotImplementedError("ticket 126")`. Contract 1–10 is in the module docstring.
  - `tests/acceptance/skill_improve_support.py`
  - `tests/acceptance/test_skill_improve.py`
  - `tests/fixtures/skill_improve/`
- `src/`, existing `evals/` files, `__main__.py` and `Makefile` are untouched.

**Evidence**
- **`make check`**: exit 0. Python: `2850 passed, 2 skipped, 79 deselected, 27 xfailed in 616.69s`, the same 2850 passed / 2 skipped as the baseline. basedpyright `0 errors`; web `Tests 442 passed`.
- **`uv run pytest tests/acceptance/test_skill_improve*.py -v`**: all 27 are XFAIL, with no XPASS and no skips. One per required case; the parametrized tests expand to 2 each:
  - holdout trials excluded; DAB run refused; unknown Skill version refused; two input jobs merged;
  - byte-exact edit applied [replace / insert_after]; anchor not matched [other-words / whitespace-only];
  - net +10 lines makes a candidate; net +11 is discarded; edit outside `skills/` [README.md / skills/../README.md];
  - 30-task cap [29+1 screened / 30+1 discarded]; one candidate per Skill passage; top 3 groups only;
  - one commit on the input commit, `skills/` only; branch on origin when its screening starts; screening arguments and `--budget`;
  - pass gets a draft PR; fail loses its remote branch; regression task rerun alone; old version too old → undecided; rerun blocked by the task cap or budget → undecided [task-cap / budget]; mixed verdicts in one round; budget stop.
- **`--runxfail`**: every group (input, candidate generation, branches, screening, verdicts, budget, rerun blocked) fails with the same line: `E AssertionError: ticket 126: improve is not implemented yet`. Every fixture is built before the `improve` call, and every negative assertion comes after a positive one.
- **Reference implementation** (under the gitignored `runs/ref125/`, never committed; it includes an `accept.py` screening mode `evaluate_screening`): with it in place all **27 passed**.
- **Reverse checks**: 22 mutants, each fails at least one test.
  - The ticket's list: holdout task reaches the judge prompt; whitespace-folded match applied; net 10 rejected; edit outside `skills/`; top 4 taken; two commits on a branch; no push before screening; failed remote branch kept; one regression task dropped; PR opened on fail; noise note missing; screening continues over budget.
  - Extra: outside file in the commit, duplicate passage, "added lines" instead of net, listed cost instead of billed, jobs-only spend, setup on main, rerun of all tasks, branching from HEAD, setup before push, rerun ignoring the cap.
  - After each run, `git status --short` was clean.
- **No real `gh`, `da-remote` or network, and no branches here**:
  - Fake `da-remote` and `gh` come first on `PATH`; judges are injected fakes.
  - Git runs in a temporary repo whose origin is a local bare repo. Every `GIT_*` variable is removed, `GIT_CONFIG_GLOBAL` and `HOME` point at temp, `GH_CONFIG_DIR` is temp, and `DA_REMOTE_HOST=fake-host.invalid`.
  - The working directory is a temporary non-repo directory. The fixture checks after each test that this repository's `skill-improve/*` branches are unchanged.
  - Afterwards `git branch --list 'skill-improve/*'` printed nothing (count 0). I ran it from the worktree, which shares refs with the main checkout; the tool refuses `-C` into the main checkout.
- **Scrub**: `uv run python -m evals scrub` on a copy of `tests/fixtures/skill_improve/` gave `changed 0 file(s) of 7; redactions: key=0 endpoint=0 host=0 ip=0 home=0 user=0`, and `diff -r` shows no differences.

**Contract for human review** (in the `evals/improve.py` docstring, the tests and the fixtures)
1. **Entry**: `improve(jobs, *, repo, manifest, regression, out_dir, attribution_judge, edit_judge, skills, today, remote_tasks_dir, remote_manifest, tasks_dir=None, budget_usd=25, poll_seconds=60, secrets=())`, async, returns an `ImproveReport`. `jobs` are several job directories merged like `load_jobs`. `skills` has the shape of `skill_sets()`. `git`, `da-remote` and `gh` are called by bare name from `PATH`, with `repo` as the working directory.
2. **Groups and choice**: the key is (FM label, `skill`, `skill_quote`); size is the number of failed trials. Ties break by FM label, then Skill, then quote. A lower group quoting a passage already taken is dropped as `same-passage`. The top 3 become candidates 1–3; the rest are `not-selected`.
3. **Edit reply**: `{"file","op":"replace|insert_after","anchor","text"}`. The anchor must match the file's bytes at the input commit exactly once.
   - Discard codes: `bad-reply`, `no-match`, `ambiguous-match`, `outside-skills` (absolute path, `..`, or not under `skills/`).
   - The edit prompt carries the group's suggestions; the fake judge answers by them.
4. **Net lines**: new line count minus old line count; at most 10. 10 makes a candidate, 11 is `net-lines`.
5. **Tasks**: targets (sorted), then the regression tasks not among them, in file order. More than 30 is `too-many-tasks`; exactly 30 is screened.
6. **Branches**:
   - `skill-improve/<YYYY-MM-DD>-<n>`: one commit whose parent is the input run's `git_commit`, changing only `skills/`.
   - The branch is pushed right before its screening. After any verdict other than pass, the remote branch is deleted.
   - Only `skill-improve/` refs are pushed or deleted.
7. **Screening**:
   - Steps: `git push`, `da-remote setup <full commit hash>`, then `start improve-<date>-<n> -- ablate …`. `ablate` gets `--tasks-dir`, `--manifest`, `--out runs/<name>`, `--split dev --config full --trials 3`, one `--task` per task, and `--budget` = what is left, rounded down to the cent.
   - Then poll `status` until `<name>: finished, exit N`, and `pull` into `<repo>/runs/<name>/`.
   - The `--tasks-dir`, `--manifest` and `--out` options are added because `ablate` requires them.
8. **After the verdict**:
   - pass → `gh pr create --draft --base main --head <branch> --body-file …`; the body includes the noise note word for word.
   - fail → `rejected`.
   - rerun-regression → one rerun of only the listed tasks; if the 30-task cap or the budget doesn't allow it, the candidate is `undecided` and its remote branch is deleted.
   - rerun-old → `undecided` (未判定).
9. **Spend** = the judges' costs plus `evals spend` billing over each pulled run directory, which includes the off-peak half rate and the smoke trial.
   - Checked before every screening, reruns included.
   - Stop condition: spend over $25, or less than a cent left for `--budget`.
   - `stopped_at` names the candidate; it and the ones after it are `not-screened`. `report.md` names it on its "stopped" line.
10. **Input**: a directory without `<trial>/result.json` (such as a DAB run) is refused with `InputRefused`, as is a job on a Skill version `skills` doesn't have. Both happen before any judge call, branch or screening. Holdout trials are only counted (`skipped_non_dev_trials`).

Choices beyond the spec, for you to decide:
- A discarded candidate is not replaced by the next group down.
- A failed candidate's local branch is kept.

**Reviews** (2 fix rounds)
- **`da-review main`** found 2 issues, both fixed in 52b49f2:
  - The fake `setup` accepted any commit. It now refuses one origin doesn't have, as the real one does, and screenings check what the server was actually at.
  - `main` stayed at the input commit. It now advances, with a Skill change, past the input commit.
- **`eng:code-review main`**, fixed in a441ce1:
  - Standards: missing return type; duplicated branch listing, start predicate and body-file read; `Any`-typed callback; a hard-coded fixture alias.
  - Spec: rerun cap and budget were untested (new test added); contract 9's boundary contradicted "超过"; the report didn't have to name the stop; the fail test didn't check for "fail"; the PR body check didn't include the diff.
- Not fixed, with reasons:
  - `skill_improve_support.py` is about 690 lines. The Authority names a single support file, and the fake executables have to stay standard-library imports inside it.
  - `remote_tasks_dir` and `remote_manifest` are two plain path strings, not worth a type of their own.
  - The PR body's screening results and spend aren't asserted. Their format isn't fixed, and the required test bullet asks only for the noise note.


  - 最终核对（agent 的补充回复）：

- **`ref3.txt`**: with the reference implementation in place, `27 passed in 59.20s`.
- **`mutants3.txt`**: all 22 mutants make at least one test fail. They range from `holdout` (1 test fails) to `no_push` (19 fail).
- **`check3.txt`**: `make check exit=0`, with `2850 passed, 2 skipped, 79 deselected, 27 xfailed`, basedpyright `0 errors`, and web `442 passed`.
- Since then the worktree is clean (`git status --short` prints nothing), there are no `skill-improve/*` branches, and the branch head is a441ce1. The next step belongs to the main session: copy the Comments, Ledger and `Status: in-review` from the previous message into the ticket, then push and open the PR.
