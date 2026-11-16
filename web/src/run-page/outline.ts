// The left pane's outline: the view model's phases, each holding its steps and the main agent's
// thinking and text between them, flattened into rows for a filter and the open sections.

import type { Narration, Phase, RunView, Step } from '../run-view/model'
import type { Filter } from './route'
import { toolKind } from './tools'

export type Item =
  | { kind: 'step'; id: string; seq: number; step: Step }
  | { kind: 'narration'; id: string; seq: number; narration: Narration }

export type Section = {
  /** `orient`, `explore`, …; `verify-<n>` and `fix-<n>` after a verification. Stable while a
   *  replay or a live run grows. */
  id: string
  phase: Phase
  /** Steps and narration, in `seq` order. */
  items: Item[]
}

export type Row =
  | { type: 'group'; id: string; section: Section; open: boolean; matches: number | null }
  | { type: 'item'; id: string; item: Item; depth: 0 | 1; section: Section | null }

/** Outline id of a thinking or text item. */
export const narrationId = (seq: number) => `n${seq}`

export const sectionId = (phase: Phase) => (phase.dispatch === null ? phase.kind : `${phase.kind}-${phase.dispatch}`)

export const isSql = (step: Step) => toolKind(step.tool) === 'sql' || step.sql !== null
export const isIssue = (step: Step) => step.rejectedBy !== null || step.isError
/** A write, an edit or a shell call (dbt): what changes the project between verifications. */
export const isChange = (step: Step) => ['edit', 'shell'].includes(toolKind(step.tool))

/**
 * One section per phase. A thinking or text goes with the main agent's next step (it leads up to
 * it); after the last step, into the last phase. Before the first step there is no phase: `loose`.
 */
export function sectionsOf(view: RunView): { sections: Section[]; loose: Item[] } {
  const stepById = new Map(view.steps.map((s) => [s.id, s]))
  const sections: Section[] = view.phases.map((phase) => ({
    id: sectionId(phase),
    phase,
    items: phase.steps.flatMap((id) => {
      const step = stepById.get(id)
      return step ? [{ kind: 'step' as const, id, seq: step.seq, step }] : []
    }),
  }))
  const sectionOfStep = new Map(sections.flatMap((s) => s.items.map((i) => [i.id, s] as const)))
  const main = view.steps.filter((s) => s.dispatch === null && sectionOfStep.has(s.id))

  const loose: Item[] = []
  let next = 0
  for (const narration of view.narration) {
    while (next < main.length && main[next].seq < narration.seq) next++
    const home = next < main.length ? sectionOfStep.get(main[next].id) : sections.at(-1)
    const item: Item = { kind: 'narration', id: narrationId(narration.seq), seq: narration.seq, narration }
    if (home) home.items.push(item)
    else loose.push(item)
  }
  for (const s of sections) s.items.sort((a, b) => a.seq - b.seq)
  return { sections, loose }
}

/** Short runs open every section; long ones start as an outline of their phases. */
export function defaultExpanded(view: RunView, sections: Section[]): Set<string> {
  return new Set(view.steps.length <= 40 ? sections.map((s) => s.id) : [])
}

const stepMatching = (pick: (step: Step) => boolean) => (item: Item) => item.kind === 'step' && pick(item.step)

export function buildRows(sections: Section[], loose: Item[], filter: Filter, expanded: ReadonlySet<string>): Row[] {
  const rows: Row[] = []
  if (filter === 'all') {
    for (const item of loose) rows.push({ type: 'item', id: item.id, item, depth: 0, section: null })
  }
  const push = (section: Section, items: Item[], open: boolean, matches: number | null) => {
    rows.push({ type: 'group', id: section.id, section, open, matches })
    if (open) for (const item of items) rows.push({ type: 'item', id: item.id, item, depth: 1, section })
  }
  for (const section of sections) {
    const kind = section.phase.kind
    if (filter === 'all') push(section, section.items, expanded.has(section.id), null)
    else if (filter === 'verifier') {
      if (kind === 'verify') push(section, section.items, expanded.has(section.id), null)
      else if (kind === 'fix') {
        const changes = section.items.filter(stepMatching(isChange))
        if (changes.length) push(section, changes, true, changes.length)
      }
    } else {
      const matching = section.items.filter(stepMatching(filter === 'sql' ? isSql : isIssue))
      if (matching.length) push(section, matching, true, matching.length)
    }
  }
  return rows
}
