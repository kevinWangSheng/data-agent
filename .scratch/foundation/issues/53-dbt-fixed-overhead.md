# 53 — dbt 固定开销：scan 每个 yml 只解析一次，评测适配层设 DBT_TARGET，Skill 不来回切 --vars

Status: done
Blocked by: —
Spec: ../../../docs/research/2026-11-05-foundation-latency-audit.md（§5）
Claimed by: claude (worktree 53-dbt-fixed-overhead, 2026-11-05)
Branch: worktree-53-dbt-fixed-overhead

## Goal

scan 一次从约 74 s 降到 15 s 以内；data-eng-bench 的每个 trial 第一次跑 dbt 不再完整重新解析；Skill 提醒不要来回切换 `--vars`。scan 的输出内容不变。

## 已和用户定下的（2026-11-05，grilling Q9、Q10、Q18）

- `scan_project.py` 改成每个 yml 只解析一次，建好"模型 → 所在 yml 文件和同级模型"的映射；文件头和 `NOTICE` 照现有格式注明修改。
- `DBT_TARGET=dev` 只放在评测适配层 `evals/harbor_agent.py`，只对 data-eng-bench 生效；agent 本体（`src/data_agent/`）和 Skill 里都不写（ADR-0005）。
- "不要来回切换 `--vars`（每切一次多一次完整解析）"写进 `skills/dbt-workflow/SKILL.md`。工单 47 也改这个文件；后合入的那张解决冲突（先例：49 与 47）。

## Authority

- May edit: `skills/dbt-workflow/scan_project.py`、`skills/dbt-workflow/SKILL.md`（只加 `--vars` 那一句，放在讲 dbt 命令的地方，不动 Step 8）、`evals/harbor_agent.py`（只加环境变量）、`NOTICE`、`tests/test_dbt_skills.py`、`tests/test_harbor_agent.py`、新的测试文件
- Must not edit: `src/data_agent/`、`skills/` 下其他文件、`tests/acceptance/`、其他工单的文件、`.claude/`、`CODING_STANDARDS.md`
- Server access: none（服务器真实运行由主会话跑）
- Spend cap: $0

## Context

- scan：`scan_project.py:576-590` 对每个缺 SQL 的模型都重新 `rglob("*.yml")` 并 `yaml.safe_load`；本地 base-image 项目 34 × 31 = 1055 次，40 s 里占 35.7 s。服务器 16 次 scan 有 15 次是 70–79 s。scan 本身不调 dbt。
- 完整重新解析：镜像用 `--target dev` 构建（data-eng-bench `base-image/Dockerfile:64`），target 算进 vars_hash；agent 跑 dbt 不带 `--target`，所以每个 trial 第一次是完整解析（43–83 s，之后 25–38 s）。工单 40（`.scratch/phase1/issues/40-guards-workdir-dbt-timeouts.md:29`）当时认为避免不了；本机 dbt 1.10.23 实测：设环境变量 `DBT_TARGET=dev` 后再跑 `--target dev`，走部分解析（dbt `cli/params.py:678` 的 `envvar="DBT_TARGET"`）。服务器容器里是否同样生效、镜像自带的 `partial_parse.msgpack` 能否复用，未验证。
- 环境变量注入点：要核实 `evals/harbor_agent.py` 里传给 `exec_as_agent` 的 env（`:312-325` 附近），以及 validate 子进程会不会继承。
- `--vars`：receivables 两个 trial 里来回切 `--vars`，多出 5 次 60–68 s 的完整解析。
- 数据：本机 `runs/t47-screen/foundation-audit/a5/dbtcalls.txt`（每 trial 的 dbt 调用序列）。

## Acceptance

- [ ] 新测试：在一个有多个 yml 的小项目上，scan 输出和修改前逐字相同；yml 解析次数等于 yml 文件数（修复前会失败）。
- [ ] 新测试：Harbor agent 给 data-eng-bench 运行设了 `DBT_TARGET=dev`。
- [ ] `test_dbt_skills.py` 断言 SKILL.md 有 `--vars` 那一句，且不含针对某道题的内容。
- [ ] 本地复测：base-image 项目的 scan 单次 ≤15 s（记下前后对比）。
- [ ] `make check` 通过。

## Evidence required

- 测试输出和本地复测数。
- 主会话的真实运行：服务器 `dbt-receivables-aging-buckets` × 1（full）。scan 单次 ≤15 s；trial 第一次 dbt 调用的输出里没有 "Unable to do partial parsing"。命令和输出贴到 Comments。

## Stop conditions

- scan 输出必须变化才能做下去。
- 服务器上 `DBT_TARGET=dev` 仍然触发完整解析：记录下来，交回由人定是否保留。
- 第 2 轮审查仍不过。

## Failure handling

Status 改为 `ready-for-human`，Comment 写明试了什么、哪里失败、分支名。

## Ledger

- Attempts: 1
- Review rounds: 1
- Model spend: $0
- Outcome: done，PR #50 squash 合入；本地 scan 19.6 s → 4.2 s，输出不变；服务器 t53-real2 1/1，scan 7–9 s，首次 dbt 16 s 部分解析；保留 DBT_TARGET=dev
- Model spend（主会话真实运行）: $0.1345
- Human review minutes: —

## Comments

### 2026-11-05 工单会话（1da43bb、3a1e09d）

