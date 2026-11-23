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
  void view
  throw new Error('answerCharts: not implemented (ticket 120)')
}
