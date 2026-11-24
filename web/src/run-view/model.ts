// The run view model: one pure function turns a run's events (all of them, or a prefix while the
// run is live or being replayed) plus the optional run notes into what the run page shows
// (.scratch/web-ui/spec.md, "运行视图模型"). Types and signature fixed by ticket 73; the body is
// ticket 75's; ticket 86 added the narration and the times.

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
  /** Set by the local backend for a run whose process exited without a `result` event. */
  failed?: boolean
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
  /** When the `tool_start` event came, in milliseconds after the run's first event; null when unknown. */
  startMs: number | null
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
  /** When `verifier_start` came, in milliseconds after the run's first event; null before it or when unknown. */
  startMs: number | null
  /** When `verifier_end` came; null while the verifier is still running or when unknown. */
  endMs: number | null
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
  /** Set when the answer names no query and `sqlStep` is the last successful query of the main agent
   *  before it (a new file's `answer` event with `sql_tool_use_id` null, or an old file without one);
   *  absent when the agent named the query or there is none. Absent rather than false, so a named
   *  answer keeps the exact shape the acceptance tests compare with `toEqual`. */
  sqlInferred?: true
}

/** One `thinking` or `text` event of the main agent (the verifiers' are not in the event file). */
export type Narration = {
  kind: 'thinking' | 'text'
  seq: number
  /** Milliseconds after the run's first event; null when unknown. */
  atMs: number | null
  text: string
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
  /** Skills injected into the main agent's prompt (`version.skills`, in order). `[]`: the Skill
   *  layer was off. null: not recorded (an old file); never inferred from tool calls or layers. */
  skills: string[] | null
  /** Calls per context tool. */
  contextToolUses: { discover: number; hydrate: number; dictionary_search: number }
  /** The main agent's thinking and text, in `seq` order; the answer's text is one of them. */
  narration: Narration[]
}

// --- Tool kinds -----------------------------------------------------------------------------

/** The data agent's MCP tools (src/data_agent/mcp_server.py, server `data`). */
const DATA_TOOL_PREFIX = 'mcp__data__'
/** Tools that return query rows (`format_rows`): `query_labels` reads the label tables
 *  `classify_text` made. An answer is backed by one of them. */
const QUERY_TOOLS = new Set([`${DATA_TOOL_PREFIX}query_database`, `${DATA_TOOL_PREFIX}query_labels`])
/** Tools that run SQL: the first of them ends "learning the environment". */
const SQL_TOOLS = new Set([...QUERY_TOOLS, `${DATA_TOOL_PREFIX}explain_query`, `${DATA_TOOL_PREFIX}classify_text`])
const WRITE_TOOLS = new Set(['Write', 'Edit'])
const AGENT_TOOLS = new Set(['Agent', 'Task'])
const CONTEXT_TOOLS = ['discover', 'hydrate', 'dictionary_search'] as const
const SANDBOX_TOOL = `${DATA_TOOL_PREFIX}sandbox_exec`
const DBT_TOOL = `${DATA_TOOL_PREFIX}dbt_execute`
/** The tools the workspace guard hook is registered on (src/data_agent/agent.py). */
const GUARDED_TOOLS = new Set(['Read', 'Glob', 'Grep', 'Write', 'Edit'])
/** Legacy event files (before ADR-0010 / ticket 100) also ran a command guard on built-in Bash. */
const LEGACY_GUARDED_TOOLS = new Set([...GUARDED_TOOLS, 'Bash'])

/** A call that builds the dbt project: `dbt_execute`, a sandbox validate script, or legacy Bash. */
function isDbtBuild(step: Step): boolean {
  const command = String((step.input as { command?: unknown } | null)?.command ?? '').trim()
  if (step.tool === DBT_TOOL) return /^(run|build|seed|snapshot)\b/i.test(command)
  if (step.tool === SANDBOX_TOOL) return /validate_project\.py/.test(command)
  if (step.tool !== 'Bash') return false
  return /\bdbt\s+(?:[-\w=/. ]+\s+)?(run|build)\b/.test(command) || /validate_project\.py/.test(command)
}

// --- Tool results ---------------------------------------------------------------------------

/** The text of a tool result: text blocks joined, an MCP `{"result": "..."}` envelope opened. */
function resultText(content: unknown): string {
  if (content == null) return ''
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === 'string') return block
        const text = (block as { text?: unknown } | null)?.text
        return typeof text === 'string' ? text : JSON.stringify(block)
      })
      .join('\n')
  }
  if (typeof content !== 'string') return JSON.stringify(content)
  if (content.startsWith('{')) {
    try {
      const parsed = JSON.parse(content) as unknown
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        const keys = Object.keys(parsed)
        const result = (parsed as { result?: unknown }).result
        if (keys.length === 1 && typeof result === 'string') return result
      }
    } catch {
      // Not JSON: the text as it is.
    }
  }
  return content
}

