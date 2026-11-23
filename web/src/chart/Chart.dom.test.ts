// @vitest-environment jsdom
//
// Charts drawn in jsdom at a fixed size, for cases the acceptance tests do not cover: column names
// that could clash with the component's own keys, and one column drawn as two series.

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, test, vi } from 'vitest'

import { Chart } from './Chart'
import type { ChartPayload } from './model'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const SIZE = { width: 640, height: 320 }
const mounted: Root[] = []

afterEach(() => {
  act(() => mounted.splice(0).forEach((root) => root.unmount()))
  document.body.innerHTML = ''
  vi.restoreAllMocks()
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

describe('Chart', () => {
  test('scatter: an x column named y still spreads the points along x', async () => {
    const payload: ChartPayload = {
      chart: { query_id: 'query_1', chart_type: 'scatter', x: 'y', series: [{ column: 'v', label: 'V' }], stacked: false, title: 'x named y' },
      data: [
        { y: 1, v: 10 },
        { y: 2, v: 10 },
        { y: 3, v: 10 },
      ],
    }
    const container = await draw(payload)
    const xs = [...container.querySelectorAll('.recharts-scatter-symbol path, .recharts-scatter-symbol .recharts-symbols')].map((s) =>
      s.getAttribute('transform') ?? s.getAttribute('cx'),
    )
    expect(xs).toHaveLength(3)
    expect(new Set(xs).size).toBe(3)
  })

  test('bar: a numeric x (a year) is a category shown as the query result gives it', async () => {
    const payload: ChartPayload = {
      chart: { query_id: 'query_1', chart_type: 'bar', x: 'year', series: [{ column: 'n', label: 'Orders' }], stacked: false, title: 'By year' },
      data: [
        { year: 2021, n: 3 },
        { year: 2022, n: 5 },
        { year: 2023, n: 4 },
      ],
    }
    const container = await draw(payload)
    const ticks = [...container.querySelectorAll('.recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value')].map((t) => t.textContent)
    expect(ticks).toEqual(['2021', '2022', '2023'])
  })

  test('bar: one column drawn as two series draws both, without duplicate keys', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const payload: ChartPayload = {
      chart: {
        query_id: 'query_1',
        chart_type: 'bar',
        x: 'tier',
        series: [
          { column: 'n', label: 'Orders' },
          { column: 'n', label: 'Orders again' },
        ],
        stacked: false,
        title: 'Twice',
      },
      data: [
        { tier: 'Gold', n: 3 },
        { tier: 'Silver', n: 2 },
      ],
    }
    const container = await draw(payload)
    expect(container.querySelectorAll('.recharts-bar')).toHaveLength(2)
    expect(error.mock.calls.flat().join(' ')).not.toMatch(/same key/i)
  })
})

const yTicks = (c: HTMLElement) => [...c.querySelectorAll('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')].map((t) => t.textContent)
const xTicks = (c: HTMLElement) => [...c.querySelectorAll('.recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value')].map((t) => t.textContent)
const one = (chart_type: ChartPayload['chart']['chart_type'], x: string, column: string, data: ChartPayload['data'], stacked = false): ChartPayload => ({
  chart: { query_id: 'query_1', chart_type, x, series: [{ column, label: 'Value' }], stacked, title: `${chart_type} chart` },
  data,
})

describe('Chart axis ticks', () => {
  test('small ratios keep their digits: every tick distinct, none rounded to 0', async () => {
    const c = await draw(one('bar', 'tier', 'rate', [{ tier: 'a', rate: 0.012 }, { tier: 'b', rate: 0.03 }, { tier: 'c', rate: 0.045 }]))
    const ticks = yTicks(c)
    expect(ticks.length).toBeGreaterThan(2)
    expect(new Set(ticks).size).toBe(ticks.length)
    expect(ticks.filter((t) => t === '0')).toHaveLength(1)
  })

  test('a stacked area up to 3 shows 0.75 and 2.25 as they are', async () => {
    const payload: ChartPayload = {
      chart: { query_id: 'query_1', chart_type: 'area', x: 'm', series: [{ column: 'a', label: 'A' }, { column: 'b', label: 'B' }], stacked: true, title: 'Stacked' },
      data: [{ m: 'Jan', a: 1, b: 2 }, { m: 'Feb', a: 1.5, b: 1.5 }],
    }
    expect(yTicks(await draw(payload))).toEqual(['0', '0.75', '1.5', '2.25', '3'])
  })

  test('large values stay short without losing a nice tick (2.25K, not 2.3K)', async () => {
    const ticks = yTicks(await draw(one('bar', 'k', 'v', [{ k: 'a', v: 1000 }, { k: 'b', v: 3000 }])))
    expect(ticks).toEqual(['0', '750', '1.5K', '2.25K', '3K'])
  })

  test('scatter: both axes keep small ratios distinct', async () => {
    const data = [0.012, 0.02, 0.031, 0.045].map((r, i) => ({ x: r, y: r * 2 + i * 0.001 }))
    const c = await draw(one('scatter', 'x', 'y', data))
    for (const ticks of [xTicks(c), yTicks(c)]) {
      expect(ticks.length).toBeGreaterThan(2)
      expect(new Set(ticks).size).toBe(ticks.length)
    }
  })
})

describe('Chart with a NULL x', () => {
  test('bar: the NULL category gets its own tick, (null)', async () => {
    const c = await draw(one('bar', 'region', 'n', [{ region: 'East', n: 3 }, { region: null, n: 2 }]))
    expect(xTicks(c)).toEqual(['East', '(null)'])
    expect(c.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(2)
  })

  test('pie: the NULL slice is named (null), not by the series', async () => {
    const c = await draw(one('pie', 'region', 'n', [{ region: 'East', n: 3 }, { region: null, n: 2 }]))
    const names = [...c.querySelectorAll('.recharts-legend-item-text')].map((t) => t.textContent)
    expect(names).toEqual(['East', '(null)'])
  })

  test('scatter: a point without a numeric x is not drawn', async () => {
    const c = await draw(one('scatter', 'x', 'y', [{ x: 1, y: 2 }, { x: null, y: 3 }, { x: 2, y: 4 }]))
    expect(c.querySelectorAll('.recharts-scatter-symbol')).toHaveLength(2)
  })
})

describe('Chart with rows but nothing to draw', () => {
  const nothing = (c: HTMLElement) => c.querySelector('[data-testid="chart"]')?.textContent ?? ''

  test.each([
    ['scatter, every series value NULL', one('scatter', 'x', 'y', [{ x: 1, y: null }, { x: 2, y: null }]), 'scatter'],
    ['pie, every value 0', one('pie', 'k', 'v', [{ k: 'a', v: 0 }, { k: 'b', v: 0 }]), 'pie'],
    ['line, every value NULL', one('line', 'm', 'v', [{ m: 'Jan', v: null }, { m: 'Feb', v: null }]), 'line'],
  ] as const)('%s: a note, no series, no svg', async (_, payload, type) => {
    const c = await draw(payload)
    expect(nothing(c)).toContain('Nothing to draw')
    expect(c.querySelectorAll(`.recharts-${type}`)).toHaveLength(0)
    expect(c.querySelector('svg')).toBeNull()
  })

  test('bar of real zeros is still drawn', async () => {
    const c = await draw(one('bar', 'k', 'v', [{ k: 'a', v: 0 }, { k: 'b', v: 0 }]))
    expect(c.querySelectorAll('.recharts-bar')).toHaveLength(1)
  })
})
