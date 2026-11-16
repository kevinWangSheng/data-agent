import { describe, expect, test } from 'vitest'

import type { TrialRow } from '../data/evaluation'
import { evalUrl, filterTrials, isEvalPath, readTrialFilter, sortTrials } from './trials'

const row = (id: string, config: string | null, passed: boolean): TrialRow => ({
  id,
  task: id.split('__')[0],
  config,
  attempt: 1,
  passed,
  reward: passed ? 1 : 0,
  cost_usd: 0.1,
  duration_seconds: 60,
})

const rows = [row('a__1', 'full', true), row('a__2', 'baseline', false), row('b__1', 'full', false), row('b__2', null, true)]

describe('filterTrials', () => {
  test('no filter: every row, in order', () => {
    expect(filterTrials(rows, { config: null, outcome: 'all' }).map((r) => r.id)).toEqual(['a__1', 'a__2', 'b__1', 'b__2'])
  })

  test('by config', () => {
    expect(filterTrials(rows, { config: 'full', outcome: 'all' }).map((r) => r.id)).toEqual(['a__1', 'b__1'])
  })

  test('by outcome', () => {
    expect(filterTrials(rows, { config: null, outcome: 'failed' }).map((r) => r.id)).toEqual(['a__2', 'b__1'])
    expect(filterTrials(rows, { config: null, outcome: 'passed' }).map((r) => r.id)).toEqual(['a__1', 'b__2'])
  })

  test('both', () => {
    expect(filterTrials(rows, { config: 'full', outcome: 'failed' }).map((r) => r.id)).toEqual(['b__1'])
  })
})

describe('sortTrials', () => {
  test('by task, then config, then attempt; no config last', () => {
    const shuffled = [
      { ...row('b__x', 'full', true), attempt: 2 },
      { ...row('a__y', 'full', true), attempt: 2 },
      { ...row('a__z', null, true), attempt: 1 },
      { ...row('a__w', 'baseline', true), attempt: 1 },
      { ...row('a__v', 'full', true), attempt: 1 },
    ]
    expect(sortTrials(shuffled).map((r) => r.id)).toEqual(['a__w', 'a__v', 'a__y', 'a__z', 'b__x'])
  })
})

describe('the evaluation page address', () => {
  test('is <base>eval', () => {
    expect(isEvalPath('/data-agent/eval', '/data-agent/')).toBe(true)
    expect(isEvalPath('/data-agent/eval/', '/data-agent/')).toBe(true)
    expect(isEvalPath('/eval', '/')).toBe(true)
    expect(isEvalPath('/data-agent/', '/data-agent/')).toBe(false)
    expect(isEvalPath('/data-agent/runs/eval', '/data-agent/')).toBe(false)
  })

  test('keeps the filter in the query', () => {
    expect(evalUrl({ config: null, outcome: 'all' }, '/data-agent/')).toBe('/data-agent/eval')
    expect(evalUrl({ config: 'no-verifiers', outcome: 'failed' }, '/')).toBe('/eval?config=no-verifiers&outcome=failed')
  })

  test('reads the filter back', () => {
    expect(readTrialFilter('?config=no-verifiers&outcome=failed')).toEqual({ config: 'no-verifiers', outcome: 'failed' })
    expect(readTrialFilter('')).toEqual({ config: null, outcome: 'all' })
  })

  test('an unknown outcome reads as all', () => {
    expect(readTrialFilter('?outcome=maybe')).toEqual({ config: null, outcome: 'all' })
  })
})
