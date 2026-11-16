// How the evaluation numbers read on the first screen and the evaluation page.

import type { DabOutput } from '../data/evaluation'

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

/** A percentage given in percent: `64.1%`. */
export const fmtPct = (percent: number, digits = 1) => `${percent.toFixed(digits)}%`

/** DAB's Pass@1, a fraction, as the README writes it: `0.7421`. */
export const fmtPass1 = (fraction: number) => fraction.toFixed(4)
