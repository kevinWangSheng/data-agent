// Unit tests for what the acceptance tests leave out: the injected Skills (`version.skills`,
// ticket 83), the result-table parser's edge cases, task fields per field, durations, and the
// narration and times (ticket 86).

import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { gunzipSync } from 'node:zlib'
import { describe, expect, test } from 'vitest'

import { buildRunView, eventTimes, type RunEvent } from './model'

function version(fields: Record<string, unknown>): RunEvent {
  return { seq: 0, type: 'version', ts: '2026-11-15T09:00:00+00:00', config: 'full', ...fields }
}

describe('skills', () => {
  test('the names version.skills lists, in order', () => {
    const view = buildRunView([version({ skills: ['dbt-workflow', 'duckdb-sql'] })])
    expect(view.skills).toEqual(['dbt-workflow', 'duckdb-sql'])
  })

  test('an empty list: the Skill layer was off', () => {
    expect(buildRunView([version({ skills: [], layers: { skill: false } })]).skills).toEqual([])
  })

  test('an old file without the field: not recorded, nothing inferred', () => {
    const events: RunEvent[] = [
      version({ layers: { skill: true } }),
      {
        seq: 1,
        type: 'tool_start',
        ts: '2026-11-15T09:00:01+00:00',
        tool: 'Bash',
        tool_use_id: 't1',
        input: { command: 'python skills/dbt-workflow/scan.py' },
        parent: null,
      },
    ]
    expect(buildRunView(events).skills).toBeNull()
    expect(buildRunView([]).skills).toBeNull()
  })
})

function queryCall(content: unknown): RunEvent[] {
  const ts = '2026-11-15T09:00:00+00:00'
  return [
    { seq: 0, type: 'tool_start', ts, tool: 'mcp__data__query_database', tool_use_id: 'q', input: {} },
    { seq: 1, type: 'tool_end', ts, tool: 'mcp__data__query_database', tool_use_id: 'q', content, is_error: false },
  ]
}

