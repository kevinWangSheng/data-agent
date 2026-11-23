// @vitest-environment jsdom
//
// Acceptance tests for the answer chart component (.scratch/charts/spec.md "前端" and "Testing
// Decisions"): each of the five charts of the shared fixture drawn at a fixed size in jsdom, where
// recharts draws its shapes (react-dom/server draws none). Series and shapes are counted by
// recharts' own class names.
//
// Written by ticket 118. Every test is `test.fails` until ticket 120 builds `Chart`, which then
// drops `.fails` and changes nothing else.
//
// Fixture: the `tool_end` contents of the successful display_chart calls in
// tests/fixtures/charts/run/events.jsonl (bar stacked with 2 series and 3 rows, line and area with
// 4 rows, scatter with 5 rows, pie with 3 rows).

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, test } from 'vitest'

import { Chart } from '../Chart'
import type { ChartPayload, ChartType } from '../model'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const RUN = join(import.meta.dirname, '../../../../tests/fixtures/charts/run')
const SIZE = { width: 640, height: 320 }

type Event = { type: string; tool?: string; content?: unknown; is_error?: boolean }

const payloads: ChartPayload[] = readFileSync(join(RUN, 'events.jsonl'), 'utf8')
  .split('\n')
  .filter((line) => line.trim())
  .map((line) => JSON.parse(line) as Event)
  .filter((e) => e.type === 'tool_end' && e.tool === 'mcp__data__display_chart' && !e.is_error)
  .map((e) => JSON.parse(e.content as string) as ChartPayload)

function payloadOf(type: ChartType): ChartPayload {
  const found = payloads.find((p) => p.chart.chart_type === type)
  if (!found) throw new Error(`no ${type} chart in the fixture`)
  return found
}

const CASES = (['bar', 'line', 'area', 'scatter', 'pie'] as const).map((type) => [type, payloadOf(type)] as const)

const mounted: { unmount(): void }[] = []

afterEach(() => {
  act(() => mounted.splice(0).forEach((root) => root.unmount()))
  document.body.innerHTML = ''
})

async function draw(payload: ChartPayload): Promise<HTMLElement> {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  mounted.push(root)
  await act(async () => {
    root.render(createElement(Chart, { payload, size: SIZE }))
  })
  return container
}

function rootOf(container: HTMLElement): HTMLElement {
  const roots = container.querySelectorAll<HTMLElement>('[data-testid="chart"]')
  expect(roots).toHaveLength(1)
  return roots[0]
}

/** The `fill` and `stroke` values on `el` and everything under it. */
function paints(el: Element): string[] {
  return [el, ...el.querySelectorAll('*')].flatMap((e) => [e.getAttribute('fill'), e.getAttribute('stroke')]).filter((v): v is string => v !== null)
}

/** The series colours (`var(--chart-series-N)`) among those, deduplicated. */
function seriesColours(el: Element): string[] {
  return [...new Set(paints(el).filter((v) => /^var\(--chart-series-\d+\)$/.test(v)))]
}

const colour = (i: number) => `var(--chart-series-${(i % 6) + 1})`

describe('Chart', () => {
  test.fails.each(CASES)('%s draws without throwing, under its root with type and title', async (type, payload) => {
    const root = rootOf(await draw(payload))
    expect(root.getAttribute('data-chart-type')).toBe(type)
    expect(root.textContent).toContain(payload.chart.title)
    expect(root.querySelector('svg')).not.toBeNull()
  })

  test.fails.each(CASES)('%s has one series layer per series', async (type, payload) => {
    const root = rootOf(await draw(payload))
    expect(root.querySelectorAll(`.recharts-${type}`)).toHaveLength(payload.chart.series.length)
  })

  test.fails('bar: one rectangle per row and series', async () => {
    const payload = payloadOf('bar')
    const root = rootOf(await draw(payload))
    expect(root.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(payload.data.length * payload.chart.series.length)
  })

  test.fails('scatter: one symbol per row and series', async () => {
    const payload = payloadOf('scatter')
    const root = rootOf(await draw(payload))
    expect(root.querySelectorAll('.recharts-scatter-symbol')).toHaveLength(payload.data.length * payload.chart.series.length)
  })

  test.fails('pie: one slice per row', async () => {
    const payload = payloadOf('pie')
    const root = rootOf(await draw(payload))
    expect(root.querySelectorAll('.recharts-pie-sector')).toHaveLength(payload.data.length)
  })

  test.fails.each([
    ['line', '.recharts-line-curve'],
    ['area', '.recharts-area-curve'],
  ] as const)('%s: each series draws a curve', async (type, curve) => {
    const payload = payloadOf(type)
    const root = rootOf(await draw(payload))
    const paths = [...root.querySelectorAll(curve)]
    expect(paths).toHaveLength(payload.chart.series.length)
    for (const path of paths) expect(path.getAttribute('d') ?? '').not.toBe('')
  })

  test.fails.each(CASES.filter(([type]) => type !== 'pie'))('%s: series i is coloured var(--chart-series-i+1)', async (type, payload) => {
    const root = rootOf(await draw(payload))
    const layers = [...root.querySelectorAll(`.recharts-${type}`)]
    expect(layers).toHaveLength(payload.chart.series.length)
    layers.forEach((layer, i) => expect(seriesColours(layer)).toEqual([colour(i)]))
  })

  test.fails('the seventh series takes the first colour again', async () => {
    const series = Array.from({ length: 7 }, (_, i) => ({ column: `s${i}`, label: `S${i}` }))
    const data = ['a', 'b'].map((x, r) => Object.fromEntries([['x', x], ...series.map((s, i) => [s.column, r + i + 1])]))
    const payload: ChartPayload = { chart: { query_id: 'query_1', chart_type: 'bar', x: 'x', series, stacked: false, title: 'Seven' }, data }
    const layers = [...rootOf(await draw(payload)).querySelectorAll('.recharts-bar')]
    expect(layers.map((layer) => seriesColours(layer))).toEqual(series.map((_, i) => [colour(i)]))
  })

  test.fails('pie: slice i is coloured var(--chart-series-i+1)', async () => {
    const payload = payloadOf('pie')
    const sectors = [...rootOf(await draw(payload)).querySelectorAll('.recharts-pie-sector')]
    expect(sectors.map((sector) => seriesColours(sector))).toEqual(payload.data.map((_, i) => [colour(i)]))
  })

  test.fails.each(CASES)('%s with no rows shows its title and a note, and does not throw', async (type, payload) => {
    const empty: ChartPayload = { ...payload, data: [] }
    const root = rootOf(await draw(empty))
    expect(root.getAttribute('data-chart-type')).toBe(type)
    const text = root.textContent ?? ''
    expect(text).toContain(payload.chart.title)
    expect(text.replace(payload.chart.title, '').trim()).not.toBe('')
  })
})
