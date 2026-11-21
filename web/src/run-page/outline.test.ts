import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { gunzipSync } from 'node:zlib'
import { describe, expect, test } from 'vitest'

import { buildRunView, type RunEvent, type RunView } from '../run-view/model'
import { buildRows, defaultExpanded, isChange, isIssue, isSql, sectionStats, sectionsOf, verifyId, type Item } from './outline'
import { toolKind } from './tools'

const FIXTURES = join(import.meta.dirname, '../../../tests/fixtures/web/real')

function loadEvents(dir: string): RunEvent[] {
  const path = join(FIXTURES, dir, 'events.jsonl')
  const text = existsSync(path) ? readFileSync(path, 'utf8') : gunzipSync(readFileSync(`${path}.gz`)).toString('utf8')
  return text
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as RunEvent)
}

const views: Record<string, RunView> = {
  query: buildRunView(loadEvents('query-shop')),
  cart: buildRunView(loadEvents('dbt-cart-abandonment')),
  div: buildRunView(loadEvents('dbt-division-by-zero')),
}

const stepIds = (items: Item[]) => items.filter((i) => i.kind === 'step').map((i) => i.id)

describe('sectionsOf', () => {
  test.each(Object.keys(views))('%s: one section per phase, named by kind and dispatch', (name) => {
    const view = views[name]
    const { sections } = sectionsOf(view)
    expect(sections.map((s) => s.id)).toEqual(
      view.phases.map((p) => (p.dispatch === null ? p.kind : `${p.kind}-${p.dispatch}`)),
    )
    expect(new Set(sections.map((s) => s.id)).size).toBe(sections.length)
  })

  test.each(Object.keys(views))('%s: every step and every narration appears once, in seq order', (name) => {
    const view = views[name]
    const { sections, loose } = sectionsOf(view)
    sections.forEach((s, i) => expect(stepIds(s.items)).toEqual(view.phases[i].steps.filter((id) => s.items.some((x) => x.id === id))))
    const all = [...loose, ...sections.flatMap((s) => s.items)]
    expect(all.filter((i) => i.kind === 'step').length).toBe(view.phases.flatMap((p) => p.steps).length)
    expect(all.filter((i) => i.kind === 'narration').map((i) => i.seq).sort((a, b) => a - b)).toEqual(
      view.narration.map((n) => n.seq),
    )
    for (const s of sections) {
      const seqs = s.items.map((i) => i.seq)
      expect(seqs).toEqual([...seqs].sort((a, b) => a - b))
    }
  })

  test.each(Object.keys(views))('%s: narration sits in the section of the main-agent step after it', (name) => {
    const view = views[name]
    const { sections } = sectionsOf(view)
    const sectionOf = new Map(sections.flatMap((s) => s.items.map((i) => [i.id, s.id] as const)))
    const main = view.steps.filter((s) => s.dispatch === null)
    for (const n of view.narration) {
      const next = main.find((s) => s.seq > n.seq)
      if (next) expect(sectionOf.get(`n${n.seq}`)).toBe(sectionOf.get(next.id))
      else expect(sectionOf.get(`n${n.seq}`)).toBe(sections.at(-1)!.id)
    }
  })

  test('narration before the first step has no section to go in', () => {
    const events = loadEvents('query-shop')
    const firstStep = events.findIndex((e) => e.type === 'tool_start')
    const view = buildRunView(events.slice(0, firstStep))
    expect(view.narration.length).toBeGreaterThan(0)
    const { sections, loose } = sectionsOf(view)
    expect(sections).toEqual([])
    expect(loose.map((i) => i.seq)).toEqual(view.narration.map((n) => n.seq))
  })
})