describe('result tables', () => {
  test('a one-column table keeps a cell with the separator in it', () => {
    const step = buildRunView(queryCall('{"result": "s\\n-\\na | b\\n(1 rows)"}')).steps[0]
    expect(step.table).toEqual({ columns: ['s'], rows: [['a | b']] })
  })

  test('a multi-line value in a multi-column table is no table: rows and lines cannot be told apart', () => {
    // format_rows of rows ['first', 'value'] and ['x\ny', 'z']: the cells split as ['first', 'value\nx'], ['y', 'z'].
    const step = buildRunView(queryCall('{"result": "a | b\\n-----\\nfirst | value\\nx\\ny | z\\n(2 rows)"}')).steps[0]
    expect(step.table).toBeNull()
    expect(step.result).toBe('a | b\n-----\nfirst | value\nx\ny | z\n(2 rows)')
  })

  test('a multi-line value in the last column of a multi-column table is no table', () => {
    const step = buildRunView(queryCall('{"result": "a | b\\n-----\\n1 | x\\ny\\n(1 rows)"}')).steps[0]
    expect(step.table).toBeNull()
  })

  test('a blank line in a one-column table is a row with an empty value', () => {
    const step = buildRunView(queryCall('{"result": "value\\n-----\\n\\nnext\\n(2 rows)"}')).steps[0]
    expect(step.table).toEqual({ columns: ['value'], rows: [[''], ['next']] })
  })

  test('empty values in the middle and at the end of a one-column table', () => {
    const step = buildRunView(queryCall('{"result": "v\\n-\\na\\n\\nb\\n   \\n(4 rows)"}')).steps[0]
    expect(step.table).toEqual({ columns: ['v'], rows: [['a'], [''], ['b'], ['   ']] })
  })

  test('a blank line in a multi-column table is no table', () => {
    const step = buildRunView(queryCall('{"result": "a | b\\n-----\\n1 | x\\n\\ny\\n(1 rows)"}')).steps[0]
    expect(step.table).toBeNull()
  })

  test('a table with no (n rows) line is no table', () => {
    const step = buildRunView(queryCall('{"result": "v\\n-\\na\\n\\nb"}')).steps[0]
    expect(step.table).toBeNull()
    expect(step.result).toBe('v\n-\na\n\nb')
  })

  test('a value that reads like the row count line is data', () => {
    const step = buildRunView(queryCall('{"result": "v\\n-\\n(9 rows)\\nb\\n(2 rows)"}')).steps[0]
    expect(step.table).toEqual({ columns: ['v'], rows: [['(9 rows)'], ['b']] })
  })

  test('a value that reads like the count of the rows above it is data', () => {
    const step = buildRunView(queryCall('{"result": "v\\n-\\na\\n(1 rows)\\n(2 rows)"}')).steps[0]
    expect(step.table).toEqual({ columns: ['v'], rows: [['a'], ['(1 rows)']] })
  })

  test('a row count that does not match the rows is no table', () => {
    const step = buildRunView(queryCall('{"result": "v\\n-\\na\\n(3 rows)"}')).steps[0]
    expect(step.table).toBeNull()
  })

  test('a display cut short ends at its row count line before the note', () => {
    const text = 'v\\n-\\na\\nb\\n(5 rows)\\n[INCOMPLETE DISPLAY] 5 rows total; only the first 2 are shown above.'
    const step = buildRunView(queryCall(`{"result": "${text}"}`)).steps[0]
    expect(step.table).toEqual({ columns: ['v'], rows: [['a'], ['b']] })
  })

  test('a display cut short whose rows do not match the shown count is no table', () => {
    // A two-line value in a one-column table reads as two rows; the note says one row is shown.
    const text = 'v\\n-\\na\\nb\\n(5 rows)\\n[INCOMPLETE DISPLAY] 5 rows total; only the first 1 are shown above.'
    const step = buildRunView(queryCall(`{"result": "${text}"}`)).steps[0]
    expect(step.table).toBeNull()
  })

  test('a display cut short whose note gives no shown count is no table', () => {
    const text = 'v\\n-\\na\\nb\\n(5 rows)\\n[INCOMPLETE DISPLAY] 5 rows total.'
    const step = buildRunView(queryCall(`{"result": "${text}"}`)).steps[0]
    expect(step.table).toBeNull()
  })

  test('text with no dash rule is no table', () => {
    const step = buildRunView(queryCall('{"result": "0 duplicate keys"}')).steps[0]
    expect(step.table).toBeNull()
    expect(step.result).toBe('0 duplicate keys')
  })

  test('text-block content is joined', () => {
    const step = buildRunView(queryCall([{ type: 'text', text: 'n\n-\n3\n(1 rows)' }])).steps[0]
    expect(step.result).toBe('n\n-\n3\n(1 rows)')
    expect(step.table).toEqual({ columns: ['n'], rows: [['3']] })
  })
})

describe('task fields and durations', () => {
  const ts = '2026-11-15T09:00:00+00:00'

  test('a field the task event lacks comes from the notes', () => {
    const events: RunEvent[] = [
      version({}),
      { seq: 1, type: 'task', ts, task: 'Build it.', task_type: 'other' },
    ]
    const view = buildRunView(events, { task_type: 'dbt', data_source: 'shop' })
    const { summary } = view
    expect([summary.task, summary.taskType, summary.dataSource]).toEqual(['Build it.', 'dbt', 'shop'])
  })

  test('a call without tool_timing has no duration', () => {
    const later = '2026-11-15T09:00:05+00:00'
    const events: RunEvent[] = [
      { seq: 0, type: 'tool_start', ts, tool: 'Bash', tool_use_id: 'b', input: { command: 'ls' } },
      { seq: 1, type: 'tool_end', ts: later, tool: 'Bash', tool_use_id: 'b', content: 'x', is_error: false },
    ]
    expect(buildRunView(events).steps[0].durationMs).toBeNull()
  })
})

