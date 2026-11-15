// Acceptance tests for the run view model (seam 1 of .scratch/web-ui/spec.md, "Testing
// Decisions"): real event files in, the run page's structure out. Written by ticket 73; every test
// is `test.fails` until ticket 75 implements `buildRunView`, which then drops `.fails` and changes
// nothing else.
//
// Fixtures (tests/fixtures/web/): three real runs, scrubbed, written before the new event fields
// existed (they test the fallbacks for old files), and one hand-made run with every new field.
//   query-shop            query run, 12 calls, no verifier, notes without a grade
//   dbt-cart-abandonment  dbt run, 122 calls, verifier FAIL -> fix -> PASS, graded pass
//   dbt-division-by-zero  dbt run, 270 calls, 10 dispatches, graded fail (gzipped: >1000 KB)
//   new-fields            dbt run, 2 dispatches, `task` / `answer` / `check_names` /
//                         `rejected_by` / `verifier_dispatch.tool_use_id`

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { gunzipSync } from 'node:zlib'
import { describe, expect, test } from 'vitest'

import {
  buildRunView,
  type PhaseKind,
  type RunEvent,
  type RunNotes,
  type RunView,
} from '../model'

const FIXTURES = join(import.meta.dirname, '../../../../tests/fixtures/web')

function parseLines(text: string): RunEvent[] {
  return text
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as RunEvent)
}

function loadEvents(dir: string): RunEvent[] {
  const path = join(FIXTURES, dir, 'events.jsonl')
  try {
    return parseLines(readFileSync(path, 'utf8'))
  } catch {
    return parseLines(gunzipSync(readFileSync(path + '.gz')).toString('utf8'))
  }
}

function loadNotes(dir: string): RunNotes {
  return JSON.parse(readFileSync(join(FIXTURES, dir, 'meta.json'), 'utf8')) as RunNotes
}

const QUERY = 'real/query-shop'
const CART = 'real/dbt-cart-abandonment'
const DIV = 'real/dbt-division-by-zero'
const NEW = 'new-fields'

const query = loadEvents(QUERY)
const cart = loadEvents(CART)
const div = loadEvents(DIV)
const fresh = loadEvents(NEW)

/** Events up to and including `seq`: what a live run or a replay has shown so far. */
function upTo(events: RunEvent[], seq: number): RunEvent[] {
  return events.filter((e) => e.seq <= seq)
}

function stepOf(view: RunView, id: string) {
  const step = view.steps.find((s) => s.id === id)
  if (!step) throw new Error(`no step ${id}`)
  return step
}

/** The raw tool calls of a fixture, to compare the pairing against. */
function toolStarts(events: RunEvent[]): RunEvent[] {
  return events.filter((e) => e.type === 'tool_start')
}

function agentCalls(events: RunEvent[]): string[] {
  return toolStarts(events)
    .filter((e) => e.tool === 'Agent')
    .map((e) => e.tool_use_id as string)
}

// Agent tool calls that carried the verifier dispatches, in dispatch order.
const CART_AGENT = ['call_00_Kurpm7cpaBVPKpU2PwwL7879', 'call_00_18Zjttaz6UytkLOVRZ5u6277']

// --- Step pairing --------------------------------------------------------------------------

