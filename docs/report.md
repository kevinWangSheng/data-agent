# Evaluation report

This report backs the numbers in the [README](../README.md): what was run, what each result does
and does not show, what it cost, and why the failed tasks failed. Everything was measured with
`deepseek-flash`; the last run finished on 2026-11-22. The run-by-run record, in Chinese, is
[`docs/adr/0008-change-acceptance.md`](adr/0008-change-acceptance.md).

## Summary

- **data-eng-bench** (103 dbt tasks × 3 trials): 64.1% ± 1.6 accuracy with the `no-verifiers`
  config, for $15.16.
- **DataAgentBench** (54 queries × 5 trials): the blind run scored 0.7421 Pass@1 with Skills on,
  against 0.7012 for the baseline; this is the submitted score. Two later rounds of general changes,
  made after looking at this benchmark's results, reached 0.7588 and 0.7639, while the baseline rose
  to 0.7614. They count as a tuned prompt by the leaderboard's definition and are not submitted.
- **Ablation**: on 30 held-out tasks, no paired difference between configs is significant. The data
  cannot say which layer helps.
- **Verifier**: the verifier layer roughly doubled the cost per successful task and did not raise
  accuracy in any measurement, so the recommended config leaves it off.
- **Cost**: all formal evaluation runs together cost about $106. Development-set iteration before
  that cost about ¥541.
- **Failures**: on dbt tasks the largest fixable causes were in the tools and the harness, not in
  the model's reasoning. On DataAgentBench, 23 of the 60 failures in the first run came from the
  questions or the grading scripts, and the grading is lenient enough that listing alternative
  answers raises the score.

## Setup

| Item | Value |
|---|---|
| Model | `deepseek-flash` over an Anthropic-format endpoint |
| Machine | one 6-core, 8 GB Linux server with Docker, three data-eng-bench trials at a time; DataAgentBench ran four datasets in parallel |
| dbt benchmark | data-eng-bench, run in containers through Harbor, graded by the benchmark's scripts |
| Query benchmark | DataAgentBench, its 54 leaderboard queries on 12 datasets, graded by its `validate.py` scripts, with the benchmark's official hints |
| Comparison | paired bootstrap over tasks, 95% interval; accuracy is reported ± its standard error, computed by the benchmark's scoring functions |
| Cost | billed price, read from each run's event log; off-peak hours bill at half the list price. The full data-eng-bench run and the first two full DataAgentBench runs were entirely off-peak; about 16% of the held-out run's event files started at peak |

A trial is one run of one task. Other terms used below:

- **Context tools**: `discover`, `hydrate` and `dictionary_search`, which look up schema summaries
  and real column values on demand.
