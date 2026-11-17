// The turn layout over the chat view model's fixtures (tests/fixtures/chat/conversation/, described in
// ../chat-view/acceptance/chat-view.test.ts): turn 1 ends with a clarification, turn 3 has a steer
// delivered between two queries.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

import { parseEventLines } from '../data/source'
import { buildConversationView, type ConversationIndex, type TurnRun } from '../chat-view/model'
import { answerable, composer, conversationTitle, steerState, turnLayout } from './layout'

const CHAT = join(import.meta.dirname, '../../../tests/fixtures/chat/conversation')
const index = JSON.parse(readFileSync(join(CHAT, 'conversation.json'), 'utf8')) as ConversationIndex
const eventsOf = (runId: string) => parseEventLines(readFileSync(join(CHAT, runId, 'events.jsonl'), 'utf8'))
const runs: Record<string, TurnRun> = Object.fromEntries(index.turns.map((t) => [t.run_id, { events: eventsOf(t.run_id) }]))

/** The view of the first `n` turns, the last one cut after `seq` when given. */
function viewOf(n: number, seq?: number) {
  const turns = index.turns.slice(0, n)
  const last = turns[n - 1].run_id
  const cut = seq === undefined ? runs : { ...runs, [last]: { events: runs[last].events.filter((e) => e.seq <= seq) } }
  return buildConversationView({ ...index, turns }, cut)
}

const summary = (layout: ReturnType<typeof turnLayout>) =>
  layout.groups.map((g) => ({ phase: g.phase.kind, steps: g.steps.map((s) => s.id), steers: g.steers.map((s) => s.id) }))

describe('turnLayout', () => {
  test('a clarification turn: its steps by phase, the question call among them, and no answer', () => {
    const layout = turnLayout(viewOf(5).turns[0])
    expect(summary(layout)).toEqual([{ phase: 'orient', steps: ['t1-list', 't1-ask'], steers: [] }])
    // The thinking before the first step goes with the first phase.
    expect(layout.groups[0].notes.map((n) => [n.kind, n.seq])).toEqual([['thinking', 3]])
    expect(layout.showAnswer).toBe(false)
  })

  test('a delivered steer goes with the phase of the step it came after; the answer text is not repeated', () => {
    const layout = turnLayout(viewOf(5).turns[2])
    expect(summary(layout)).toEqual([
      { phase: 'explore', steps: ['t3-q1'], steers: ['steer-1'] },
      { phase: 'answer', steps: ['t3-q2'], steers: [] },
    ])
    expect(layout.groups.flatMap((g) => g.notes)).toEqual([])
    expect(layout.pending).toEqual([])
    expect(layout.showAnswer).toBe(true)
  })

  test('a steer sent but not delivered yet waits at the end of the turn', () => {
    const layout = turnLayout(viewOf(3, 6).turns[2])
    expect(summary(layout)).toEqual([{ phase: 'explore', steps: ['t3-q1'], steers: [] }])
    expect(layout.pending.map((s) => [s.id, s.delivered])).toEqual([['steer-1', false]])
    expect(layout.showAnswer).toBe(false)
  })

  test('before any step, the thinking so far stands on its own', () => {
    const layout = turnLayout(viewOf(1, 3).turns[0])
    expect(layout.groups).toEqual([])
    expect(layout.before.map((n) => [n.kind, n.seq])).toEqual([['thinking', 3]])
  })
})

describe('conversationTitle', () => {
  test('the first message; a single run without one, its id; an empty conversation, a placeholder', () => {
    expect(conversationTitle('c1', false, 'How many orders?')).toBe('How many orders?')
    expect(conversationTitle('r1', true, null)).toBe('r1')
    expect(conversationTitle('c1', false, undefined)).toBe('New conversation')
  })
})

describe('answerable', () => {
  test("only the last turn's clarification, once the conversation is idle", () => {
    expect(answerable(viewOf(1), 0, false)).toBe(true)
    expect(answerable(viewOf(1), 0, true)).toBe(false)
    expect(answerable(viewOf(5), 0, false)).toBe(false)
  })

  test('a single run takes no answer', () => {
    expect(answerable({ ...viewOf(1), singleRun: true, canFollowUp: false }, 0, false)).toBe(false)
  })
})

describe('composer', () => {
  test('idle: a message starts the next turn', () => {
    expect(composer(viewOf(1), false)).toBe('turn')
  })

  test('a turn still running: a message goes into it', () => {
    expect(composer(viewOf(3, 6), true)).toBe('steer')
  })

  test("the turn wrote its result but its process has not exited: nothing is sent until it does", () => {
    // The backend would take the message as a steer for a session that already ended.
    expect(composer(viewOf(1), true)).toBe('settling')
  })

  test('a single run takes no message', () => {
    expect(composer({ ...viewOf(1), singleRun: true, canFollowUp: false }, false)).toBe('none')
  })
})

describe('steerState', () => {
  const sent = viewOf(3, 6).turns[2].steers[0]
  const delivered = viewOf(5).turns[2].steers[0]

  test('sent while the turn runs: on its way', () => {
    expect(steerState(sent, 'running')).toBe('sending')
  })

  test('the turn ended (cancelled, failed or done) before the agent read it: not delivered', () => {
    expect(steerState(sent, 'cancelled')).toBe('undelivered')
    expect(steerState(sent, 'failure')).toBe('undelivered')
    expect(steerState(sent, 'success')).toBe('undelivered')
  })

  test('delivered stays delivered', () => {
    expect(steerState(delivered, 'success')).toBe('delivered')
  })
})
