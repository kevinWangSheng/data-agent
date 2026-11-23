// Answer charts (.scratch/charts/spec.md "前端"): the charts a run's answer shows, read from the
// `display_chart` calls of its event file. Types and the function's signature are ticket 118's
// contract; ticket 120 writes the body (the acceptance tests in ./acceptance/).
//
// A successful `display_chart` returns a dict (ticket 118, contract 5), so its `tool_end.content`
// is that dict's JSON, `{"chart": ChartSpec, "data": ChartRow[]}`, not the `{"result": "..."}`
// envelope of the tools that return text; `Step.result` holds it as it is.

import type { RunView } from '../run-view/model'

export type ChartType = 'bar' | 'line' | 'area' | 'scatter' | 'pie'

/** One series: a numeric column of the query's result, and its name in the legend (the column's
 *  own name when the agent gave none: the server fills it in). */
export type ChartSeries = { column: string; label: string }

/** The arguments of the call, as the server checked them and filled in the defaults. */
export type ChartSpec = {
  /** The query result it draws, e.g. `query_2`. */
  query_id: string
  chart_type: ChartType
  /** The x axis column; a pie's slice names. */
  x: string
  /** One or more; a pie has exactly one. */
  series: ChartSeries[]
  /** Only ever true on `bar` and `area`. */
  stacked: boolean
  title: string
}

/** One row of the query's result, keyed by `x` and the series' columns only. Numbers are numbers;
 *  another value is its text as the query result shows it (a date is `2024-01-01`); NULL is null. */
export type ChartRow = Record<string, number | string | null>

/** What a successful `display_chart` returns. */
export type ChartPayload = { chart: ChartSpec; data: ChartRow[] }

export type AnswerChart = {
  /** The call's `tool_use_id` (its step's id). */
  step: string
  /** `seq` of the call's `tool_start` event, as `Step.seq`. */
  seq: number
  payload: ChartPayload
  /** Which of the component's three code paths draws it: `bar`, `line` and `area` are cartesian. */
  kind: 'cartesian' | 'scatter' | 'pie'
  /** Number of series. */
  series: number
  /** Number of rows of `payload.data` (not rows times series). */
  rows: number
}

/** The charts the answer shows: the main agent's successful `display_chart` calls, in `tool_start`
 *  order. A call that failed, is still open, or whose result is not a chart payload is left out
 *  (the timeline still shows it). A verifier's call is not the main agent's. */
export function answerCharts(view: RunView): AnswerChart[] {
  const charts: AnswerChart[] = []
  for (const step of view.steps) {
    if (step.tool !== DISPLAY_CHART || step.dispatch !== null || step.isError || step.result === null) continue
    const payload = chartPayload(step.result)
    if (!payload) continue
    const type = payload.chart.chart_type
    charts.push({
      step: step.id,
      seq: step.seq,
      payload,
      kind: chartKind(type),
      series: payload.chart.series.length,
      rows: payload.data.length,
    })
  }
  return charts.sort((a, b) => a.seq - b.seq)
}

const DISPLAY_CHART = 'mcp__data__display_chart'

const CHART_TYPES = ['bar', 'line', 'area', 'scatter', 'pie'] as const satisfies readonly ChartType[]

/** Which of the component's three code paths draws a chart type. */
export const chartKind = (type: ChartType): AnswerChart['kind'] => (type === 'scatter' || type === 'pie' ? type : 'cartesian')

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/** The payload a `display_chart` result holds, or null when the text is not one: not JSON, or not
 *  the shape the component draws (a known chart type, an x column, series with columns, rows). */
export function chartPayload(text: string): ChartPayload | null {
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch {
    return null
  }
  if (!isObject(value) || !isObject(value.chart) || !Array.isArray(value.data)) return null
  const { chart, data } = value
  if (typeof chart.chart_type !== 'string' || !(CHART_TYPES as readonly string[]).includes(chart.chart_type)) return null
  if (typeof chart.x !== 'string' || typeof chart.title !== 'string') return null
  if (!Array.isArray(chart.series) || chart.series.length === 0) return null
  if (!chart.series.every((s) => isObject(s) && typeof s.column === 'string' && typeof s.label === 'string')) return null
  if (!data.every(isObject)) return null
  return value as ChartPayload
}