describe('label queries', () => {
  const ts = '2026-11-15T09:00:00+00:00'
  const call = (seq: number, tool: string, id: string, content: string): RunEvent[] => [
    { seq, type: 'tool_start', ts, tool: `mcp__data__${tool}`, tool_use_id: id, input: {} },
    { seq: seq + 1, type: 'tool_end', ts, tool: `mcp__data__${tool}`, tool_use_id: id, content, is_error: false },
  ]
  // classify_text runs SQL on the data; query_labels runs SQL on its label tables (mcp_server.py).
  const events: RunEvent[] = [
    { seq: 0, type: 'task', ts, task: 'Which reviews complain?', task_type: 'query' },
    ...call(1, 'list_tables', 'l', 'reviews'),
    ...call(3, 'classify_text', 'c', 'stored as labels_1'),
    ...call(5, 'query_labels', 'q', 'label | n\n-----\nshipping | 4\n(1 rows)'),
    { seq: 7, type: 'text', ts, text: 'Four complain about shipping.' },
    { seq: 8, type: 'result', ts, subtype: 'success', is_error: false },
  ]

  test('classify_text ends learning the environment and query_labels backs the answer', () => {
    const view = buildRunView(events)
    expect(view.phases.map((p) => [p.kind, p.steps])).toEqual([
      ['orient', ['l']],
      ['explore', ['c']],
      ['answer', ['q']],
    ])
    expect(view.answer?.sqlStep).toBe('q')
    expect(view.answer?.table).toEqual({ columns: ['label', 'n'], rows: [['shipping', '4']] })
  })
})

// --- Narration and times (ticket 86), on the real fixtures ----------------------------------

const FIXTURES = join(import.meta.dirname, '../../../tests/fixtures/web')

function loadEvents(dir: string): RunEvent[] {
  const path = join(FIXTURES, dir, 'events.jsonl')
  // Files over pre-commit's size limit are stored gzipped.
  const text = existsSync(path)
    ? readFileSync(path, 'utf8')
    : gunzipSync(readFileSync(path + '.gz')).toString('utf8')
  return text
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as RunEvent)
}

const FIXTURE_DIRS = ['real/query-shop', 'real/dbt-cart-abandonment', 'real/dbt-division-by-zero', 'new-fields']
const fixtures = FIXTURE_DIRS.map((dir) => [dir, loadEvents(dir)] as const)
const queryShop = loadEvents('real/query-shop')
const cart = loadEvents('real/dbt-cart-abandonment')

describe('event times', () => {
  test.each(fixtures)('%s: one time per event, from the first event, never decreasing', (_dir, events) => {
    const times = eventTimes(events)
    expect(times).toHaveLength(events.length)
    expect(times[0]).toBe(0)
    expect(times.every((t) => t !== null)).toBe(true)
    const known = times as number[]
    expect(known.every((t, i) => i === 0 || t >= known[i - 1])).toBe(true)
  })

  test('milliseconds after the first event', () => {
    // version 14:31:53.303, thinking 14:31:55.917, tool_start 14:31:55.976
    expect(eventTimes(queryShop).slice(0, 5)).toEqual([0, 36, 1845, 2614, 2673])
  })

  test('a missing or unreadable ts is null; the others still count from the first event', () => {
    const events: RunEvent[] = [
      { seq: 0, type: 'version', ts: '2026-11-15T09:00:00+00:00' },
      { seq: 1, type: 'status', ts: 'not a time' },
      { seq: 2, type: 'thinking' } as unknown as RunEvent,
      { seq: 3, type: 'text', ts: '2026-11-15T09:00:02.500+00:00' },
    ]
    expect(eventTimes(events)).toEqual([0, null, null, 2500])
  })

  test('no time at all when the first event has none', () => {
    const events: RunEvent[] = [
      { seq: 0, type: 'version', ts: '' },
      { seq: 1, type: 'text', ts: '2026-11-15T09:00:02+00:00' },
    ]
    expect(eventTimes(events)).toEqual([null, null])
    expect(eventTimes([])).toEqual([])
  })
})

