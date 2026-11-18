// Replay timing: the run view model gives each event's time (`eventTimes`); replay turns those into
// a clock it can play at any speed, and cuts the event prefix the page shows at a clock reading.

/** Longest pause between two events while idle gaps are skipped. */
export const IDLE_CAP_MS = 1500

/** The speeds the replay controls offer. */
export const SPEEDS = [1, 10, 50, 200, 1000] as const

/** The offered speeds, with `autoSpeed` among them when the replay starts at it. */
export function speedsWith(autoSpeed: number | null): readonly number[] {
  return autoSpeed !== null && !SPEEDS.some((s) => s === autoSpeed) ? [autoSpeed, ...SPEEDS].sort((a, b) => a - b) : SPEEDS
}

/**
 * Each event's place on the replay clock, in milliseconds from the first event. An unknown time
 * (null) adds no wait; a time earlier than the one before it adds none either, so the clock never
 * runs backwards. With `skipIdle`, each wait is cut to `IDLE_CAP_MS`.
 */
export function replayClock(times: readonly (number | null)[], skipIdle: boolean): number[] {
  const clock: number[] = []
  let at = 0
  let last: number | null = null
  for (const time of times) {
    if (time !== null) {
      if (last !== null) {
        const gap = Math.max(0, time - last)
        at += skipIdle ? Math.min(gap, IDLE_CAP_MS) : gap
      }
      last = last === null ? time : Math.max(last, time)
    }
    clock.push(at)
  }
  return clock
}

/** How long the online site's first screen takes to play its run (spec: about 20–30 seconds). */
export const AUTOPLAY_MS = 25_000

/** The speed that plays a replay clock of `totalMs` in `targetMs`; 1x when the clock is empty. */
export function autoplaySpeed(totalMs: number, targetMs: number = AUTOPLAY_MS): number {
  return totalMs > 0 ? totalMs / targetMs : 1
}

/** How many events have come by clock reading `t` (at least the first one). */
export function revealedAt(clock: readonly number[], t: number): number {
  let lo = 0
  let hi = clock.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (clock[mid] <= t) lo = mid + 1
    else hi = mid
  }
  return Math.min(clock.length, Math.max(1, lo))
}

/**
 * Whether a key press plays or pauses the replay: space, without a modifier, unless it lands in an
 * input control (where it types) or on a button, link or summary (which it presses), as on the run
 * page (./RunPage.tsx).
 */
export function togglesPlay(e: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'altKey' | 'target'>): boolean {
  if (e.key !== ' ' || e.metaKey || e.ctrlKey || e.altKey) return false
  const tag = (e.target as HTMLElement | null)?.tagName ?? ''
  return !['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'SUMMARY', 'A'].includes(tag)
}
