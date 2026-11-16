import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, test } from 'vitest'

import { exportFiles } from './site-data'

/** An export directory in `evals export`'s layout, with a secret file beside it. */
function makeExport(withEval: boolean): string {
  const root = mkdtempSync(join(tmpdir(), 'site-data-'))
  const dir = join(root, 'site')
  mkdirSync(join(dir, 'runs', 'q'), { recursive: true })
  writeFileSync(join(root, 'secret.json'), '"outside"')
  writeFileSync(join(dir, 'secret.json'), '"beside runs"')
  const runs = [
    { id: 'q', featured: true, task_type: 'query' },
    { id: 't1', featured: false },
  ]
  writeFileSync(join(dir, 'index.json'), JSON.stringify({ runs }))
  writeFileSync(join(dir, 'runs', 'q', 'meta.json'), JSON.stringify(runs[0]))
  writeFileSync(join(dir, 'runs', 'q', 'events.jsonl'), '{"seq":1}\n')
  if (withEval) {
    const board = { leaderboard: { accuracy: 64.1 }, holdout: { accuracy: null } }
    writeFileSync(join(dir, 'eval.json'), JSON.stringify({ dev: { trials: [{ id: 't1' }] }, data_eng_bench: board, dab: null }))
  }
  return dir
}

const text = (file: { body: Buffer } | null) => file?.body.toString('utf8')

describe('exportFiles', () => {
  test("serves a run's files", () => {
    const file = exportFiles(makeExport(true))
    expect(text(file('runs/q/meta.json'))).toContain('"id":"q"')
    expect(file('runs/q/events.jsonl')?.type).toBe('application/x-ndjson')
  })

  test('makes home.json: featured runs and the leaderboard summaries, no trials', () => {
    const home = JSON.parse(text(exportFiles(makeExport(true))('home.json'))!)
    expect(home).toEqual({
      runs: [{ id: 'q', featured: true, task_type: 'query' }],
      data_eng_bench: { leaderboard: { accuracy: 64.1 }, holdout: { accuracy: null } },
      dab: null,
    })
  })

  test('without eval.json: no eval.json, and home.json has no scores', () => {
    const file = exportFiles(makeExport(false))
    expect(file('eval.json')).toBeNull()
    expect(JSON.parse(text(file('home.json'))!).data_eng_bench).toBeNull()
  })

  test('a run id that climbs out of runs/ is not served', () => {
    const file = exportFiles(makeExport(true))
    expect(file('runs/..%2F..%2Fsecret.json/meta.json')).toBeNull()
    expect(file('runs/%2E%2E/meta.json')).toBeNull()
    expect(file('runs/..%2Fsecret.json/meta.json')).toBeNull()
  })

  test('only the files the site reads', () => {
    const file = exportFiles(makeExport(true))
    expect(file('secret.json')).toBeNull()
    expect(file('index.json')).toBeNull()
    expect(file('runs/q/notes.json')).toBeNull()
    expect(file('runs/nope/meta.json')).toBeNull()
  })
})
