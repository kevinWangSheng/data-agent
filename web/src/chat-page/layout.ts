// How one turn of the chat view lays out (spec decision 5, the chosen A layout): the turn's steps
// grouped by phase, each group holding the main agent's thinking and text that came during it and
// the steers delivered after one of its steps; then the answer. Pure, so the page only renders.

import type { ConversationView, Steer, TurnView } from '../chat-view/model'
import type { Narration, Phase, Step, VerifierDispatch } from '../run-view/model'

export type Group = {
  phase: Phase
  /** In the phase's order. */
  steps: Step[]
  /** Thinking and text from the group's first step until the next group's. */
  notes: Narration[]
  /** The verification a verify phase shows. */
  dispatch: VerifierDispatch | null
  /** Delivered steers whose `afterStep` is one of this group's steps. */
  steers: Steer[]
}

export type TurnLayout = {
  /** Thinking and text before the turn's first step, while it has none. */
  before: Narration[]
  /** Steers delivered before any step. */
  earlySteers: Steer[]
  groups: Group[]
  /** Steers sent but not delivered yet. */
  pending: Steer[]
  /** Whether the turn shows a final answer: a clarification turn ends with its question instead. */
  showAnswer: boolean
}

export function turnLayout(turn: TurnView): TurnLayout {
  const { run, clarification } = turn
  const byId = new Map(run.steps.map((s) => [s.id, s]))
  const groups: Group[] = run.phases.flatMap((phase) => {
    const steps = phase.steps.flatMap((id) => byId.get(id) ?? [])
    if (!steps.length) return []
    const dispatch = phase.kind === 'verify' ? (run.dispatches.find((d) => d.n === phase.dispatch) ?? null) : null
    return [{ phase, steps, notes: [], dispatch, steers: [] }]
  })

  const showAnswer = run.answer !== null && clarification === null
  const answerText = showAnswer ? run.answer?.text : undefined
  const before: Narration[] = []
  const firstSeq = groups.map((g) => Math.min(...g.steps.map((s) => s.seq)))
  for (const n of run.narration) {
    if (n.seq === run.answer?.seq || n.text === answerText) continue
    const at = firstSeq.findLastIndex((seq) => seq <= n.seq)
    if (groups.length) groups[Math.max(0, at)].notes.push(n)
    else before.push(n)
  }

  const earlySteers: Steer[] = []
  const pending: Steer[] = []
  for (const steer of turn.steers) {
    if (!steer.delivered) pending.push(steer)
    else if (steer.afterStep === null) earlySteers.push(steer)
    else {
      const group = groups.find((g) => g.steps.some((s) => s.id === steer.afterStep))
      ;(group?.steers ?? earlySteers).push(steer)
    }
  }
  return { before, earlySteers, groups, pending, showAnswer }
}

/** Whether turn `i`'s clarification options can be clicked: the conversation's last turn, one that
 *  takes follow-ups, and no turn running (`running`: the backend's, true until the turn's process
 *  exited, when a message would go into the turn instead of starting the next one). */
export function answerable(view: ConversationView, i: number, running: boolean): boolean {
  return view.canFollowUp && !running && i === view.turns.length - 1 && view.turns[i].clarification !== null
}

/** What a conversation is called: its first message; a single run without one, its id. */
export function conversationTitle(id: string, singleRun: boolean, firstMessage: string | null | undefined): string {
  return firstMessage ?? (singleRun ? id : 'New conversation')
}
