// What the answer charts draw, decided without recharts (so the pages can decide it before the
// recharts chunk arrives, ./AnswerCharts.tsx): the numbers' text, the label of a NULL x, a
// scatter's points, and whether a chart has anything to draw at all.

import { chartKind, type ChartPayload, type ChartRow, type ChartSpec } from './model'

/** A NULL x: its category's tick, its slice's name, its hover card's heading. */
export const NULL_LABEL = '(null)'

// Up to 12 significant digits: enough for every tick recharts picks (0.0375, 2.25K), short of the
// float noise of its arithmetic (0.30000000000000004).
const clean = (v: number) => Number(v.toPrecision(12))
const plain = new Intl.NumberFormat('en', { maximumFractionDigits: 20 })
const short = new Intl.NumberFormat('en', { notation: 'compact', maximumSignificantDigits: 12 })

/** A value axis tick, as exact as the tick: 0.0375, 2.25, 750; from 1000 on short (2.25K, 1.5M). */
export const tickText = (v: unknown) => (typeof v !== 'number' ? String(v) : Math.abs(v) >= 1000 ? short.format(clean(v)) : plain.format(clean(v)))

const exact = new Intl.NumberFormat('en', { maximumFractionDigits: 2 })
/** A value on the hover card: numbers in full with thousands separators; NULL as a dash. */
export const valueText = (v: unknown) => (typeof v === 'number' ? exact.format(v) : v === null || v === undefined ? '—' : String(v))

/** The rows with a NULL x named NULL_LABEL, for the charts whose x is a category (bar, line, area,
 *  pie): recharts would drop the category, or name the slice by the series. */
export function withNullX(chart: ChartSpec, data: ChartRow[]): ChartRow[] {
  const { x } = chart
  return data.some((r) => r[x] == null) ? data.map((r) => (r[x] == null ? { ...r, [x]: NULL_LABEL } : r)) : data
}

// A scatter point's own keys, so no column name (even `y`) can clash with them. Its series' label
// rides along: recharts names a scatter tooltip's entries by axis, not by series.
export const POINT_X = '__x'
export const POINT_Y = '__y'
export const POINT_SERIES = '__series'

/** Each series' points: one per row whose x and series value are both numbers. */
export function scatterPoints(chart: ChartSpec, data: ChartRow[]): Record<string, unknown>[][] {
  const { x } = chart
  return chart.series.map((s) =>
    data.filter((r) => typeof r[x] === 'number' && typeof r[s.column] === 'number').map((r) => ({ [POINT_X]: r[x], [POINT_Y]: r[s.column], [POINT_SERIES]: s.label })),
  )
}

/** The line the chart shows instead of a drawing when it has nothing to draw, or null when it
 *  has: no rows; or rows without a value to plot (every value NULL; a scatter without a point with
 *  a numeric x; a pie without a slice above 0). A bar chart of zeros is drawn: zero is a value. */
export function nothingNote({ chart, data }: ChartPayload): string | null {
  if (data.length === 0) return 'No data to chart: the query returned no rows.'
  const kind = chartKind(chart.chart_type)
  if (kind === 'scatter')
    return scatterPoints(chart, data).some((points) => points.length > 0) ? null : 'Nothing to draw: no row has both a numeric x and a value.'
  if (kind === 'pie') {
    const [s] = chart.series
    return data.some((r) => typeof r[s.column] === 'number' && (r[s.column] as number) > 0) ? null : 'Nothing to draw: no slice has a value above 0.'
  }
  return data.some((r) => chart.series.some((s) => typeof r[s.column] === 'number')) ? null : 'Nothing to draw: every value is NULL.'
}
