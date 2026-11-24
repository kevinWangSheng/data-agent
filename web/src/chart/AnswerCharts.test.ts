// The answer's charts load recharts lazily: the pages (and the online site's first screen) import
// AnswerCharts, which shows each chart's frame and a box of the chart's height until ./Chart, the
// module with recharts, arrives. Here ./Chart fails to load, so whatever renders, renders without it.

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test, vi } from 'vitest'

import type { RunView } from '../run-view/model'
import { AnswerCharts } from './AnswerCharts'
import { CHART_HEIGHT } from './ChartFrame'
import type { ChartPayload } from './model'

vi.mock('./Chart', () => {
  throw new Error('AnswerCharts loaded the recharts module eagerly')
})

const payload = (title: string, data: ChartPayload['data']): ChartPayload => ({
  chart: { query_id: 'query_1', chart_type: 'bar', x: 'k', series: [{ column: 'v', label: 'V' }], stacked: false, title },
  data,
})

function viewOf(...payloads: ChartPayload[]): RunView {
  const steps = payloads.map((p, i) => ({
    id: `t-${i}`,
    seq: i,
    tool: 'mcp__data__display_chart',
    dispatch: null,
    isError: false,
    result: JSON.stringify(p),
  }))
  return { steps } as unknown as RunView
}

describe('AnswerCharts', () => {
  test('a run without charts renders nothing', () => {
    expect(renderToStaticMarkup(createElement(AnswerCharts, { view: viewOf() }))).toBe('')
  })

  test("renders each chart's frame, title and a box of the chart's height before recharts loads", () => {
    const html = renderToStaticMarkup(createElement(AnswerCharts, { view: viewOf(payload('First', [{ k: 'a', v: 1 }]), payload('Second', [{ k: 'b', v: 2 }])) }))
    expect(html.match(/data-testid="chart"/g)).toHaveLength(2)
    expect(html.indexOf('First')).toBeLessThan(html.indexOf('Second'))
    expect(html.match(new RegExp(`height:${CHART_HEIGHT}px`, 'g'))).toHaveLength(2)
  })

  test('a chart with nothing to draw shows its note at once, not a box', () => {
    const html = renderToStaticMarkup(createElement(AnswerCharts, { view: viewOf(payload('Empty', []), payload('Nulls', [{ k: 'a', v: null }])) }))
    expect(html).toContain('No data to chart')
    expect(html).toContain('Nothing to draw')
    expect(html).not.toContain(`height:${CHART_HEIGHT}px`)
  })
})
