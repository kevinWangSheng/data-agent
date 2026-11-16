import { describe, expect, test } from 'vitest'

import { dabTotals, fmtPass1, fmtPoints } from './scores'

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

  test('no coverage: queries summed over the datasets', () => {
    expect(dabTotals({ pass_at_1: 1, datasets: { a: { pass_at_1: 1, queries: 2, runs: 10, passed: 10 } } })).toEqual({ queries: 2, runs: 10 })
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
})
