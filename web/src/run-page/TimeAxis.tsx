// The left pane's miniature time axis: the main agent's calls in one lane, each verification in
// another, rejected and failed calls marked below. Click a mark to select it.

import { useEffect, useRef, useState } from 'react'

import { fmtClock } from '../format'
import type { RunView } from '../run-view/model'
import { isIssue, verifyId, type Section } from './outline'
import type { Span, Timeline } from './timeline'
import { toolKind } from './tools'

const PAD = 6
const H = 88

export function TimeAxis({
  view,
  axis,
  sections,
  selected,
  playhead,
  onPick,
}: {
  view: RunView
  axis: Timeline
  sections: Section[]
  selected: string | null
  /** The replay's position in the axis's unit; null when everything is shown. */
  playhead: number | null
  onPick: (id: string) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(320)
  useEffect(() => {
    const el = box.current
    if (!el) return
    const observer = new ResizeObserver(() => setWidth(Math.max(80, el.clientWidth)))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  const extent = Math.max(1, axis.extent)
  const x = (t: number) => PAD + (Math.min(Math.max(t, 0), extent) / extent) * (width - PAD * 2)
  const label = (t: number) => (axis.unit === 'ms' ? fmtClock(t) : `#${Math.round(t)}`)

  const main = view.steps.filter((s) => s.dispatch === null)
  const verifier = view.steps.filter((s) => s.dispatch !== null)
  const texts = view.narration.filter((n) => n.kind === 'text')
  const issues = view.steps.filter(isIssue)

  let mark: Span | null = null
  if (selected !== null) {
    const step = view.steps.find((s) => s.id === selected)
    const section = sections.find((s) => s.id === selected)
    if (step) mark = axis.step(step)
    else if (section) {
      const spans = section.items.flatMap((i) => (i.kind === 'step' ? [axis.step(i.step)] : []))
      if (spans.length) mark = { start: Math.min(...spans.map((s) => s.start)), end: Math.max(...spans.map((s) => s.end)) }
    }
  }

  const pickAt = (clientX: number) => {
    const rect = box.current!.getBoundingClientRect()
    const t = ((clientX - rect.left - PAD) / (width - PAD * 2)) * extent
    let best: string | null = null
    let distance = Infinity
    for (const s of view.steps) {
      const d = Math.abs(axis.step(s).start - t)
      if (d < distance) {
        distance = d
        best = s.id
      }
    }
    if (best !== null) onPick(best)
  }

  return (
    <div ref={box} className="relative select-none">
      <svg width={width} height={H} className="block cursor-crosshair" onClick={(e) => pickAt(e.clientX)} role="img" aria-label="Time axis of the run">
        <text x={PAD} y={9} fontSize="9" fill="var(--ink-3)" fontFamily="var(--font-mono)">
          MAIN AGENT
        </text>
        <text x={PAD} y={47} fontSize="9" fill="var(--ink-3)" fontFamily="var(--font-mono)">
          VERIFIER
        </text>
        <rect x={PAD} y={13} width={width - PAD * 2} height={18} fill="var(--panel-3)" rx="2" />
        <rect x={PAD} y={50} width={width - PAD * 2} height={14} fill="var(--panel-3)" rx="2" />
        {main.map((s) => {
          const span = axis.step(s)
          return <rect key={s.id} x={x(span.start)} y={18} width={Math.max(1.5, x(span.end) - x(span.start))} height={10} fill={`var(--k-${toolKind(s.tool)})`} opacity={0.85} />
        })}
        {texts.map((n) => (
          <rect key={n.seq} x={x(axis.at(n)) - 0.5} y={14} width={1.5} height={4} fill="var(--ink-2)" />
        ))}
        {view.dispatches.map((d) => {
          const span = axis.dispatch(d)
          const colour = d.overall === 'PASS' ? 'var(--ok)' : d.overall === 'FAIL' ? 'var(--fail)' : 'var(--verifier)'
          const w = Math.max(2, x(span.end) - x(span.start))
          return (
            <g
              key={d.n}
              onClick={(e) => {
                e.stopPropagation()
                onPick(verifyId(d.n))
              }}
              style={{ cursor: 'pointer' }}
            >
              <title>{`Verifier #${d.n}: ${d.overall ?? 'running'}`}</title>
              <rect x={x(span.start)} y={50} width={w} height={14} fill={colour} opacity={0.22} />
              <rect x={x(span.start)} y={50} width={w} height={2} fill={colour} />
              {w > 14 && (
                <text x={x(span.start) + w / 2} y={61} fontSize="9" textAnchor="middle" fill={colour} fontFamily="var(--font-mono)" fontWeight="600">
                  {d.n}
                </text>
              )}
            </g>
          )
        })}
        {verifier.map((s) => (
          <rect key={s.id} x={x(axis.step(s).start)} y={53} width={1} height={3} fill="var(--verifier)" opacity={0.7} />
        ))}
        {issues.map((s) => {
          const colour = s.rejectedBy === 'gateway' ? 'var(--block)' : s.rejectedBy !== null ? 'var(--deny)' : 'var(--fail)'
          const y = s.dispatch !== null ? 65 : 32
          const cx = x(axis.step(s).start)
          return (
            <path
              key={s.id}
              d={`M${cx - 3.5} ${y + 6} L${cx + 3.5} ${y + 6} L${cx} ${y} Z`}
              fill={colour}
              onClick={(e) => {
                e.stopPropagation()
                onPick(s.id)
              }}
              style={{ cursor: 'pointer' }}
            >
              <title>{s.rejectedBy !== null ? `rejected (${s.rejectedBy})` : 'error'}</title>
            </path>
          )
        })}
        {mark && (
          <rect x={x(mark.start) - 1} y={12} width={Math.max(2, x(mark.end) - x(mark.start) + 2)} height={53} fill="var(--accent)" opacity={0.18} stroke="var(--accent)" strokeWidth={1} />
        )}
        {playhead !== null && <rect x={x(playhead) - 0.75} y={10} width={1.5} height={60} fill="var(--accent)" />}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <rect x={x(f * extent)} y={73} width={1} height={3} fill="var(--line-2)" />
            <text x={x(f * extent)} y={85} fontSize="9" fill="var(--ink-3)" textAnchor={f === 0 ? 'start' : f === 1 ? 'end' : 'middle'} fontFamily="var(--font-mono)">
              {label(f * extent)}
            </text>
          </g>
        ))}
      </svg>
    </div>
  )
}