describe('buildRows', () => {
  const div = views.div
  const { sections, loose } = sectionsOf(div)

  test('all, nothing expanded: one row per section', () => {
    const rows = buildRows(sections, loose, 'all', new Set())
    expect(rows.map((r) => r.id)).toEqual(sections.map((s) => s.id))
    expect(rows.every((r) => r.type === 'group' && !r.open)).toBe(true)
  })

  test('all, one section expanded: its items follow its row', () => {
    const open = sections[1]
    const rows = buildRows(sections, loose, 'all', new Set([open.id]))
    const at = rows.findIndex((r) => r.id === open.id)
    expect(rows.slice(at + 1, at + 1 + open.items.length).map((r) => r.id)).toEqual(open.items.map((i) => i.id))
    expect(rows.length).toBe(sections.length + open.items.length)
  })

  test('sql: only SQL steps, under open sections that have one', () => {
    const rows = buildRows(sections, loose, 'sql', new Set())
    const items = rows.filter((r) => r.type === 'item')
    expect(items.length).toBe(div.steps.filter(isSql).length)
    expect(items.every((r) => r.type === 'item' && r.item.kind === 'step' && isSql(r.item.step))).toBe(true)
    for (const r of rows) if (r.type === 'group') expect(r.open && r.matches! > 0).toBe(true)
  })

  test('issues: exactly the rejected and failed calls', () => {
    const rows = buildRows(sections, loose, 'issues', new Set())
    const ids = rows.filter((r) => r.type === 'item').map((r) => r.id)
    expect(ids).toEqual(div.steps.filter(isIssue).map((s) => s.id))
    expect(ids.length).toBeGreaterThan(0)
  })

  test('verifier: every dispatch, and the changes the main agent made between them', () => {
    const rows = buildRows(sections, loose, 'verifier', new Set())
    const groups = rows.filter((r) => r.type === 'group').map((r) => r.id)
    for (const d of div.dispatches) expect(groups).toContain(`verify-${d.n}`)
    const items = rows.filter((r) => r.type === 'item')
    expect(items.length).toBeGreaterThan(0)
    for (const r of items) {
      expect(r.type === 'item' && r.item.kind === 'step' && isChange(r.item.step)).toBe(true)
      expect(r.type === 'item' && r.section?.phase.kind).toBe('fix')
    }
  })

  test('narration without a section comes first, at the top level', () => {
    const events = loadEvents('query-shop')
    const view = buildRunView(events.slice(0, events.findIndex((e) => e.type === 'tool_start')))
    const parts = sectionsOf(view)
    const rows = buildRows(parts.sections, parts.loose, 'all', new Set())
    expect(rows.map((r) => r.id)).toEqual(view.narration.map((n) => `n${n.seq}`))
    expect(rows.every((r) => r.type === 'item' && r.depth === 0)).toBe(true)
  })
})

describe('sectionStats', () => {
  test('counts the calls of a phase and spans them in time', () => {
    const { sections } = sectionsOf(views.cart)
    const explore = sections.find((s) => s.id === 'explore')!
    const steps = explore.items.flatMap((i) => (i.kind === 'step' ? [i.step] : []))
    const stats = sectionStats(explore)
    expect(stats.calls).toBe(steps.length)
    expect(stats.sql).toBe(steps.filter(isSql).length)
    expect(stats.changes).toBe(steps.filter(isChange).length)
    expect(stats.issues).toBe(steps.filter(isIssue).length)
    const start = Math.min(...steps.map((s) => s.startMs!))
    const end = Math.max(...steps.map((s) => s.startMs! + (s.durationMs ?? 0)))
    expect(stats.startMs).toBe(start)
    expect(stats.durationMs).toBe(end - start)
  })

  test('no known times: no span', () => {
    const { sections } = sectionsOf(buildRunView(loadEvents('query-shop').map(({ ts: _ts, ...e }) => e as RunEvent)))
    expect(sectionStats(sections[0])).toMatchObject({ startMs: null, durationMs: null })
  })
})

describe('section ids', () => {
  test('verifyId names the verify section of a dispatch', () => {
    const { sections } = sectionsOf(views.cart)
    for (const d of views.cart.dispatches) expect(sections.some((s) => s.id === verifyId(d.n))).toBe(true)
  })
})

describe('defaultExpanded', () => {
  test('a short run opens every section', () => {
    const { sections } = sectionsOf(views.query)
    expect([...defaultExpanded(views.query, sections)].sort()).toEqual(sections.map((s) => s.id).sort())
  })

  test('a long run starts as an outline of its phases', () => {
    expect(defaultExpanded(views.div, sectionsOf(views.div).sections).size).toBe(0)
  })
})

describe('toolKind', () => {
  test.each([
    ['mcp__data__query_database', 'sql'],
    ['mcp__data__explain_query', 'sql'],
    ['mcp__data__discover', 'context'],
    ['mcp__data__dictionary_search', 'context'],
    ['mcp__data__list_tables', 'schema'],
    ['mcp__data__compare_by_key', 'check'],
    ['Read', 'file'],
    ['Edit', 'edit'],
    ['Bash', 'shell'],
    ['mcp__data__sandbox_exec', 'sandbox'],
    ['mcp__data__dbt_execute', 'dbt'],
    ['Agent', 'agent'],
    ['WebSearch', 'other'],
  ])('%s is %s', (tool, kind) => {
    expect(toolKind(tool)).toBe(kind)
  })
})