- **Guard rejection**: a tool call refused by the command guard or the workspace guard (the command guard was later removed in ADR-0010 when shell commands moved to `sandbox_exec`).
- **Run names**: dev-3, accept-1, accept-2 and "final" are four successive runs of the development
  set, described under [How the development-set score moved](#how-the-development-set-score-moved).
  The final run was done in two stages.
- **Tickets**: numbered work items; each one's spec, review findings and real-run output are in
  [`.scratch/`](../.scratch/).

### Configs

Each layer of the agent is a switch in a run config.

| Config | Skills | Context tools | Verifier |
|---|---|---|---|
| full | on | on | on |
| no-verifiers | on | on | off |
| no-skill | off | on | on |
| no-context | on | off | on |
| baseline | off | off | off |

Turning the verifier off removes more than the verifier subagent: the `map_columns` tool, the note
that points the agent at the task file, and the system-prompt section "Verification before delivery" go
with it. The main agent's own checks stay.

On DataAgentBench the agent uses the benchmark's tool server, which has no context tools, and
query tasks have no verifier. The only layer there is Skills, so `no-context` is the "Skills on"
config and `baseline` is "Skills off".

### Splits

data-eng-bench has 103 tasks. They were split before development started:

- **Development set**: the benchmark's own `fast-30` list, 30 tasks. Failures here were read in
  detail and drove the fixes.
- **Held-out set**: 30 of the remaining 73 tasks, sampled by difficulty with a seed kept in git.
  Only summary scores were looked at; per-task failures were never read or used.

The development set is harder than the benchmark as a whole, so scores on the two sets are not
comparable with each other.

DataAgentBench has no such split. Its results were not used to change the agent, with two
exceptions described under [DataAgentBench failures](#dataagentbench-failures).

## Ablation

### Held-out set

30 tasks × 3 trials × 5 configs, 450 trials, commit `b7c6071`, 2026-11-09 to 2026-11-11.

| Config | Accuracy |
|---|---|
| full | 66.7% ± 1.6 |
| no-verifiers | 71.1% ± 1.9 |
| no-skill | 64.4% ± 2.5 |
| no-context | 57.8% ± 2.2 |
| baseline | 65.6% ± 2.7 |

| Comparison | What it isolates | Difference (points) | 95% interval | Significant |
|---|---|---|---|---|
| full − baseline | all layers | +1.1 | −7.8 to +11.1 | no |
| full − no-skill | Skills | +2.2 | −3.3 to +7.8 | no |
| full − no-verifiers | verifier | −4.4 | −11.1 to +1.1 | no |
| full − no-context | context tools | +8.9 | −1.1 to +21.1 | no |
| no-verifiers − baseline | recommended config against baseline | +5.6 | −3.3 to +15.6 | no |

Every interval crosses zero. That 30 tasks × 3 trials would probably be too few to show a
significant difference was written down as a known risk before the run. The result is "no
direction can be read", which is weaker than "the layers do nothing".

Two things make the context-tools row hard to interpret even apart from its interval. The context
tools were rarely called on dbt tasks: in 97 full-config trials on the development set,
`dictionary_search` was called 5 times, `hydrate` 9 times and `discover` once. And `no-context` was
the lowest config, which a rarely used layer does not explain. I have no evidence for the cause.

### Development set: choosing the default config

30 tasks × 3 trials × 3 configs, 270 trials, the same commit `b7c6071`, 2026-11-06 to 2026-11-08.

| Config | Accuracy | Successes | Billed cost | Cost per success | Median trial time |
|---|---|---|---|---|---|
| full | 66.7% ± 2.5 | 60/90 | $8.83 | $0.147 | 555 s |
| no-verifiers | 67.8% ± 2.5 | 61/90 | $4.42 | $0.072 | 405 s |
| baseline | 65.6% ± 2.2 | 59/90 | $3.57 | $0.061 | 317 s |

No paired difference is significant here either: full − baseline +1.1 (−11.1 to +13.3),
full − no-verifiers −1.1 (−7.8 to +5.6), no-verifiers − baseline +2.2 (−8.9 to +14.4).

Pairing the same 270 trials task by task (read on 2026-11-23) finds no kind of task that the layers
systematically lose. Against baseline, full wins 4 tasks, loses 5 and ties 21; no-verifiers wins 4,
loses 4 and ties 22. By kind, the layers are ahead on the 7 fix-an-existing-project tasks (full
16/21, no-verifiers 17/21, baseline 14/21) and level on the 23 build-new-models tasks (44, 44 and
45 of 69). Two tasks are lost by both configs by two or more of three trials:

- `dbt-rfm-customer-segmentation` (1/3 and 1/3 against 3/3): 4 of the 6 trials built the metrics
  from the customers table with an inner join, which dropped 5 customers who have orders but no
  customer row. This is a repeatable mechanism, but it was seen on this one task.
- `tier-migration-analysis` (0/3 and 0/3 against 2/3): all 6 trials, and one baseline trial, counted
  distinct customers where the grader counts rows, an ambiguity in the task statement. In one full
  trial the verifier's finding changed a correct count into the wrong one.

Two tasks are 6.7% of the set, below the 10% at which a general fix round would have been
considered, and the two mechanisms differ. The largest wins (`dbt-customer-cross-sell-insights`
and `dbt-test-orders-filter`, both 3/3 against 0/3) hold without the verifier, so they do not come
from it; what in the Skills or tools causes them was not traced.

The verifier costs twice as much per success and takes about 150 seconds longer per trial, and it
did not score higher on either set. That is why `no-verifiers` is the recommended config and the
one used for the data-eng-bench submission. This is a cost decision. The data does not show the
verifier lowers accuracy.

### How the development-set score moved

The agent was changed in rounds against the development set. Each row is 30 tasks × 3 trials.

| Run | Commit state | full | baseline |
|---|---|---|---|
| dev-3 | starting point (`f095f2d`, after one round of Skill rules) | 52/90 | 62/90 |
| accept-1 | verifier reads the task text; output columns as the contract | 60/90 | not run |
| accept-2 | database facts added to the base tools | 56/90 | 62/90 |
| final | after the tool and harness fixes, and verifier v2 | 60/90 | 59/90 |

The full config ended where accept-1 had it, and the baseline moved within noise. The fixes
between accept-2 and the final run are visible in process metrics, not in the score: oversize tool
outputs per trial went from 1.70 to 0 and guard rejections from 4.90 to 2.83 (measured on the 40
full-config trials of the final run's first stage, against accept-2 full weighted to the same
tasks). The code changed between these rows, so they are not repeated measurements. The paired
comparisons run on the earlier rows had full below baseline: dev-3 by 11.1 points (−20.0 to −3.3,
significant) and accept-2 by 6.7 points (−16.7 to +4.4, not significant).

## Benchmark results

### data-eng-bench

103 tasks × 3 trials, 309 trials, `no-verifiers`, commit `ea740b5` (agent code identical to
`b7c6071`), run from the benchmark registry's v1.0 dataset as the leaderboard requires.

| Measure | Value |
|---|---|
| Accuracy | 64.1% ± 1.6 |
| pass@2 / pass@3 | 0.715 / 0.748 |
| Easy (3 tasks) | 100% |
| Medium | 70.9% |
| Hard | 57.8% |
| Very hard | 45.8% |
| Billed cost | $15.16, $0.049 per trial, $0.077 per success |
| Mean trial time | 547 s |

The registry's v1.0 was published before an upstream fix to the grader of one task
(`dbt-cart-abandonment-recovery`), so that task scores 0 for every submitter. Without it the
accuracy over 102 tasks is 64.7% ± 1.6.

There is no baseline run of all 103 tasks. The comparison against baseline on this benchmark is
the held-out table above.

### DataAgentBench

54 queries × 5 trials × 2 configs, 540 trials per run. Pass@1 is the benchmark's metric, averaged
over datasets.

| | Blind run (`b7c6071`) | First exception (`cc8cdef`) | Second exception (`69a7e2b`) |
|---|---|---|---|
| Skills on (`no-context`) | 0.7421 (210/270) | 0.7588 (212/270) | 0.7639 (209/270) |
| Skills off (`baseline`) | 0.7012 (206/270) | 0.7218 (205/270) | 0.7614 (213/270) |
| Difference | +4.09 points, +1.18 to +6.88, significant | +3.70 points, −1.51 to +9.07, not significant | +0.25 points, −6.93 to +7.62, not significant |

Skills on scored higher in all three runs, but the gap shrank to almost nothing in the third,
where the baseline gained 4 points; only the first difference is significant. The runs used
different code, so they are not repeats of one measurement.

Only the blind run is submitted, marked "Tuned prompt: no". The leaderboard marks a prompt as tuned
when it is "built from a close study of DAB's task conventions (domain rules, parsing and
interpretation choices, and expected answer shapes)". The two later rounds name no query or
dataset, but they were built after reading this benchmark's failures (answer shapes) and other
submissions' notes on it (parsing and interpretation), so they meet that definition. Submitted as
tuned, 0.7639 would rank 25th of 26 tuned entries; the blind run ranks 9th of 32 untuned ones
(leaderboard as of 2026-11-22).

Per dataset in the first exception (baseline → Skills on) are below; the second exception's are
in the [ADR](adr/0008-change-acceptance.md).

DEPS_DEV_V1 0.20 → 0.50, GITHUB_REPOS 0.60 → 0.60,
PANCANCER_ATLAS 0.67 → 0.67, PATENTS 0.60 → 0.47, agnews 0.25 → 0.40, bookreview 0.93 → 1.00,
crmarenapro 0.82 → 0.83, googlelocal 1.00 → 0.95, music_brainz_20k 1.00 → 1.00, stockindex
0.93 → 1.00, stockmarket 0.72 → 0.72, yelp 0.94 → 0.97.

Only the 54 leaderboard queries were run. The benchmark clone holds 50 more on five newer datasets
that the leaderboard does not score.

### Postgres and dbt outside the benchmarks

dbt Labs' `jaffle_shop` on a local Postgres, 7 cases in the format of SignalPilot's e2e eval set:
3 questions whose numbers are checked within a tolerance, 2 dbt changes checked for the rebuilt
model's row count, grain, columns and column sums, and 2 requests to write to the data directly,
which must leave it unchanged. Run on commit `71a5bcc` (2026-11-23), after the sandbox and
`dbt_execute` changes, with the `full` config (the runner's default): 7/7 passed for $0.137. The
read-only gateway refused statements in both negative cases and in one question case. The earlier
result, also 7/7, was from commit `47808bd` before those changes. The command is `python -m evals jaffle`; the cases
are in [`evals/jaffle_shop/eval.json`](../evals/jaffle_shop/eval.json).

## Cost

| Run | Trials | Billed | Per trial |
|---|---|---|---|
| Held-out ablation, 5 configs | 450 | $36.22 | $0.080 |
| data-eng-bench, all tasks | 309 | $15.16 | $0.049 |
| DataAgentBench trial run and first run | 34 + 540 | $10.88 | $0.019 |
| DataAgentBench fix round: experiments and three small validations | | about $5.26 | |
| DataAgentBench rerun after the first exception | 540 | $13.57 | $0.025 |
| DataAgentBench second exception: effort comparison and validation | 194 | $10.66 | $0.055 |
| DataAgentBench rerun after the second exception | 540 | $14.04 | $0.026 |
| **Formal evaluation total** | | **about $105.8** | |

Development-set iteration, from the first runs to the choice of the default config, cost about
¥541 against a stop line that was raised three times, from ¥300 to ¥600. Each raise was a recorded
human decision.

The DataAgentBench rerun was dearer per trial mostly because of text classification: the agnews dataset
alone cost $5.33 of the $13.57, with single trials between $0.03 and $0.75.

Cost controls that were in force: every large run needed a human go-ahead with an estimate; each
run had a dollar budget that stopped it at 80%; a run that came in more than 20% over its estimate
stopped for a decision. One batch of the held-out ablation crossed that 20% line ($14.38 against
$13.80).

## Failure attribution

### dbt tasks

After the accept-2 run, all 180 of its trials were read and the findings compared with the
upstream projects the tools were adapted from. Three causes ran through them:

1. **Interfaces were copied without the designs that keep them bounded.** `list_tables` returned
   665,000 characters on a project of about 2,400 models; 125 of 180 trials hit it, and 210 calls
   got only an error back. `dictionary_search` scanned every text column at call time with no time
   limit; 5 trials hung for the full 1,800 seconds, and the hang was often followed by a database lock
   that broke the next `dbt run`. The table catalog was re-read on every call, so `describe_table` took 14
   seconds at the median.
2. **Facts that can be computed were left to the model's judgment.** The first verifier judged
   null primary keys by reading, and once passed a model with 256 null keys. Trials where every
   check passed succeeded 63% of the time, against 62% overall, so a pass carried almost no
   information. Of 34 failed trials only 10 had received a FAIL.
3. **The local test environment differed from the container.** A dependency (`pytz`) was present
   only through development packages, causing 38 query errors. A relative path in the dbt profile
   broke in 97 of 180 trials. The first dbt command of every trial re-parsed the whole project,
   taking 60 to 75 seconds against a 60-second limit.

Calls in one turn also ran one after another, which ticket 43 traced to the tools not being marked
read-only. Later rounds added two more: the agent could not see its deadline, so trials were cut
off mid-verification; and timing percentiles were wrong because tool results arrived in batches.

These were fixed in tickets 38 to 67: output caps that say what was left out, a dictionary built
once at the start of the run, a cached catalog, the missing dependency, read-only annotations, a
visible deadline, and a second verifier whose facts come from tools (what changed since the run
started, column contract, null and duplicate keys, comparison by key). The process metrics show the
hangs and the oversize outputs gone; the table in
[How the development-set score moved](#how-the-development-set-score-moved) shows no measurable
gain in accuracy.

What the fixes could not reach:

- Six development tasks scored 0 in both configs in accept-2. For five of them the reading was a
  mismatch between the task statement and the grader. For one of those five the expected values
  look self-contradictory, and that was not confirmed because the reference solution does not run
  locally. These were recorded as known limits and not pursued, because fixing them would mean
  writing task-specific content into the Skills.
- The second verifier still raised false alarms. In one trial it failed a model for tied ranks;
  the main agent removed the window functions in response, and the grader required them.
- The failed trials of the final run's first stage (80 of its 270 trials) were read and classified
  as model variance or as failure types already seen in accept-2; none was a new type caused by
  the fixes. The second stage's failures were not read.

### DataAgentBench failures

**First run.** With Skills on, 60 of 270 trials failed, across 15 queries. They were classified
against the official grading scripts:

| Class | Trials | Fix aimed at it |
|---|---|---|
| The question or the grading script | 23 | none |
| Answer format: right values, but another column between a name and its value, a truncated name, or other candidates listed beside the answer | 15 | answer rule |
| No means of classifying text by meaning | 9, plus 4 that ran out of turns or context | `classify_text` tool |
| Had an answer but did not give it before the turns ran out | 2 | visible turn budget |
| Reasoning errors | 7 | none |

Five passes in that run were judged to come from lenient grading.

The agnews dataset is the third row. It has no category column, so the agent must classify
articles by meaning, and the grader requires the counts to equal those from the dataset's own
labels: exactly 16 of 111 on one query, a count of about 14,860 articles with no error on another.
A classifier that disagrees with the original labels on one borderline article fails. Two of its
four queries failed in every trial of both runs.

**The fix round.** A human approved one exception to the rule that DataAgentBench results do not
change the agent, on the condition that each fix was general and contained nothing about a query,
a dataset or a grading script:

- Answers hold only what was asked, each value right after its name, no alternatives.
- Query agents see their turn limit and are reminded at 10 and 3 turns left.
- A `classify_text` tool labels query rows with the run's model.

An experiment before the fixes doubled the turn limit instead. No trial reached the doubled limit,
and empty answers disappeared on two of the three queries, but the pass count did not rise. So the
limit stayed and the budget was made visible.

**What the rerun changed.** With Skills on, the queries the fixes aimed at gained 12 passes and
nine other queries lost 10, a net gain of 2. The old and new trajectories of those nine queries
were read. Per-trial verdicts were not at hand for this reading, so which trial failed is mostly
inferred from comparing the ten answers; three queries were checked against their reference
answers.

| Query | Passes, old → new | Reading | Counted as | Confidence |
|---|---|---|---|---|
| GITHUB_REPOS 2 | 4 → 2 | Lenient passes disappeared: three old answers named the wrong repository but mentioned the right one as a fallback, and the grader matches substrings. Truly right: 1 old, 2 new | answer rule | high |
| crmarenapro 12 | 2 → 1 | Same mechanism. Truly right: 1 old, 1 new | answer rule | high |
| crmarenapro 13 | 5 → 4 | One new trial joined on fewer conditions; one old pass was the same lenient kind. Truly right: 4 old, 4 new, against an inferred reference answer | answer rule | medium |
| PATENTS 2 | 1 → 0 | A real regression from the answer-format rule. The grader wants all 23 classes; all five new trials computed 23 and reported only the top two, quoting the rule in their reasoning | answer rule | high |
| yelp 4 | 5 → 4 | One new trial chose the other of two averaging methods; old trials listed it as an alternative. Could be the rule or variance | answer rule | medium |
| agnews 1 | 5 → 4 | One new trial gave `classify_text` only the title column, and the right article's title says nothing about its category | new tool | medium to high |
| stockmarket 2 | 5 → 4 | One new trial filled its context by reading large SQL files back in. An old trial did the same with 7% less text and finished: model variance | variance | medium to high |
| DEPS_DEV_V1 2 | 5 → 4 | Cannot tell which trial failed | undetermined | low |
| googlelocal 3 | 5 → 4 | Ten answers with the same content in different order and format; cannot tell | undetermined | low |

So 6 of the 10 lost passes are counted against the answer-format rule (4 with high confidence, 2
that may be variance), 1 against the new tool, and 3 are variance or undetermined. The turn reminder never fired in these 45 new trials.

Two conclusions follow. The answer-format rule did what it was meant to and also removed passes
that were never correct answers; on GITHUB_REPOS 2 and crmarenapro 12 the number of truly correct
answers did not fall. And it caused one real regression, PATENTS 2, where "only what was asked" was
read as "only the top entries".

**The second exception.** The leaderboard placed 0.7588 31st of 56, below the median, while two
other submissions with the same model scored 0.9230 and 0.8114. Their public notes showed that most
of the larger gap came from per-dataset and per-query instructions, which this project does not
write. A human approved a second exception limited to three general changes, written without
copying either submission's text:

- A run-level reasoning-effort option. Against the default, `max` passed fewer of the weak queries
  (24 of 60 against 31) at 1.65 times the cost, so the rerun kept the default.
- General analysis rules in the SQL skill: keep ties in a top N, read qualifiers such as "at least"
  and which event a time window applies to, normalize join keys and check the match rate, parse
  numbers and lists out of text, choose between code and label columns, recompute by a second path.
- `classify_text` can leave a row unresolved instead of forcing a label, resume from an earlier
  result to relabel only the missing rows, and read several text columns.

On the weak queries the changes passed 28 of 54 trials, against 25 for the same queries on the code before them.
In the full rerun Skills on rose only 0.51 points and passed 3 fewer trials, within run-to-run
variation, and the baseline rose 4 points; the drops were not read. Two trials broke on
infrastructure (the benchmark's tool server failed to start, cause not found) and were rerun once.
No further round is planned.

Not checked: the baseline config's drops on the same queries, whose trajectories are only on the
server; and DEPS_DEV_V1 2, googlelocal 3 and yelp 4, which need the per-trial verdicts to settle.

## Limitations

- **One model.** Every number, and the judgment about which layers are worth their cost, may differ
  on another model.
- **Sample size.** 30 tasks × 3 trials did not separate the layers; every paired interval crosses
  zero.
- **Context tools on dbt tasks.** They were rarely called, so their row in the ablation says
  little about what they would do if used.
- **Verifier.** It did not raise accuracy at its cost. Its second version computes its facts but
  still raises false alarms, and it sometimes re-verified every model after a small change.
- **DataAgentBench score.** 0.7588 and 0.7639 followed changes made after seeing that benchmark's
  results; 0.7421 is the blind score and the one submitted. The grader matches substrings on many queries, so a score can rise by
  hedging, and part of the difference between the two runs is hedged passes that went away.
- **Text classification.** Queries that need classification to match a dataset's own labels
  exactly fail, and long classification runs can still fill the context.
- **Offline checks.** On agnews, some trials tried to download the original labels. The sandbox has
  no network and every attempt failed. In the first run none of the Skills-on trials that tried
  passed; in the rerun no Skills-on trial tried.
- **dbt tasks against the baseline.** The layers are not significantly above the baseline on
  data-eng-bench: on the held-out set no-verifiers − baseline is +5.6 (−3.3 to +15.6). The per-task
  pairing on the development set found no kind of task to fix in general, so this is reported as
  measured, not pursued.
- **Larger context window on dbt tasks.** The 1M window and automatic compaction (ticket 110) were
  added after every full run. On the development set's three longest dbt tasks, 3 trials each on
  `71a5bcc`, every trial completed and the score was 5/9 against 6/9 at `b7c6071` (3/3, 2/3, 0/3
  against 2/3, 3/3, 1/3), within trial-to-trial variation, at a similar cost (per-task means $0.116 to $0.225 per
  trial). None of the 270 development-set trials at `b7c6071` had reached the old context limit, and
  the mean request in the new trials stayed under 74,000 tokens, far below the 367,000 at which
  compaction starts, so the change is not expected to matter on dbt tasks. Whether compaction ran is
  not confirmed: the CLI transcripts stay in the trial container.
- **No baseline for the full data-eng-bench run**, and no comparison against other submitters here:
  they used different models at different prices.
- **Infrastructure.** Three concurrent trials fill the server's 8 GB; one trial in the full run was
  excluded for an image-build race and rerun.

## Where the records are

| What | Where |
|---|---|
| Run-by-run decisions, results and known limits | [`docs/adr/0008-change-acceptance.md`](adr/0008-change-acceptance.md) |
| Split and ablation design | [`docs/adr/0004-ablation-holdout.md`](adr/0004-ablation-holdout.md) |
| DataAgentBench integration and the fix-round exception | [`docs/adr/0005-dab-integration.md`](adr/0005-dab-integration.md) |
| Verifier v2 design | [`docs/adr/0009-verifier-v2.md`](adr/0009-verifier-v2.md) |
| Tool and harness latency audit | [`docs/research/2026-11-05-foundation-latency-audit.md`](research/2026-11-05-foundation-latency-audit.md) |
| Tickets with review findings and real-run output | [`.scratch/`](../.scratch/) |
| data-eng-bench trajectories, 309 trials | Harbor Hub, linked from [PR #20](https://github.com/Snowflake-Labs/data-eng-bench/pull/20) |
| DataAgentBench trajectories of the submitted blind run, 270 trials | [release `dab-submission-2026-11-13`](https://github.com/kevinWangSheng/data-agent/releases/tag/dab-submission-2026-11-13) |

Run outputs under `runs/` are not in git; the held-out set's per-task results stay on the server.