describe('narration', () => {
  const counts = (events: RunEvent[]) => ({
    thinking: events.filter((e) => e.type === 'thinking').length,
    text: events.filter((e) => e.type === 'text').length,
  })

  test('the counts in the fixtures', () => {
    // Counted with jq; no thinking or text event has a parent (agent.py record_message), so all are
    // the main agent's.
    expect(fixtures.map(([, events]) => counts(events))).toEqual([
      { thinking: 7, text: 1 },
      { thinking: 40, text: 12 },
      { thinking: 85, text: 30 },
      { thinking: 0, text: 2 },
    ])
  })

  test.each(fixtures)('%s: every thinking and text event, in seq order, with its time', (_dir, events) => {
    const { narration } = buildRunView(events)
    const times = eventTimes(events)
    const expected = events.flatMap((e, i) =>
      e.type === 'thinking' || e.type === 'text' ? [{ kind: e.type, seq: e.seq, atMs: times[i], text: e.text }] : [],
    )
    expect(narration).toEqual(expected)
    expect({
      thinking: narration.filter((n) => n.kind === 'thinking').length,
      text: narration.filter((n) => n.kind === 'text').length,
    }).toEqual(counts(events))
  })

  test('the first entry of a run', () => {
    expect(buildRunView(queryShop).narration[0]).toMatchObject({ kind: 'thinking', seq: 3, atMs: 2614 })
  })

  test('the answer text is an entry; the answer event is not', () => {
    const events = loadEvents('new-fields')
    const view = buildRunView(events)
    const answerSeq = events.find((e) => e.type === 'answer')?.seq
    expect(view.narration.map((n) => n.seq)).toEqual([41, 43])
    expect(view.narration.some((n) => n.seq === answerSeq)).toBe(false)
    expect(view.narration[0]).toMatchObject({ kind: 'text', text: view.answer?.text })
  })

  test('a prefix holds the entries so far', () => {
    expect(buildRunView(cart.filter((e) => e.seq <= 10)).narration.map((n) => [n.kind, n.seq])).toEqual([
      ['thinking', 3],
      ['text', 4],
      ['thinking', 10],
    ])
  })
})

describe('step start times', () => {
  test.each(fixtures)('%s: each step starts at its tool_start, never decreasing', (_dir, events) => {
    const times = eventTimes(events)
    const starts = events.flatMap((e, i) => (e.type === 'tool_start' ? [times[i]] : []))
    const view = buildRunView(events)
    expect(view.steps.map((s) => s.startMs)).toEqual(starts)
    const known = view.steps.map((s) => s.startMs as number)
    expect(known.every((t, i) => t !== null && (i === 0 || t >= known[i - 1]))).toBe(true)
  })

  test('the first call of a run', () => {
    expect(buildRunView(queryShop).steps[0].startMs).toBe(2673)
  })

  test('a tool_start without ts has no start time', () => {
    const events: RunEvent[] = [
      version({}),
      { seq: 1, type: 'tool_start', tool: 'Bash', tool_use_id: 'b', input: {} } as unknown as RunEvent,
    ]
    expect(buildRunView(events).steps[0].startMs).toBeNull()
  })
})

describe('dispatch times', () => {
  test.each(fixtures)('%s: each dispatch runs from its verifier_start to its verifier_end', (_dir, events) => {
    const times = eventTimes(events)
    const at = (type: string) => events.flatMap((e, i) => (e.type === type ? [times[i]] : []))
    const view = buildRunView(events)
    expect(view.dispatches.map((d) => d.startMs)).toEqual(at('verifier_start'))
    expect(view.dispatches.map((d) => d.endMs)).toEqual(at('verifier_end'))
  })

  test('the two dispatches of the cart run', () => {
    // verifier_start / verifier_end at 19:13:49.709 / 19:17:33.263 and 19:18:23.025 / 19:21:40.846;
    // the run began at 19:08:42.318.
    expect(buildRunView(cart).dispatches.map((d) => [d.startMs, d.endMs])).toEqual([
      [307391, 530945],
      [580707, 778528],
    ])
  })

  test('a dispatch still running has no end; before its verifier starts, no start either', () => {
    // seq 307 verifier_dispatch, 308 verifier_start, 366 verifier_end.
    expect(buildRunView(cart.filter((e) => e.seq <= 307)).dispatches[0]).toMatchObject({ startMs: null, endMs: null })
    expect(buildRunView(cart.filter((e) => e.seq <= 320)).dispatches[0]).toMatchObject({ startMs: 307391, endMs: null })
  })
})