const ROW_COUNT = /^\((\d+) rows\)$/
const INCOMPLETE_DISPLAY = '[INCOMPLETE DISPLAY]'
const SHOWN_COUNT = /only the first (\d+) are shown/
const RESULT_ID_NOTE = /^\[result id: [^\]]+\]$/

/** The gateway's text table (`format_rows`, `mcp_server.py`): a `a | b` header, a dash rule, the
 *  rows, then `(n rows)` as the last line, optionally followed by `[INCOMPLETE DISPLAY]` and/or
 *  the sandbox `[result id: ...]` note. The footer is found from the end, so every line above it is
 *  data: in a one-column table a blank line is an empty value, and a value that reads like `(n rows)`
 *  stays a value. A value with a line break is written as is, so in a multi-column table a line
 *  whose cell count differs from the header's cannot be told from a row: no table. The rows must
 *  number the footer's count, or the note's shown count when the display is cut; otherwise no table. */
function parseTable(text: string): ResultTable | null {
  const rawLines = text.split('\n')
  const lines = rawLines.length > 0 && RESULT_ID_NOTE.test(rawLines[rawLines.length - 1]) ? rawLines.slice(0, -1) : rawLines
  if (lines.length < 2 || !/^-+$/.test(lines[1].trim())) return null
  const note = lines[lines.length - 1]
  const cut = note.startsWith(INCOMPLETE_DISPLAY)
  const footer = lines.length - (cut ? 2 : 1)
  const count = footer >= 2 ? ROW_COUNT.exec(lines[footer]) : null
  const expected = cut ? SHOWN_COUNT.exec(note) : count
  if (!count || !expected) return null
  const columns = lines[0].split(' | ')
  const rows = lines.slice(2, footer).map((line) => (columns.length === 1 ? [line] : line.split(' | ')))
  if (rows.some((cells) => cells.length !== columns.length)) return null
  return rows.length === Number(expected[1]) ? { columns, rows } : null
}

const GATEWAY_BLOCKED = /^Error executing tool \S+: Query blocked by the read-only gateway:/
const HOOK_DENIED = /^PreToolUse:(\S+) hook error/

/** The rejection kind when the event file does not name it (spec, "旧事件文件缺新字段时的退路").
 *  Legacy runs (without `sandbox_exec` or `dbt_execute`) keep the Bash -> `guard` fallback; runs on
 *  the ADR-0010 tool surface treat only the workspace-guarded file tools as `guard`. */
function rejectionFromText(text: string, legacyBashGuard: boolean): Rejection | null {
  if (GATEWAY_BLOCKED.test(text)) return 'gateway'
  const hook = HOOK_DENIED.exec(text)
  if (hook) {
    const guarded = legacyBashGuard ? LEGACY_GUARDED_TOOLS : GUARDED_TOOLS
    return guarded.has(hook[1]) ? 'guard' : 'unknown'
  }
  return null
}

// --- The model ------------------------------------------------------------------------------

export function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function asTaskType(value: unknown): TaskType | null {
  return value === 'query' || value === 'dbt' ? value : null
}

/** Milliseconds since the epoch, or null when `ts` is missing or cannot be read. */
function parseTs(ts: unknown): number | null {
  if (typeof ts !== 'string') return null
  const ms = Date.parse(ts)
  return Number.isFinite(ms) ? ms : null
}

/** A time reader for one run: an event's `ts` as milliseconds after the run's first event; null
 *  when either `ts` is missing or cannot be read. */
function clockOf(events: readonly RunEvent[]): (e: RunEvent) => number | null {
  const start = events.length ? parseTs(events[0].ts) : null
  return (e) => {
    if (start === null) return null
    const ms = parseTs(e.ts)
    return ms === null ? null : ms - start
  }
}

/** The run's end, in milliseconds after its first event: `result.ended_ts`, which the backend takes
 *  before the trace export waits for its relay to drain (src/data_agent/agent.py), so the export's
 *  events and `result` itself come later. null when there is none (the run is still going, or an
 *  older file) or it cannot be read. */
function endedMs(events: readonly RunEvent[]): number | null {
  const start = events.length ? parseTs(events[0].ts) : null
  const ended = parseTs(events.findLast((e) => e.type === 'result')?.ended_ts)
  return start === null || ended === null ? null : ended - start
}

