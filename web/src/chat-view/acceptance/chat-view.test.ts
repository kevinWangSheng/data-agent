// Acceptance tests for the chat view model (.scratch/chat-ui/spec.md "Testing Decisions", no-model
// group 1): a conversation's index plus its turns' event files in, the chat page's structure out.
// Written by ticket 88; every test is `test.fails` (TICKET_94) until ticket 94 implements
// `buildConversationView`, which then turns `TICKET_94(...)` into `test(...)` and changes nothing
// else.
//
// Fixtures:
//   tests/fixtures/chat/conversation/   hand-made, five turns in one conversation (no real multi-turn
//                                       file exists yet; the fields are the contract in
//                                       tests/acceptance/test_chat_api.py):
//     1 a1a1a1  revenue question, ends with a clarification (2 options)
//     2 b2b2b2  the first option, clicked; one query on `orders`; answered
//     3 c3c3c3  a query on `orders`, a steer delivered, a query on `customers`; answered
//     4 d4d4d4  cancelled during its first query (notes.json: cancelled)
//     5 e5e5e5  one query on `customers`; answered
//   tests/fixtures/web/real/            real single runs written before the chat fields existed

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

import { buildRunView, type RunEvent, type RunNotes } from '../../run-view/model'
import { buildConversationView, type ConversationIndex, type TurnRun } from '../model'

const TICKET_94 = test.fails

const FIXTURES = join(import.meta.dirname, '../../../../tests/fixtures')
const CHAT = join(FIXTURES, 'chat/conversation')
const WEB = join(FIXTURES, 'web/real')

function parseLines(text: string): RunEvent[] {
  return text
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as RunEvent)
}

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, 'utf8')) as T
}

const index = readJson<ConversationIndex>(join(CHAT, 'conversation.json'))
const [T1, T2, T3, T4, T5] = index.turns.map((t) => t.run_id)

function chatRun(runId: string): TurnRun {
  const events = parseLines(readFileSync(join(CHAT, runId, 'events.jsonl'), 'utf8'))
  return runId === T4 ? { events, notes: readJson<RunNotes>(join(CHAT, runId, 'notes.json')) } : { events }
}

const runs: Record<string, TurnRun> = Object.fromEntries(index.turns.map((t) => [t.run_id, chatRun(t.run_id)]))

/** The index cut to its first `n` turns: what the backend had written when turn `n` was the last. */
function firstTurns(n: number): ConversationIndex {
  return { ...index, turns: index.turns.slice(0, n) }
}

/** Events up to and including `seq`: what a live turn has written so far. */
function upTo(run: TurnRun, seq: number): TurnRun {
  return { ...run, events: run.events.filter((e) => e.seq <= seq) }
}

function seqOf(run: TurnRun, type: string): number {
  const event = run.events.find((e) => e.type === type)
  if (!event) throw new Error(`no ${type} event`)
  return event.seq
}

const QUERY_SHOP = 'query-shop'
const CART = 'dbt-cart-abandonment'

function webRun(dir: string, withNotes: boolean): TurnRun {
  const events = parseLines(readFileSync(join(WEB, dir, 'events.jsonl'), 'utf8'))
  return withNotes ? { events, notes: readJson<RunNotes>(join(WEB, dir, 'meta.json')) } : { events }
}

/** A single run as `GET /api/conversations` lists it. */
function singleRun(runId: string, message: string | null): ConversationIndex {
  return {
    id: runId,
    single_run: true,
    created_at: null,
    turns: [{ turn: 1, run_id: runId, message, steers: [] }],
  }
}

// --- Turns -------------------------------------------------------------------------------------

