// The "Open in Langfuse" link on the pages (ticket 114): the run page's summary and each chat
// turn's meta line link to the run's `trace` event's url in a new tab, and show nothing without
// one. Node environment: the pages render to markup (react-dom/server), as in
// web/src/chart/acceptance/answer-charts.test.ts.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test } from 'vitest'

import { Transcript } from '../chat-page/Transcript'
import { buildConversationView, type ConversationIndex, type TurnRun } from '../chat-view/model'
import { parseEventLines } from '../data/source'
import { RunPage } from '../run-page/RunPage'
import { buildRunView, type RunEvent, type RunNotes } from './model'
import { traceLink } from './trace'

const RUN = join(import.meta.dirname, '../../../tests/fixtures/web/real/query-shop')
const events = parseEventLines(readFileSync(join(RUN, 'events.jsonl'), 'utf8'))
const notes = JSON.parse(readFileSync(join(RUN, 'meta.json'), 'utf8')) as RunNotes

const FIRST = { trace_id: '0af7651916cd43dd8448eb211c80319c', url: 'https://langfuse.example/project/p/traces/0af76519' }
const SECOND = { trace_id: '4bf92f3577b34da6a3ce929d0e0e4736', url: 'https://langfuse.example/project/p/traces/4bf92f35' }
const LABEL = 'Open in Langfuse'

function traceEvent(seq: number, trace: { trace_id: string; url: string }): RunEvent {
  return { seq, type: 'trace', ts: '2026-11-15T14:32:11+00:00', ...trace, forwarded_spans: 9, forwarded_llm_requests: 3 }
}

/** The fixture's events with a `trace` event appended (after `result`, as the relay usually writes it). */
function withTrace(trace: { trace_id: string; url: string }, from: readonly RunEvent[] = events): RunEvent[] {
  return [...from, traceEvent(from.length, trace)]
}

/** The fixture's events with the `trace` event before `result`: the order is not fixed. */
function traceBeforeResult(trace: { trace_id: string; url: string }): RunEvent[] {
  const at = events.findIndex((e) => e.type === 'result')
  return [...events.slice(0, at), traceEvent(events.length, trace), ...events.slice(at)]
}

/** The `<a>` tags that carry the link text. */
function langfuseLinks(html: string): string[] {
  return [...html.matchAll(/<a\b[^>]*>[^<]*Open in Langfuse[^<]*<\/a>/g)].map((m) => m[0])
}

function expectLinkTo(html: string, url: string) {
  const links = langfuseLinks(html)
  expect(links).toHaveLength(1)
  expect(links[0]).toContain(`href="${url}"`)
  expect(links[0]).toContain('target="_blank"')
  expect(links[0]).toContain('rel="noreferrer"')
}

const noop = () => {}

function runPage(evs: RunEvent[]): string {
  return renderToStaticMarkup(createElement(RunPage, { run: { events: evs, notes }, route: { id: 'query-shop', step: null, filter: 'all' }, navigate: noop }))
}

function chatPage(index: ConversationIndex, runs: Record<string, TurnRun>): string {
  const view = buildConversationView(index, runs)
  return renderToStaticMarkup(createElement(Transcript, { view, title: 'Chat', stick: { current: true }, runHref: (id: string) => id, onGo: noop, footer: null }))
}

/** Each turn's own markup, split at its `data-testid="turn"` section. */
function turnsOf(html: string): string[] {
  return html.split('data-testid="turn"').slice(1)
}

const T1 = '20261123T100000Z-aaaaaa'
const T2 = '20261123T100100Z-bbbbbb'
const TWO_TURNS: ConversationIndex = {
  id: '20261123T100000Z-c0nv01',
  created_at: '2026-11-23T10:00:00+00:00',
  turns: [
    { turn: 1, run_id: T1, message: 'first question', steers: [] },
    { turn: 2, run_id: T2, message: 'second question', steers: [] },
  ],
}

describe('the run page', () => {
  test('links to the trace in a new tab when the run has a trace event', () => {
    expectLinkTo(runPage(withTrace(FIRST)), FIRST.url)
  })

  test('links to it when the trace event comes before result too', () => {
    expectLinkTo(runPage(traceBeforeResult(FIRST)), FIRST.url)
  })

  test('has no link without a trace event', () => {
    const html = runPage(events)
    expect(langfuseLinks(html)).toEqual([])
    expect(html).not.toContain(LABEL)
  })

  // Not the wall time: the summary ends at the last event, and the trace event comes after the
  // relay stops (run-view/model.ts, outside this ticket).
  test("a trace event leaves the run's steps, status and answer as they were", () => {
    const plain = buildRunView(events, notes)
    for (const evs of [withTrace(FIRST), traceBeforeResult(FIRST)]) {
      const view = buildRunView(evs, notes)
      expect(view.steps).toEqual(plain.steps)
      expect(view.summary.status).toBe(plain.summary.status)
      expect(view.answer).toEqual(plain.answer)
    }
  })
})

describe('the chat page', () => {
  test("each turn links to its own turn's trace", () => {
    const html = chatPage(TWO_TURNS, { [T1]: { events: withTrace(FIRST) }, [T2]: { events: withTrace(SECOND) } })
    const [first, second] = turnsOf(html)
    expectLinkTo(first, FIRST.url)
    expect(first).not.toContain(SECOND.url)
    expectLinkTo(second, SECOND.url)
    expect(second).not.toContain(FIRST.url)
  })

  test('a turn without a trace event has no link', () => {
    const html = chatPage(TWO_TURNS, { [T1]: { events: withTrace(FIRST) }, [T2]: { events } })
    const [first, second] = turnsOf(html)
    expectLinkTo(first, FIRST.url)
    expect(langfuseLinks(second)).toEqual([])
  })

  test('a running turn has no link until its trace event is written', () => {
    const running = events.filter((e) => e.type !== 'result')
    const html = chatPage(TWO_TURNS, { [T1]: { events: withTrace(FIRST) }, [T2]: { events: running } })
    const [, second] = turnsOf(html)
    expect(langfuseLinks(second)).toEqual([])
  })

  test('the turn view carries the link, and its run view is untouched', () => {
    const view = buildConversationView(TWO_TURNS, { [T1]: { events: withTrace(FIRST) }, [T2]: { events } })
    expect(view.turns.map((t) => t.trace)).toEqual([{ traceId: FIRST.trace_id, url: FIRST.url }, null])
    expect(view.turns[0].run).toEqual(buildRunView(withTrace(FIRST)))
  })
})

describe('traceLink', () => {
  test('only a web address becomes a link: the page puts it in an href', () => {
    expect(traceLink(withTrace({ trace_id: FIRST.trace_id, url: 'javascript:alert(1)' }))).toBeNull()
    expect(traceLink([...events, { seq: 99, type: 'trace', ts: '2026-11-15T14:32:11+00:00', trace_id: FIRST.trace_id }])).toBeNull()
  })
})