/** Each event's time, in milliseconds after the run's first event (null when unknown): replay cuts
 *  its prefix and skips idle gaps by these. None is later than the run's end, so replay does not
 *  wait for the trace export. */
export function eventTimes(events: readonly RunEvent[]): (number | null)[] {
  const end = endedMs(events)
  return events.map(clockOf(events)).map((ms) => (ms === null || end === null ? ms : Math.min(ms, end)))
}

type OpenDispatch = VerifierDispatch & { seq: number; agentId: string | null }

export function buildRunView(events: readonly RunEvent[], notes?: RunNotes): RunView {
  const hasNewToolSurface = events.some(
    (e) => e.type === 'tool_start' && (e.tool === SANDBOX_TOOL || e.tool === DBT_TOOL),
  )
  const steps: Step[] = []
  const stepById = new Map<string, Step>()
  const parentOf = new Map<string, string | null>()
  const endSeq = new Map<string, number>()
  const dispatches: OpenDispatch[] = []
  const dispatchByAgentCall = new Map<string, OpenDispatch>()
  const dispatchByAgentId = new Map<string, OpenDispatch>()
  /** Main-agent Agent calls not yet tied to a dispatch, in start order. */
  const openAgentCalls: Step[] = []
  const removeOpenCall = (step: Step) => {
    const at = openAgentCalls.indexOf(step)
    if (at >= 0) openAgentCalls.splice(at, 1)
  }
  const verdicts: KeyMoment[] = []
  const narration: Narration[] = []
  const timeOf = clockOf(events)
  let version: RunEvent | undefined
  let task: RunEvent | undefined
  let status: RunEvent | undefined
  let result: RunEvent | undefined
  let markedAnswer: RunEvent | undefined
  let lastText: RunEvent | undefined
  let current: OpenDispatch | undefined

  for (const e of events) {
    switch (e.type) {
      case 'version':
        version = e
        break
      case 'task':
        task = e
        break
      case 'status':
        status ??= e
        break
      case 'result':
        result = e
        break
      case 'answer':
        markedAnswer = e
        break
      case 'text':
        lastText = e
        narration.push({ kind: 'text', seq: e.seq, atMs: timeOf(e), text: String(e.text ?? '') })
        break
      case 'thinking':
        narration.push({ kind: 'thinking', seq: e.seq, atMs: timeOf(e), text: String(e.text ?? '') })
        break
      case 'tool_start': {
        const id = String(e.tool_use_id)
        const step: Step = {
          id,
          seq: e.seq,
          startMs: timeOf(e),
          tool: String(e.tool),
          input: e.input ?? null,
          result: null,
          table: null,
          durationMs: null,
          isError: false,
          rejectedBy: null,
          dispatch: null,
          sql: null,
        }
        const parent = asString(e.parent)
        steps.push(step)
        stepById.set(id, step)
        parentOf.set(id, parent)
        if (parent === null && AGENT_TOOLS.has(step.tool)) openAgentCalls.push(step)
        break
      }
      case 'sql': {
        const step = stepById.get(String(e.tool_use_id))
        if (step) step.sql = asString(e.sql)
        break
      }
      case 'tool_timing': {
        const step = stepById.get(String(e.tool_use_id))
        const ms = asNumber(e.duration_ms)
        if (step && ms !== null) step.durationMs = ms
        break
      }
      case 'tool_end': {
        const id = String(e.tool_use_id)
        const step = stepById.get(id)
        if (!step) break
        const text = resultText(e.content)
        step.result = text
        step.isError = e.is_error === true
        step.table = step.tool.startsWith(DATA_TOOL_PREFIX) && !step.isError ? parseTable(text) : null
        const named = e.rejected_by
        step.rejectedBy =
          named === 'guard' || named === 'gateway'
            ? named
            : step.isError
              ? rejectionFromText(text, !hasNewToolSurface)
              : null
        endSeq.set(id, e.seq)
        removeOpenCall(step)
        break
      }
      case 'verifier_dispatch': {
        // New files name the Agent call; in old ones it is the latest one still open.
        const named = asString(e.tool_use_id)
        const agentCall = named !== null ? stepById.get(named) : openAgentCalls.at(-1)
        const toolUseId = named ?? agentCall?.id ?? null
        if (agentCall) removeOpenCall(agentCall)
        const models = Array.isArray(e.models) ? e.models.map(String) : []
        current = {
          n: asNumber(e.n) ?? dispatches.length + 1,
          toolUseId,
          models,
          checks: [],
          overall: null,
          report: null,
          steps: [],
          startMs: null,
          endMs: null,
          seq: agentCall?.seq ?? e.seq,
          agentId: null,
        }
        dispatches.push(current)
        if (toolUseId !== null) dispatchByAgentCall.set(toolUseId, current)
        break
      }
      case 'verifier_start': {
        const agentId = asString(e.agent_id)
        if (current) current.startMs = timeOf(e)
        if (current && agentId !== null) {
          current.agentId = agentId
          dispatchByAgentId.set(agentId, current)
        }
        break
      }
      case 'verifier_end': {
        const agentId = asString(e.agent_id)
        const d = (agentId !== null ? dispatchByAgentId.get(agentId) : undefined) ?? current
        if (!d) break
        const names = (e.check_names ?? {}) as Record<string, unknown>
        const checks = (e.checks ?? {}) as Record<string, Record<string, unknown>>
        d.checks = Object.entries(checks).flatMap(([model, byCheck]) =>
          Object.entries(byCheck ?? {}).map(([check, verdict]) => ({
            model,
            check,
            name: asString(names[check]) ?? check,
            verdict: String(verdict),
          })),
        )
        d.overall = asString(e.overall)
        d.report = asString(e.report)
        d.endMs = timeOf(e)
        verdicts.push({ kind: 'verdict', seq: e.seq, step: d.toolUseId, dispatch: d.n })
        if (d === current) current = undefined
        break
      }
    }
  }

  // The verifier's steps: calls whose parent is a dispatch's Agent call.
  for (const step of steps) {
    const parent = parentOf.get(step.id)
    const d = parent ? dispatchByAgentCall.get(parent) : undefined
    if (d) {
      step.dispatch = d.n
      d.steps.push(step.id)
    }
  }

  const answer = finalAnswer(steps, stepById, markedAnswer, result ? lastText : undefined)
  const kind = asTaskType(task?.task_type) ?? asTaskType(notes?.task_type)

  return {
    summary: summarize(events, notes, kind, { version, task, status, result }),
    steps,
    dispatches: dispatches.map(({ seq: _seq, agentId: _agentId, ...d }) => d),
    phases: phasesOf(steps, dispatches, dispatchByAgentCall, answer, kind),
    keyMoments: keyMomentsOf(steps, endSeq, verdicts, answer),
    answer,
    skills: Array.isArray(version?.skills) ? version.skills.map(String) : null,
    contextToolUses: countContextTools(steps),
    narration,
  }
}