describe('steps', () => {
  test.fails.each([
    [QUERY, query, 12],
    [CART, cart, 122],
    [DIV, div, 270],
  ])('%s pairs every tool call into one step', (_name, events, count) => {
    const view = buildRunView(events)
    const starts = toolStarts(events)
    expect(view.steps).toHaveLength(count)
    expect(view.steps.map((s) => s.id)).toEqual(starts.map((e) => e.tool_use_id))
    expect(view.steps.map((s) => s.tool)).toEqual(starts.map((e) => e.tool))
    // A finished run has no open call.
    expect(view.steps.every((s) => s.result !== null)).toBe(true)
  })

  test.fails('a step carries input, result, duration and its SQL', () => {
    const view = buildRunView(query)
    const first = stepOf(view, 'call_00_hz9clQ9EZC12cT3SAqYS1422')
    expect(first.tool).toBe('mcp__data__list_tables')
    expect(first.durationMs).toBe(49)
    expect(first.isError).toBe(false)
    expect(first.sql).toBeNull()

    const answerQuery = stepOf(view, 'call_01_UEDIFvctiVADBylZHXCK1428')
    expect(answerQuery.sql).toMatch(/^SELECT c\.customer_id, c\.customer_name, c\.tier/)
    expect((answerQuery.input as { sql: string }).sql).toMatch(/^SELECT c\.customer_id/)
    expect(answerQuery.result).toContain('C0001 | Avery Lane | Gold | 6 | 285.25')
  })

  test.fails('a text table in a query result becomes columns and rows', () => {
    const table = stepOf(buildRunView(query), 'call_01_UEDIFvctiVADBylZHXCK1428').table
    expect(table?.columns).toEqual(['customer_id', 'customer_name', 'tier', 'n', 'amt'])
    expect(table?.rows).toHaveLength(12)
    expect(table?.rows[0]).toEqual(['C0001', 'Avery Lane', 'Gold', '6', '285.25'])
    expect(table?.rows[11]).toEqual(['C0012', 'Logan Pierce', 'Bronze', '1', '100.75'])
  })

  test.fails('a result that is no table keeps its text', () => {
    const step = stepOf(buildRunView(cart), 'call_01_6f46r1APvazYrpfCoix59101')
    expect(step.isError).toBe(true)
    expect(step.table).toBeNull()
    expect(step.result).toContain('Binder Error: Ambiguous reference to column name "session_id"')
  })
})

// --- Verifier dispatches -------------------------------------------------------------------

