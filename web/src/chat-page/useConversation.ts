// A conversation kept live: its row from the backend, every turn's events, the running turn
// followed over SSE (the run page's `follow`). The row is read again while a turn runs, so new
// turns, steers and the end of the turn's process show up; a message's kind (new turn or steer)
// is the backend's to decide.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { buildConversationView, type ConversationView, type TurnRun } from '../chat-view/model'
import { turnNotes, type ConversationRow, type LiveSource } from '../data/source'
import { errorText } from '../format'
import type { RunEvent } from '../run-view/model'

/** How often the row is read again while a turn runs. */
const POLL_MS = 1000

/** The events not seen yet appended, by `seq`. */
function merged(have: readonly RunEvent[] | undefined, more: readonly RunEvent[]): RunEvent[] {
  const last = have?.at(-1)?.seq ?? -1
  const fresh = more.filter((e) => e.seq > last)
  return fresh.length ? [...(have ?? []), ...fresh] : ((have as RunEvent[] | undefined) ?? [])
}

export type LiveConversation = {
  row: ConversationRow | null
  view: ConversationView | null
  error: string | null
  /** Reads the row again now (after a message or a cancel). */
  refresh: () => Promise<void>
  /** Takes a row the backend answered with (a cancel's). */
  setRow: (row: ConversationRow) => void
}

export function useConversation(live: LiveSource, id: string): LiveConversation {
  const [row, setRow] = useState<ConversationRow | null>(null)
  const [events, setEvents] = useState<Record<string, RunEvent[]>>({})
  const [error, setError] = useState<string | null>(null)
  /** Turns whose events are loaded or being loaded, and the stop functions of the followed ones. */
  const loaded = useRef(new Set<string>())
  const followers = useRef(new Map<string, () => void>())
  /** The id this hook is showing; null once unmounted, so late answers are dropped. */
  const current = useRef<string | null>(id)

  const refresh = useCallback(async () => {
    try {
      const next = await live.conversation(id)
      if (current.current !== id) return
      setRow(next)
      setError(null)
    } catch (e: unknown) {
      if (current.current === id) setError(errorText(e))
    }
  }, [live, id])

  useEffect(() => {
    current.current = id
    setRow(null)
    setEvents({})
    setError(null)
    void refresh()
    const followed = followers.current
    const seen = loaded.current
    return () => {
      current.current = null
      followed.forEach((stop) => stop())
      followed.clear()
      seen.clear()
    }
  }, [id, refresh])

  // Each turn's events once; a running turn is then followed until its `result`.
  useEffect(() => {
    if (!row) return
    for (const turn of row.turns) {
      const runId = turn.run_id
      if (loaded.current.has(runId)) continue
      loaded.current.add(runId)
      const add = (batch: readonly RunEvent[]) => setEvents((all) => ({ ...all, [runId]: merged(all[runId], batch) }))
      live
        .events(runId)
        .then((batch) => {
          if (current.current !== id) return
          add(batch)
          if (turn.status !== 'running' || batch.some((e) => e.type === 'result')) return
          followers.current.set(runId, live.follow(runId, batch.at(-1)?.seq ?? -1, { events: add, notes: () => void refresh() }))
        })
        // Not written yet, or the backend is away for a moment: tried again with the next row.
        .catch(() => loaded.current.delete(runId))
    }
  }, [row, live, id, refresh])

  const running = row?.running ?? false
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(timer)
  }, [running, refresh])

  const view = useMemo(() => {
    if (!row) return null
    const runs: Record<string, TurnRun> = {}
    for (const turn of row.turns) runs[turn.run_id] = { events: events[turn.run_id] ?? [], notes: turnNotes(turn) }
    return buildConversationView(row, runs)
  }, [row, events])

  return { row, view, error, refresh, setRow }
}