function summarize(
  events: readonly RunEvent[],
  notes: RunNotes | undefined,
  kind: TaskType | null,
  found: { version?: RunEvent; task?: RunEvent; status?: RunEvent; result?: RunEvent },
): RunSummary {
  const { version, task, status, result } = found
  const end = events.at(-1)
  const durationMs = endedMs(events) ?? (end ? clockOf(events)(end) : null) ?? 0

  let runStatus: RunStatus = 'running'
  if (notes?.cancelled) runStatus = 'cancelled'
  else if (notes?.grade) runStatus = notes.grade.passed ? 'success' : 'failure'
  else if (result) {
    runStatus = result.is_error === true || result.subtype !== 'success' ? 'failure' : 'success'
  } else if (notes?.failed) runStatus = 'failure'

  const uncached = asNumber(result?.uncached_input_tokens)
  const cached = asNumber(result?.cached_input_tokens)
  const output = asNumber(result?.output_tokens)
  return {
    task: asString(task?.task) ?? notes?.task ?? null,
    taskType: kind,
    config: asString(version?.config),
    model: asString(version?.model) ?? asString(status?.model),
    dataSource: asString(task?.data_source) ?? notes?.data_source ?? null,
    status: runStatus,
    durationMs,
    turns: asNumber(result?.num_turns),
    costUsd: asNumber(result?.cost_usd),
    tokens:
      uncached === null && cached === null && output === null
        ? null
        : { uncachedInput: uncached ?? 0, cachedInput: cached ?? 0, output: output ?? 0 },
  }
}

/** The marked answer (the `answer` event); in an old file the last text of a finished run. It rests on
 *  the query the `answer` event names; when it names none (or there is no `answer` event), on the last
 *  successful query of the main agent before it, marked as inferred. */
