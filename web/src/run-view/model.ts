// The run view model: one pure function turns a run's events (all of them, or a prefix while the
// run is live or being replayed) plus the optional run notes into what the run page shows
// (.scratch/web-ui/spec.md, "运行视图模型"). Types and signature fixed by ticket 73; the body is
// ticket 75's.

/** One line of a run's event file (src/data_agent/events.py): `seq`, `type`, `ts` plus flat fields. */
export type RunEvent = { seq: number; type: string; ts: string; [field: string]: unknown }

export type TaskType = 'query' | 'dbt'

/** Grader outcome of an evaluation trial. */
export type Grade = { passed: boolean; detail: string | null }

/**
 * The run notes (运行说明): what the event file does not say about a run. The export command
 * writes all of these fields to each run's `meta.json`; the local backend gives what it knows.
 */
export type RunNotes = {
  id?: string
  title?: string | null
  task?: string | null
  task_type?: TaskType | null
  /** The data source's name, never a connection string. */
  data_source?: string | null
  /** Set for evaluation trials; the run's status then follows it. */
  grade?: Grade | null
  failure_reason?: string | null
  featured?: boolean
  /** Set by the local backend for a run it cancelled. */
  cancelled?: boolean
}

export type RunStatus = 'running' | 'success' | 'failure' | 'cancelled'

export type RunSummary = {
  /** null: not recorded (no `task` event and no notes). */
  task: string | null
  taskType: TaskType | null
  config: string | null
  model: string | null
  dataSource: string | null
  status: RunStatus
  /** From the first event to the last one given. */
  durationMs: number
  /** `result.num_turns`; null before the result event. */
  turns: number | null
  /** `result.cost_usd` (never `sdk_cost_usd`); null before the result event. */
  costUsd: number | null
  tokens: { uncachedInput: number; cachedInput: number; output: number } | null
}

/** Who rejected a call; 'unknown' when it was rejected but the kind cannot be told. */
export type Rejection = 'guard' | 'gateway' | 'unknown'

/** A text table from a query result, split into cells. */
export type ResultTable = { columns: string[]; rows: string[][] }

/** One tool call: its `tool_start`, `tool_end`, `tool_timing` and `sql` events, paired by `tool_use_id`. */
export type Step = {
  /** The call's `tool_use_id`. */
  id: string
  /** `seq` of the `tool_start` event. */
  seq: number
  /** Tool name as the event gives it, e.g. `mcp__data__query_database`. */
  tool: string
  input: unknown
  /** Text of the tool result; null while the call is still open. */
  result: string | null
  /** The result as a table when it is a text table; null otherwise (show `result` as is). */
  table: ResultTable | null
  durationMs: number | null
  isError: boolean
  rejectedBy: Rejection | null
  /** `n` of the verifier dispatch whose verifier made this call; null for the main agent. */
  dispatch: number | null
  /** The statement from the call's `sql` event, if any. */
  sql: string | null
}

/** One verdict of one check on one model. */
export type CheckResult = {
  model: string
  /** Check number as the verifier wrote it, e.g. "4". */
  check: string
  /** The check's name; the check number when the event file has no name for it. */
  name: string
  verdict: string
}

export type VerifierDispatch = {
  n: number
  /** `tool_use_id` of the Agent call that carried this dispatch. */
  toolUseId: string | null
  models: string[]
  /** Per model, per check, in the order of `verifier_end.checks`. Empty until the verdict. */
  checks: CheckResult[]
  /** `verifier_end.overall`; null until the verdict. */
  overall: string | null
  report: string | null
  /** Ids of the steps the verifier made, in order. */
  steps: string[]
}

/**
 * orient 了解环境, explore 探索数据, build 写并运行 dbt, self-check 自查, answer 回答 (query
 * runs), verify 验证, fix 修复.
 */
export type PhaseKind = 'orient' | 'explore' | 'build' | 'self-check' | 'answer' | 'verify' | 'fix'

export type Phase = {
  kind: PhaseKind
  /** verify: the dispatch it shows; fix: the dispatch it follows; otherwise null. */
  dispatch: number | null
  /** Ids of its steps, in order. A verify phase holds the Agent call and the verifier's steps. */
  steps: string[]
}

export type KeyMomentKind = 'verdict' | 'rejection' | 'error' | 'answer'

export type KeyMoment = {
  kind: KeyMomentKind
  /** `seq` of the event where it happened: `verifier_end`, `tool_end`, or the answer's event. */
  seq: number
  /** The step it points to: the Agent call for a verdict, the call itself for a rejection or an
   *  error, the backing query (if any) for the answer. */
  step: string | null
  dispatch: number | null
}

export type FinalAnswer = {
  text: string
  /** `seq` of the event holding the answer text. */
  seq: number
  /** Id of the query step the answer rests on; null when there is none. */
  sqlStep: string | null
  /** That query's result table. */
  table: ResultTable | null
}

export type RunView = {
  summary: RunSummary
  /** Every tool call, main agent and verifiers, in `tool_start` order. */
  steps: Step[]
  dispatches: VerifierDispatch[]
  phases: Phase[]
  /** In `seq` order. */
  keyMoments: KeyMoment[]
  answer: FinalAnswer | null
  /** Skills the run loaded. */
  skills: string[]
  /** Calls per context tool. */
  contextToolUses: { discover: number; hydrate: number; dictionary_search: number }
}

export function buildRunView(events: readonly RunEvent[], notes?: RunNotes): RunView {
  void events
  void notes
  throw new Error('buildRunView: not implemented (ticket 75)')
}
