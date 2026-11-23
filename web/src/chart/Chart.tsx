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
// - It draws without animation (`isAnimationActive={false}` on each series, as AblationChart.tsx
//   does): mid-animation, jsdom shows the bars at one x and the pie without its slices.
// - Series i is coloured `var(--chart-series-{i % 6 + 1})`; a pie colours its slices that way, by
//   slice number. index.css defines the six in each theme block as opaque `#hex` or
//   `rgb(r, g, b)` literals (the palette test computes their contrast from the CSS text).
// - `stacked` bars of a row stand on one another (one x); unstacked they stand side by side.
// - With no rows it shows the title and a line saying there is no data, draws no series, and does
//   not throw.

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
