import { describe, expect, test } from 'vitest'

import type { Evaluation } from './evaluation'
import { firstScreenRun, homeData } from './home'

const board = {
  complete: true,
  trials_per_task: 3,
  config: 'no-verifiers',
  model: 'deepseek-flash',
  leaderboard: { accuracy: 64.1, accuracy_stderr: 1.6, n_tasks: 103, n_trials: 309, n_succeeded: 198 },
  holdout: { n_tasks: 30, n_trials: 90, n_succeeded: 61, accuracy: 67.8 },
}
const dab = { candidate: { pass_at_1: 0.74, coverage: { queries: 54 }, missing_runs: 0, datasets: {} } }
const evaluation: Evaluation = {
  dev: {
    reference: 'full',
    configs: [],
    contributions: [],
    trials: [{ id: 't1', task: 'a', config: 'full', attempt: 1, passed: true, reward: 1, cost_usd: 0.1, duration_seconds: 60 }],
  },
  data_eng_bench: board,
  dab,
}

describe('homeData', () => {
  test('keeps the featured runs and the two leaderboard summaries, not the trials', () => {
    const index = { runs: [{ id: 'q', featured: true }, { id: 't1', featured: false }, { id: 'd', featured: true }] }
    expect(homeData(index, evaluation)).toEqual({
      runs: [
        { id: 'q', featured: true },
        { id: 'd', featured: true },
      ],
      data_eng_bench: board,
      dab,
    })
  })

  test('without an evaluation summary, no scores', () => {
    expect(homeData({ runs: [] }, null)).toEqual({ runs: [], data_eng_bench: null, dab: null })
  })

  test("keeps the recorded conversations' indexes whole", () => {
    const conversation = { id: 'c', title: 'Demo', created_at: null, turns: [{ turn: 1, run_id: 'r1', message: 'How many?', steers: [] }] }
    expect(homeData({ runs: [], conversations: [conversation] }, null).conversations).toEqual([conversation])
    expect(homeData({ runs: [], conversations: [] }, null)).not.toHaveProperty('conversations')
  })
})

describe('firstScreenRun', () => {
  test('the first query run', () => {
    const runs = [
      { id: 'd', task_type: 'dbt' as const },
      { id: 'q', task_type: 'query' as const },
      { id: 'q2', task_type: 'query' as const },
    ]
    expect(firstScreenRun(runs)?.id).toBe('q')
  })

  test('no query run: the first run', () => {
    expect(firstScreenRun([{ id: 'd', task_type: 'dbt' }])?.id).toBe('d')
  })

  test('no runs: none', () => {
    expect(firstScreenRun([])).toBeNull()
  })
})
