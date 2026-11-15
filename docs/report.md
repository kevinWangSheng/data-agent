# Evaluation report

This report backs the numbers in the [README](../README.md): what was run, what each result does
and does not show, what it cost, and why the failed tasks failed. Everything was measured with
`deepseek-flash`; the last run finished on 2026-11-15. The run-by-run record, in Chinese, is
[`docs/adr/0008-change-acceptance.md`](adr/0008-change-acceptance.md).

## Summary

- **data-eng-bench** (103 dbt tasks × 3 trials): 64.1% ± 1.6 accuracy with the `no-verifiers`
  config, for $15.16.
- **DataAgentBench** (54 queries × 5 trials): 0.7588 Pass@1 with Skills on, against 0.7218 for the
  baseline. The blind score, before a round of fixes made after reading failures, was 0.7421
  against 0.7012.
- **Ablation**: on 30 held-out tasks, no paired difference between configs is significant. The data
  cannot say which layer helps.
- **Verifier**: the verifier layer roughly doubled the cost per successful task and did not raise
  accuracy in any measurement, so the recommended config leaves it off.
- **Cost**: all formal evaluation runs together cost about $81. Development-set iteration before
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
| Cost | billed price, read from each run's event log; off-peak hours bill at half the list price. The full data-eng-bench run and both DataAgentBench runs were entirely off-peak; about 16% of the held-out run's event files started at peak |

A trial is one run of one task. Other terms used below:

- **Context tools**: `discover`, `hydrate` and `dictionary_search`, which look up schema summaries
  and real column values on demand.
- **Guard rejection**: a tool call refused by the command guard or the workspace guard.
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

DataAgentBench has no such split. Its results were not used to change the agent, with one
exception described under [DataAgentBench failures](#dataagentbench-failures).

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

| | First run (`b7c6071`) | Rerun after fixes (`cc8cdef`) |
|---|---|---|
| Skills on (`no-context`) | 0.7421 (210/270) | 0.7588 (212/270) |
| Skills off (`baseline`) | 0.7012 (206/270) | 0.7218 (205/270) |
| Difference | +4.09 points, +1.18 to +6.88, significant | +3.70 points, −1.51 to +9.07, not significant |

Skills on scored about 4 points higher in both runs; the difference was significant in the first
run only. The two runs used different code, so they are not repeats of one measurement.

The first run is the blind score. The rerun followed three general fixes made after reading the
first run's failures, so 0.7588 is not blind. The submission says so.

Per dataset in the rerun (baseline → Skills on): DEPS_DEV_V1 0.20 → 0.50, GITHUB_REPOS 0.60 → 0.60,
PANCANCER_ATLAS 0.67 → 0.67, PATENTS 0.60 → 0.47, agnews 0.25 → 0.40, bookreview 0.93 → 1.00,
crmarenapro 0.82 → 0.83, googlelocal 1.00 → 0.95, music_brainz_20k 1.00 → 1.00, stockindex
0.93 → 1.00, stockmarket 0.72 → 0.72, yelp 0.94 → 0.97.

Only the 54 leaderboard queries were run. The benchmark clone holds 50 more on five newer datasets
that the leaderboard does not score.

## Cost

| Run | Trials | Billed | Per trial |
|---|---|---|---|
| Held-out ablation, 5 configs | 450 | $36.22 | $0.080 |
| data-eng-bench, all tasks | 309 | $15.16 | $0.049 |
| DataAgentBench trial run and first run | 34 + 540 | $10.88 | $0.019 |
| DataAgentBench fix round: experiments and three small validations | | about $5.26 | |
| DataAgentBench rerun | 540 | $13.57 | $0.025 |
| **Formal evaluation total** | | **about $81.1** | |

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
read as "only the top entries". No further fix round was run.

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
- **DataAgentBench score.** 0.7588 followed fixes made after seeing that benchmark's failures;
  0.7421 is the blind score. The grader matches substrings on many queries, so a score can rise by
  hedging, and part of the difference between the two runs is hedged passes that went away.
- **Text classification.** Queries that need classification to match a dataset's own labels
  exactly fail, and long classification runs can still fill the context.
- **Offline checks.** On agnews, some trials tried to download the original labels. The sandbox has
  no network and every attempt failed. In the first run none of the Skills-on trials that tried
  passed; in the rerun no Skills-on trial tried.
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
| DataAgentBench trajectories, 270 trials | [release `dab-submission-2026-11-15`](https://github.com/kevinWangSheng/data-agent/releases/tag/dab-submission-2026-11-15) |

Run outputs under `runs/` are not in git; the held-out set's per-task results stay on the server.
