import { describe, expect, test } from 'vitest'

import { parseEventLines, staticSource } from './source'

const home = { runs: [{ id: 'r1', title: 'One', featured: true }], data_eng_bench: null, dab: null }
const evaluation = { dev: { reference: 'full', configs: [], contributions: [], trials: [] }, data_eng_bench: null, dab: null }

const files: Record<string, string> = {
  '/data/home.json': JSON.stringify(home),
  '/data/eval.json': JSON.stringify(evaluation),
  '/data/runs/r1/meta.json': JSON.stringify({ id: 'r1', title: 'One', featured: true }),
  '/data/runs/r1/events.jsonl': '{"seq":1,"type":"version","ts":"t"}\n\n{"seq":2,"type":"result","ts":"t"}\n',
  '/data/runs/a%20b/meta.json': JSON.stringify({ id: 'a b' }),
  '/data/runs/a%20b/events.jsonl': '',
}

function fakeFetch(served: Record<string, string>, asked: string[] = []): typeof fetch {
  return (async (url: string) => {
    asked.push(url)
    return url in served ? new Response(served[url], { status: 200 }) : new Response('missing', { status: 404, statusText: 'Not Found' })
  }) as typeof fetch
}

describe('parseEventLines', () => {
  test('one event per non-blank line', () => {
    expect(parseEventLines('{"seq":1}\n\n {"seq":2} \n').map((e) => e.seq)).toEqual([1, 2])
  })
})

describe('staticSource', () => {
  const source = staticSource('/data/', fakeFetch(files))

  test('lists the featured runs from home.json, not the whole index', async () => {
    expect(await source.listRuns()).toEqual([{ id: 'r1', title: 'One', featured: true }])
  })

  test('reads home.json once for the runs and the scores', async () => {
    const asked: string[] = []
    const counted = staticSource('/data/', fakeFetch(files, asked))
    await counted.listRuns()
    expect(await counted.home()).toEqual(home)
    expect(asked).toEqual(['/data/home.json'])
  })

  test('reads the evaluation summary', async () => {
    expect(await source.evaluation()).toEqual(evaluation)
  })

  test('no eval.json: no evaluation summary', async () => {
    expect(await staticSource('/data/', fakeFetch({})).evaluation()).toBeNull()
  })

  test('loads a run: its notes and events', async () => {
    const run = await source.loadRun('r1')
    expect(run.notes).toEqual({ id: 'r1', title: 'One', featured: true })
    expect(run.events.map((e) => e.type)).toEqual(['version', 'result'])
  })

  test('escapes the run id in the path', async () => {
    expect((await source.loadRun('a b')).notes.id).toBe('a b')
  })

  test('a missing run fails with the status', async () => {
    await expect(source.loadRun('nope')).rejects.toThrow(/404/)
  })
})
