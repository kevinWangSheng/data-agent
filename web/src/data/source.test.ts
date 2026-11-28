import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import type { RunEvent } from '../run-view/model'
import {
  isLive,
  localSource,
  newestFirst,
  notesOf,
  parseEventLines,
  staticSource,
  turnNotes,
  type ConversationRow,
  type EventStream,
  type RunRow,
} from './source'

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

  test('offers no live operations', () => {
    expect(source.live).toBeUndefined()
  })

  test('history: a row per featured run, from its notes and events, newest start first', async () => {
    const featured = [
      { id: 'old', title: 'Old', task: 'Fix the model', task_type: 'dbt', featured: true, grade: { passed: false, detail: 'x' } },
      { id: 'new', title: 'New', featured: true },
    ]
    const lines = (events: object[]) => events.map((e) => JSON.stringify(e)).join('\n')
    const site = staticSource(
      '/data/',
      fakeFetch({
        '/data/home.json': JSON.stringify({ runs: featured, data_eng_bench: null, dab: null }),
        '/data/runs/old/meta.json': JSON.stringify(featured[0]),
        '/data/runs/old/events.jsonl': lines([
          { seq: 0, type: 'version', ts: '2026-11-01T10:00:00Z', config: 'full' },
          { seq: 1, type: 'result', ts: '2026-11-01T10:02:00Z', cost_usd: 0.5 },
        ]),
        '/data/runs/new/meta.json': JSON.stringify(featured[1]),
        '/data/runs/new/events.jsonl': lines([
          { seq: 0, type: 'version', ts: '2026-11-02T08:00:00Z', config: 'baseline' },
          { seq: 1, type: 'task', ts: '2026-11-02T08:00:00Z', task: 'How many orders?', task_type: 'query', data_source: 'shop' },
          { seq: 2, type: 'result', ts: '2026-11-02T08:00:30Z', subtype: 'success', cost_usd: 0.01 },
        ]),
      }),
    )
    expect(await site.history()).toEqual([
      {
        id: 'new',
        task: 'How many orders?',
        task_type: 'query',
        config: 'baseline',
        status: 'success',
        started_at: '2026-11-02T08:00:00Z',
        duration_ms: 30_000,
        cost_usd: 0.01,
      },
      {
        id: 'old',
        task: 'Fix the model',
        task_type: 'dbt',
        config: 'full',
        status: 'failure',
        started_at: '2026-11-01T10:00:00Z',
        duration_ms: 120_000,
        cost_usd: 0.5,
      },
    ])
  })

  test('history: a run without events has no start or duration', async () => {
    expect(await source.history()).toEqual([expect.objectContaining({ id: 'r1', started_at: 't' })])
    const empty = staticSource(
      '/data/',
      fakeFetch({ ...files, '/data/home.json': JSON.stringify({ runs: [{ id: 'a b', featured: true }], data_eng_bench: null, dab: null }) }),
    )
    expect(await empty.history()).toEqual([expect.objectContaining({ id: 'a b', started_at: null, duration_ms: null, cost_usd: null })])
  })
})

// --- The local backend (src/data_agent/web/app.py) ---

const row = (fields: Partial<RunRow>): RunRow => ({
  id: 'r1',
  task: 'How many orders?',
  task_type: 'query',
  config: 'full',
  status: 'running',
  started_at: '2026-11-16T09:00:00+00:00',
  duration_ms: null,
  cost_usd: null,
  ...fields,
})

const ev = (seq: number, type = 'tool_start'): RunEvent => ({ seq, type, ts: '2026-11-16T09:00:00+00:00' })

type Call = { url: string; method: string; body: unknown }

