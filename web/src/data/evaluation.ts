// The evaluation page's summary, `eval.json` of the export (evals/export.py, module docstring and
// `build`). Aggregates only: the holdout and the whole benchmark appear as totals (ADR-0004).

/** One config's pass rate over the dev set (`evals.report.summary`). */
export type ConfigRow = {
  config: string
  /** Percent. */
  accuracy: number
  /** Percent; the standard error of `accuracy`. */
  accuracy_stderr: number
  pass_at_2?: number
  pass_at_3?: number
  n_tasks: number
  n_trials: number
  n_succeeded: number
  n_failed: number
  cost_usd?: number | null
}

/** The reference config minus one without a layer, paired bootstrap; points are percent. */
export type Contribution = {
  layer: string
  config: string
  diff: number
  ci_low: number
  ci_high: number
  significant: boolean
}

/** One dev trial; `attempt` numbers a task's trials of one config. */
export type TrialRow = {
  id: string
  task: string
  config: string | null
  attempt: number
  passed: boolean
  reward: number | null
  cost_usd: number | null
  duration_seconds: number | null
}

export type DevSummary = {
  reference: string
  configs: ConfigRow[]
  contributions: Contribution[]
  trials: TrialRow[]
}

/** Counts and accuracy of a set of trials, naming none; `accuracy` (percent) is null for a set
 * with no trials (evals/board.py `aggregate`). */
export type Aggregate = { n_tasks?: number; n_trials?: number; n_succeeded?: number; accuracy?: number | null }

/** data-eng-bench: the leaderboard over every task, and the holdout's total. */
export type Board = {
  complete?: boolean | null
  trials_per_task?: number | null
  config?: string | null
  model?: string | null
  leaderboard: Aggregate & {
    accuracy_stderr?: number | null
    pass_at_2?: number | null
    pass_at_3?: number | null
  }
  holdout: Aggregate
}

export type DabDataset = { pass_at_1: number | null; queries: number; runs: number; passed: number }

/** One scored DAB output: Pass@1 is a fraction, not percent. */
export type DabOutput = {
  pass_at_1: number
  coverage?: { queries?: number; clone_queries?: number }
  missing_runs?: number
  datasets: Record<string, DabDataset>
}

/** `evals dab-score`'s report: `candidate` is the scored output, `baseline` the optional
 * comparison, `comparison` the candidate minus the baseline in points. */
export type Dab = {
  candidate?: DabOutput
  baseline?: DabOutput
  /** No interval (`ci_low`, `ci_high` null) when every dataset has 1 query (evals/dab_score.py). */
  comparison?: { diff: number; ci_low: number | null; ci_high: number | null; significant: boolean } | null
}

export type Evaluation = {
  dev: DevSummary
  data_eng_bench: Board | null
  dab: Dab | null
}
