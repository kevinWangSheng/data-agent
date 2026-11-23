// The answer's charts (.scratch/charts/spec.md "前端"): every chart `answerCharts` finds in the
// run, in call order, one under the other. The chat page's turn and the run page's answer block put
// it after the answer's text, inside their `data-testid="answer"` element.
//
// The drawing (./Chart.tsx, with recharts) loads lazily, so a page without charts never fetches
// recharts; until it arrives each chart shows its frame, title, and its note or an empty box of the
// drawing's height (./ChartFrame.tsx). On the server, the markup is that placeholder.

import { lazy, Suspense, useMemo } from 'react'

import type { RunView } from '../run-view/model'
import { ChartPlaceholder } from './ChartFrame'
import { answerCharts } from './model'

const Chart = lazy(() => import('./Chart').then((m) => ({ default: m.Chart })))

export function AnswerCharts({ view }: { view: RunView }) {
  const charts = useMemo(() => answerCharts(view), [view])
  if (charts.length === 0) return null
  return (
    <div className="flex flex-col gap-2">
      {charts.map((c) => (
        <Suspense key={c.step} fallback={<ChartPlaceholder payload={c.payload} />}>
          <Chart payload={c.payload} />
        </Suspense>
      ))}
    </div>
  )
}