describe('unknown times', () => {
  test('a thinking, text, verifier_start or verifier_end event without a readable ts has no time', () => {
    const ts = '2026-11-15T09:00:00+00:00'
    const events: RunEvent[] = [
      version({}),
      { seq: 1, type: 'thinking', ts: 'later', text: 'Look first.' },
      { seq: 2, type: 'text', text: 'Checking.' } as unknown as RunEvent,
      { seq: 3, type: 'tool_start', ts, tool: 'Agent', tool_use_id: 'a', input: {}, parent: null },
      { seq: 4, type: 'verifier_dispatch', ts, n: 1, models: ['m'], tool_use_id: 'a' },
      { seq: 5, type: 'verifier_start', ts: '', agent_id: 'v' },
      { seq: 6, type: 'verifier_end', agent_id: 'v', overall: 'PASS', checks: {} } as unknown as RunEvent,
    ]
    const view = buildRunView(events)
    expect(view.narration.map((n) => n.atMs)).toEqual([null, null])
    expect(view.dispatches[0]).toMatchObject({ overall: 'PASS', startMs: null, endMs: null })
    expect(view.summary.durationMs).toBe(0)
  })
})

describe('status from the local backend', () => {
  const started: RunEvent[] = [version({}), { seq: 1, type: 'status', ts: '2026-11-15T09:00:01+00:00', state: 'started' }]
  const result: RunEvent = { seq: 2, type: 'result', ts: '2026-11-15T09:00:02+00:00', subtype: 'success', is_error: false }

  test('a run whose process exited without a result event failed', () => {
    expect(buildRunView(started, { failed: true }).summary.status).toBe('failure')
  })

  test('the result event wins over the exit', () => {
    expect(buildRunView([...started, result], { failed: true }).summary.status).toBe('success')
  })

  test('cancelled wins over the exit', () => {
    expect(buildRunView(started, { cancelled: true, failed: true }).summary.status).toBe('cancelled')
  })
})

// --- The query the answer rests on (ticket 87) ----------------------------------------------

describe('the query the answer rests on', () => {
  const ts = '2026-11-15T09:00:00+00:00'
  const call = (seq: number, tool: string, id: string, content: string, isError = false): RunEvent[] => [
    { seq, type: 'tool_start', ts, tool: `mcp__data__${tool}`, tool_use_id: id, input: {} },
    { seq: seq + 1, type: 'tool_end', ts, tool: `mcp__data__${tool}`, tool_use_id: id, content, is_error: isError },
  ]
  const answer = (seq: number, sqlToolUseId: string | null): RunEvent => ({
    seq,
    type: 'answer',
    ts,
    text: 'Gold leads.',
    sql_tool_use_id: sqlToolUseId,
  })
  const task: RunEvent = { seq: 0, type: 'task', ts, task: 'Which tier leads?', task_type: 'query' }
  const tierTable = { columns: ['tier', 'n'], rows: [['Gold', '3']] }
  // q0 and q1 succeed, q2 fails, then the answer; q3 runs after it.
  const run = (marked: RunEvent): RunEvent[] => [
    task,
    ...call(1, 'list_tables', 'l', 'orders'),
    ...call(3, 'query_database', 'q0', 'n\n-\n7\n(1 rows)'),
    ...call(5, 'query_database', 'q1', 'tier | n\n-----\nGold | 3\n(1 rows)'),
    ...call(7, 'query_database', 'q2', 'syntax error', true),
    marked,
    ...call(10, 'query_database', 'q3', 'n\n-\n9\n(1 rows)'),
  ]

  test('the query the answer event names: used, not inferred', () => {
    const view = buildRunView(run(answer(9, 'q0')))
    expect(view.answer).toMatchObject({ sqlStep: 'q0', table: { columns: ['n'], rows: [['7']] } })
    expect(view.answer?.sqlInferred).toBeUndefined()
  })

  test('no query named: the last successful query before the answer, inferred', () => {
    const view = buildRunView(run(answer(9, null)))
    expect(view.answer).toMatchObject({ seq: 9, text: 'Gold leads.', sqlStep: 'q1', table: tierTable, sqlInferred: true })
    expect(view.keyMoments.find((m) => m.kind === 'answer')?.step).toBe('q1')
    // The answer phase starts at the same query.
    expect(view.phases.map((p) => [p.kind, p.steps])).toEqual([
      ['orient', ['l']],
      ['explore', ['q0']],
      ['answer', ['q1', 'q2', 'q3']],
    ])
  })

  test('no query named and no successful query before the answer: no query, no table', () => {
    const events: RunEvent[] = [
      task,
      ...call(1, 'query_database', 'q2', 'syntax error', true),
      answer(3, null),
      ...call(4, 'query_database', 'q3', 'n\n-\n9\n(1 rows)'),
    ]
    const view = buildRunView(events)
    expect(view.answer).toMatchObject({ sqlStep: null, table: null })
    expect(view.answer?.sqlInferred).toBeUndefined()
  })

  test('old file without an answer event: the last text, its query inferred as before', () => {
    const events: RunEvent[] = [
      ...run({ seq: 9, type: 'text', ts, text: 'Gold leads.' }),
      { seq: 12, type: 'result', ts, subtype: 'success', is_error: false },
    ]
    expect(buildRunView(events).answer).toMatchObject({ seq: 9, sqlStep: 'q1', table: tierTable, sqlInferred: true })
  })
})

