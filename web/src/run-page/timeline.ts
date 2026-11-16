// Where the left pane's time axis puts each mark. With times (the view model's milliseconds after
// the run's first event) the axis is the run's clock; when any time is unknown it falls back to
// event order (`seq`), as the spec says ("时间轴按序号排").

import type { Narration, RunView, Step, VerifierDispatch } from '../run-view/model'

export type Span = { start: number; end: number }

export type Timeline = {
  unit: 'ms' | 'seq'
  /** The axis length: the whole run's duration or last `seq`, so it stays put while a replay grows. */
  extent: number
  step(step: Step): Span
  dispatch(dispatch: VerifierDispatch): Span
  at(narration: Narration): number
}

/** `extent`: the whole run's duration (ms) if every time is known, else its last `seq`. */
export function timeline(view: RunView, extent: number): Timeline {
  const timed =
    view.steps.every((s) => s.startMs !== null) &&
    view.narration.every((n) => n.atMs !== null) &&
    view.dispatches.every((d) => d.startMs !== null || d.endMs === null)
  const stepById = new Map(view.steps.map((s) => [s.id, s]))
  const now = view.summary.durationMs

  if (timed) {
    return {
      unit: 'ms',
      extent,
      step: (s) => ({ start: s.startMs!, end: s.startMs! + (s.durationMs ?? 0) }),
      dispatch: (d) => {
        const agent = d.toolUseId !== null ? stepById.get(d.toolUseId) : undefined
        const start = d.startMs ?? agent?.startMs ?? now
        return { start, end: d.endMs ?? now }
      },
      at: (n) => n.atMs!,
    }
  }
  return {
    unit: 'seq',
    extent,
    step: (s) => ({ start: s.seq, end: s.seq }),
    dispatch: (d) => {
      const agent = d.toolUseId !== null ? stepById.get(d.toolUseId) : undefined
      const seqs = d.steps.map((id) => stepById.get(id)?.seq ?? 0)
      const start = agent?.seq ?? Math.min(...seqs, extent)
      return { start, end: Math.max(start, ...seqs) }
    },
    at: (n) => n.seq,
  }
}
