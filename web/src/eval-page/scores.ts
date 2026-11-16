// How the evaluation numbers read on the first screen and the evaluation page.

import type { Dab, DabOutput } from '../data/evaluation'

/** A DAB output's query and run counts: queries from its coverage, else its datasets. */
export function dabTotals(output: DabOutput): { queries: number; runs: number } {
  const datasets = Object.values(output.datasets)
  return {
    queries: output.coverage?.queries ?? datasets.reduce((n, d) => n + d.queries, 0),
    runs: datasets.reduce((n, d) => n + d.runs, 0),
  }
}

/** A difference in points: `+4.1`, `−1.1`, `0.0`. */
export function fmtPoints(points: number, digits = 1): string {
  const text = Math.abs(points).toFixed(digits)
  if (Number(text) === 0) return text
  return `${points > 0 ? '+' : '−'}${text}`
}

/** DAB's scored output minus its baseline. With one query per dataset `evals dab-score` has no
 * interval (`ci_low`, `ci_high` null), and says so the same way. */
export function fmtDabComparison(c: NonNullable<Dab['comparison']>): string {
  const head = `${fmtPoints(c.diff, 2)} points`
  if (c.ci_low === null || c.ci_high === null) return `${head} (every dataset has 1 query: too few to estimate an interval)`
  return `${head} (95% CI ${fmtPoints(c.ci_low, 2)} to ${fmtPoints(c.ci_high, 2)}), ${c.significant ? 'significant' : 'not significant'}`
}

/** A percentage given in percent: `64.1%`. */
export const fmtPct = (percent: number, digits = 1) => `${percent.toFixed(digits)}%`

/** DAB's Pass@1, a fraction, as the README writes it: `0.7421`. */
export const fmtPass1 = (fraction: number) => fraction.toFixed(4)
