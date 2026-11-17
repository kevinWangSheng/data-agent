// The chat view model: one pure function turns a conversation's index plus each turn's events (all
// of them, or a prefix while the last turn runs) into what the chat page shows
// (.scratch/chat-ui/spec.md decisions 5-9, 17, 20). Types and signature fixed by ticket 88; the body
// is ticket 94's. The index and the event fields are the backend contract in
// tests/acceptance/test_chat_api.py's docstring.

import { buildRunView, type RunEvent, type RunNotes, type RunStatus, type RunView } from '../run-view/model'

/** A message sent while a turn was running, as the index lists it. */
export type IndexSteer = { id: string; text: string }

/** One turn in `conversation.json` (or a turn of a `GET /api/conversations` row). */
export type IndexTurn = {
  turn: number
  run_id: string
  /** The user message that started the turn; null for a single run whose file records no task. */
  message: string | null
  steers: IndexSteer[]
}

/**
 * A conversation's index: `conversation.json`, or a row of `GET /api/conversations`, whose other
 * fields (`running`, each turn's `status`) the view model does not read.
 */
export type ConversationIndex = {
  id: string
  /** A run started on its own (command line or run form, dbt included): one turn, no follow-up. */
  single_run?: boolean
  created_at?: string | null
  turns: IndexTurn[]
}

/** What the page has of one turn's run: its events so far ([] before the file exists) and the run
 *  notes the backend gives (`cancelled`, `failed`). */
export type TurnRun = { events: readonly RunEvent[]; notes?: RunNotes }

/** The question a turn ended with (`clarification` event). */
export type Clarification = {
  /** `seq` of the `clarification` event. */
  seq: number
  toolUseId: string | null
  question: string
  options: string[]
  /** The next turn's message when it is one of the options (the user clicked it); else null. */
  chosen: string | null
}

/** A message sent into a running turn. */
export type Steer = {
  id: string
  text: string
  /** Whether the run wrote its `steer_delivered` event (shown as "delivered to the agent"). */
  delivered: boolean
  /** `seq` of the `steer_delivered` event; null until delivered. */
  seq: number | null
  /** The main agent's last step started before the delivery: the steer shows after it. null when
   *  undelivered or delivered before any step. */
  afterStep: string | null
}

/** Which database a query step ran on. */
export type QueryDatabase = {
  /** The step's id (its `tool_use_id`). */
  step: string
  /** The `sql` event's `database`; null in files written before the field existed. */
  database: string | null
}

export type TurnView = {
  turn: number
  runId: string
  /** The index's message; for a single run without one, the run's task (from its file or notes). */
  message: string | null
  /** Whether this turn's message is an option the previous turn's clarification offered. */
  answersClarification: boolean
  /** The turn's cards: exactly `buildRunView(events, notes)` of web/src/run-view/model.ts. */
  run: RunView
  /** `run.summary.status`. */
  status: RunStatus
  clarification: Clarification | null
  /** The index's steers in the order sent, then any delivered one the index does not list. */
  steers: Steer[]
  /** One per step that has an `sql` event, in step order. */
  queries: QueryDatabase[]
}

export type ConversationView = {
  id: string
  singleRun: boolean
  /** False for a single run. */
  canFollowUp: boolean
  /** Whether a turn is still running. */
  running: boolean
  /** In the index's order. */
  turns: TurnView[]
  /** Sum of the turns' `run.summary.costUsd` that are known; null when none is. */
  costUsd: number | null
}

export function buildConversationView(
  index: ConversationIndex,
  runs: Readonly<Record<string, TurnRun>>,
): ConversationView {
  const singleRun = index.single_run === true
  const turns = index.turns.map((t, i): TurnView => {
    const { events, notes } = runs[t.run_id] ?? NO_RUN
    const run = buildRunView(events, notes)
    const previous = index.turns[i - 1]
    const asked = previous ? clarificationOf(runs[previous.run_id]?.events ?? []) : null
    return {
      turn: t.turn,
      runId: t.run_id,
      message: t.message ?? (singleRun ? run.summary.task : null),
      answersClarification: asked !== null && isOption(asked, t.message),
      run,
      status: run.summary.status,
      clarification: withChoice(clarificationOf(events), index.turns[i + 1]?.message ?? null),
      steers: steersOf(t.steers, events),
      queries: queriesOf(run, events),
    }
  })
  const costs = turns.map((t) => t.run.summary.costUsd).filter((c) => c !== null)
  return {
    id: index.id,
    singleRun,
    canFollowUp: !singleRun,
    running: turns.some((t) => t.status === 'running'),
    turns,
    costUsd: costs.length ? costs.reduce((a, b) => a + b, 0) : null,
  }
}

const NO_RUN: TurnRun = { events: [] }

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

type Question = Omit<Clarification, 'chosen'>

/** The turn's `clarification` event (the last one, should a file hold more), without its choice. */
function clarificationOf(events: readonly RunEvent[]): Question | null {
  const e = events.findLast((ev) => ev.type === 'clarification')
  if (!e) return null
  return {
    seq: e.seq,
    toolUseId: asString(e.tool_use_id),
    question: String(e.question ?? ''),
    options: Array.isArray(e.options) ? e.options.map(String) : [],
  }
}

function isOption(question: Question, message: string | null): boolean {
  return message !== null && question.options.includes(message)
}

/**
 * The index's steers in the order sent, each marked delivered once the run wrote its
 * `steer_delivered` event (matched by id), then the delivered ones the index does not list yet.
 * A delivered steer shows after the main agent's last step started before the delivery.
 */
function steersOf(sent: readonly IndexSteer[], events: readonly RunEvent[]): Steer[] {
  const delivered = new Map<string, { text: string; seq: number; afterStep: string | null }>()
  let lastStep: string | null = null
  for (const e of events) {
    if (e.type === 'tool_start' && e.parent == null) lastStep = String(e.tool_use_id)
    if (e.type !== 'steer_delivered') continue
    const id = String(e.id)
    if (!delivered.has(id)) delivered.set(id, { text: String(e.text ?? ''), seq: e.seq, afterStep: lastStep })
  }
  const listed = new Set(sent.map((s) => s.id))
  const steers: Steer[] = sent.map(({ id, text }) => {
    const d = delivered.get(id)
    return { id, text, delivered: d !== undefined, seq: d?.seq ?? null, afterStep: d?.afterStep ?? null }
  })
  for (const [id, d] of delivered) {
    if (!listed.has(id)) steers.push({ id, text: d.text, delivered: true, seq: d.seq, afterStep: d.afterStep })
  }
  return steers
}

/** The database of each step that has an `sql` event, in step order; the last `sql` event of a
 *  step wins, as it does for the step's statement in the run view. */
function queriesOf(run: RunView, events: readonly RunEvent[]): QueryDatabase[] {
  const database = new Map<string, string | null>()
  for (const e of events) {
    if (e.type === 'sql') database.set(String(e.tool_use_id), asString(e.database))
  }
  return run.steps.flatMap((step) =>
    database.has(step.id) ? [{ step: step.id, database: database.get(step.id) ?? null }] : [],
  )
}

/** The question, with the next turn's message as its choice when that message is one of the options. */
function withChoice(question: Question | null, next: string | null): Clarification | null {
  if (!question) return null
  return { ...question, chosen: isOption(question, next) ? next : null }
}
