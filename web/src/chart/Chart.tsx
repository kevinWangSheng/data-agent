// One answer chart (.scratch/charts/spec.md "前端"): a cartesian chart (bar, line, area), a scatter
// or a pie over a `display_chart` payload, with recharts. The props are ticket 118's contract;
// ticket 120 writes the body (the acceptance tests in ./acceptance/).
//
// The tests' contract for what it renders:
// - The root element has `data-testid="chart"` and `data-chart-type` (the payload's chart type),
//   and shows the title. It renders on the server too (react-dom/server), where recharts draws no
//   shapes: the pages' SSR tests count and order the charts by this root.
// - With `size` it draws at that fixed size (recharts draws nothing in jsdom inside a
//   ResponsiveContainer); how it fits its container without `size` is ticket 120's choice.
// - Series i is coloured `var(--chart-series-{i % 6 + 1})`; a pie colours its slices that way, by
//   slice number.
// - With no rows it shows the title and a line saying there is no data, and does not throw.

import type { JSX } from 'react'

import type { ChartPayload } from './model'

export type ChartProps = {
  payload: ChartPayload
  /** A fixed size in pixels; without it the chart fits its container. */
  size?: { width: number; height: number }
}

export function Chart(props: ChartProps): JSX.Element {
  void props
  throw new Error('Chart: not implemented (ticket 120)')
}
