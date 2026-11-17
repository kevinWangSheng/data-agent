// The chat view model: one pure function turns a conversation's index plus each turn's events (all
// of them, or a prefix while the last turn runs) into what the chat page shows
// (.scratch/chat-ui/spec.md decisions 5-9, 17, 20). Types and signature fixed by ticket 88; the body
// is ticket 94's. The index and the event fields are the backend contract in
// tests/acceptance/test_chat_api.py's docstring.

import type { RunEvent, RunNotes, RunStatus, RunView } from '../run-view/model'

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
  void index
  void runs
  throw new Error('buildConversationView is not implemented yet (ticket 94)')
}