**What changed**
- **Scan:** `scan_project.py` now reads each yml file under `models/` once and builds a lookup from model to its yml file and sibling models. Before, it re-read every yml for each model missing its SQL file. The old logic is kept exactly: the last matching file wins, and the `dbt_packages` and outside-the-project filters are unchanged. The file header notes the change. `NOTICE` is not edited because its entry already says "modified as noted at its top".
- **Adapter:** `evals/harbor_agent.py` adds `DBT_TARGET=dev` to the environment of the `data-agent run` call. The reviewer traced it through: Harbor's docker exec, then `cli.py` passing `os.environ`, then the SDK subprocess, which inherits the environment. Bash and `validate_project.py` therefore inherit it, and the command guard doesn't touch it.
- **Skill:** `skills/dbt-workflow/SKILL.md` Step 7 now says not to switch `--vars` between dbt calls, because each switch re-parses the whole project. I also added one clause to the header comment to record the change to this copied upstream file, which is slightly beyond the literal "only that sentence" in Authority. Step 8 is untouched.

**Tests (written first; each failed before its fix)**
- `test_scan_parses_each_yml_once_and_reports_the_same`: checks each yml is parsed exactly once and compares the report with `tests/fixtures/scan_multi_yml.md`, captured from the scan before the change. It failed with 35 parses for 5 files.
- `test_dbt_defaults_to_the_target_the_image_parsed_with`: checks that the `data-agent run` call gets `DBT_TARGET=dev`.
- `test_workflow_says_not_to_switch_vars_between_dbt_calls`: checks the sentence is in Step 7. The existing `test_skills_and_verifier_prompts_name_no_task` already covers task names.

**Local re-test** on a copy of data-eng-bench `base-image/dbt_models_duckdb` (35 yml files, 34 models to build):
```
uv run python <main's scan_project.py> /tmp/t53-bench/old_proj   real 19.61  (38.87 while other suites were running)
uv run python skills/dbt-workflow/scan_project.py /tmp/t53-bench/new_proj   real 4.19  (4.49 while other suites were running)
cmp of the two 10,537-line reports without the title line → IDENTICAL
```

**Not run yet (main session):** the server trial `dbt-receivables-aging-buckets` × 1 (full), checking scan time ≤15 s and that the first dbt call doesn't print "Unable to do partial parsing".

**Reviews**
- **Codex `da-review main`:** "Selected model is at capacity" on two attempts. Per the standing memory note, a read-only fable subagent reviewed instead.
- **Fable review:**
  1. Medium: the `DBT_TARGET` override risk above. Not fixed (open decision).
  2. Low: the test fixture doesn't cover a model defined in several yml files, or broken yml. The reviewer ran old and new code on those cases by hand and got identical output. Not fixed: which file wins depends on directory-listing order, so a pinned expected report could fail on Linux CI.
  3. Nit: missing space in the scan code. Fixed.
- **`eng:code-review main`, Standards axis:** no breaches of documented standards.
  - It said the parse test doesn't prove "only once". Not fixed: the test compares full lists, so a second parse fails it, and the Spec reviewer confirmed it fails on main's code.
  - It flagged copied-over duplication: a second yml walk, a substring `dbt_packages` check, and `.yaml` files ignored. Not fixed: changing any of these would change the scan output, which the ticket forbids.
  - It suggested a clearer name for `DBT_ENV`. Not fixed: the adapter only serves data-eng-bench.
- **`eng:code-review main`, Spec axis:** all files are inside Authority.
  - It flagged the header clause; that is noted above.
  - It said the env test didn't pin the variable to the `data-agent run` call. Fixed in `3a1e09d`.
  - It flagged the fixture coverage gap; same reason as fable item 2.
- **Rounds:** one round of fixes, then `make check` re-run: 1702 passed in 318 s.

### 2026-11-05 主会话

- `DBT_TARGET=dev` 会盖掉 agent 自己选的 target，这个风险按 grilling 的决定保留。理由：工单 49 合入后，提示词和 Skill 已经不再教 agent 改 `profiles.yml`，t49-screen 里 `profiles.yml` 改动 0 次。工单会话统计的 21 次 `target: prod`，都来自工单 49 之前的运行。服务器上是否真的走部分解析，看下面的真实运行；如果仍然完整解析，就按 Stop condition 交人定。
- 服务器真实运行 `t53-real2`（commit 3a1e09d）：`scripts/da-remote start t53-real2 -- ablate --tasks-dir ../da-benchmarks/data-eng-bench/tasks --manifest evals/manifest.json --split dev --task dbt-receivables-aging-buckets --out runs/t53-real2 --budget 2 --config full --trials 1`，结果 exit 0，reward 1（1/1），实际价 $0.1345（闲时），内存峰值 1.9 GB。第一次启动 `t53-real` 时我漏了 `--tasks-dir`/`--manifest`，evals 直接 exit 2，$0。
  - scan 共 2 次，8.6 s 和 7.4 s（≤15 s；t47-screen 是 70–79 s）。
  - 第一次 dbt 调用 `dbt ls` 用了 16.4 s，事件文件里 "Unable to do partial parsing" 出现 0 次。之前每个 trial 的第一次调用要 43–83 s。服务器上 `DBT_TARGET=dev` 生效。
  - Skill 新加了一句"不来回切 `--vars`"，agent 仍切了一次：`dbt run --vars 'reference_date: …'` 用了 52.5 s，切回去后的下一次用了 52.9 s，其余 dbt run 是 22–23 s。这句没能完全约束住，记成观察，不在本工单范围内。
- PR #50：CI 绿，Codex GitHub app 审查 3a1e09d 完成，没有意见。squash 合入。
