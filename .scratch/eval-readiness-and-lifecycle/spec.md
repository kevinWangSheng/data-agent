# Evaluation readiness and lifecycle

## Problem Statement

The project has already spent substantial model budget on development and ablation runs. The
current evaluator checks that a batch can start, but it does not consistently check for obvious
efficiency and resource problems before a large batch begins. A smoke run can pass while a later
task repeatedly scans a project, misses a cache, dispatches every changed model, or reaches the
trial deadline.

The evaluator also needs an explicit lifecycle contract. A completed logical failure must remain a
real result, while an interrupted trial or infrastructure failure must be resumable without being
scored or charged twice. Stage 1 must distinguish correctness failures from efficiency warnings,
and Stage 2 must run only when its inputs and commit are still the ones that Stage 1 measured.

Without this contract, the project can spend money on an evaluation that is technically runnable
but produces ambiguous evidence about the agent, verifier, or infrastructure.

## Solution

Make the evaluation lifecycle explicit from preflight through final reporting:

1. Add a lightweight readiness gate before a large development or ablation batch. It reuses the
   existing environment, balance, smoke, scrub, and version checks, and adds only broad checks for
   resource headroom, obvious timeout risk, repeated uncached work, and abnormal full-model
   redispatch. The gate is intentionally shallow and cheap; it is not a full performance audit.
2. Preserve the existing fast-fail behavior for account, configuration, and repeated
   infrastructure errors.
3. Define resume behavior so that interrupted and infrastructure-error trials are removed from the
   scored table, retained for spend accounting, and rerun with the same request conditions.
   Completed trials are reused. Logical agent failures are not silently retried.
4. Treat the development-set comparison of `full`, `baseline`, and `no-verifiers` as the decision
   stage for the product default. The current `pos-operations` redispatch condition is reported as
   an efficiency warning when correctness and infrastructure gates pass; it does not by itself
   authorize a claim that every efficiency line passed.
5. Require Stage 1 and Stage 2 to use one fixed code, Skill, benchmark, model, machine, and request
   contract. If code or evaluation behavior changes, the Stage 1 evidence is invalidated and must
   be regenerated before Stage 2 is merged with it.
6. Defer the six-configuration holdout ablation until the development-set decision is complete and
   the project explicitly needs component-attribution evidence. Do not start it automatically as a
   response to the current redispatch warning.
7. Keep holdout output aggregate-only and retain the existing separation between development-set
   diagnosis and holdout evidence.

## User Stories

1. As the evaluation operator, I want a cheap readiness check before a large batch, so that I do
   not spend model budget on a broken environment.
2. As the evaluation operator, I want the readiness check to detect obvious resource, timeout,
   cache, and dispatch problems, so that predictable long-tail waste is found early.
3. As the evaluation operator, I want readiness checks to stay shallow and bounded, so that the
   check itself does not become another expensive evaluation.
4. As the evaluation operator, I want account, benchmark, machine, image, database, and request
   identity checked together, so that results from incompatible conditions cannot be merged.
5. As the evaluation operator, I want a smoke failure to stop the batch before a real job starts,
   so that a known-bad configuration does not consume a full budget.
6. As the evaluation operator, I want repeated infrastructure errors to stop the batch quickly,
   so that a broken service does not burn the remaining budget.
7. As the evaluation operator, I want an interrupted batch to resume from the missing work, so that
   completed trials are not paid for or run again.
8. As the evaluation operator, I want infrastructure-error trials retained for accounting but
   excluded from scores, so that a transient service failure does not look like agent failure.
9. As the evaluation operator, I want logical agent failures to remain scored failures unless I
   explicitly create a rerun, so that retrying does not hide model variance or real regressions.
10. As the evaluation operator, I want Stage 1 and Stage 2 to be mergeable only when their run
    contracts match, so that partial results cannot silently represent different versions.
11. As the project maintainer, I want `full`, `baseline`, and `no-verifiers` compared on the same
    development set, so that I can choose the product default based on success, cost, and process
    behavior.
12. As the project maintainer, I want correctness failures separated from efficiency warnings,
    so that a conservative verifier implementation is not misreported as a correctness failure.
13. As the project maintainer, I want a code change to invalidate earlier Stage 1 evidence, so that
    Stage 2 cannot combine results from different implementations.
14. As the project maintainer, I want the six-configuration holdout run deferred until the
    development decision is complete, so that expensive causal attribution is only purchased when
    it answers a committed project question.
