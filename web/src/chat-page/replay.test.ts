// The online site's replay of a recorded conversation (ticket 96), on the hand-made conversation of
// tests/fixtures/chat/conversation/ (its layout: web/src/chat-view/acceptance/chat-view.test.ts).

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

import { buildConversationView, type ConversationIndex, type TurnRun } from '../chat-view/model'
import { parseEventLines, type Recording } from '../data/source'
import type { RunNotes } from '../run-view/model'
import { IDLE_CAP_MS, replayClock } from '../run-page/replay'
import { recordingAt, recordingTimes } from './replay'

const CHAT = join(import.meta.dirname, '../../../tests/fixtures/chat/conversation')
const index = JSON.parse(readFileSync(join(CHAT, 'conversation.json'), 'utf8')) as ConversationIndex
const [T1, T2, T3, T4] = index.turns.map((t) => t.run_id)

const runs: Record<string, TurnRun> = Object.fromEntries(
  index.turns.map((t) => {
    const events = parseEventLines(readFileSync(join(CHAT, t.run_id, 'events.jsonl'), 'utf8'))
    // As the export writes a turn's notes (evals/export.py `turn_notes`).
    const notes: RunNotes = { id: t.run_id, title: t.message, cancelled: t.run_id === T4, failed: false }
    return [t.run_id, { events, notes }]
  }),
)
const recording: Recording = { conversation: { ...index, title: 'Revenue, then customers' }, runs }
const counts = index.turns.map((t) => runs[t.run_id].events.length)
const total = counts.reduce((a, b) => a + b, 0)
/** How many events come before turn `i` (0-based). */
const before = (i: number) => counts.slice(0, i).reduce((a, b) => a + b, 0)
const viewAt = (revealed: number) => {
  const frame = recordingAt(recording, revealed)
  return buildConversationView(frame.index, frame.runs)
}

describe('recordingTimes', () => {
  test("every turn's events in turn order, on one clock", () => {
    const times = recordingTimes(recording)
    expect(times).toHaveLength(total)
    expect(times[0]).toBe(Date.parse(runs[T1].events[0].ts as string))
    expect(times[before(1)]).toBe(Date.parse(runs[T2].events[0].ts as string))
  })

  test('skipping idle cuts the pause between two turns as any other', () => {
    const clock = replayClock(recordingTimes(recording), true)
    expect(clock[before(1)] - clock[before(1) - 1]).toBe(IDLE_CAP_MS)
  })
})

describe('recordingAt', () => {
  test('everything revealed: the conversation as recorded', () => {
    expect(viewAt(total)).toEqual(buildConversationView(index, runs))
  })

  test('at the start: the first turn, running, with its first event only', () => {
    const view = viewAt(1)
    expect(view.turns.map((t) => t.runId)).toEqual([T1])
    expect(view.turns[0].message).toBe(index.turns[0].message)
    expect(view.turns[0].status).toBe('running')
    expect(view.running).toBe(true)
  })

  test('a turn shows once its first event comes, not before', () => {
    expect(viewAt(before(1)).turns.map((t) => t.runId)).toEqual([T1])
    expect(viewAt(before(1) + 1).turns.map((t) => t.runId)).toEqual([T1, T2])
  })

  test("a clarification's choice shows once the next turn has started", () => {
    expect(viewAt(before(1)).turns[0].clarification?.chosen).toBeNull()
    expect(viewAt(before(1) + 1).turns[0].clarification?.chosen).toBe(index.turns[1].message)
  })

  test('a steer shows once the run delivered it, not from the start of its turn', () => {
    const delivered = runs[T3].events.findIndex((e) => e.type === 'steer_delivered')
    expect(viewAt(before(2) + delivered).turns[2].steers).toEqual([])
    const [steer] = viewAt(before(2) + delivered + 1).turns[2].steers
    expect(steer).toMatchObject({ id: index.turns[2].steers[0].id, delivered: true })
  })

  test('a cancelled turn plays as running and ends cancelled', () => {
    expect(viewAt(before(3) + 1).turns[3].status).toBe('running')
    expect(viewAt(before(4)).turns[3].status).toBe('cancelled')
  })

  test('the cost so far: only the turns that have ended count theirs', () => {
    const ended = (n: number) => index.turns.slice(0, n).reduce((sum, t) => sum + (buildConversationView(index, runs).turns.find((v) => v.runId === t.run_id)?.run.summary.costUsd ?? 0), 0)
    expect(viewAt(before(2)).costUsd).toBeCloseTo(ended(2))
    expect(viewAt(total).costUsd).toBeCloseTo(ended(5))
  })
})
