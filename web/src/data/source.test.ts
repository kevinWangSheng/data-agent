import { describe, expect, test } from 'vitest'

import { parseEventLines, staticSource } from './source'

const files: Record<string, string> = {
  '/data/index.json': JSON.stringify({ runs: [{ id: 'r1', title: 'One' }] }),
  '/data/runs/r1/meta.json': JSON.stringify({ id: 'r1', title: 'One', featured: true }),
  '/data/runs/r1/events.jsonl': '{"seq":1,"type":"version","ts":"t"}\n\n{"seq":2,"type":"result","ts":"t"}\n',
  '/data/runs/a%20b/meta.json': JSON.stringify({ id: 'a b' }),
  '/data/runs/a%20b/events.jsonl': '',
}

const fakeFetch = (async (url: string) =>
  url in files
    ? new Response(files[url], { status: 200 })
    : new Response('missing', { status: 404, statusText: 'Not Found' })) as typeof fetch

describe('parseEventLines', () => {
  test('one event per non-blank line', () => {
    expect(parseEventLines('{"seq":1}\n\n {"seq":2} \n').map((e) => e.seq)).toEqual([1, 2])
  })
})

describe('staticSource', () => {
  const source = staticSource('/data/', fakeFetch)

  test('lists the runs from the index', async () => {
    expect(await source.listRuns()).toEqual([{ id: 'r1', title: 'One' }])
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
