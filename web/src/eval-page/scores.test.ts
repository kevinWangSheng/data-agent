import { describe, expect, test } from 'vitest'

import { dabTotals, fmtBoardScore, fmtDabCounts, fmtDabComparison, fmtPass1, fmtPct, fmtPoints } from './scores'

describe('dabTotals', () => {
  test('queries from the coverage, runs summed over the datasets', () => {
    const output = {
      pass_at_1: 0.74,
      coverage: { queries: 54, clone_queries: 104 },
      datasets: {
        yelp: { pass_at_1: 1, queries: 7, runs: 35, passed: 35 },
        agnews: { pass_at_1: 0.3, queries: 4, runs: 20, passed: 6 },
      },
    }
    expect(dabTotals(output)).toEqual({ queries: 54, runs: 55 })
  })

  test('reads as queries and runs', () => {
    expect(fmtDabCounts({ pass_at_1: 1, coverage: { queries: 54 }, datasets: { a: { pass_at_1: 1, queries: 2, runs: 270, passed: 10 } } })).toBe('54 queries, 270 runs')
  })

  test('no coverage: queries summed over the datasets', () => {
    expect(dabTotals({ pass_at_1: 1, datasets: { a: { pass_at_1: 1, queries: 2, runs: 10, passed: 10 } } })).toEqual({ queries: 2, runs: 10 })
  })
})

describe('fmtDabComparison', () => {
  test('a difference with its interval and verdict', () => {
    expect(fmtDabComparison({ diff: 4.086, ci_low: 1.178, ci_high: 6.876, significant: true })).toBe(
      '+4.09 points (95% CI +1.18 to +6.88), significant',
    )
  })

  test('no interval (every dataset has 1 query): says so, not a zero interval', () => {
    expect(fmtDabComparison({ diff: -11.76, ci_low: null, ci_high: null, significant: false })).toBe(
      '−11.76 points (every dataset has 1 query: too few to estimate an interval)',
    )
  })
})

describe('formats', () => {
  test('points carry their sign, with a true minus', () => {
    expect(fmtPoints(4.086)).toBe('+4.1')
    expect(fmtPoints(-1.111)).toBe('−1.1')
    expect(fmtPoints(0)).toBe('0.0')
  })

  test('Pass@1 has four decimals, as the README', () => {
    expect(fmtPass1(0.7420726)).toBe('0.7421')
  })

  test('a percentage that was not recorded reads as a dash (a holdout with no trials has accuracy null)', () => {
    expect(fmtPct(67.777)).toBe('67.8%')
    expect(fmtPct(null)).toBe('—')
    expect(fmtPct(undefined)).toBe('—')
  })
})

describe('fmtBoardScore', () => {
  test('accuracy with its standard error', () => {
    expect(fmtBoardScore({ accuracy: 64.077, accuracy_stderr: 1.552 })).toBe('64.1% ± 1.6')
  })

  test('no standard error: accuracy alone', () => {
    expect(fmtBoardScore({ accuracy: 64.077 })).toBe('64.1%')
  })
})