describe('verifier dispatches', () => {
  test.fails('a query run has none', () => {
    expect(buildRunView(query).dispatches).toEqual([])
  })

  test.fails('each dispatch has its number, models and overall verdict', () => {
    const cartView = buildRunView(cart)
    expect(cartView.dispatches.map((d) => [d.n, d.overall])).toEqual([
      [1, 'FAIL'],
      [2, 'PASS'],
    ])
    expect(cartView.dispatches[0].models).toEqual([
      'int_ecommerce__cart_recovery_metrics',
      'mart_ecommerce__recovery_scorecard',
      'stg_ecommerce__abandoned_carts',
    ])
    expect(cartView.dispatches[1].models).toEqual(['mart_ecommerce__recovery_scorecard'])
    expect(cartView.dispatches[0].report).toMatch(/^## Verification Report/)

    const divView = buildRunView(div)
    expect(divView.dispatches.map((d) => d.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    expect(divView.dispatches.map((d) => d.overall)).toEqual([
      ...Array<string>(9).fill('FAIL'),
      'PASS',
    ])
  })

  test.fails('each dispatch lists every verdict per model and check', () => {
    const [first, second] = buildRunView(cart).dispatches
    const verdicts = (d: typeof first) => d.checks.map((c) => [c.model, c.check, c.verdict])
    expect(verdicts(second)).toEqual([
      ['mart_ecommerce__recovery_scorecard', '1', 'PASS'],
      ['mart_ecommerce__recovery_scorecard', '2', 'PASS'],
      ['mart_ecommerce__recovery_scorecard', '3', 'PASS'],
      ['mart_ecommerce__recovery_scorecard', '4', 'PASS'],
      ['mart_ecommerce__recovery_scorecard', '5', 'PASS'],
      ['mart_ecommerce__recovery_scorecard', '6', 'N/A'],
      ['mart_ecommerce__recovery_scorecard', '7', 'PASS'],
    ])
    expect(first.checks).toHaveLength(21)
    expect(first.checks.filter((c) => c.verdict === 'FAIL')).toEqual([
      expect.objectContaining({ model: 'mart_ecommerce__recovery_scorecard', check: '4' }),
    ])
  })

  test.fails('the verifier steps belong to their dispatch, the rest to the main agent', () => {
    const view = buildRunView(div)
    expect(view.dispatches.map((d) => d.steps.length)).toEqual([11, 19, 12, 8, 25, 19, 11, 6, 12, 24])
    const parentOf = new Map(toolStarts(div).map((e) => [e.tool_use_id as string, e.parent]))
    const agents = agentCalls(div)
    for (const d of view.dispatches) {
      for (const id of d.steps) {
        expect(parentOf.get(id)).toBe(agents[d.n - 1])
        expect(stepOf(view, id).dispatch).toBe(d.n)
      }
    }
    // The Agent calls are the main agent's own.
    const main = view.steps.filter((s) => s.dispatch === null)
    expect(main).toHaveLength(123)
    expect(main.map((s) => s.id)).toEqual(expect.arrayContaining(agents))
  })
})

// --- Phases --------------------------------------------------------------------------------

const BEFORE_VERIFY: PhaseKind[] = ['orient', 'explore', 'build', 'self-check']
const QUERY_PHASES: PhaseKind[] = ['orient', 'explore', 'answer']

/** True when `kinds` is a non-empty run of `order`, in that order, each kind at most once. */
function inOrder(kinds: PhaseKind[], order: PhaseKind[]): boolean {
  const at = kinds.map((k) => order.indexOf(k))
  return at.length > 0 && at.every((i, j) => i >= 0 && (j === 0 || i > at[j - 1]))
}

function phaseOf(view: RunView, stepId: string) {
  const phase = view.phases.find((p) => p.steps.includes(stepId))
  if (!phase) throw new Error(`step ${stepId} is in no phase`)
  return phase
}

describe('phases', () => {
  test.fails.each([
    [QUERY, query],
    [CART, cart],
    [DIV, div],
  ])('%s: the phases cover every step once, in order', (_name, events) => {
    const view = buildRunView(events)
    expect(view.phases.flatMap((p) => p.steps)).toEqual(view.steps.map((s) => s.id))
    expect(view.phases.every((p) => p.steps.length > 0)).toBe(true)
  })

  test.fails('a query run: orient, explore, answer', () => {
    const view = buildRunView(query)
    expect(inOrder(view.phases.map((p) => p.kind), QUERY_PHASES)).toBe(true)
    expect(view.phases.every((p) => p.dispatch === null)).toBe(true)
    // list_tables learns the environment; the first query explores the data.
    expect(phaseOf(view, 'call_00_hz9clQ9EZC12cT3SAqYS1422').kind).toBe('orient')
    expect(phaseOf(view, 'call_00_o1viFmXIfpFKcTLGe44C2067').kind).toBe('explore')
  })

  test.fails.each([
    [CART, cart, 2],
    [DIV, div, 10],
  ])('%s: by tool kind before the first verification, then verify and fix', (_n, events, n) => {
    const view = buildRunView(events)
    const firstVerify = view.phases.findIndex((p) => p.kind === 'verify')
    const before = view.phases.slice(0, firstVerify)
    expect(inOrder(before.map((p) => p.kind), BEFORE_VERIFY)).toBe(true)
    // Both runs look around, query the data and write models before they verify; self-check is
    // optional. The run opens by learning its environment (Bash: `echo $DB_TYPE`, `ls`).
    expect(before.map((p) => p.kind)).toEqual(expect.arrayContaining(['orient', 'explore', 'build']))
    expect(phaseOf(view, view.steps[0].id).kind).toBe('orient')

    // Both runs end on their last verification: no main-agent call follows it.
    const after = view.phases.slice(firstVerify)
    const expected: [PhaseKind, number][] = []
    for (let k = 1; k <= n; k++) {
      expected.push(['verify', k])
      if (k < n) expected.push(['fix', k])
    }
    expect(after.map((p) => [p.kind, p.dispatch])).toEqual(expected)

    // A verify phase is the Agent call plus that verifier's steps; a fix phase is the main
    // agent's steps between two verifications.
    for (const d of view.dispatches) {
      const verify = after[2 * (d.n - 1)]
      expect(verify.steps).toEqual([d.toolUseId, ...d.steps])
    }
    for (const fix of after.filter((p) => p.kind === 'fix')) {
      const from = stepOf(view, view.dispatches[fix.dispatch! - 1].toolUseId!).seq
      const to = stepOf(view, view.dispatches[fix.dispatch!].toolUseId!).seq
      const between = view.steps.filter((s) => s.dispatch === null && s.seq > from && s.seq < to)
      expect(fix.steps).toEqual(between.map((s) => s.id))
    }
  })

  test.fails.each([
    [CART, cart],
    [DIV, div],
  ])('%s: the first query explores, the first write builds', (_name, events) => {
    const view = buildRunView(events)
    const main = toolStarts(events).filter((e) => !e.parent)
    const firstSql = main.find((e) => e.tool === 'mcp__data__query_database')!
    const firstWrite = main.find((e) => e.tool === 'Write' || e.tool === 'Edit')!
    expect(phaseOf(view, firstSql.tool_use_id as string).kind).toBe('explore')
    expect(phaseOf(view, firstWrite.tool_use_id as string).kind).toBe('build')
  })
})

// --- Key moments ---------------------------------------------------------------------------

describe('key moments', () => {
  test.fails('verdicts, rejections, errors and the answer, in order', () => {
    const view = buildRunView(cart)
    expect(view.keyMoments.map((m) => [m.kind, m.seq])).toEqual([
      ['rejection', 9],
      ['rejection', 54],
      ['error', 94],
      ['rejection', 224],
      ['verdict', 366],
      ['rejection', 432],
      ['error', 436],
      ['verdict', 467],
      ['answer', 471],
    ])
    const steps = view.keyMoments.filter((m) => m.kind !== 'answer').map((m) => m.step)
    expect(steps).toEqual([
      'call_01_ET_QZkjmQFAkDcmoBG3HfNH9615',
      'call_01_9mOl35SDrcGgOyi0rwnb7381',
      'call_01_6f46r1APvazYrpfCoix59101',
      'call_01_jcUkvNXHGIrqENI4egLc4532',
      CART_AGENT[0],
      'call_00_FE3S0bKV38kfVNuk5wal8745',
      'call_00_ECrf6UxJOt6X8IxVWxFr9343',
      CART_AGENT[1],
    ])
    expect(view.keyMoments.filter((m) => m.kind === 'verdict').map((m) => m.dispatch)).toEqual([
      1, 2,
    ])
  })

  test.fails('a clean query run has only its answer', () => {
    const view = buildRunView(query)
    expect(view.keyMoments).toEqual([
      { kind: 'answer', seq: 57, step: 'call_01_UEDIFvctiVADBylZHXCK1428', dispatch: null },
    ])
  })
})

// --- Final answer --------------------------------------------------------------------------

describe('final answer', () => {
  test.fails('the marked answer and the query it names', () => {
    const view = buildRunView(fresh)
    expect(view.answer).toEqual({
      text: 'orders_mart is built: 3 orders.',
      seq: 42,
      sqlStep: 't-q1',
      table: { columns: ['n'], rows: [['3']] },
    })
  })

  test.fails('old file: the last text, backed by the last successful query before it', () => {
    const view = buildRunView(query)
    expect(view.answer?.seq).toBe(57)
    expect(view.answer?.text).toMatch(/^\*\*Gold — \$710\.00\*\*/)
    expect(view.answer?.sqlStep).toBe('call_01_UEDIFvctiVADBylZHXCK1428')
    expect(view.answer?.table?.rows).toHaveLength(12)
  })
})

// --- Rejected calls ------------------------------------------------------------------------

describe('rejected calls', () => {
  test.fails('the rejected_by field names the kind', () => {
    const view = buildRunView(fresh)
    expect(stepOf(view, 't-g1').rejectedBy).toBe('guard')
    expect(stepOf(view, 't-q2').rejectedBy).toBe('gateway')
    expect(view.steps.filter((s) => s.rejectedBy !== null).map((s) => s.id)).toEqual([
      't-g1',
      't-q2',
    ])
  })

  test.fails('old file: the kind is read from the result text', () => {
    const view = buildRunView(cart)
    // The Bash and file-tool hooks are the guards (src/data_agent/agent.py, run options).
    expect(stepOf(view, 'call_01_ET_QZkjmQFAkDcmoBG3HfNH9615').rejectedBy).toBe('guard')
    expect(stepOf(view, 'call_01_9mOl35SDrcGgOyi0rwnb7381').rejectedBy).toBe('guard')
    expect(stepOf(view, 'call_01_jcUkvNXHGIrqENI4egLc4532').rejectedBy).toBe('guard')
    expect(stepOf(view, 'call_00_FE3S0bKV38kfVNuk5wal8745').rejectedBy).toBe('gateway')
    // Query errors are errors, not rejections.
    expect(stepOf(view, 'call_01_6f46r1APvazYrpfCoix59101').rejectedBy).toBeNull()
    expect(stepOf(view, 'call_00_ECrf6UxJOt6X8IxVWxFr9343').rejectedBy).toBeNull()
    expect(view.steps.filter((s) => s.rejectedBy !== null)).toHaveLength(4)
  })

  test.fails('old file: a rejection of unknown kind is marked rejected only', () => {
    // A hook denied the verifier's query; the text names neither a guard nor the gateway.
    const step = stepOf(buildRunView(div), 'call_00_wbvPiTppaOP5SYWqKFSW9791')
    expect(step.result).toMatch(/^PreToolUse:mcp__data__query_database hook error/)
    expect(step.rejectedBy).toBe('unknown')
  })
})

// --- Run summary and status ----------------------------------------------------------------

describe('run summary', () => {
  test.fails('config, model, turns, tokens and duration come from the events', () => {
    const { summary } = buildRunView(query)
    expect(summary.config).toBe('no-verifiers')
    expect(summary.model).toBe('deepseek-flash')
    expect(summary.turns).toBe(13)
    expect(summary.tokens).toEqual({ uncachedInput: 11688, cachedInput: 64512, output: 1957 })
    expect(Math.round(summary.durationMs)).toBe(16948)
  })

  test.fails('cost is cost_usd, never sdk_cost_usd', () => {
    expect(buildRunView(query).summary.costUsd).toBe(0.006241872)
    expect(buildRunView(fresh).summary.costUsd).toBe(0.05)
  })

  test.fails('the task event gives task, type and data source', () => {
    const notes: RunNotes = { task: 'something else', task_type: 'query', data_source: 'other' }
    const { summary } = buildRunView(fresh, notes)
    expect(summary.task).toBe('Build the orders mart from raw.orders.')
    expect(summary.taskType).toBe('dbt')
    expect(summary.dataSource).toBe('shop')
  })

  test.fails('old file: task, type and data source come from the notes', () => {
    const { summary } = buildRunView(query, loadNotes(QUERY))
    expect(summary.task).toBe(
      'Which customer tier has the highest total amount of completed orders, and how much is it?',
    )
    expect(summary.taskType).toBe('query')
    expect(summary.dataSource).toBe('shop.duckdb')
  })

  test.fails('old file without notes: not recorded', () => {
    const { summary } = buildRunView(query)
    expect([summary.task, summary.taskType, summary.dataSource]).toEqual([null, null, null])
  })
})

describe('status', () => {
  test.fails('an evaluation run follows its grade, not the result event', () => {
    // Both runs end with a successful result event; the grader failed one of them.
    expect(buildRunView(div, loadNotes(DIV)).summary.status).toBe('failure')
    expect(buildRunView(cart, loadNotes(CART)).summary.status).toBe('success')
  })

  test.fails('a local run follows its result event', () => {
    expect(buildRunView(query, loadNotes(QUERY)).summary.status).toBe('success')
    const failed = query.map((e) =>
      e.type === 'result' ? { ...e, subtype: 'error_max_turns', is_error: true } : e,
    )
    expect(buildRunView(failed).summary.status).toBe('failure')
  })

  test.fails('a run the backend cancelled is cancelled', () => {
    const view = buildRunView(upTo(query, 30), { cancelled: true })
    expect(view.summary.status).toBe('cancelled')
  })

  test.fails('without a result event or other information the run is running', () => {
    expect(buildRunView(upTo(query, 57)).summary.status).toBe('running')
    expect(buildRunView(upTo(query, 57), loadNotes(QUERY)).summary.status).toBe('running')
  })
})

// --- Context tools -------------------------------------------------------------------------

describe('context tools', () => {
  test.fails('calls are counted per context tool', () => {
    expect(buildRunView(cart).contextToolUses).toEqual({
      discover: 2,
      hydrate: 0,
      dictionary_search: 0,
    })
    expect(buildRunView(query).contextToolUses).toEqual({
      discover: 0,
      hydrate: 0,
      dictionary_search: 0,
    })
  })
})

// --- Prefixes (replay and live view) -------------------------------------------------------

describe('event prefixes', () => {
  test.fails('an open call is a step without a result', () => {
    const view = buildRunView(upTo(query, 52))
    expect(view.steps).toHaveLength(12)
    expect(view.steps.at(-1)).toMatchObject({
      id: 'call_01_UEDIFvctiVADBylZHXCK1428',
      result: null,
      table: null,
    })
    expect(view.answer).toBeNull()
    expect(view.summary).toMatchObject({ status: 'running', costUsd: null, turns: null })
  })

  test.fails('a dispatch still running has its steps so far and no verdict', () => {
    const view = buildRunView(upTo(cart, 340))
    expect(view.steps).toHaveLength(89)
    expect(view.dispatches).toHaveLength(1)
    expect(view.dispatches[0]).toMatchObject({ n: 1, overall: null, checks: [] })
    expect(view.dispatches[0].steps).toHaveLength(11)
    expect(view.phases.at(-1)).toMatchObject({ kind: 'verify', dispatch: 1 })
    expect(view.keyMoments.some((m) => m.kind === 'verdict')).toBe(false)
  })

  test.fails('every prefix shows the calls started so far, in order', () => {
    const full = buildRunView(cart).steps.map((s) => s.id)
    for (const seq of [0, 2, 3, 50, 120, 307, 366, 400, 471]) {
      const prefix = upTo(cart, seq)
      const started = toolStarts(prefix).length
      expect(buildRunView(prefix).steps.map((s) => s.id)).toEqual(full.slice(0, started))
    }
  })

  test.fails('the header alone is an empty running view', () => {
    const view = buildRunView(upTo(cart, 0))
    expect(view.steps).toEqual([])
    expect(view.phases).toEqual([])
    expect(view.dispatches).toEqual([])
    expect(view.answer).toBeNull()
    expect(view.summary.status).toBe('running')
    expect(view.summary.config).toBe('full')
  })
})

// --- Fallbacks for verifier events ---------------------------------------------------------

describe('verifier events, new and old', () => {
  test.fails('check names come from check_names', () => {
    const [first] = buildRunView(fresh).dispatches
    expect(first.checks).toEqual([
      { model: 'orders_mart', check: '1', name: 'exists', verdict: 'PASS' },
      { model: 'orders_mart', check: '2', name: 'columns', verdict: 'FAIL' },
    ])
  })

  test.fails('old file: a check without a name shows its number', () => {
    const [first] = buildRunView(cart).dispatches
    expect(first.checks.every((c) => c.name === c.check)).toBe(true)
  })

  test.fails('the dispatch names its Agent call', () => {
    // Another Agent call started after the verifier's: only the field links the right one.
    const view = buildRunView(fresh)
    expect(view.dispatches.map((d) => d.toolUseId)).toEqual(['t-a1', 't-a2'])
    expect(view.dispatches.map((d) => d.steps)).toEqual([['t-v1'], ['t-v2']])
    expect(stepOf(view, 't-x1').dispatch).toBeNull()
  })

  test.fails('old file: the dispatch is matched by position', () => {
    const view = buildRunView(cart)
    expect(view.dispatches.map((d) => d.toolUseId)).toEqual(CART_AGENT)
    expect(view.dispatches.map((d) => d.steps.length)).toEqual([18, 18])
  })
})
