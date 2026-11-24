// Acceptance tests for how the run page shows external MCP tools (.scratch/mcp/spec.md "Testing
// Decisions": 网页; "网页", Q9). Written by ticket 115. The tests marked `test.fails` are for ticket
// 117, which makes them pass by dropping `.fails` and changes nothing else; the plain `test`s hold
// today and must keep holding (`mcp__data__*` tools are shown and parsed as before).
//
// The contract fixed here (ticket 115 Context item 7; human-reviewed):
//
// 7. `mcp__<server>__<tool>` from a server other than `data` is shown as `<server> · <tool>` (a
//    U+00B7 middle dot with a space on each side) and is of the new kind `'external'`; the kind
//    has a tag and a name of its own (`KIND_TAG`, `KIND_NAME`), different from `'other'`'s. Their
//    wording is ticket 117's (ticket 115 "spec 里的解读" 5): the spec says "外部工具", the page's
//    labels are English. An external tool's result is text: never parsed into a table, even when it
//    looks like one; only the data server's query tools are (`run-view/model.ts`).
//
// `'external'` is not yet a `ToolKind`, so the kind is compared with `toBe('external')` and the
// tag and name maps are read through `Record<string, string>`: the file type-checks without
// `tools.ts` changing.

import { describe, expect, test } from 'vitest'

import { buildRunView, type RunEvent } from '../../run-view/model'
import { KIND_NAME, KIND_TAG, shortTool, toolKind } from '../tools'

const EXTERNAL: [string, string][] = [
  ['mcp__mongo__find', 'mongo · find'],
  ['mcp__github__get_issue', 'github · get_issue'],
  ['mcp__mongo__list-collections', 'mongo · list-collections'],
]

const tags = KIND_TAG as Record<string, string>
const kindNames = KIND_NAME as Record<string, string>

describe('external MCP tools (ticket 117)', () => {
  for (const [tool, shown] of EXTERNAL) {
    test(`${tool} is shown as "${shown}"`, () => {
      expect(shortTool(tool)).toBe(shown)
    })

    test(`${tool} is an external tool`, () => {
      expect(toolKind(tool)).toBe('external')
    })
  }

  test('the external kind has a tag and a name of its own', () => {
    expect(tags.external).toBeTruthy()
    expect(kindNames.external).toBeTruthy()
    expect(tags.external).not.toBe(tags.other)
    expect(kindNames.external).not.toBe(kindNames.other)
  })
})

describe('the data server tools are unchanged', () => {
  test('mcp__data__query_database keeps its short name and kind', () => {
    expect(shortTool('mcp__data__query_database')).toBe('query_database')
    expect(toolKind('mcp__data__query_database')).toBe('sql')
  })

  // The same table-shaped result for both: only the data server's query tool is parsed.
  const TABLE_TEXT = 'customer_id | tickets\n---------------------\nC0004 | 5\nC0001 | 3\n(2 rows)'

  function call(seq: number, id: string, tool: string, input: Record<string, unknown>): RunEvent[] {
    const ts = (n: number) => `2026-11-23T10:00:${String(n).padStart(2, '0')}.000000+00:00`
    return [
      { seq, type: 'tool_start', ts: ts(seq), tool, tool_use_id: id, parent: null, input },
      {
        seq: seq + 1,
        type: 'tool_end',
        ts: ts(seq + 1),
        tool,
        tool_use_id: id,
        parent: null,
        is_error: false,
        content: JSON.stringify({ result: TABLE_TEXT }),
      },
    ]
  }

  const events: RunEvent[] = [
    { seq: 0, type: 'version', ts: '2026-11-23T10:00:00.000000+00:00', config: 'full', model: 'm' },
    { seq: 1, type: 'task', ts: '2026-11-23T10:00:00.500000+00:00', task: 'Who opened most tickets?', task_type: 'query' },
    ...call(2, 'call-mongo', 'mcp__mongo__find', { collection: 'tickets' }),
    ...call(4, 'call-sql', 'mcp__data__query_database', { sql: 'SELECT 1' }),
  ]

  test('an external tool result stays text; a data query result is still a table', () => {
    const view = buildRunView(events)
    const mongo = view.steps.find((step) => step.id === 'call-mongo')
    const sql = view.steps.find((step) => step.id === 'call-sql')
    expect(mongo?.result).toBe(TABLE_TEXT)
    expect(mongo?.table).toBeNull()
    expect(sql?.table).toEqual({ columns: ['customer_id', 'tickets'], rows: [['C0004', '5'], ['C0001', '3']] })
  })
})
