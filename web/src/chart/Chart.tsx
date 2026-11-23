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

import type { JSX, ReactNode } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { ChartPayload, ChartRow, ChartSpec } from './model'

export type ChartProps = {
  payload: ChartPayload
  /** A fixed size in pixels; without it the chart fits its container. */
  size?: { width: number; height: number }
}

/** Without `size`: the container's full width at this height. */
export const CHART_HEIGHT = 260

export const seriesColour = (i: number) => `var(--chart-series-${(i % 6) + 1})`

const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })
const exact = new Intl.NumberFormat('en', { maximumFractionDigits: 2 })

/** An axis tick: numbers short (12K), anything else as the query result shows it. */
const tickText = (v: unknown) => (typeof v === 'number' ? compact.format(v) : String(v))
/** A value on the hover card: numbers in full with thousands separators; NULL as a dash. */
export const valueText = (v: unknown) => (typeof v === 'number' ? exact.format(v) : v === null || v === undefined ? '—' : String(v))

const AXIS = { stroke: 'var(--line-2)', tick: { fill: 'var(--ink-3)', fontSize: 11 }, tickLine: false } as const
const MARGIN = { top: 8, right: 12, bottom: 4, left: 0 }
const grid = () => <CartesianGrid key="grid" vertical={false} stroke="var(--line)" strokeWidth={1} />
// In series (or slice) order, the names in ink: the colour beside each name carries the identity.
const legendName = (name: unknown) => <span style={{ color: 'var(--ink-2)' }}>{String(name)}</span>
const legend = () => <Legend key="legend" iconSize={9} itemSorter={null} formatter={legendName} wrapperStyle={{ fontSize: 11.5 }} />

type Dims = { width?: number; height?: number }
type TipEntry = { name?: unknown; value?: unknown; color?: string; payload?: Record<string, unknown> }

/** The hover card: the row's x, then each series' value beside its colour. */
function Tip({ active, payload, label, x }: { active?: boolean; payload?: readonly TipEntry[]; label?: unknown; x: string }) {
  if (!active || !payload?.length) return null
  const heading = label ?? payload[0]?.payload?.[x]
  return (
    <div className="pane px-2.5 py-1.5 shadow-md text-[11.5px]">
      {heading !== undefined && <div className="text-ink-2 mb-0.5">{valueText(heading)}</div>}
      {payload.map((e, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <span className="inline-block w-2 h-2 rounded-[2px]" style={{ background: e.color }} />
          <span className="text-ink-2">{String(e.name ?? '')}</span>
          <span className="mono ml-auto pl-3 text-ink">{valueText(e.value)}</span>
        </div>
      ))}
    </div>
  )
}

/** A scatter point's card: its x and its series' value, both by name. */
function ScatterTip({ active, payload, x }: { active?: boolean; payload?: readonly TipEntry[]; x: string }) {
  const first = payload?.[0]
  const point = first?.payload
  if (!active || !first || !point) return null
  return (
    <div className="pane px-2.5 py-1.5 shadow-md text-[11.5px]">
      <div className="text-ink-2">
        {x} <span className="mono text-ink">{valueText(point[x])}</span>
      </div>
      <div className="text-ink-2">
        {String(first.name ?? '')} <span className="mono text-ink">{valueText(point.y)}</span>
      </div>
    </div>
  )
}

