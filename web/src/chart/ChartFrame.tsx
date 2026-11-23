// An answer chart's frame, without recharts: the root the tests count (`data-testid="chart"`,
// `data-chart-type`) and the title, around the drawing (./Chart.tsx), the note that stands for it
// (./plot.ts `nothingNote`), or the box the drawing will fill once the recharts chunk has arrived
// (./AnswerCharts.tsx).

import type { JSX, ReactNode } from 'react'

import type { ChartPayload, ChartSpec } from './model'
import { nothingNote } from './plot'

/** Without `size`: the container's full width at this height. */
export const CHART_HEIGHT = 260

export function Frame({ chart, children }: { chart: ChartSpec; children: ReactNode }): JSX.Element {
  return (
    <figure data-testid="chart" data-chart-type={chart.chart_type} className="pane m-0 px-3 py-2.5 min-w-0">
      <figcaption className="text-[12.5px] font-semibold text-ink mb-1.5">{chart.title}</figcaption>
      {children}
    </figure>
  )
}

export function Note({ text }: { text: string }): JSX.Element {
  return <div className="text-[12px] text-ink-3">{text}</div>
}

/** The chart until its drawing loads: the frame, and its note or an empty box of the drawing's
 *  size, so nothing moves when the drawing arrives. */
export function ChartPlaceholder({ payload, size }: { payload: ChartPayload; size?: { width: number; height: number } }): JSX.Element {
  const note = nothingNote(payload)
  return (
    <Frame chart={payload.chart}>
      {note !== null ? <Note text={note} /> : <div className="w-full" style={size ?? { height: CHART_HEIGHT }} />}
    </Frame>
  )
}
