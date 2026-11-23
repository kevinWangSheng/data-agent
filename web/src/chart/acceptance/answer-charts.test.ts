// Acceptance tests for answer charts on the pages (.scratch/charts/spec.md "前端" and "Testing
// Decisions"): which calls become charts, where the chat page, the run page and the online site put
// them, the timeline's kind for the call, and the chart colours of both themes. Node environment:
// the pages render to markup (react-dom/server), where each chart's root element shows but recharts
// draws no shapes (./chart-render.test.ts draws them in jsdom).
//
// Written by ticket 118. Every test is `test.fails` until ticket 120 builds the feature, which then
// drops `.fails` and changes nothing else.
//
// Fixture (tests/fixtures/charts/run/, shared with the backend's replay test): a query run with
// three query_database calls and six display_chart calls in this order:
//   t-c1 bar, stacked, 2 series (query_1, 3 rows)    t-c2 line (query_2, 4 rows)
//   t-c3 an error: a column the result does not have  t-c4 area (query_2, 4 rows)
//   t-c5 scatter (query_3, 5 rows)                    t-c6 pie (query_1, 3 rows)
// then its text answer.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test } from 'vitest'

import { Transcript } from '../../chat-page/Transcript'
import { buildConversationView, type ConversationIndex } from '../../chat-view/model'
import { homeData } from '../../data/home'
import { parseEventLines, staticSource } from '../../data/source'
import { RunPage } from '../../run-page/RunPage'
import { toolKind } from '../../run-page/tools'
import { buildRunView, type RunEvent, type RunNotes } from '../../run-view/model'
import { RecordedConversation } from '../../site/RecordedConversation'
import { answerCharts, type ChartPayload } from '../model'

const RUN = join(import.meta.dirname, '../../../../tests/fixtures/charts/run')
const CSS = join(import.meta.dirname, '../../index.css')

const eventsText = readFileSync(join(RUN, 'events.jsonl'), 'utf8')
const metaText = readFileSync(join(RUN, 'meta.json'), 'utf8')
const events = parseEventLines(eventsText)
const notes = JSON.parse(metaText) as RunNotes
const RUN_ID = notes.id as string

const CHART_STEPS = ['t-c1', 't-c2', 't-c4', 't-c5', 't-c6']
const FAILED_STEP = 't-c3'
const TYPES = ['bar', 'line', 'area', 'scatter', 'pie']

function startOf(id: string): RunEvent {
  const found = events.find((e) => e.type === 'tool_start' && e.tool_use_id === id)
  if (!found) throw new Error(`no tool_start ${id}`)
  return found
}

function endOf(id: string): RunEvent {
  const found = events.find((e) => e.type === 'tool_end' && e.tool_use_id === id)
  if (!found) throw new Error(`no tool_end ${id}`)
  return found
}

/** The payloads the fixture's successful calls returned, read straight from the event file. */
const payloads = CHART_STEPS.map((id) => JSON.parse(endOf(id).content as string) as ChartPayload)
const TITLES = payloads.map((p) => p.chart.title)
const FAILED_TITLE = (startOf(FAILED_STEP).input as { title: string }).title
// The answer's first words, as the markdown renders them.
const ANSWER_START = 'Gold customers bring the most revenue'

// --- which calls are charts ----------------------------------------------------------------

