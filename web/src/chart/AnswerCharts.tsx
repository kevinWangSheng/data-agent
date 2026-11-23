// The answer's charts (.scratch/charts/spec.md "前端"): every chart `answerCharts` finds in the
// run, in call order, one under the other. The chat page's turn and the run page's answer block put
// it after the answer's text, inside their `data-testid="answer"` element.

import { useMemo } from 'react'

import type { RunView } from '../run-view/model'
import { Chart } from './Chart'
import { answerCharts } from './model'

export function AnswerCharts({ view }: { view: RunView }) {
  const charts = useMemo(() => answerCharts(view), [view])
  if (charts.length === 0) return null
  return (
    <div className="flex flex-col gap-2">
      {charts.map((c) => (
        <Chart key={c.step} payload={c.payload} />
      ))}
    </div>
  )
}
