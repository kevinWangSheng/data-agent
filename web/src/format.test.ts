import { expect, test } from 'vitest'

import { fmtClock, fmtCost, fmtDur, fmtTokens } from './format'

test('fmtClock', () => {
  expect(fmtClock(0)).toBe('0:00')
  expect(fmtClock(65_400)).toBe('1:05')
  expect(fmtClock(3_008_000)).toBe('50:08')
  expect(fmtClock(3_725_000)).toBe('1:02:05')
  expect(fmtClock(-10)).toBe('0:00')
})

test('fmtDur', () => {
  expect(fmtDur(11)).toBe('11 ms')
  expect(fmtDur(8_900)).toBe('8.9 s')
  expect(fmtDur(17_000)).toBe('17 s')
  expect(fmtDur(247_000)).toBe('4m 07s')
  expect(fmtDur(3_725_000)).toBe('1h 02m')
})

test('fmtTokens and fmtCost', () => {
  expect(fmtTokens(950)).toBe('950')
  expect(fmtTokens(11_700)).toBe('11.7k')
  expect(fmtTokens(9_920_000)).toBe('9.92M')
  expect(fmtCost(0.0061)).toBe('$0.006')
  expect(fmtCost(12.5)).toBe('$12.50')
})