describe('turns', () => {
  TICKET_94('come in the index order, with the messages that started them', () => {
    const view = buildConversationView(index, runs)
    expect(view.id).toBe('20261117T100000Z-c0ffee')
    expect(view.turns.map((t) => [t.turn, t.runId])).toEqual([
      [1, T1],
      [2, T2],
      [3, T3],
      [4, T4],
      [5, T5],
    ])
    expect(view.turns.map((t) => t.message)).toEqual(index.turns.map((t) => t.message))
    expect(view.singleRun).toBe(false)
    expect(view.canFollowUp).toBe(true)
    expect(view.running).toBe(false)
  })

  TICKET_94("each turn's cards are the run view model of its events", () => {
    const view = buildConversationView(index, runs)
    for (const turn of view.turns) {
      const { events, notes } = runs[turn.runId]
      expect(turn.run).toEqual(buildRunView(events, notes))
      expect(turn.status).toBe(turn.run.summary.status)
    }
    expect(view.turns[2].run.steps.map((s) => s.id)).toEqual(['t3-q1', 't3-q2'])
  })

  TICKET_94('a cancelled turn shows as cancelled, and the conversation goes on after it', () => {
    const view = buildConversationView(index, runs)
    expect(view.turns.map((t) => t.status)).toEqual(['success', 'success', 'success', 'cancelled', 'success'])
    expect(view.turns[3].run.answer).toBeNull()
    expect(view.turns[4].run.answer?.text).toBe('East 4, North 3, South 2, West 5.')
    expect(view.canFollowUp).toBe(true)
  })
})

// --- Clarification -----------------------------------------------------------------------------

describe('clarification', () => {
  TICKET_94('the question and its options end the turn; the clicked option is the next message', () => {
    const view = buildConversationView(index, runs)
    const [first, second] = view.turns
    expect(first.clarification).toEqual({
      seq: seqOf(runs[T1], 'clarification'),
      toolUseId: 't1-ask',
      question: 'Should refunded orders count toward 2024 revenue?',
      options: ['Net: exclude refunded orders', 'Gross: include refunded orders'],
      chosen: 'Net: exclude refunded orders',
    })
    expect(first.run.answer).toBeNull()
    expect(second.message).toBe('Net: exclude refunded orders')
    expect(second.answersClarification).toBe(true)
    expect(second.clarification).toBeNull()
    expect(view.turns.slice(2).map((t) => t.answersClarification)).toEqual([false, false, false])
  })

  TICKET_94('a reply that is not one of the options chooses none', () => {
    const typed: ConversationIndex = {
      ...index,
      turns: index.turns.map((t) => (t.turn === 2 ? { ...t, message: 'Use the net figure.' } : t)),
    }
    const view = buildConversationView(typed, runs)
    expect(view.turns[0].clarification?.chosen).toBeNull()
    expect(view.turns[1].answersClarification).toBe(false)
  })

  TICKET_94('a clarification still waiting for its reply has no choice yet', () => {
    const view = buildConversationView(firstTurns(1), { [T1]: runs[T1] })
    expect(view.turns[0].clarification?.options).toHaveLength(2)
    expect(view.turns[0].clarification?.chosen).toBeNull()
    expect(view.running).toBe(false)
  })
})

// --- Steering ----------------------------------------------------------------------------------

describe('steers', () => {
  TICKET_94('a delivered steer shows after the step that started before it', () => {
    const view = buildConversationView(index, runs)
    expect(view.turns[2].steers).toEqual([
      {
        id: 'steer-1',
        text: 'Only the top 3, please.',
        delivered: true,
        seq: seqOf(runs[T3], 'steer_delivered'),
        afterStep: 't3-q1',
      },
    ])
    for (const turn of [0, 1, 3, 4]) expect(view.turns[turn].steers).toEqual([])
  })

  TICKET_94('a steer sent but not delivered yet has no mark and no place', () => {
    const live = upTo(runs[T3], seqOf(runs[T3], 'steer_delivered') - 1)
    const view = buildConversationView(firstTurns(3), { [T1]: runs[T1], [T2]: runs[T2], [T3]: live })
    expect(view.turns[2].steers).toEqual([
      { id: 'steer-1', text: 'Only the top 3, please.', delivered: false, seq: null, afterStep: null },
    ])
  })

  TICKET_94('a delivered steer the index does not list yet still shows', () => {
    const lagging: ConversationIndex = {
      ...index,
      turns: index.turns.map((t) => ({ ...t, steers: [] })),
    }
    const view = buildConversationView(lagging, runs)
    expect(view.turns[2].steers.map((s) => [s.id, s.text, s.delivered, s.afterStep])).toEqual([
      ['steer-1', 'Only the top 3, please.', true, 't3-q1'],
    ])
  })
})

// --- Cost and databases --------------------------------------------------------------------------