function finalAnswer(
  steps: Step[],
  stepById: Map<string, Step>,
  markedAnswer: RunEvent | undefined,
  lastText: RunEvent | undefined,
): FinalAnswer | null {
  const answer = markedAnswer ?? lastText
  if (!answer) return null
  const text = String(answer.text ?? '')
  const named = markedAnswer ? asString(markedAnswer.sql_tool_use_id) : null
  if (named !== null) return { text, seq: answer.seq, sqlStep: named, table: stepById.get(named)?.table ?? null }
  let backing: Step | null = null
  for (const step of steps) {
    if (step.seq > answer.seq) break
    const ok = QUERY_TOOLS.has(step.tool) && step.result !== null && !step.isError
    if (ok && step.dispatch === null) backing = step
  }
  if (!backing) return { text, seq: answer.seq, sqlStep: null, table: null }
  return { text, seq: answer.seq, sqlStep: backing.id, table: backing.table, sqlInferred: true }
}

/**
 * Before the first verification, the main agent's steps split by tool kind at fixed points:
 * orient until the first SQL or write, explore until the first write, build through the last
 * dbt build, self-check after it. A query run (no writes) is orient, explore, then answer from
 * the query the answer rests on. Each verification is the Agent call plus the verifier's steps;
 * the main agent's steps after it, up to the next one, are a fix.
 */
function phasesOf(
  steps: Step[],
  dispatches: OpenDispatch[],
  dispatchByAgentCall: Map<string, OpenDispatch>,
  answer: FinalAnswer | null,
  kind: TaskType | null,
): Phase[] {
  const phases: Phase[] = []
  const push = (k: PhaseKind, dispatch: number | null, ids: string[]) => {
    if (ids.length) phases.push({ kind: k, dispatch, steps: ids })
  }
  const main = steps.filter((s) => s.dispatch === null && !dispatchByAgentCall.has(s.id))
  const firstVerify = dispatches[0]?.seq ?? Infinity
  const before = main.filter((s) => s.seq < firstVerify)

  const indexOf = (pred: (s: Step) => boolean, from = 0) => {
    const i = before.findIndex((s, j) => j >= from && pred(s))
    return i < 0 ? before.length : i
  }
  const ids = (from: number, to: number) => before.slice(from, to).map((s) => s.id)
  const firstWrite = indexOf((s) => WRITE_TOOLS.has(s.tool))
  const isQueryRun = kind === 'query' || (kind === null && firstWrite === before.length && !dispatches.length)
  const orientEnd = Math.min(indexOf((s) => SQL_TOOLS.has(s.tool)), firstWrite)
  if (isQueryRun) {
    const backing = answer?.sqlStep ? before.findIndex((s) => s.id === answer.sqlStep) : -1
    const answerStart = backing >= orientEnd ? backing : before.length
    push('orient', null, ids(0, orientEnd))
    push('explore', null, ids(orientEnd, answerStart))
    push('answer', null, ids(answerStart, before.length))
  } else {
    let lastBuild = -1
    before.forEach((s, i) => {
      if (i >= firstWrite && isDbtBuild(s)) lastBuild = i
    })
    const buildEnd = lastBuild < 0 ? before.length : lastBuild + 1
    push('orient', null, ids(0, orientEnd))
    push('explore', null, ids(orientEnd, firstWrite))
    push('build', null, ids(firstWrite, buildEnd))
    push('self-check', null, ids(buildEnd, before.length))
  }

  dispatches.forEach((d, i) => {
    push('verify', d.n, [...(d.toolUseId !== null ? [d.toolUseId] : []), ...d.steps])
    const next = dispatches[i + 1]?.seq ?? Infinity
    push(
      'fix',
      d.n,
      main.filter((s) => s.seq > d.seq && s.seq < next).map((s) => s.id),
    )
  })
  return phases
}

function keyMomentsOf(
  steps: Step[],
  endSeq: Map<string, number>,
  verdicts: KeyMoment[],
  answer: FinalAnswer | null,
): KeyMoment[] {
  const moments = [...verdicts]
  for (const step of steps) {
    const seq = endSeq.get(step.id)
    if (seq === undefined) continue
    const kind = step.rejectedBy !== null ? 'rejection' : step.isError ? 'error' : null
    if (kind) moments.push({ kind, seq, step: step.id, dispatch: step.dispatch })
  }
  if (answer) moments.push({ kind: 'answer', seq: answer.seq, step: answer.sqlStep, dispatch: null })
  return moments.sort((a, b) => a.seq - b.seq)
}

function countContextTools(steps: Step[]): RunView['contextToolUses'] {
  const counts = { discover: 0, hydrate: 0, dictionary_search: 0 }
  for (const step of steps) {
    for (const name of CONTEXT_TOOLS) if (step.tool === `${DATA_TOOL_PREFIX}${name}`) counts[name]++
  }
  return counts
}
