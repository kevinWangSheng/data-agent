// @vitest-environment jsdom
//
// When the chart's drawing fails to load (offline, or a page left open across a deploy of the
// online site), each chart keeps its frame and says so; the page around it stays.

import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, test, vi } from 'vitest'

import type { RunView } from '../run-view/model'
import { AnswerCharts } from './AnswerCharts'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('./Chart', () => {
  throw new Error('the chart chunk did not load')
})

const payload = {
  chart: { query_id: 'query_1', chart_type: 'bar', x: 'k', series: [{ column: 'v', label: 'V' }], stacked: false, title: 'Revenue' },
  data: [{ k: 'a', v: 1 }],
}
const view = { steps: [{ id: 't-1', seq: 1, tool: 'mcp__data__display_chart', dispatch: null, isError: false, result: JSON.stringify(payload) }] } as unknown as RunView

test("a drawing that fails to load leaves the chart's frame and a note, not a blank page", async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  await act(async () => root.render(createElement('main', null, 'answer text', createElement(AnswerCharts, { view }))))
  await vi.waitFor(() => expect(container.textContent).toContain('Could not load the chart'))
  expect(container.textContent).toContain('answer text')
  expect(container.querySelector('[data-testid="chart"]')?.textContent).toContain('Revenue')
  act(() => root.unmount())
})
