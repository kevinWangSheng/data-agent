// The live conversation feed against a stand-in backend: rows and event reads answered when the test
// says, timers faked.

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import type { ConversationRow, ConversationTurn, LiveSource } from '../data/source'
import type { RunEvent } from '../run-view/model'
import { openFeed, RETRY_CEILING_MS } from './feed'

const TIMING = { pollMs: 1000, retryMs: 2000 }

function turn(n: number, status: ConversationTurn['status']): ConversationTurn {
  return { turn: n, run_id: `run-${n}`, message: `message ${n}`, steers: [], status }
}
function row(running: boolean, ...turns: ConversationTurn[]): ConversationRow {
  return { id: 'c1', single_run: false, created_at: null, running, turns }
}

/** A promise the test settles. */
function later<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** Each `conversation` and `events` call waits for the test to answer it. */
function standIn() {
  const rowReads: ReturnType<typeof later<ConversationRow>>[] = []
  const eventReads: { runId: string; read: ReturnType<typeof later<RunEvent[]>> }[] = []
  const live = {
    conversation: vi.fn(() => {
      const read = later<ConversationRow>()
      rowReads.push(read)
      return read.promise
    }),
    events: vi.fn((runId: string) => {
      const read = later<RunEvent[]>()
      eventReads.push({ runId, read })
      return read.promise
    }),
    follow: vi.fn(() => () => {}),
  } as unknown as LiveSource
  return { live, rowReads, eventReads }
}

function handlers() {
  return { rows: [] as ConversationRow[], events: [] as [string, readonly RunEvent[]][], errors: [] as (string | null)[] }
}
function feedOf(live: LiveSource, seen = handlers()) {
  const feed = openFeed(
    live,
    'c1',
    { row: (r) => seen.rows.push(r), events: (id, b) => seen.events.push([id, b]), error: (e) => seen.errors.push(e) },
    TIMING,
  )
  return { feed, seen }
}

const flush = () => vi.advanceTimersByTimeAsync(0)
const RESULT: RunEvent[] = [{ seq: 1, type: 'result', ts: 't' } as RunEvent]

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('openFeed', () => {
  test('a row read that answers after a newer one is dropped, so a new turn keeps showing and is polled', async () => {
    const { live, rowReads } = standIn()
    const { feed, seen } = feedOf(live)
    rowReads[0].resolve(row(false, turn(1, 'cancelled')))
    await flush()

    // Cancelled, then a message right away: a read from before the message is still out
    // when the one after it answers with the new turn.
    const before = feed.refresh()
    const after = feed.refresh()
    rowReads[2].resolve(row(true, turn(1, 'cancelled'), turn(2, 'running')))
    await after
    rowReads[1].resolve(row(false, turn(1, 'cancelled')))
    await before

    expect(seen.rows.at(-1)?.turns.map((t) => t.run_id)).toEqual(['run-1', 'run-2'])
    const reads = rowReads.length
    await vi.advanceTimersByTimeAsync(TIMING.pollMs)
    expect(rowReads.length).toBe(reads + 1)
    feed.stop()
  })

  test("a row the backend answered a cancel with outranks a read that was already out", async () => {
    const { live, rowReads } = standIn()
    const { feed, seen } = feedOf(live)
    rowReads[0].resolve(row(true, turn(1, 'running')))
    await flush()

    const polled = feed.refresh()
    feed.take(row(false, turn(1, 'cancelled')))
    rowReads[1].resolve(row(true, turn(1, 'running')))
    await polled

    expect(seen.rows.at(-1)?.running).toBe(false)
    feed.stop()
  })

  test('a backend slower than the poll still has its rows shown, and one read that never answers stops nothing', async () => {
    const { live, rowReads } = standIn()
    const { feed, seen } = feedOf(live)
    rowReads[0].resolve(row(true, turn(1, 'running')))
    await flush()

    // Read 1 never answers; the poll goes on, and read 2 is shown although read 1 started first.
    await vi.advanceTimersByTimeAsync(TIMING.pollMs)
    await vi.advanceTimersByTimeAsync(TIMING.pollMs)
    expect(rowReads.length).toBe(3)
    rowReads[2].resolve(row(false, turn(1, 'success')))
    await flush()
    expect(seen.rows.at(-1)?.running).toBe(false)
    feed.stop()
  })

  test("a row the backend answered a cancel with clears an earlier read's error", async () => {
    const { live, rowReads } = standIn()
    const { feed, seen } = feedOf(live)
    rowReads[0].reject(new Error('502 Bad Gateway'))
    await flush()
    expect(seen.errors.at(-1)).toBe('502 Bad Gateway')

    feed.take(row(false, turn(1, 'cancelled')))
    expect(seen.errors.at(-1)).toBeNull()
    feed.stop()
  })

  test("a turn's events that could not be read are read again while the conversation is idle", async () => {
    const { live, rowReads, eventReads } = standIn()
    const { feed, seen } = feedOf(live)
    rowReads[0].resolve(row(false, turn(1, 'success')))
    await flush()
    eventReads[0].read.reject(new Error('502 Bad Gateway'))
    await flush()

    // Idle: no row read is coming, so the feed tries the events again on its own.
    await vi.advanceTimersByTimeAsync(TIMING.retryMs)
    expect(eventReads.map((r) => r.runId)).toEqual(['run-1', 'run-1'])
    eventReads[1].read.resolve(RESULT)
    await flush()
    expect(seen.events).toEqual([['run-1', RESULT]])
    feed.stop()
  })

  test("a turn's events that keep failing are read again less and less often, up to a ceiling", async () => {
    const { live, rowReads, eventReads } = standIn()
    const { feed } = feedOf(live)
    rowReads[0].resolve(row(false, turn(1, 'failure')))
    await flush()
    const failNext = async () => {
      eventReads.at(-1)?.read.reject(new Error('404 Not Found'))
      await flush()
    }

    await failNext()
    await vi.advanceTimersByTimeAsync(TIMING.retryMs)
    expect(eventReads.length).toBe(2)
    await failNext()
    await vi.advanceTimersByTimeAsync(TIMING.retryMs)
    expect(eventReads.length).toBe(2)
    await vi.advanceTimersByTimeAsync(TIMING.retryMs)
    expect(eventReads.length).toBe(3)
    for (let i = 0; i < 10; i++) {
      await failNext()
      await vi.advanceTimersByTimeAsync(RETRY_CEILING_MS)
    }
    expect(eventReads.length).toBe(13)
    feed.stop()
  })

  test('nothing is read again once stopped', async () => {
    const { live, rowReads, eventReads } = standIn()
    const { feed } = feedOf(live)
    rowReads[0].resolve(row(true, turn(1, 'running')))
    await flush()
    eventReads[0].read.reject(new Error('502 Bad Gateway'))
    await flush()
    feed.stop()

    await vi.advanceTimersByTimeAsync(TIMING.retryMs * 3)
    expect(rowReads.length).toBe(1)
    expect(eventReads.length).toBe(1)
  })
})
