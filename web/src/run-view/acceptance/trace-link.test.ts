// Acceptance tests for the Langfuse link (.scratch/observability/spec.md "Testing Decisions",
// 网页). Written by ticket 111; every test is `test.fails` until ticket 114 implements
// `traceLink`, which then drops `.fails` and changes nothing else. The web UI has no DOM test
// library, so the seam is this pure function; ticket 114 shows the rendered link in its own tests
// and screenshots.

import { describe, expect, test } from 'vitest'

import type { RunEvent } from '../model'
import { traceLink } from '../trace'

const BASE = 'https://langfuse.example'

let seq = 0
function event(type: string, fields: Record<string, unknown> = {}): RunEvent {
  return { seq: seq++, type, ts: '2026-11-23T00:00:00+00:00', ...fields }
}

/** One run's events, as `data-agent run` writes them; `trace` when the export was on. */
function run(trace?: { trace_id: string; url: string }): RunEvent[] {
  seq = 0
  return [
    event('version', { config: 'full', model: 'deepseek-flash' }),
    event('task', { task: 'How many customers?', task_type: 'query' }),
    event('answer', { text: '42', sql_tool_use_id: null }),
    event('result', { subtype: 'success', is_error: false, cost_usd: 0.01 }),
    ...(trace ? [event('trace', { ...trace, forwarded_spans: 9, forwarded_llm_requests: 3 })] : []),
  ]
}

const FIRST = { trace_id: '0af7651916cd43dd8448eb211c80319c', url: `${BASE}/trace/0af76519` }
const SECOND = { trace_id: '4bf92f3577b34da6a3ce929d0e0e4736', url: `${BASE}/trace/4bf92f35` }

describe('traceLink', () => {
  test.fails('a run with a trace event links to its trace', () => {
    expect(traceLink(run(FIRST))).toEqual({ traceId: FIRST.trace_id, url: FIRST.url })
  })

  test.fails('a run without a trace event has no link', () => {
    expect(traceLink(run())).toBeNull()
  })

  test.fails("each turn of a conversation links to its own turn's trace", () => {
    const turns = [run(FIRST), run(SECOND)]
    expect(turns.map((events) => traceLink(events))).toEqual([
      { traceId: FIRST.trace_id, url: FIRST.url },
      { traceId: SECOND.trace_id, url: SECOND.url },
    ])
  })
})