/** A backend answering from `routes` ("METHOD url" → [status, body]); records every call. */
function fakeBackend(routes: Record<string, [number, unknown]>) {
  const calls: Call[] = []
  const fetchFn = (async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const found = routes[`${method} ${url}`]
    if (!found) return new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404, statusText: 'Not Found' })
    return new Response(JSON.stringify(found[1]), { status: found[0], headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  return { fetchFn, calls }
}

class FakeStream implements EventStream {
  static all: FakeStream[] = []
  onmessage: ((message: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  closed = false
  constructor(public url: string) {
    FakeStream.all.push(this)
  }
  close() {
    this.closed = true
    this.readyState = 2
  }
  send(event: RunEvent) {
    this.onmessage?.({ data: JSON.stringify(event) })
  }
  /** The server closed the stream; the browser will reconnect (readyState CONNECTING). */
  drop(readyState = 0) {
    this.readyState = readyState
    this.onerror?.()
  }
}

const openStream = (url: string) => new FakeStream(url)

describe('notesOf and isLive', () => {
  test('the row gives the task and type; cancelled and failed come from its status', () => {
    expect(notesOf(row({ status: 'cancelled' }))).toEqual({ id: 'r1', task: 'How many orders?', task_type: 'query', cancelled: true, failed: false })
    expect(notesOf(row({ status: 'failure' }))).toMatchObject({ cancelled: false, failed: true })
    expect(notesOf(row({ status: 'running' }))).toMatchObject({ cancelled: false, failed: false })
  })

  test('a run is live until it has a result event or the backend says it ended', () => {
    expect(isLive({ notes: {}, events: [ev(0, 'version')] })).toBe(true)
    expect(isLive({ notes: {}, events: [ev(0, 'version'), ev(1, 'result')] })).toBe(false)
    expect(isLive({ notes: { cancelled: true }, events: [] })).toBe(false)
    expect(isLive({ notes: { failed: true }, events: [] })).toBe(false)
  })
})

describe('newestFirst', () => {
  test('sorts by start time, newest first; runs without one go last', () => {
    const rows = [row({ id: 'a', started_at: '2026-11-16T09:00:00+00:00' }), row({ id: 'b', started_at: null }), row({ id: 'c', started_at: '2026-11-16T10:00:00+00:00' })]
    expect(newestFirst(rows).map((r) => r.id)).toEqual(['c', 'a', 'b'])
  })

  test('compares instants, not strings', () => {
    const rows = [row({ id: 'utc', started_at: '2026-11-16T09:30:00+00:00' }), row({ id: 'plus2', started_at: '2026-11-16T11:00:00+02:00' })]
    expect(newestFirst(rows).map((r) => r.id)).toEqual(['utc', 'plus2'])
  })
})

describe('localSource', () => {
  beforeEach(() => {
    FakeStream.all = []
  })

  test('lists the runs as notes', async () => {
    const { fetchFn } = fakeBackend({ 'GET /api/runs': [200, [row({ status: 'cancelled' })]] })
    expect(await localSource('/api/', fetchFn, openStream).listRuns()).toEqual([notesOf(row({ status: 'cancelled' }))])
  })

  test('loads a run: its events and the notes from its row', async () => {
    const { fetchFn, calls } = fakeBackend({
      'GET /api/runs': [200, [row({ id: 'other' }), row({ id: 'a b', status: 'failure' })]],
      'GET /api/runs/a%20b/events': [200, { events: [ev(0, 'version'), ev(1)] }],
    })
    const run = await localSource('/api/', fetchFn, openStream).loadRun('a b')
    expect(run.events.map((e) => e.seq)).toEqual([0, 1])
    expect(run.notes).toEqual(notesOf(row({ id: 'a b', status: 'failure' })))
    expect(calls.map((c) => c.url).sort()).toEqual(['/api/runs', '/api/runs/a%20b/events'])
  })

  test('an unknown run fails with the reason', async () => {
    const { fetchFn } = fakeBackend({ 'GET /api/runs': [200, []], 'GET /api/runs/nope/events': [404, { detail: 'no run nope' }] })
    await expect(localSource('/api/', fetchFn, openStream).loadRun('nope')).rejects.toThrow('no run nope')
  })

  test('history: every row, newest first', async () => {
    const rows = [row({ id: 'old', started_at: '2026-11-15T09:00:00+00:00' }), row({ id: 'new' })]
    const { fetchFn } = fakeBackend({ 'GET /api/runs': [200, rows] })
    expect((await localSource('/api/', fetchFn, openStream).history()).map((r) => r.id)).toEqual(['new', 'old'])
  })

  test('options: what the form offers', async () => {
    const config = {
      name: 'default',
      layers: { skill: true, verifiers: true, context: true },
      model: 'deepseek-flash',
      effort: null,
      max_budget_usd: 1,
      max_turns: { query: 50, dbt: 200, large: 75 },
      agent_timeout_sec: null,
      network: true,
    }
    const options = { data_sources: ['shop'], task_types: ['query', 'dbt'], config }
    const { fetchFn } = fakeBackend({ 'GET /api/options': [200, options] })
    expect(await localSource('/api/', fetchFn, openStream).live!.options()).toEqual(options)
  })

  test('submit posts the form and answers the new run id', async () => {
    const { fetchFn, calls } = fakeBackend({ 'POST /api/runs': [201, { id: 'new-run' }] })
    const submission = { task: 'How many orders?', task_type: 'query' as const, data_source: 'shop' }
    expect(await localSource('/api/', fetchFn, openStream).live!.submit(submission)).toBe('new-run')
    expect(calls).toEqual([{ url: '/api/runs', method: 'POST', body: submission }])
  })

  test('submit passes network: false when networking is turned off', async () => {
    const { fetchFn, calls } = fakeBackend({ 'POST /api/runs': [201, { id: 'offline-run' }] })
    const submission = { task: 'How many orders?', task_type: 'query' as const, data_source: 'shop', network: false }
    expect(await localSource('/api/', fetchFn, openStream).live!.submit(submission)).toBe('offline-run')
    expect(calls).toEqual([{ url: '/api/runs', method: 'POST', body: submission }])
  })

  test('a refused submit fails with the reason the backend gives', async () => {
    const detail = 'no data source is configured; start the server with --data-sources FILE'
    const { fetchFn } = fakeBackend({ 'POST /api/runs': [400, { detail }] })
    const submission = { task: 'x', task_type: 'query' as const, data_source: '' }
    await expect(localSource('/api/', fetchFn, openStream).live!.submit(submission)).rejects.toThrow(detail)
  })

  test('a malformed submit fails with the fields FastAPI names', async () => {
    const detail = [{ loc: ['body', 'task'], msg: 'Field required', type: 'missing' }]
    const { fetchFn } = fakeBackend({ 'POST /api/runs': [422, { detail }] })
    const submission = { task: 'x', task_type: 'query' as const, data_source: 's' }
    await expect(localSource('/api/', fetchFn, openStream).live!.submit(submission)).rejects.toThrow('body.task: Field required')
  })

  test('cancel posts to the run and answers its row', async () => {
    const { fetchFn, calls } = fakeBackend({ 'POST /api/runs/r1/cancel': [200, row({ status: 'cancelled' })] })
    expect((await localSource('/api/', fetchFn, openStream).live!.cancel('r1')).status).toBe('cancelled')
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(['POST /api/runs/r1/cancel'])
  })

  test('a refused cancel fails with the reason', async () => {
    const { fetchFn } = fakeBackend({ 'POST /api/runs/r1/cancel': [409, { detail: 'run r1 is not a run this server is running' }] })
    await expect(localSource('/api/', fetchFn, openStream).live!.cancel('r1')).rejects.toThrow('not a run this server is running')
  })
})

describe('localSource: conversations', () => {
  const conversation = (fields: Partial<ConversationRow> = {}): ConversationRow => ({
    id: 'c1',
    single_run: false,
    created_at: '2026-11-17T10:00:00+00:00',
    running: false,
    turns: [{ turn: 1, run_id: 'r1', message: 'How many orders?', steers: [], status: 'success' }],
    ...fields,
  })
  const live = (fetchFn: typeof fetch) => localSource('/api/', fetchFn, openStream).live!

  test('lists the conversations as the backend orders them', async () => {
    const rows = [conversation({ id: 'new' }), conversation({ id: 'old' })]
    const { fetchFn } = fakeBackend({ 'GET /api/conversations': [200, rows] })
    expect(await live(fetchFn).conversations()).toEqual(rows)
  })

  test('reads one conversation, its id escaped', async () => {
    const { fetchFn } = fakeBackend({ 'GET /api/conversations/a%20b': [200, conversation({ id: 'a b' })] })
    expect((await live(fetchFn).conversation('a b')).id).toBe('a b')
  })

  test('starts a conversation with an empty body: no task type, no data source', async () => {
    const { fetchFn, calls } = fakeBackend({ 'POST /api/conversations': [201, { id: 'c2' }] })
    expect(await live(fetchFn).startConversation()).toBe('c2')
    expect(calls).toEqual([{ url: '/api/conversations', method: 'POST', body: {} }])
  })

  test('a message to an idle conversation answers the new turn', async () => {
    const { fetchFn, calls } = fakeBackend({ 'POST /api/conversations/c1/messages': [201, { kind: 'turn', turn: 2, run_id: 'r2' }] })
    expect(await live(fetchFn).send('c1', 'And in 2024?')).toEqual({ kind: 'turn', turn: 2, run_id: 'r2' })
    expect(calls).toEqual([{ url: '/api/conversations/c1/messages', method: 'POST', body: { text: 'And in 2024?' } }])
  })

  test('send forwards network: false when networking is turned off', async () => {
    const { fetchFn, calls } = fakeBackend({ 'POST /api/conversations/c1/messages': [201, { kind: 'turn', turn: 2, run_id: 'r2' }] })
    expect(await live(fetchFn).send('c1', 'Offline turn', false)).toEqual({ kind: 'turn', turn: 2, run_id: 'r2' })
    expect(calls).toEqual([{ url: '/api/conversations/c1/messages', method: 'POST', body: { text: 'Offline turn', network: false } }])
  })

  test('a message to a running conversation answers the steer', async () => {
    const { fetchFn } = fakeBackend({ 'POST /api/conversations/c1/messages': [202, { kind: 'steer', run_id: 'r1', id: 'm1' }] })
    expect(await live(fetchFn).send('c1', 'Only the top 3')).toEqual({ kind: 'steer', run_id: 'r1', id: 'm1' })
  })

  test('a refused message fails with the reason', async () => {
    const { fetchFn } = fakeBackend({ 'POST /api/conversations/c1/messages': [409, { detail: 'another run is going' }] })
    await expect(live(fetchFn).send('c1', 'x')).rejects.toThrow('another run is going')
  })

  test('cancel posts to the conversation and answers its row', async () => {
    const cancelled = conversation({ turns: [{ turn: 1, run_id: 'r1', message: 'x', steers: [], status: 'cancelled' }] })
    const { fetchFn, calls } = fakeBackend({ 'POST /api/conversations/c1/cancel': [200, cancelled] })
    expect(await live(fetchFn).cancelTurn('c1')).toEqual(cancelled)
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(['POST /api/conversations/c1/cancel'])
  })

  test("reads a turn's events without the run list", async () => {
    const { fetchFn, calls } = fakeBackend({ 'GET /api/runs/r1/events': [200, { events: [ev(0, 'version'), ev(1)] }] })
    expect((await live(fetchFn).events('r1')).map((e) => e.seq)).toEqual([0, 1])
    expect(calls.map((c) => c.url)).toEqual(['/api/runs/r1/events'])
  })
})

describe('turnNotes', () => {
  test("a turn's status as run notes: cancelled and failed are what the events cannot say", () => {
    expect(turnNotes({ run_id: 'r1', message: 'x', status: 'cancelled' })).toEqual({ id: 'r1', task: 'x', cancelled: true, failed: false })
    expect(turnNotes({ run_id: 'r1', message: null, status: 'failure' })).toEqual({ id: 'r1', task: null, cancelled: false, failed: true })
    expect(turnNotes({ run_id: 'r1', message: 'x', status: 'running' })).toEqual({ id: 'r1', task: 'x', cancelled: false, failed: false })
  })
})

describe('follow', () => {
  beforeEach(() => {
    FakeStream.all = []
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  function follow(rows: RunRow[], after = 1, routes: Record<string, [number, unknown]> = {}) {
    const { fetchFn } = fakeBackend({ 'GET /api/runs': [200, rows], ...routes })
    const batches: number[][] = []
    const notes: unknown[] = []
    const stop = localSource('/api/', fetchFn, openStream).live!.follow('r1', after, {
      events: (batch) => batches.push(batch.map((e) => e.seq)),
      notes: (n) => notes.push(n),
    })
    return { batches, notes, stop, stream: () => FakeStream.all.at(-1)! }
  }

  test('streams the events after the cursor, in batches', async () => {
    const { batches, stream } = follow([row({})])
    expect(stream().url).toBe('/api/runs/r1/events?after=1')
    stream().send(ev(2))
    stream().send(ev(3))
    expect(batches).toEqual([])
    await vi.advanceTimersByTimeAsync(200)
    expect(batches).toEqual([[2, 3]])
  })

  test('drops events it already has, so a reconnect neither loses nor repeats any', async () => {
    const { batches, stream } = follow([row({})])
    stream().send(ev(1))
    stream().send(ev(2))
    stream().drop()
    await vi.advanceTimersByTimeAsync(0)
    // The browser reconnects with Last-Event-ID; a frame it sees again is dropped.
    stream().send(ev(2))
    stream().send(ev(3))
    await vi.advanceTimersByTimeAsync(200)
    expect(batches.flat()).toEqual([2, 3])
    expect(stream().closed).toBe(false)
  })

  test('closes at the result event and hands over what it has at once', async () => {
    const { batches, stream } = follow([row({})])
    stream().send(ev(2))
    stream().send(ev(3, 'result'))
    expect(batches).toEqual([[2, 3]])
    expect(stream().closed).toBe(true)
  })

  test('a run that ended without a result: closes and hands over the row as notes', async () => {
    const { notes, stream } = follow([row({ status: 'cancelled' })], 1, { 'GET /api/runs/r1/events?after=1': [200, { events: [] }] })
    stream().drop()
    await vi.advanceTimersByTimeAsync(0)
    expect(stream().closed).toBe(true)
    expect(notes).toEqual([notesOf(row({ status: 'cancelled' }))])
  })

  test('a run that ended while the connection was down: the rest of its events still come', async () => {
    const rest = { events: [ev(3), ev(4, 'result')] }
    const { batches, notes, stream } = follow([row({ status: 'success' })], 1, { 'GET /api/runs/r1/events?after=2': [200, rest] })
    stream().send(ev(2))
    stream().drop()
    await vi.advanceTimersByTimeAsync(200)
    expect(batches.flat()).toEqual([2, 3, 4])
    expect(stream().closed).toBe(true)
    expect(notes).toEqual([notesOf(row({ status: 'success' }))])
  })

  test('a run the backend no longer lists: stops following instead of reopening forever', async () => {
    const { stream } = follow([])
    stream().drop(2)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(FakeStream.all).toHaveLength(1)
    expect(stream().closed).toBe(true)
  })

  test('a stream the browser gave up on is opened again after the last event seen', async () => {
    const { batches, stream } = follow([row({})])
    stream().send(ev(2))
    const first = stream()
    first.drop(2)
    await vi.advanceTimersByTimeAsync(5000)
    expect(FakeStream.all).toHaveLength(2)
    expect(stream().url).toBe('/api/runs/r1/events?after=2')
    stream().send(ev(3))
    await vi.advanceTimersByTimeAsync(200)
    expect(batches.flat()).toEqual([2, 3])
  })

  test('stop closes the stream and hands over nothing more', async () => {
    const { batches, stop, stream } = follow([row({})])
    stream().send(ev(2))
    stop()
    await vi.advanceTimersByTimeAsync(200)
    expect(stream().closed).toBe(true)
    expect(batches).toEqual([])
  })
})
