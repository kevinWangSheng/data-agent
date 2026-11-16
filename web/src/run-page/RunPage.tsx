// The run page (spec "运行页"): three panes over one run, read only through the run view model. A
// replay feeds the model the events up to the replay clock; "show all" feeds it every event.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { RunData } from '../data/source'
import { fmtCost, fmtDur } from '../format'
import { buildRunView, eventTimes } from '../run-view/model'
import { Detail, type Selected } from './Detail'
import { Outline } from './OutlinePane'
import { ReplayControls } from './ReplayControls'
import { Side } from './Side'
import { TimeAxis } from './TimeAxis'
import { buildRows, defaultExpanded, isIssue, isSql, sectionsOf, type Item } from './outline'
import { replayClock, revealedAt } from './replay'
import type { Filter, Route } from './route'
import { timeline } from './timeline'
import { StatusChip } from './ui'

const SPEEDS = [1, 10, 50, 200, 1000] as const

type Navigate = (patch: Partial<Route>, push?: boolean) => void

const FILTER_LABELS: [Filter, string][] = [
  ['all', 'All'],
  ['sql', 'SQL'],
  ['issues', 'Rejects & errors'],
  ['verifier', 'Verifier'],
]

export function RunPage({ run, route, navigate }: { run: RunData; route: Route; navigate: Navigate }) {
  const { events, notes } = run
  const [reading, setReading] = useState<number | null>(null)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<number>(10)
  const [skipIdle, setSkipIdle] = useState(true)
  const [follow, setFollow] = useState(false)
  const [expanded, setExpanded] = useState<Set<string> | null>(null)

  const times = useMemo(() => eventTimes(events), [events])
  const clock = useMemo(() => replayClock(times, skipIdle), [times, skipIdle])
  const total = clock.at(-1) ?? 0
  const revealed = reading === null ? events.length : revealedAt(clock, reading)
  const replaying = revealed < events.length

  const full = useMemo(() => buildRunView(events, notes), [events, notes])
  const view = useMemo(() => (revealed === events.length ? full : buildRunView(events.slice(0, revealed), notes)), [events, notes, revealed, full])
  const { sections, loose } = useMemo(() => sectionsOf(view), [view])
  const fullSections = useMemo(() => sectionsOf(full).sections, [full])
  const numbers = useMemo(() => new Map(view.steps.map((s, i) => [s.id, i + 1])), [view])
  const order = useMemo<Item[]>(() => [...loose, ...sections.flatMap((s) => s.items)], [loose, sections])

  const axis = useMemo(() => timeline(view, full), [view, full])
  const playhead = replaying ? axis.now : null

  const selectedId = route.step
  const selected = useMemo<Selected>(() => {
    if (selectedId === null) return null
    const section = sections.find((s) => s.id === selectedId)
    if (section) return { kind: 'section', section }
    const item = order.find((i) => i.id === selectedId)
    if (!item) return null
    return item.kind === 'step' ? { kind: 'step', step: item.step } : { kind: 'narration', narration: item.narration }
  }, [selectedId, sections, order])

  const open = useMemo(() => {
    const set = new Set(expanded ?? defaultExpanded(full, fullSections))
    const home = sections.find((s) => s.items.some((i) => i.id === selectedId))
    if (home) set.add(home.id)
    return set
  }, [expanded, full, fullSections, sections, selectedId])
  const rows = useMemo(() => buildRows(sections, loose, route.filter, open), [sections, loose, route.filter, open])

  const select = useCallback(
    (id: string | null) => {
      setFollow(false)
      navigate({ step: id })
    },
    [navigate],
  )
  const toggle = useCallback(
    (id: string) =>
      setExpanded((current) => {
        const next = new Set(current ?? open)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      }),
    [open],
  )

  // --- Replay ---
  const lastFrame = useRef<number | null>(null)
  useEffect(() => {
    if (!playing) return
    let frame = 0
    const tick = (now: number) => {
      const dt = lastFrame.current === null ? 0 : now - lastFrame.current
      lastFrame.current = now
      // null: the end was reached (play and restart set a number first), so stay there.
      setReading((r) => {
        if (r === null) return null
        const next = r + dt * speed
        return next >= total ? null : next
      })
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
      lastFrame.current = null
    }
  }, [playing, speed, total])
  // The replay reached the end: everything is shown, so it stops.
  useEffect(() => {
    if (playing && reading === null) setPlaying(false)
  }, [playing, reading])

  // While following, the newest item is the selected one.
  const newest = order.reduce<Item | null>((a, b) => (a === null || b.seq > a.seq ? b : a), null)
  useEffect(() => {
    if (follow && replaying && newest && newest.id !== selectedId) navigate({ step: newest.id })
  }, [follow, replaying, newest, selectedId, navigate])

  const togglePlay = () => {
    if (playing) return setPlaying(false)
    if (reading === null) setReading(0)
    setFollow(true)
    setPlaying(true)
  }
  const restart = () => {
    setReading(0)
    setFollow(true)
    setPlaying(true)
  }
  const showAll = () => {
    setPlaying(false)
    setReading(null)
  }
  const changeSkipIdle = (skip: boolean) => {
    // Stay on the same event when the clock changes under the replay.
    if (reading !== null) setReading(replayClock(times, skip)[revealed - 1] ?? 0)
    setSkipIdle(skip)
  }

  // --- Keyboard ---
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const target = e.target as HTMLElement
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return
      if ((e.key === ' ' || e.key === 'Enter') && ['BUTTON', 'SUMMARY', 'A'].includes(target.tagName)) return
      const i = rows.findIndex((r) => r.id === selectedId)
      const pick = (j: number) => {
        const row = rows[Math.max(0, Math.min(rows.length - 1, j))]
        if (row) select(row.id)
      }
      const here = selected === null ? -1 : selected.kind === 'section' ? (selected.section.items[0]?.seq ?? -1) : selected.kind === 'step' ? selected.step.seq : selected.narration.seq
      switch (e.key) {
        case 'j':
        case 'ArrowDown':
          e.preventDefault()
          pick(i + 1)
          break
        case 'k':
        case 'ArrowUp':
          e.preventDefault()
          pick(i < 0 ? 0 : i - 1)
          break
        case 'Enter':
        case 'ArrowRight':
        case 'l':
          if (i >= 0 && rows[i].type === 'group' && !(rows[i] as { open: boolean }).open) {
            e.preventDefault()
            toggle(rows[i].id)
          }
          break
        case 'ArrowLeft':
        case 'h': {
          if (i < 0) break
          e.preventDefault()
          const row = rows[i]
          if (row.type === 'group' && row.open) toggle(row.id)
          else if (row.type === 'item' && row.section) select(row.section.id)
          break
        }
        case 'Escape':
          select(null)
          break
        case '1':
        case '2':
        case '3':
        case '4':
          navigate({ filter: FILTER_LABELS[Number(e.key) - 1][0] })
          break
        case 'e':
        case 'E': {
          const issues = view.steps.filter(isIssue)
          const next = e.shiftKey ? [...issues].reverse().find((s) => s.seq < here) : issues.find((s) => s.seq > here)
          if (next) select(next.id)
          break
        }
        case 'v':
        case 'V': {
          const verifies = sections.filter((s) => s.phase.kind === 'verify')
          const first = (s: (typeof verifies)[number]) => s.items[0]?.seq ?? 0
          const next = e.shiftKey ? [...verifies].reverse().find((s) => first(s) < here) : verifies.find((s) => first(s) > here)
          if (next) select(next.id)
          break
        }
        case ' ':
          e.preventDefault()
          togglePlay()
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const counts: Record<Filter, number> = {
    all: view.steps.length,
    sql: view.steps.filter(isSql).length,
    issues: view.steps.filter(isIssue).length,
    verifier: view.dispatches.length,
  }
  const title = notes.title || full.summary.task?.split('\n')[0].replace(/^#+\s*/, '') || notes.id || 'Run'

  return (
    <>
      <div className="flex items-center gap-3 px-4 min-h-11 py-1.5 border-b border-line bg-bg shrink-0 flex-wrap">
        <button type="button" className="text-[14px] font-semibold truncate max-w-[420px] text-left hover:underline min-w-[8em]" onClick={() => select(null)} title="Key moments">
          {title}
        </button>
        {replaying ? <span className="chip chip-info">REPLAY</span> : <StatusChip status={full.summary.status} />}
        <span className="mono text-[11px] text-ink-2 whitespace-nowrap">
          {fmtDur(full.summary.durationMs)}
          {full.summary.costUsd !== null && ` · ${fmtCost(full.summary.costUsd)}`}
        </span>
        <div className="flex-1" />
        <div className="max-[900px]:hidden">
          <ReplayControls
            elapsed={reading ?? total}
            totalMs={total}
            playing={playing}
            showingAll={reading === null}
            speed={speed}
            speeds={SPEEDS}
            skipIdle={skipIdle}
            onSpeedChange={setSpeed}
            onTogglePlay={togglePlay}
            onRestart={restart}
            onScrub={(ms) => {
              setReading(ms >= total ? null : ms)
              if (ms >= total) setPlaying(false)
            }}
            onSkipIdleChange={changeSkipIdle}
            onShowAll={showAll}
          />
        </div>
      </div>

      <main className="flex-1 min-h-0 grid grid-cols-[minmax(330px,26%)_minmax(0,1fr)_minmax(330px,25%)] gap-2 p-2 max-[900px]:flex max-[900px]:flex-col max-[900px]:overflow-auto">
        <section className="pane flex flex-col overflow-hidden max-[900px]:hidden" aria-label="Run outline">
          <div className="px-3 pt-2.5 pb-2 border-b border-line">
            <div className="flex items-center mb-1">
              <h2 className="eyebrow">Timeline</h2>
              <div className="flex-1" />
              <span className="mono text-[10px] text-ink-3">{axis.unit === 'ms' ? 'click to jump' : 'by event order (no times recorded)'}</span>
            </div>
            <TimeAxis view={view} axis={axis} sections={sections} selected={selectedId} playhead={playhead} onPick={select} />
          </div>
          <div className="px-2 py-2 border-b border-line">
            <div className="seg" role="group" aria-label="Filter steps">
              {FILTER_LABELS.map(([f, label], i) => (
                <button type="button" key={f} className={route.filter === f ? 'on' : ''} aria-pressed={route.filter === f} onClick={() => navigate({ filter: f })} title={`${label} (key ${i + 1})`}>
                  {label}
                  <span className="n">{counts[f]}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="px-3 h-7 flex items-center gap-3 border-b border-line">
            <h2 className="eyebrow">Outline</h2>
            <span className="mono text-[10px] text-ink-3">{rows.length} rows</span>
            <div className="flex-1" />
            {(route.filter === 'all' || route.filter === 'verifier') && (
              <>
                <button type="button" className="link" onClick={() => setExpanded(new Set(sections.map((s) => s.id)))}>
                  expand all
                </button>
                <button type="button" className="link" onClick={() => setExpanded(new Set())}>
                  collapse all
                </button>
              </>
            )}
          </div>
          <Outline rows={rows} view={view} selected={selectedId} numbers={numbers} onSelect={select} onToggle={toggle} />
          <div className="px-3 py-1.5 border-t border-line text-[10.5px] text-ink-3 flex flex-wrap gap-x-2.5 gap-y-0.5">
            <span>
              <kbd>j</kbd>
              <kbd>k</kbd> move
            </span>
            <span>
              <kbd>←</kbd>
              <kbd>→</kbd> fold
            </span>
            <span>
              <kbd>e</kbd> next reject/error
            </span>
            <span>
              <kbd>v</kbd> next verifier
            </span>
            <span>
              <kbd>1</kbd>–<kbd>4</kbd> filter
            </span>
            <span>
              <kbd>space</kbd> play
            </span>
            <span>
              <kbd>esc</kbd> key moments
            </span>
          </div>
        </section>

        <section className="pane scroll max-[900px]:hidden" aria-label="Detail">
          <Detail view={view} selectedId={selectedId} selected={selected} order={order} numbers={numbers} sections={sections} onSelect={select} />
        </section>

        <aside className="pane scroll" aria-label="Run summary">
          <Side view={view} notes={notes} replaying={replaying} numbers={numbers} onSelect={select} selected={selectedId} />
          <div className="hidden max-[900px]:block sec text-[12px] text-ink-3">The step-by-step trace needs a desktop-width screen (1280 px and up).</div>
        </aside>
      </main>
    </>
  )
}
