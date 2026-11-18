import { describe, expect, test } from 'vitest'

import { autoplaySpeed, IDLE_CAP_MS, replayClock, revealedAt, SPEEDS, speedsWith } from './replay'

describe('speedsWith', () => {
  test('the fixed speeds, with the autoplay speed in order when it is not one of them', () => {
    expect(speedsWith(null)).toEqual(SPEEDS)
    expect(speedsWith(10)).toEqual(SPEEDS)
    expect(speedsWith(1.2)).toEqual([1, 1.2, 10, 50, 200, 1000])
  })
})

describe('autoplaySpeed', () => {
  test('plays a run in about the target time', () => {
    expect(autoplaySpeed(50_000, 25_000)).toBe(2)
    expect(autoplaySpeed(10_000, 25_000)).toBe(0.4)
  })

  test('a run with no time plays at 1x', () => {
    expect(autoplaySpeed(0, 25_000)).toBe(1)
  })

  test('the default target is 25 seconds', () => {
    expect(autoplaySpeed(250_000)).toBe(10)
  })
})

describe('replayClock', () => {
  test('follows the event times when idle gaps are kept', () => {
    expect(replayClock([0, 100, 5000, 5200], false)).toEqual([0, 100, 5000, 5200])
  })

  test('caps each gap at the idle cap when idle gaps are skipped', () => {
    expect(replayClock([0, 100, 5000, 5200], true)).toEqual([0, 100, 100 + IDLE_CAP_MS, 300 + IDLE_CAP_MS])
  })

  test('an unknown time keeps the previous event time', () => {
    expect(replayClock([0, null, 300, null], false)).toEqual([0, 0, 300, 300])
  })

  test('all times unknown: every event at zero', () => {
    expect(replayClock([null, null, null], true)).toEqual([0, 0, 0])
  })

  test('never runs backwards when a later event has an earlier time, and ends at the last time', () => {
    expect(replayClock([0, 500, 400, 900], false)).toEqual([0, 500, 500, 900])
  })
})

describe('revealedAt', () => {
  const clock = [0, 100, 100, 700]

  test('counts the events whose replay time has passed', () => {
    expect(revealedAt(clock, 0)).toBe(1)
    expect(revealedAt(clock, 99)).toBe(1)
    expect(revealedAt(clock, 100)).toBe(3)
    expect(revealedAt(clock, 699)).toBe(3)
    expect(revealedAt(clock, 700)).toBe(4)
    expect(revealedAt(clock, 10_000)).toBe(4)
  })

  test('shows at least the first event', () => {
    expect(revealedAt(clock, -5)).toBe(1)
  })

  test('an empty run reveals nothing', () => {
    expect(revealedAt([], 0)).toBe(0)
  })
})
