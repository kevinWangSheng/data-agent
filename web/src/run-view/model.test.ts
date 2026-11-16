// Unit tests for what the acceptance tests leave out: the injected Skills (`version.skills`,
// ticket 83) and the result-table parser's edge cases.

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