describe('answerCharts', () => {
  test.fails("the main agent's successful display_chart calls, in call order", () => {
    const charts = answerCharts(buildRunView(events, notes))
    expect(charts.map((c) => c.step)).toEqual(CHART_STEPS)
    expect(charts.map((c) => c.seq)).toEqual(CHART_STEPS.map((id) => startOf(id).seq))
    expect(charts.map((c) => c.payload)).toEqual(payloads)
    expect(charts.map((c) => c.payload.chart.chart_type)).toEqual(TYPES)
    expect(charts.map((c) => c.kind)).toEqual(['cartesian', 'cartesian', 'cartesian', 'scatter', 'pie'])
    expect(charts.map((c) => c.series)).toEqual([2, 1, 1, 1, 1])
    expect(charts.map((c) => c.rows)).toEqual([3, 4, 4, 5, 3])
    expect(charts.map((c) => c.step)).not.toContain(FAILED_STEP)
  })

  test.fails('a call still open is not a chart yet', () => {
    const openSeq = endOf('t-c4').seq
    const charts = answerCharts(buildRunView(events.filter((e) => e.seq < openSeq), notes))
    expect(charts.map((c) => c.step)).toEqual(['t-c1', 't-c2'])
  })

  test.fails('a call that failed is left out, even when its result reads as a chart', () => {
    const failed = events.map((e) => (e.type === 'tool_end' && e.tool_use_id === 't-c2' ? { ...e, is_error: true } : e))
    const charts = answerCharts(buildRunView(failed, notes))
    expect(charts.map((c) => c.step)).toEqual(['t-c1', 't-c4', 't-c5', 't-c6'])
  })

  test.fails("a verifier's call is not the main agent's", () => {
    // t-c6 made inside a verifier dispatch: an Agent call the dispatch names, and t-c6 under it.
    const at = startOf('t-c6').seq
    const agent: RunEvent[] = [
      { seq: at - 0.6, type: 'tool_start', ts: startOf('t-c6').ts, tool: 'Agent', tool_use_id: 't-a1', input: {}, parent: null },
      { seq: at - 0.5, type: 'verifier_dispatch', ts: startOf('t-c6').ts, n: 1, models: [], source: 'first', tool_use_id: 't-a1' },
    ]
    const nested = events.flatMap((e) => {
      const moved = e.tool_use_id === 't-c6' && (e.type === 'tool_start' || e.type === 'tool_end') ? { ...e, parent: 't-a1' } : e
      return e.seq === at ? [...agent, moved] : [moved]
    })
    const view = buildRunView(nested, notes)
    expect(view.steps.find((s) => s.id === 't-c6')?.dispatch).toBe(1)
    expect(answerCharts(view).map((c) => c.step)).toEqual(CHART_STEPS.slice(0, 4))
  })

  test.fails('a result that is not a chart payload is left out', () => {
    const broken = events.map((e) => (e.type === 'tool_end' && e.tool_use_id === 't-c1' ? { ...e, content: 'not json' } : e))
    const charts = answerCharts(buildRunView(broken, notes))
    expect(charts.map((c) => c.step)).toEqual(CHART_STEPS.slice(1))
  })
})

// --- where the pages put them --------------------------------------------------------------

/** The outer markup of every element whose opening tag has `data-testid="<id>"`. React's markup is
 *  well formed, so the element ends where the tags of its name balance. */
function elements(html: string, id: string): string[] {
  const found: string[] = []
  const marker = `data-testid="${id}"`
  for (let at = html.indexOf(marker); at !== -1; at = html.indexOf(marker, at + 1)) {
    const start = html.lastIndexOf('<', at)
    const name = /^<([a-zA-Z][\w-]*)/.exec(html.slice(start))?.[1]
    if (!name) throw new Error(`no tag before ${marker}`)
    const tags = new RegExp(`<${name}[\\s>]|</${name}>`, 'g')
    tags.lastIndex = start
    let depth = 0
    let end = -1
    for (let m = tags.exec(html); m; m = tags.exec(html)) {
      depth += m[0].startsWith('</') ? -1 : 1
      if (depth === 0) {
        end = m.index + m[0].length
        break
      }
    }
    if (end === -1) throw new Error(`${marker}: the element does not end`)
    found.push(html.slice(start, end))
  }
  return found
}

/** The page's answer shows the five charts after its text, in call order, and nothing else of them
 *  is outside it. */
function expectChartsInAnswer(html: string) {
  const answers = elements(html, 'answer')
  expect(answers).toHaveLength(1)
  const [answer] = answers
  const charts = elements(answer, 'chart')
  expect(charts.map((c) => /data-chart-type="([^"]*)"/.exec(c)?.[1])).toEqual(TYPES)
  charts.forEach((c, i) => expect(c).toContain(TITLES[i]))
  // Each title after the previous one; every chart after the answer's text.
  const text = answer.indexOf(ANSWER_START)
  expect(text).toBeGreaterThan(-1)
  const at = charts.map((c) => answer.indexOf(c))
  expect(at[0]).toBeGreaterThan(text)
  expect([...at].sort((a, b) => a - b)).toEqual(at)
  // The failed call is no chart anywhere on the page (its title is its input's alone).
  expect(elements(html, 'chart').filter((c) => c.includes(FAILED_TITLE))).toEqual([])
}

const CONVERSATION: ConversationIndex = {
  id: '20261123T100000Z-ch4rt5',
  created_at: '2026-11-23T10:00:00+00:00',
  turns: [{ turn: 1, run_id: RUN_ID, message: notes.task as string, steers: [] }],
}

/** The online site's files for the fixture: home.json, as the site build makes it, with the run
 *  featured and the one-turn conversation, and the run's own files. */
function siteFiles(): Record<string, string> {
  const home = homeData({ runs: [notes], conversations: [{ ...CONVERSATION, title: 'Revenue charts' }] }, null)
  return {
    'home.json': JSON.stringify(home),
    [`runs/${RUN_ID}/meta.json`]: metaText,
    [`runs/${RUN_ID}/events.jsonl`]: eventsText,
  }
}

