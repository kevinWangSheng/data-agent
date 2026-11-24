// The payload guard behind answerCharts: a `display_chart` result becomes a chart only when it has
// the shape the component draws, so a malformed result never reaches recharts.

import { describe, expect, test } from 'vitest'

import { chartPayload } from './model'

const GOOD = {
  chart: { query_id: 'query_1', chart_type: 'bar', x: 'tier', series: [{ column: 'n', label: 'Orders' }], stacked: false, title: 'Orders by tier' },
  data: [
    { tier: 'Gold', n: 3 },
    { tier: 'Silver', n: null },
  ],
}

const withChart = (patch: Record<string, unknown>) => JSON.stringify({ ...GOOD, chart: { ...GOOD.chart, ...patch } })

describe('chartPayload', () => {
  test('a well-formed payload comes back as it is', () => {
    expect(chartPayload(JSON.stringify(GOOD))).toEqual(GOOD)
  })

  test('an empty data array is still a chart (the component shows its note)', () => {
    expect(chartPayload(JSON.stringify({ ...GOOD, data: [] }))?.data).toEqual([])
  })

  test.each([
    ['not JSON', 'Error executing tool display_chart: no such query'],
    ['the text envelope', JSON.stringify({ result: 'done' })],
    ['an array', JSON.stringify([GOOD])],
    ['no data', JSON.stringify({ chart: GOOD.chart })],
    ['data not an array', JSON.stringify({ ...GOOD, data: {} })],
    ['a row that is not an object', JSON.stringify({ ...GOOD, data: [1, 2] })],
    ['an unknown chart type', withChart({ chart_type: 'donut' })],
    ['no x', withChart({ x: undefined })],
    ['no title', withChart({ title: 3 })],
    ['no series', withChart({ series: undefined })],
    ['an empty series list', withChart({ series: [] })],
    ['a series without a column', withChart({ series: [{ label: 'Orders' }] })],
  ])('%s is not a chart', (_name, text) => {
    expect(chartPayload(text)).toBeNull()
  })
})
