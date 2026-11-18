// The replay's state over a recorded sequence of event times (./replay.ts): the clock reading, play
// and pause, speed, skip idle, and the props of the replay controls (./ReplayControls.tsx). The run
// page replays one run's events; the online site's recorded conversation (../site/
// RecordedConversation.tsx) the turns' events one after the other.

import { useEffect, useMemo, useRef, useState, type ComponentProps } from 'react'

import { autoplaySpeed, replayClock, revealedAt, speedsWith } from './replay'
import type { ReplayControls } from './ReplayControls'

export type Replay = {
  /** How many events are shown: all of them unless replaying. */
  revealed: number
  replaying: boolean
  togglePlay: () => void
  /** The replay controls' props. */
  controls: ComponentProps<typeof ReplayControls>
}

/**
 * `autoplay`: the replay starts at once, skipping idle gaps, at the speed that plays it in about
 * `targetMs` (`autoplaySpeed`'s default when omitted), and stops with everything shown; otherwise
 * everything is shown at first. `onPlay` is told whenever playing starts (play or restart).
 */
export function useReplay(times: readonly (number | null)[], autoplay: boolean, { targetMs, onPlay }: { targetMs?: number; onPlay?: () => void } = {}): Replay {
  // The clock reading; null while everything is shown.
  const [reading, setReading] = useState<number | null>(autoplay ? 0 : null)
  const [playing, setPlaying] = useState(autoplay)
  // Two significant digits, so the speed reads as a short label (0.58×).
  const [autoSpeed] = useState(() => Number(autoplaySpeed(replayClock(times, true).at(-1) ?? 0, targetMs).toPrecision(2)))
  const [speed, setSpeed] = useState<number>(autoplay ? autoSpeed : 10)
  const [skipIdle, setSkipIdle] = useState(true)
  const speeds = useMemo(() => speedsWith(autoplay ? autoSpeed : null), [autoplay, autoSpeed])

  const clock = useMemo(() => replayClock(times, skipIdle), [times, skipIdle])
  const total = clock.at(-1) ?? 0
  const revealed = reading === null ? times.length : revealedAt(clock, reading)

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

  const togglePlay = () => {
    if (playing) return setPlaying(false)
    if (reading === null) setReading(0)
    onPlay?.()
    setPlaying(true)
  }
  return {
    revealed,
    replaying: revealed < times.length,
    togglePlay,
    controls: {
      elapsed: reading ?? total,
      totalMs: total,
      playing,
      showingAll: reading === null,
      speed,
      speeds,
      skipIdle,
      onSpeedChange: setSpeed,
      onTogglePlay: togglePlay,
      onRestart: () => {
        setReading(0)
        onPlay?.()
        setPlaying(true)
      },
      onScrub: (ms) => {
        setReading(ms >= total ? null : ms)
        if (ms >= total) setPlaying(false)
      },
      onSkipIdleChange: (skip) => {
        // Stay on the same event when the clock changes under the replay.
        if (reading !== null) setReading(replayClock(times, skip)[revealed - 1] ?? 0)
        setSkipIdle(skip)
      },
      onShowAll: () => {
        setPlaying(false)
        setReading(null)
      },
    },
  }
}
