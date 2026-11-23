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