describe('cost', () => {
  TICKET_94("the conversation's cost is the sum of its turns' cost_usd", () => {
    const view = buildConversationView(index, runs)
    expect(view.costUsd).toBeCloseTo(0.004 + 0.006 + 0.01 + 0.003, 10)
    const known = view.turns.map((t) => t.run.summary.costUsd).filter((c): c is number => c !== null)
    expect(known).toHaveLength(4)
    expect(view.costUsd).toBeCloseTo(known.reduce((a, b) => a + b, 0), 10)
  })

  TICKET_94('no cost before any turn has finished', () => {
    const live = upTo(runs[T1], 4)
    expect(buildConversationView(firstTurns(1), { [T1]: live }).costUsd).toBeNull()
  })
})

describe('databases', () => {
  TICKET_94('each query names the database it ran on', () => {
    const view = buildConversationView(index, runs)
    expect(view.turns.map((t) => t.queries)).toEqual([
      [],
      [{ step: 't2-q1', database: 'orders' }],
      [
        { step: 't3-q1', database: 'orders' },
        { step: 't3-q2', database: 'customers' },
      ],
      [{ step: 't4-q1', database: 'orders' }],
      [{ step: 't5-q1', database: 'customers' }],
    ])
  })
})

// --- Single runs and old files -------------------------------------------------------------------

describe('single runs', () => {
  TICKET_94('a query run from before the chat fields replays as a one-turn conversation', () => {
    const run = webRun(QUERY_SHOP, false)
    const view = buildConversationView(singleRun('20261115T143153Z-old', null), { '20261115T143153Z-old': run })
    expect(view.singleRun).toBe(true)
    expect(view.canFollowUp).toBe(false)
    expect(view.running).toBe(false)
    const [turn] = view.turns
    expect(view.turns).toHaveLength(1)
    expect(turn.run).toEqual(buildRunView(run.events))
    expect(turn.status).toBe('success')
    expect(turn.message).toBeNull()
    expect(turn.clarification).toBeNull()
    expect(turn.steers).toEqual([])
    expect(turn.queries).toHaveLength(11)
    expect(turn.queries.every((q) => q.database === null)).toBe(true)
    expect(view.costUsd).toBeCloseTo(0.006241872, 12)
  })

  TICKET_94('a dbt run is a one-turn conversation that cannot be followed up', () => {
    const run = webRun(CART, true)
    const view = buildConversationView(singleRun(CART, null), { [CART]: run })
    expect(view.canFollowUp).toBe(false)
    const [turn] = view.turns
    expect(turn.run).toEqual(buildRunView(run.events, run.notes))
    expect(turn.run.summary.taskType).toBe('dbt')
    expect(turn.message).toBe(run.notes?.task)
    const sqlSteps = run.events.filter((e) => e.type === 'sql').map((e) => e.tool_use_id)
    expect(turn.queries.map((q) => q.step)).toEqual(sqlSteps)
    expect(turn.queries.every((q) => q.database === null)).toBe(true)
  })
})

// --- A running turn ----------------------------------------------------------------------------

describe('prefix', () => {
  TICKET_94('a turn still running shows what it has done so far', () => {
    const cut = seqOf(runs[T3], 'steer_delivered') - 1
    const live = upTo(runs[T3], cut)
    const view = buildConversationView(firstTurns(3), { [T1]: runs[T1], [T2]: runs[T2], [T3]: live })
    expect(view.running).toBe(true)
    expect(view.turns.map((t) => t.status)).toEqual(['success', 'success', 'running'])
    const turn = view.turns[2]
    expect(turn.run).toEqual(buildRunView(live.events))
    expect(turn.run.answer).toBeNull()
    expect(turn.queries).toEqual([{ step: 't3-q1', database: 'orders' }])
    expect(view.costUsd).toBeCloseTo(0.004 + 0.006, 10)
  })

  TICKET_94('a turn whose event file is not written yet shows its message and nothing else', () => {
    const view = buildConversationView(firstTurns(3), { [T1]: runs[T1], [T2]: runs[T2] })
    expect(view.running).toBe(true)
    const turn = view.turns[2]
    expect(turn.message).toBe(index.turns[2].message)
    expect(turn.status).toBe('running')
    expect(turn.run).toEqual(buildRunView([]))
    expect(turn.steers.map((s) => s.delivered)).toEqual([false])
    expect(turn.queries).toEqual([])
  })
})
