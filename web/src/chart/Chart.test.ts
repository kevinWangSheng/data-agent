// The chart without a fixed size, as the pages draw it: on the server it renders its frame (root,
// title) and a box of a fixed height for ResponsiveContainer to fill, so the chart never collapses
// to zero height in the answer's column. The tooltip's number format is shared by every chart.

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test } from 'vitest'

import { AnswerCharts } from './AnswerCharts'
import { CHART_HEIGHT, Chart, ScatterTip, scatterPoints, valueText } from './Chart'
import type { ChartPayload } from './model'
import type { RunView } from '../run-view/model'

const PAYLOAD: ChartPayload = {
  chart: { query_id: 'query_1', chart_type: 'line', x: 'month', series: [{ column: 'revenue', label: 'Revenue' }], stacked: false, title: 'Monthly revenue' },
  data: [
    { month: '2024-01-01', revenue: 1520.5 },
    { month: '2024-02-01', revenue: 1710 },
  ],
}

describe('Chart without size', () => {
  test('renders its root, its title and a box of the default height', () => {
    const html = renderToStaticMarkup(createElement(Chart, { payload: PAYLOAD }))
    expect(html).toContain('data-testid="chart"')
    expect(html).toContain('data-chart-type="line"')
    expect(html).toContain('Monthly revenue')
    expect(html).toContain(`height:${CHART_HEIGHT}px`)
  })

  test('with no rows shows the note and no box', () => {
    const html = renderToStaticMarkup(createElement(Chart, { payload: { ...PAYLOAD, data: [] } }))
    expect(html).toContain('No data to chart')
    expect(html).not.toContain(`height:${CHART_HEIGHT}px`)
  })
})

describe('ScatterTip', () => {
  test("names the point's series, not the axis recharts names each entry by", () => {
    const point = scatterPoints({ ...PAYLOAD.chart, chart_type: 'scatter', x: 'items', series: [{ column: 'amount', label: 'Order amount' }] }, [{ items: 3, amount: 52 }])[0][0]
    // What recharts 3 hands a scatter tooltip: one entry per axis, each named by the axis.
    const payload = [
      { name: 'items', value: 3, payload: point },
      { name: 'items', value: 52, payload: point },
    ]
    const html = renderToStaticMarkup(createElement(ScatterTip, { active: true, payload, x: 'items' }))
    expect(html).toContain('Order amount')
    expect(html).toContain('52')
  })
})

describe('AnswerCharts', () => {
  test('a run without charts renders nothing', () => {
    const view = { steps: [] } as unknown as RunView
    expect(renderToStaticMarkup(createElement(AnswerCharts, { view }))).toBe('')
  })
})

describe('valueText', () => {
  test.each([
    [1234567.891, '1,234,567.89'],
    [0.5, '0.5'],
    [null, '—'],
    ['2024-01-01', '2024-01-01'],
  ] as const)('%s reads %s', (v, text) => {
    expect(valueText(v)).toBe(text)
  })
})
