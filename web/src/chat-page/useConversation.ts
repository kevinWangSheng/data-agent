// A conversation kept live for the page: ./feed.ts's row and events held as React state, and the
// conversation view built from them.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { buildConversationView, type ConversationView, type TurnRun } from '../chat-view/model'
import { turnNotes, type ConversationRow, type LiveSource } from '../data/source'
import type { RunEvent } from '../run-view/model'
import { openFeed, type Feed } from './feed'

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
  /** Reads the row again now (after a message). */
  refresh: () => Promise<void>
  /** Takes a row the backend answered with (a cancel's). */
  setRow: (row: ConversationRow) => void
}

export function useConversation(live: LiveSource, id: string): LiveConversation {
  const [row, setRow] = useState<ConversationRow | null>(null)
  const [events, setEvents] = useState<Record<string, RunEvent[]>>({})
  const [error, setError] = useState<string | null>(null)
  const feed = useRef<Feed | null>(null)

  useEffect(() => {
    setRow(null)
    setEvents({})
    setError(null)
    const opened = openFeed(live, id, {
      row: setRow,
      events: (runId, batch) => setEvents((all) => ({ ...all, [runId]: merged(all[runId], batch) })),
      error: setError,
    })
    feed.current = opened
    return () => {
      opened.stop()
      if (feed.current === opened) feed.current = null
    }
  }, [live, id])

  const refresh = useCallback(async () => {
    await feed.current?.refresh()
  }, [])
  const take = useCallback((next: ConversationRow) => feed.current?.take(next), [])

  const view = useMemo(() => {
    if (!row) return null
    const runs: Record<string, TurnRun> = {}
    for (const turn of row.turns) runs[turn.run_id] = { events: events[turn.run_id] ?? [], notes: turnNotes(turn) }
    return buildConversationView(row, runs)
  }, [row, events])

  return { row, view, error, refresh, setRow: take }
}