// --- Agent tools (ticket 101): sandbox_exec, dbt_execute, result_id note, guard classification --

describe('agent tools (ticket 101)', () => {
  const ts = '2026-11-21T10:00:00+00:00'

  test('query_database result with trailing [result id: ...] note still parses as a table', () => {
    const text =
      'tier | revenue\\n--------------\\nGold | 710.00\\n(1 rows)\\n[result id: query_1; list it in sandbox_exec\'s data_files to read all 1 rows as data/query_1.csv]'
    const step = buildRunView(queryCall(`{"result": "${text}"}`)).steps[0]
    expect(step.table).toEqual({ columns: ['tier', 'revenue'], rows: [['Gold', '710.00']] })
  })

  test('new tool surface: Bash without rejected_by is not classified as guard, file tools still are', () => {
    const events: RunEvent[] = [
      version({}),
      { seq: 1, type: 'tool_start', ts, tool: 'mcp__data__sandbox_exec', tool_use_id: 's1', input: { command: 'echo ok' }, parent: null },
      { seq: 2, type: 'tool_end', ts, tool: 'mcp__data__sandbox_exec', tool_use_id: 's1', content: 'exit_code: 0\nstdout:\nok', is_error: false, parent: null },
      { seq: 3, type: 'tool_start', ts, tool: 'Read', tool_use_id: 'r1', input: { file_path: '.env' }, parent: null },
      { seq: 4, type: 'tool_end', ts, tool: 'Read', tool_use_id: 'r1', content: 'PreToolUse:Read hook error: credential file', is_error: true, parent: null },
      { seq: 5, type: 'tool_start', ts, tool: 'Bash', tool_use_id: 'b1', input: { command: 'ls' }, parent: null },
      { seq: 6, type: 'tool_end', ts, tool: 'Bash', tool_use_id: 'b1', content: 'PreToolUse:Bash hook error: disallowed', is_error: true, parent: null },
    ]
    const view = buildRunView(events)
    expect(view.steps.find((s) => s.id === 'r1')?.rejectedBy).toBe('guard')
    expect(view.steps.find((s) => s.id === 'b1')?.rejectedBy).toBe('unknown')
  })

  test('dbt_execute splits build and self-check phases on the agent-tools fixture', () => {
    const events = loadEvents('agent-tools')
    const view = buildRunView(events)
    expect(view.phases.map((p) => p.kind)).toEqual(['explore', 'build', 'self-check'])
    expect(view.phases.find((p) => p.kind === 'build')?.steps).toEqual(['t-w1', 't-dbt1'])
    expect(view.answer?.table).toEqual({
      columns: ['tier', 'revenue'],
      rows: [
        ['Gold', '710.00'],
        ['Silver', '420.50'],
        ['Bronze', '190.00'],
      ],
    })
  })
})
