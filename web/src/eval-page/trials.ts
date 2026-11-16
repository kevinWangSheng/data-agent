// The evaluation page's address, `<base>eval?config=<config>&outcome=<outcome>`, and the dev trial
// table's filter it keeps, so the browser's back button returns from a replay to the same rows.

import type { TrialRow } from '../data/evaluation'

export type Outcome = 'all' | 'passed' | 'failed'

export const OUTCOMES: readonly Outcome[] = ['all', 'passed', 'failed']

/** `config: null` is every config. */
export type TrialFilter = { config: string | null; outcome: Outcome }

export function filterTrials(rows: readonly TrialRow[], filter: TrialFilter): TrialRow[] {
  return rows.filter(
    (r) =>
      (filter.config === null || r.config === filter.config) &&
      (filter.outcome === 'all' || r.passed === (filter.outcome === 'passed')),
  )
}

/** A task's trials together: by task, then config (none last), then attempt. */
export function sortTrials(rows: readonly TrialRow[]): TrialRow[] {
  const text = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)
  return [...rows].sort(
    (a, b) =>
      text(a.task, b.task) ||
      (a.config === b.config ? 0 : a.config === null ? 1 : b.config === null ? -1 : text(a.config, b.config)) ||
      a.attempt - b.attempt,
  )
}

const rootOf =(base: string) => (base.endsWith('/') ? base : `${base}/`)

export function isEvalPath(pathname: string, base: string): boolean {
  const root = rootOf(base)
  return pathname === `${root}eval` || pathname === `${root}eval/`
}

export function evalUrl(filter: TrialFilter, base: string): string {
  const query = new URLSearchParams()
  if (filter.config !== null) query.set('config', filter.config)
  if (filter.outcome !== 'all') query.set('outcome', filter.outcome)
  const qs = query.toString()
  return `${rootOf(base)}eval${qs ? `?${qs}` : ''}`
}

export function readTrialFilter(search: string): TrialFilter {
  const query = new URLSearchParams(search)
  const outcome = query.get('outcome') as Outcome | null
  return { config: query.get('config'), outcome: outcome !== null && OUTCOMES.includes(outcome) ? outcome : 'all' }
}
