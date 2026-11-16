// Unit tests for what the acceptance tests leave out: the injected Skills (`version.skills`,
// ticket 83), the result-table parser's edge cases, task fields per field, durations.

import { describe, expect, test } from 'vitest'

import { buildRunView, type RunEvent } from './model'

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

  test('a multi-line value continues the cell above it', () => {
    const step = buildRunView(queryCall('{"result": "a | b\\n-----\\n1 | x\\ny\\n(1 rows)"}')).steps[0]
    expect(step.table).toEqual({ columns: ['a', 'b'], rows: [['1', 'x\ny']] })
  })

  test('a blank line in a one-column table is a row with an empty value', () => {
    const step = buildRunView(queryCall('{"result": "value\\n-----\\n\\nnext\\n(2 rows)"}')).steps[0]
    expect(step.table).toEqual({ columns: ['value'], rows: [[''], ['next']] })
  })

  test('empty values in the middle and at the end of a one-column table', () => {
    const step = buildRunView(queryCall('{"result": "v\\n-\\na\\n\\nb\\n   \\n(4 rows)"}')).steps[0]
    expect(step.table).toEqual({ columns: ['v'], rows: [['a'], [''], ['b'], ['   ']] })
  })

  test('a blank line in a multi-column table continues the last cell', () => {
    const step = buildRunView(queryCall('{"result": "a | b\\n-----\\n1 | x\\n\\ny\\n(1 rows)"}')).steps[0]
    expect(step.table).toEqual({ columns: ['a', 'b'], rows: [['1', 'x\n\ny']] })
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
