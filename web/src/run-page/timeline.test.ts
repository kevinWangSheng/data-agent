import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

import { buildRunView, type RunEvent } from '../run-view/model'
import { timeline } from './timeline'

const events = readFileSync(join(import.meta.dirname, '../../../tests/fixtures/web/real/dbt-cart-abandonment/events.jsonl'), 'utf8')
  .split('\n')
  .filter((l) => l.trim())
  .map((l) => JSON.parse(l) as RunEvent)

describe('timeline', () => {
  test('with times: places marks by milliseconds within the run', () => {
    const view = buildRunView(events)
    const axis = timeline(view, view.summary.durationMs)
    expect(axis.unit).toBe('ms')
    expect(axis.extent).toBe(view.summary.durationMs)
    for (const s of view.steps) {
      const span = axis.step(s)
      expect(span.start).toBe(s.startMs)
      expect(span.end).toBeGreaterThanOrEqual(span.start)
    }
    const d = view.dispatches[0]
    expect(axis.dispatch(d)).toEqual({ start: d.startMs, end: d.endMs })
  })

  test('a dispatch still running ends at the edge of the prefix', () => {
    const cut = events.findIndex((e) => e.type === 'verifier_start') + 3
    const view = buildRunView(events.slice(0, cut))
    const axis = timeline(view, 999_999_999)
    const d = view.dispatches[0]
    expect(d.endMs).toBeNull()
    expect(axis.dispatch(d)).toEqual({ start: d.startMs, end: view.summary.durationMs })
  })

  test('without times: places marks by event order', () => {
    const view = buildRunView(events.map(({ ts: _ts, ...e }) => e as RunEvent))
    const lastSeq = events.at(-1)!.seq
    const axis = timeline(view, lastSeq)
    expect(axis.unit).toBe('seq')
    expect(axis.extent).toBe(lastSeq)
    expect(axis.step(view.steps[3])).toEqual({ start: view.steps[3].seq, end: view.steps[3].seq })
    const d = view.dispatches[0]
    const span = axis.dispatch(d)
    const agent = view.steps.find((s) => s.id === d.toolUseId)!
    const last = view.steps.find((s) => s.id === d.steps.at(-1))!
    expect(span).toEqual({ start: agent.seq, end: last.seq })
    expect(axis.at(view.narration[0])).toBe(view.narration[0].seq)
  })
})