/** bar, line and area: one x axis of categories, one y axis, a series component per series. */
function cartesian(chart: ChartSpec, data: ChartRow[], dims: Dims): JSX.Element {
  const { x, series, stacked } = chart
  const common = [
    grid(),
    <XAxis key="x" dataKey={x} {...AXIS} tickFormatter={tickText} />,
    <YAxis key="y" width={48} {...AXIS} tickFormatter={tickText} />,
    <Tooltip
      key="tip"
      cursor={chart.chart_type === 'bar' ? { fill: 'var(--sel)' } : { stroke: 'var(--line-2)' }}
      content={<Tip x={x} />}
      isAnimationActive={false}
    />,
    series.length > 1 ? legend() : null,
  ]
  const stackId = stacked ? 'stack' : undefined
  if (chart.chart_type === 'bar')
    return (
      <BarChart {...dims} data={data} margin={MARGIN} accessibilityLayer>
        {common}
        {series.map((s, i) => (
          <Bar
            key={s.column}
            dataKey={s.column}
            name={s.label}
            fill={seriesColour(i)}
            stackId={stackId}
            maxBarSize={48}
            radius={!stacked || i === series.length - 1 ? [4, 4, 0, 0] : 0}
            isAnimationActive={false}
          />
        ))}
      </BarChart>
    )
  if (chart.chart_type === 'line')
    return (
      <LineChart {...dims} data={data} margin={MARGIN} accessibilityLayer>
        {common}
        {series.map((s, i) => (
          <Line
            key={s.column}
            dataKey={s.column}
            name={s.label}
            stroke={seriesColour(i)}
            strokeWidth={2}
            dot={{ r: 3, fill: 'var(--panel)', strokeWidth: 2 }}
            activeDot={{ r: 4, stroke: 'var(--panel)', strokeWidth: 2 }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    )
  return (
    <AreaChart {...dims} data={data} margin={MARGIN} accessibilityLayer>
      {common}
      {series.map((s, i) => (
        <Area
          key={s.column}
          dataKey={s.column}
          name={s.label}
          stroke={seriesColour(i)}
          strokeWidth={2}
          fill={seriesColour(i)}
          fillOpacity={0.22}
          stackId={stackId}
          activeDot={{ r: 4, stroke: 'var(--panel)', strokeWidth: 2 }}
          isAnimationActive={false}
        />
      ))}
    </AreaChart>
  )
}

/** scatter: x and every series are numbers (the server checks); one point per row with a value. */
function scatter(chart: ChartSpec, data: ChartRow[], dims: Dims): JSX.Element {
  const { x, series } = chart
  return (
    <ScatterChart {...dims} margin={MARGIN} accessibilityLayer>
      {grid()}
      <XAxis dataKey={x} type="number" name={x} domain={['auto', 'auto']} {...AXIS} tickFormatter={tickText} />
      <YAxis dataKey="y" type="number" width={48} domain={['auto', 'auto']} {...AXIS} tickFormatter={tickText} />
      <Tooltip cursor={{ stroke: 'var(--line-2)', strokeDasharray: '3 3' }} content={<ScatterTip x={x} />} isAnimationActive={false} />
      {series.length > 1 ? legend() : null}
      {series.map((s, i) => (
        <Scatter
          key={s.column}
          name={s.label}
          data={data.filter((r) => typeof r[s.column] === 'number').map((r) => ({ [x]: r[x], y: r[s.column] }))}
          fill={seriesColour(i)}
          stroke="var(--panel)"
          strokeWidth={1.5}
          isAnimationActive={false}
        />
      ))}
    </ScatterChart>
  )
}

/** pie: one slice per row, named by x, sized by the one series; slice i takes colour i. */
function pie(chart: ChartSpec, data: ChartRow[], dims: Dims): JSX.Element {
  const [s] = chart.series
  return (
    <PieChart {...dims} accessibilityLayer>
      <Tooltip content={<Tip x={chart.x} />} isAnimationActive={false} />
      {legend()}
      <Pie data={data} dataKey={s.column} nameKey={chart.x} name={s.label} outerRadius="80%" stroke="var(--panel)" strokeWidth={2} isAnimationActive={false}>
        {data.map((_, i) => (
          <Cell key={i} fill={seriesColour(i)} />
        ))}
      </Pie>
    </PieChart>
  )
}

function Frame({ chart, children }: { chart: ChartSpec; children: ReactNode }) {
  return (
    <figure data-testid="chart" data-chart-type={chart.chart_type} className="pane m-0 px-3 py-2.5 min-w-0">
      <figcaption className="text-[12.5px] font-semibold text-ink mb-1.5">{chart.title}</figcaption>
      {children}
    </figure>
  )
}

export function Chart({ payload, size }: ChartProps): JSX.Element {
  const { chart, data } = payload
  if (data.length === 0)
    return (
      <Frame chart={chart}>
        <div className="text-[12px] text-ink-3">No data to chart: the query returned no rows.</div>
      </Frame>
    )
  const draw = chart.chart_type === 'pie' ? pie : chart.chart_type === 'scatter' ? scatter : cartesian
  if (size) return <Frame chart={chart}>{draw(chart, data, size)}</Frame>
  return (
    <Frame chart={chart}>
      <div className="w-full" style={{ height: CHART_HEIGHT }} role="img" aria-label={chart.title}>
        <ResponsiveContainer width="100%" height="100%">
          {draw(chart, data, {})}
        </ResponsiveContainer>
      </div>
    </Frame>
  )
}