15. As a report reader, I want the report to state which gates passed, which efficiency warnings
    remained, and which results were excluded as infrastructure errors, so that the evidence is
    interpretable.
16. As a benchmark maintainer, I want holdout reports to remain aggregate-only, so that the
    holdout is not turned into a second development set.

## Implementation Decisions

- The evaluation lifecycle is one contract spanning readiness, execution, resume, processing, and
  reporting. It is not a new agent behavior or verifier policy.
- Readiness reuses the existing identity, balance, wheel, smoke, scrub, and fast-fail checks. New
  checks are bounded observations: one resource snapshot, broad timeout sanity, evidence of
  obvious uncached repeated work, and evidence of abnormal full-model redispatch when such evidence
  is available. It does not run a full performance benchmark.
- A readiness finding blocks a batch only when it indicates a systemic environment or execution
  problem. An isolated efficiency warning is recorded and carried into the evaluation report.
- The current `pos-operations` redispatch line is an efficiency warning for the Stage 2 decision.
  The report must still state that the line did not pass; changing it to a hard product requirement
  requires a separate decision before implementation.
- Resume is keyed by the existing run identity and request contract. Only interrupted or
  infrastructure-error trials are eligible for automatic resume. Their prior artifacts remain in
  the dropped record for spend accounting. Logical failures are not automatically retried.
- Stage 1 and Stage 2 are combined only when code commit, Skill version, benchmark commit, model,
  machine, split, task set, and run parameters match. A relevant code or evaluator change makes
  Stage 1 stale and requires a new Stage 1 before Stage 2.
- The three development configurations are the immediate product-selection experiment. The
  `no-verifiers` comparison is retained because it is the direct evidence for whether the verifier
  layer earns its cost and latency.
- The six-configuration holdout ablation remains a later, one-time experiment for component
  attribution. It is not part of readiness and is not triggered by a single efficiency warning.
- Existing report privacy rules remain: development results may be inspected for diagnosis;
  holdout results expose aggregates only.

## Testing Decisions

- The primary external seam is the evaluation CLI and its Harbor subprocess seam. A fake Harbor
  implementation should exercise the lifecycle without model calls or network spend.
- Tests must cover the readiness decision, bounded failure behavior, request/identity mismatch,
  resume of interrupted and infrastructure-error trials, preservation of logical failures, and
  spend accounting for dropped trials.
- Tests must cover the Stage 1/Stage 2 merge contract and rejection of mismatched commits,
  configurations, task sets, or run parameters.
- Existing process and report seams should verify dispatch counts, model counts, timeout and
  deadline indicators, disputed/unverified outcomes, gate classification, and the separation of
  correctness results from efficiency warnings.
- Existing CLI acceptance tests remain the highest-level contract for report shape, development
  versus holdout visibility, and scrub behavior.
- Unit tests should use fixtures and fake event files for cache, timeout, dispatch, and resume
  cases. They must not require a model call.
- A real run is evidence only for a bounded changed behavior, such as one representative task or a
  small resume check. The full development evaluation and holdout evaluation remain explicit,
  human-approved operations rather than test cases.

## Out of Scope

- Changing the data-agent's task-solving behavior, verifier rules, or dbt logic.
- Guaranteeing a particular cache hit rate or verifier latency percentile across all tasks.
- Re-running the current Stage 1 or starting Stage 2 as part of the code change itself.
- Automatically repairing macro dependency analysis or the post-dispute fallback; those are
  separate implementation tickets if the readiness evidence confirms they need code changes.
- Using development results to make per-task claims about the holdout.
- Starting the six-configuration holdout, data-eng-bench full run, or DAB formal evaluation before
  the development-set decision and explicit spend review.

## Further Notes

- The current Stage 1 gate has no infrastructure errors, passes the target-success and verifier-wall
  lines, and reports an efficiency warning because two `pos-operations` trials re-dispatched all 21
  models. That evidence motivates this lifecycle contract but does not by itself prove a new
  correctness failure.
- The next operational decision after this spec is whether the lightweight readiness result is
  acceptable. If it is, Stage 2 may run on the same commit. If implementation changes are made,
  Stage 1 must be regenerated first.
- The spec deliberately keeps readiness broad and cheap. Detailed profiling belongs in a separate
  investigation only if the bounded checks identify a systemic problem.