const fakeFetch = (files: Record<string, string>): typeof fetch =>
  (async (url: string) => {
    const body = files[url.replace(/^\/data\//, '')]
    return new Response(body ?? 'Not Found', { status: body === undefined ? 404 : 200 })
  }) as typeof fetch

const site = staticSource('/data/', fakeFetch(siteFiles()))
const noop = () => {}

describe('the answer area', () => {
  test.fails('the chat page shows the charts in the answer, after its text', () => {
    const view = buildConversationView(CONVERSATION, { [RUN_ID]: { events, notes } })
    const html = renderToStaticMarkup(
      createElement(Transcript, { view, title: 'Charts', stick: { current: true }, runHref: (id: string) => id, onGo: noop, footer: null }),
    )
    expectChartsInAnswer(html)
  })

  test.fails('the run page shows the charts in the answer, after its text', () => {
    const html = renderToStaticMarkup(
      createElement(RunPage, { run: { events, notes }, route: { id: RUN_ID, step: null, filter: 'all' }, navigate: noop }),
    )
    expectChartsInAnswer(html)
  })

  test.fails("the online site's recorded conversation shows them the same way", async () => {
    const recording = await site.recording(CONVERSATION.id)
    const html = renderToStaticMarkup(createElement(RecordedConversation, { recording, autoplay: false, runHref: (id: string) => id, onGo: noop }))
    expectChartsInAnswer(html)
  })

  test.fails("the online site's run page shows them the same way", async () => {
    const run = await site.loadRun(RUN_ID)
    const html = renderToStaticMarkup(createElement(RunPage, { run, route: { id: RUN_ID, step: null, filter: 'all' }, navigate: noop }))
    expectChartsInAnswer(html)
  })
})

// --- the timeline ---------------------------------------------------------------------------

describe('the timeline', () => {
  test.fails('a display_chart call is a chart step', () => {
    // `toBe`, not `===`: 'chart' is not a ToolKind until ticket 120 adds it.
    expect(toolKind('mcp__data__display_chart')).toBe('chart')
  })
})

// --- colours --------------------------------------------------------------------------------

const SERIES_VARS = [1, 2, 3, 4, 5, 6].map((n) => `--chart-series-${n}`)
// The three colour blocks of index.css: light, the system's dark, and data-theme="dark".
const BLOCKS: [string, string][] = [
  ['light', ':root {'],
  ['system dark', ":root:not([data-theme='light']) {"],
  ['dark', ":root[data-theme='dark'] {"],
]

function block(css: string, opening: string): Record<string, string> {
  const start = css.indexOf(opening)
  if (start === -1) throw new Error(`no block ${opening}`)
  const body = css.slice(start + opening.length, css.indexOf('}', start))
  const vars: Record<string, string> = {}
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) vars[m[1]] = m[2].trim()
  return vars
}

/** sRGB channels 0-255 of `#rgb`, `#rrggbb` or `rgb(r, g, b)`: the notations the series colours
 *  are written in (web/src/chart/Chart.tsx), as every colour of index.css is. */
function rgb(color: string): [number, number, number] {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color)
  if (hex) {
    const h = hex[1].length === 3 ? [...hex[1]].map((c) => c + c).join('') : hex[1]
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number]
  }
  const fn = /^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$/.exec(color)
  if (fn) return [Number(fn[1]), Number(fn[2]), Number(fn[3])]
  throw new Error(`not an opaque colour: ${color}`)
}

/** WCAG 2.x relative luminance and contrast ratio. */
function luminance(color: string): number {
  const [r, g, b] = rgb(color).map((v) => {
    const c = v / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

describe('chart colours', () => {
  const css = readFileSync(CSS, 'utf8')

  test.fails('each theme block defines six series colours, distinct, and both dark blocks agree', () => {
    const series = BLOCKS.map(([, opening]) => SERIES_VARS.map((v) => block(css, opening)[v]))
    for (const colours of series) {
      expect(colours.every((c) => typeof c === 'string' && c.length > 0)).toBe(true)
      expect(new Set(colours.map((c) => rgb(c).join(','))).size).toBe(6)
    }
    expect(series[1].map(rgb)).toEqual(series[2].map(rgb))
  })

  test.fails.each(BLOCKS)('%s: every series colour has 3:1 contrast with --panel (WCAG 1.4.11)', (_name, opening) => {
    const vars = block(css, opening)
    for (const v of SERIES_VARS) {
      expect(vars[v], v).toBeDefined()
      expect(contrast(vars[v], vars['--panel']), `${v} ${vars[v]} on ${vars['--panel']}`).toBeGreaterThanOrEqual(3)
    }
  })
})
