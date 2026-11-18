// Copied from SignalPilot (https://github.com/SignalPilot-Labs/SignalPilot, commit f5eee22,
// signalpilot/web/components/chat/replay-controls.tsx), Apache License 2.0; see NOTICE.
// Modified: this app's classes and colours instead of upstream's, and no "Replay" badge; the play
// button has a text label and a title; speeds are a prop, without per-speed test ids, and the root
// test id is `replay-controls`; the exit button became "Show all" (spec: replay controls have show
// all), and the position slider sits at the end while everything is shown; added the "skip idle"
// toggle; the time label is a clock (m:ss) and reads "all shown" when the whole run is shown; with
// `narrowLayout`, a narrow screen keeps only play, the clock and Show all, and the bar wraps.
import { ListChecks, Pause, Play, RotateCcw } from 'lucide-react'

import { fmtClock, fmtDur } from '../format'
import { IDLE_CAP_MS } from './replay'

/**
 * Control bar for replaying a recorded run. With "skip idle", every wait between two events is cut
 * to a short cap, so a long run plays in a fraction of its wall time.
 */
export function ReplayControls({
  elapsed,
  totalMs,
  playing,
  showingAll,
  speed,
  speeds,
  skipIdle,
  onSpeedChange,
  onTogglePlay,
  onRestart,
  onScrub,
  onSkipIdleChange,
  onShowAll,
  narrowLayout = false,
}: {
  elapsed: number
  totalMs: number
  playing: boolean
  showingAll: boolean
  speed: number
  speeds: readonly number[]
  skipIdle: boolean
  onSpeedChange: (speed: number) => void
  onTogglePlay: () => void
  onRestart: () => void
  onScrub: (ms: number) => void
  onSkipIdleChange: (skip: boolean) => void
  onShowAll: () => void
  /** Fit a narrow screen (the online site's recorded conversation); the run page hides the bar there. */
  narrowLayout?: boolean
}) {
  return (
    <div className={'flex items-center gap-2' + (narrowLayout ? ' flex-wrap' : '')} data-testid="replay-controls">
      <button
        type="button"
        aria-label={playing ? 'Pause replay' : 'Play replay'}
        onClick={onTogglePlay}
        className={'btn' + (playing ? ' on' : '')}
        title="Play or pause (space)"
      >
        {playing ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
        {playing ? 'Pause' : showingAll ? 'Replay' : 'Resume'}
      </button>
      {/* Its children lay out as the bar's own; under 640px wide with narrowLayout they are hidden,
          leaving play, the clock and Show all. (.btn and .seg set their display outside the
          utility layer, so the wrapper hides them.) */}
      <div className={narrowLayout ? 'contents max-sm:hidden' : 'contents'}>
        <button type="button" aria-label="Restart replay" onClick={onRestart} className="btn" title="Restart from the first event">
          <RotateCcw className="h-3 w-3" />
        </button>
        <div role="group" aria-label="Replay speed" className="seg">
          {speeds.map((value) => (
            <button
              key={value}
              type="button"
              aria-label={`Replay at ${value}x`}
              aria-pressed={speed === value}
              onClick={() => onSpeedChange(value)}
              className={speed === value ? 'on' : ''}
            >
              {value}×
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-[11.5px] text-ink-2 cursor-pointer whitespace-nowrap" title={`Cut every pause between events to ${fmtDur(IDLE_CAP_MS)}`}>
          <input type="checkbox" checked={skipIdle} onChange={(e) => onSkipIdleChange(e.target.checked)} className="accent-[var(--accent)]" />
          skip idle
        </label>
        <input
          type="range"
          aria-label="Replay position"
          min={0}
          max={Math.max(Math.ceil(totalMs / 100) * 100, 100)}
          step={100}
          value={Math.round(Math.min(showingAll ? totalMs : elapsed, totalMs) / 100) * 100}
          onChange={(event) => onScrub(Number(event.target.value))}
          className="w-32 accent-[var(--accent)]"
        />
      </div>
      <span className="mono text-[11px] text-ink-2 w-[88px] whitespace-nowrap tabular-nums">
        {showingAll ? 'all shown' : `${fmtClock(elapsed)} / ${fmtClock(totalMs)}`}
      </span>
      <button
        type="button"
        aria-label="Show all events"
        aria-pressed={showingAll}
        onClick={onShowAll}
        className={'btn' + (showingAll ? ' on' : '')}
        title="Stop the replay and show every event"
      >
        <ListChecks className="h-3 w-3" /> Show all
      </button>
    </div>
  )
}
