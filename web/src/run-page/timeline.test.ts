import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

import { buildRunView, type RunEvent } from '../run-view/model'
import { lastSeq, timeline } from './timeline'

const events = readFileSync(join(import.meta.dirname, '../../../tests/fixtures/web/real/dbt-cart-abandonment/events.jsonl'), 'utf8')
  .split('\n')
  .filter((l) => l.trim())
  .map((l) => JSON.parse(l) as RunEvent)

describe('timeline', () => {
  test('with times: places marks by milliseconds within the run', () => {
    const view = buildRunView(events)
    const axis = timeline(view, view)
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
    const axis = timeline(view, buildRunView(events))
    expect(axis.extent).toBe(buildRunView(events).summary.durationMs)
    expect(axis.now).toBe(view.summary.durationMs)
    const d = view.dispatches[0]
    expect(d.endMs).toBeNull()
    expect(axis.dispatch(d)).toEqual({ start: d.startMs, end: view.summary.durationMs })
  })

  test('without times: places marks by event order', () => {
    const view = buildRunView(events.map(({ ts: _ts, ...e }) => e as RunEvent))
    const axis = timeline(view, view)
    expect(axis.unit).toBe('seq')
    expect(axis.extent).toBe(lastSeq(view))
    expect(timeline(buildRunView(events.slice(0, 50).map(({ ts: _ts, ...e }) => e as RunEvent)), view).now).toBeLessThan(axis.extent)
    expect(axis.step(view.steps[3])).toEqual({ start: view.steps[3].seq, end: view.steps[3].seq })
    const d = view.dispatches[0]
    const span = axis.dispatch(d)
    const agent = view.steps.find((s) => s.id === d.toolUseId)!
    const last = view.steps.find((s) => s.id === d.steps.at(-1))!
    expect(span).toEqual({ start: agent.seq, end: last.seq })
    expect(axis.at(view.narration[0])).toBe(view.narration[0].seq)
  })

  test('lastSeq: the latest seq the view model names', () => {
    const view = buildRunView(events)
    const seqs = [...view.steps.map((s) => s.seq), ...view.narration.map((n) => n.seq), ...view.keyMoments.map((m) => m.seq)]
    expect(lastSeq(view)).toBe(Math.max(...seqs))
    const prefix = buildRunView(events.slice(0, 40))
    expect(lastSeq(prefix)).toBeLessThan(lastSeq(view))
    expect(lastSeq(buildRunView([]))).toBe(0)
  })
})
