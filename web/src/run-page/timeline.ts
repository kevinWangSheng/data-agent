// Where the left pane's time axis puts each mark. With times (the view model's milliseconds after
// the run's first event) the axis is the run's clock; when any time is unknown it falls back to
// event order (`seq`), as the spec says ("时间轴按序号排").

import type { Narration, RunView, Step, VerifierDispatch } from '../run-view/model'

export type Span = { start: number; end: number }

export type Timeline = {
  unit: 'ms' | 'seq'
  /** The axis length: the whole run's duration or last `seq`, so it stays put while a replay grows. */
  extent: number
  /** Where the given view ends: the replay's position on this axis. */
  now: number
  step(step: Step): Span
  dispatch(dispatch: VerifierDispatch): Span
  at(narration: Narration): number
}

/** The latest `seq` the view model names (a call, a thinking or text, a key moment); 0 for none. */
export function lastSeq(view: RunView): number {
  return Math.max(0, ...view.steps.map((s) => s.seq), ...view.narration.map((n) => n.seq), ...view.keyMoments.map((m) => m.seq))
}

/** The axis for `view` (the events shown so far), sized to `whole` (the view of every event). */
export function timeline(view: RunView, whole: RunView): Timeline {
  const timed =
    view.steps.every((s) => s.startMs !== null) &&
    view.narration.every((n) => n.atMs !== null) &&
    view.dispatches.every((d) => d.startMs !== null || d.endMs === null)
  const stepById = new Map(view.steps.map((s) => [s.id, s]))
  const now = view.summary.durationMs

  if (timed) {
    return {
      unit: 'ms',
      extent: whole.summary.durationMs,
      now,
      step: (s) => ({ start: s.startMs!, end: s.startMs! + (s.durationMs ?? 0) }),
      dispatch: (d) => {
        const agent = d.toolUseId !== null ? stepById.get(d.toolUseId) : undefined
        const start = d.startMs ?? agent?.startMs ?? now
        return { start, end: d.endMs ?? now }
      },
      at: (n) => n.atMs!,
    }
  }
  const extent = lastSeq(whole)
  return {
    unit: 'seq',
    extent,
    now: lastSeq(view),
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
