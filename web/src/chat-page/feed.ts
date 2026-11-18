// A conversation kept live, outside React so it can be tested: its row read from the backend (again
// every `pollMs` while a turn runs, so new turns, steers and the end of the turn's process show up;
// a read answering after a newer one was shown is dropped), each turn's events read once (again after
// `retryMs` when that fails), and a running turn followed over SSE (the run page's `follow`).
// A message's kind (new turn or steer) is the backend's to decide.

import { errorText } from '../format'
import type { ConversationRow, LiveSource } from '../data/source'
import type { RunEvent } from '../run-view/model'

export type FeedHandlers = {
  row(row: ConversationRow): void
  /** A turn's events, possibly some already handed over (the receiver merges by `seq`). */
  events(runId: string, batch: readonly RunEvent[]): void
  /** The row could not be read (null once it could again). */
  error(message: string | null): void
}

export type FeedTiming = {
  /** How often the row is read again while a turn runs. */
  pollMs: number
  /** How long after a failed read of a turn's events it is read again; doubled with each failure
   *  in a row, up to `RETRY_CEILING_MS` (a turn that died before writing its file never will). */
  retryMs: number
}

export const RETRY_CEILING_MS = 30_000

const FEED_TIMING: FeedTiming = { pollMs: 1000, retryMs: 1000 }

export type Feed = {
  /** Reads the row again now (after a message). */
  refresh(): Promise<void>
  /** Takes a row the backend answered with (a cancel's). */
  take(row: ConversationRow): void
  /** Drops everything still to come: timers, follows, answers in flight. */
  stop(): void
}

export function openFeed(live: LiveSource, id: string, on: FeedHandlers, timing: FeedTiming = FEED_TIMING): Feed {
  let stopped = false
  /** Turns whose events are loaded or being loaded, and the stop functions of the followed ones. */
  const loaded = new Set<string>()
  const followers = new Map<string, () => void>()
  const retries = new Set<ReturnType<typeof setTimeout>>()
  /** Failed reads in a row, by turn. */
  const failures = new Map<string, number>()
  let poll: ReturnType<typeof setInterval> | null = null
  /** The latest row shown. */
  let lastRow: ConversationRow | null = null
  /** Rows are numbered in the order they were asked for (a cancel's when it answered); one is shown
   *  only when numbered after the last shown, so a read answering late cannot undo a newer row, and
   *  a read that never answers holds nothing up. */
  let asked = 0
  let shown = 0

  const refresh = async () => {
    const n = ++asked
    try {
      const next = await live.conversation(id)
      if (stopped || n < shown) return
      shown = n
      apply(next)
    } catch (e: unknown) {
      if (!stopped && n > shown) on.error(errorText(e))
    }
  }

  const apply = (row: ConversationRow) => {
    lastRow = row
    on.error(null)
    on.row(row)
    for (const turn of row.turns) load(turn)
    if (row.running && poll === null) poll = setInterval(() => void refresh(), timing.pollMs)
    else if (!row.running && poll !== null) {
      clearInterval(poll)
      poll = null
    }
  }

  // Each turn's events once; a running turn is then followed until its `result`.
  const load = (turn: ConversationRow['turns'][number]) => {
    const runId = turn.run_id
    if (loaded.has(runId)) return
    loaded.add(runId)
    live
      .events(runId)
      .then((batch) => {
        if (stopped) return
        failures.delete(runId)
        on.events(runId, batch)
        if (turn.status !== 'running' || batch.some((e) => e.type === 'result')) return
        followers.set(runId, live.follow(runId, batch.at(-1)?.seq ?? -1, { events: (b) => on.events(runId, b), notes: () => void refresh() }))
      })
      // Not written yet, or the backend is away for a moment: read again after a while, with the
      // turn as the latest row has it (polling may have stopped, so this cannot wait for a row).
      .catch(() => {
        if (stopped) return
        const failed = (failures.get(runId) ?? 0) + 1
        failures.set(runId, failed)
        const retry = setTimeout(() => {
          retries.delete(retry)
          loaded.delete(runId)
          load(lastRow?.turns.find((t) => t.run_id === runId) ?? turn)
        }, Math.min(timing.retryMs * 2 ** (failed - 1), RETRY_CEILING_MS))
        retries.add(retry)
      })
  }

  void refresh()
  return {
    refresh,
    take: (row) => {
      if (stopped) return
      shown = ++asked
      apply(row)
    },
    stop: () => {
      stopped = true
      if (poll !== null) clearInterval(poll)
      retries.forEach(clearTimeout)
      retries.clear()
      followers.forEach((stop) => stop())
      followers.clear()
    },
  }
}
