// The online site's replay of a recorded conversation (spec decision 4, ticket 96): the turns' events
// one after the other on the run page's replay clock (../run-page/replay.ts), and what the chat view
// model is given once some of them have come. Pure, so the page only keeps the clock.

import type { ConversationIndex, TurnRun } from '../chat-view/model'
import type { Recording } from '../data/source'
import { eventTimes } from '../run-view/model'

/** How long a recorded conversation takes to play when it opens: several turns to read, so longer
 *  than the first screen's single run (`AUTOPLAY_MS`). */
export const CONVERSATION_AUTOPLAY_MS = 60_000

/** Each event's time in epoch milliseconds (null when unknown), turn after turn in the index's
 *  order; `replayClock` makes the clock of it, the pause between two turns included. */
export function recordingTimes({ conversation, runs }: Recording): (number | null)[] {
  return conversation.turns.flatMap((t) => {
    const events = runs[t.run_id]?.events ?? []
    // eventTimes counts from the run's first event; that event's own time puts the turn in place.
    const start = events.length ? Date.parse(String(events[0].ts)) : Number.NaN
    return eventTimes(events).map((ms) => (ms === null || !Number.isFinite(start) ? null : start + ms))
  })
}

/**
 * The conversation once its first `revealed` events have come (in `recordingTimes`' order): the
 * turns started so far, the last of them cut to the events it had written. That turn is still
 * running, so it has no cancelled or failed notes yet, and only the steers it has delivered: a
 * steer shows when it reached the agent. A turn shows from its first event; one without events
 * shows once every turn before it has.
 */
export function recordingAt({ conversation, runs }: Recording, revealed: number): { index: ConversationIndex; runs: Record<string, TurnRun> } {
  const turns: ConversationIndex['turns'] = []
  const shown: Record<string, TurnRun> = {}
  let left = revealed
  for (const turn of conversation.turns) {
    const run = runs[turn.run_id] ?? { events: [] }
    if (left >= run.events.length) {
      turns.push(turn)
      shown[turn.run_id] = run
      left -= run.events.length
      continue
    }
    if (left > 0) {
      const events = run.events.slice(0, left)
      const delivered = new Set(events.filter((e) => e.type === 'steer_delivered').map((e) => String(e.id)))
      turns.push({ ...turn, steers: turn.steers.filter((s) => delivered.has(s.id)) })
      shown[turn.run_id] = { events, notes: run.notes && { ...run.notes, cancelled: false, failed: false } }
    }
    break
  }
  return { index: { id: conversation.id, created_at: conversation.created_at, turns }, runs: shown }
}
