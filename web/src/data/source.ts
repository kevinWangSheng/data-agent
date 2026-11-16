// The data source the pages read through (.scratch/web-ui/spec.md, "一套前端，两个数据来源"). The
// build picks one (`VITE_DATA_SOURCE`, src/App.tsx):
//
// - static (the published site): the export command's layout: per run `runs/<id>/events.jsonl`
//   and `runs/<id>/meta.json`, and the evaluation page's `eval.json`; plus `home.json`, which the
//   site build makes from the export for the first screen (./home.ts). The export's `index.json`
//   is not read: it holds every dev trial, and the online history list shows only the featured
//   runs (spec "历史列表"). Read only.
// - local (`data-agent web`): the backend's API (src/data_agent/web/app.py). It also offers the
//   live operations: the run list with each run's status, the form's options, submit, cancel, and
//   following a running run over SSE.

import type { RunEvent, RunNotes, RunStatus, TaskType } from '../run-view/model'
import type { Evaluation } from './evaluation'
import type { Home } from './home'

export type RunData = { notes: RunNotes; events: RunEvent[] }

/** A run's row in the backend's run list (`GET /api/runs`). */
export type RunRow = {
  id: string
  task: string | null
  task_type: TaskType | null
  config: string | null
  status: RunStatus
  /** The first event's `ts`. */
  started_at: string | null
  duration_ms: number | null
  cost_usd: number | null
}

/** What the run form offers (`GET /api/options`): data sources by name only. */
export type RunOptions = { data_sources: string[]; task_types: TaskType[]; configs: string[] }

export type Submission = { task: string; task_type: TaskType; data_source: string; config: string }

/** What `follow` hands over: new events in order, and the notes once the backend says the run ended. */
export type FollowHandlers = { events(batch: RunEvent[]): void; notes(notes: RunNotes): void }

export interface LiveSource {
  /** Every run, newest start first. */
  history(): Promise<RunRow[]>
  options(): Promise<RunOptions>
  /** Starts a run; answers its id. Fails with the backend's reason when refused. */
  submit(submission: Submission): Promise<string>
  /** Stops a run this server started; answers its row as it ended. */
  cancel(id: string): Promise<RunRow>
  /** Streams the run's events after `after` (a `seq`) until the run ends; returns a stop function. */
  follow(id: string, after: number, on: FollowHandlers): () => void
}

export interface DataSource {
  /** The notes of every run the source offers, in its order. */
  listRuns(): Promise<RunNotes[]>
  loadRun(id: string): Promise<RunData>
  /** Only the local backend has these. */
  live?: LiveSource
}

/** What only the online site has: the first screen's summary and the evaluation page's data. */
export interface SiteSource {
  home(): Promise<Home>
  /** null when the data has no `eval.json` (the development fixtures). */
  evaluation(): Promise<Evaluation | null>
}

export function parseEventLines(text: string): RunEvent[] {
  return text
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as RunEvent)
}

/** Reads the static files under `root` (a URL path ending in `/`). */
export function staticSource(root: string, fetchFn: typeof fetch = fetch): DataSource & SiteSource {
  const get = async (path: string) => {
    const response = await fetchFn(`${root}${path}`)
    if (!response.ok) throw new Error(`${root}${path}: ${response.status} ${response.statusText}`)
    return response
  }
  let home: Promise<Home> | null = null
  // Read once for the run list and the scores; a failed read is tried again next time.
  const readHome = () =>
    (home ??= get('home.json')
      .then((r) => r.json() as Promise<Home>)
      .catch((e: unknown) => {
        home = null
        throw e
      }))
  return {
    async listRuns() {
      return (await readHome()).runs
    },
    home: readHome,
    async evaluation() {
      const response = await fetchFn(`${root}eval.json`)
      if (response.status === 404) return null
      if (!response.ok) throw new Error(`${root}eval.json: ${response.status} ${response.statusText}`)
      return (await response.json()) as Evaluation
    },
    async loadRun(id) {
      const dir = `runs/${encodeURIComponent(id)}/`
      const [notes, events] = await Promise.all([
        get(`${dir}meta.json`).then((r) => r.json() as Promise<RunNotes>),
        get(`${dir}events.jsonl`).then(async (r) => parseEventLines(await r.text())),
      ])
      return { notes, events }
    },
  }
}

/** The run notes the backend's row gives: a run that ended without a `result` event was cancelled
 *  (by this server) or failed (its process exited); the run view model reads the rest from events. */
export function notesOf(row: RunRow): RunNotes {
  return { id: row.id, task: row.task, task_type: row.task_type, cancelled: row.status === 'cancelled', failed: row.status === 'failure' }
}

/** Whether the run may still write events: no `result` event yet, and the backend has not said it ended. */
export function isLive(run: RunData): boolean {
  return !run.notes.cancelled && !run.notes.failed && !run.events.some((e) => e.type === 'result')
}

export function newestFirst(rows: readonly RunRow[]): RunRow[] {
  const at = (row: RunRow) => (row.started_at === null ? Number.NEGATIVE_INFINITY : Date.parse(row.started_at) || Number.NEGATIVE_INFINITY)
  return [...rows].sort((a, b) => at(b) - at(a))
}

/** The part of `EventSource` that `follow` uses, so tests can stand in for it. */
export interface EventStream {
  onmessage: ((message: { data: string }) => void) | null
  onerror: (() => void) | null
  /** 0 connecting, 1 open, 2 closed (the browser gave up and will not reconnect). */
  readonly readyState: number
  close(): void
}

function browserStream(url: string): EventStream {
  const source = new EventSource(url)
  const stream: EventStream = {
    onmessage: null,
    onerror: null,
    get readyState() {
      return source.readyState
    },
    close: () => source.close(),
  }
  source.onmessage = (message) => stream.onmessage?.(message)
  source.onerror = () => stream.onerror?.()
  return stream
}

/** How long new events wait to be handed over together, so a burst renders once. */
const BATCH_MS = 100
/** `readyState` once the browser gave up on a stream and will not reconnect. */
const CLOSED = 2
/** How long to wait before opening a stream the browser gave up on. */
const REOPEN_MS = 2000

/** Reads the backend's API under `root` (a URL path ending in `/`, e.g. `/api/`). */
export function localSource(
  root: string,
  fetchFn: typeof fetch = fetch,
  openStream: (url: string) => EventStream = browserStream,
): DataSource {
  const send = async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const response = await fetchFn(`${root}${path}`, init)
    if (!response.ok) throw new Error(await reason(response))
    return (await response.json()) as T
  }
  const post = <T,>(path: string, body?: unknown) =>
    send<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const rows = () => send<RunRow[]>('runs')
  const runPath = (id: string) => `runs/${encodeURIComponent(id)}`

  const follow = (id: string, after: number, on: FollowHandlers) => {
    let last = after
    let stopped = false
    let buffer: RunEvent[] = []
    let batchTimer: ReturnType<typeof setTimeout> | undefined
    let reopenTimer: ReturnType<typeof setTimeout> | undefined
    let stream: EventStream

    const flush = () => {
      clearTimeout(batchTimer)
      batchTimer = undefined
      if (stopped || buffer.length === 0) return
      const batch = buffer
      buffer = []
      on.events(batch)
    }
    const end = () => {
      flush()
      stopped = true
      stream.close()
      clearTimeout(reopenTimer)
    }
    /** Takes an event not seen yet; a `result` event, the run's last, ends the follow. */
    const take = (event: RunEvent) => {
      // A frame sent again after a reconnect.
      if (stopped || event.seq <= last) return
      last = event.seq
      buffer.push(event)
      if (event.type === 'result') return end()
      batchTimer ??= setTimeout(flush, BATCH_MS)
    }
    /** After the stream fails. The server closes it once the run ended; a run that ended is told
     *  apart by its row, and the events it wrote after the last one seen (while the connection
     *  was down, say) are read before the follow ends. A run no longer listed is not followed.
     *  Otherwise the connection dropped: the browser reconnects with Last-Event-ID, or, when it
     *  gave up, the stream is opened again here. */
    const recover = async (failed: EventStream) => {
      try {
        const row = (await rows()).find((r) => r.id === id)
        if (stopped) return
        if (row === undefined) return end()
        if (row.status !== 'running') {
          const { events } = await send<{ events: RunEvent[] }>(`${runPath(id)}/events?after=${last}`)
          if (stopped) return
          events.forEach(take)
          end()
          on.notes(notesOf(row))
          return
        }
      } catch {
        // The backend is unreachable for now; try again below.
      }
      if (!stopped && failed === stream && failed.readyState === CLOSED) {
        reopenTimer = setTimeout(() => {
          if (!stopped) open()
        }, REOPEN_MS)
      }
    }
    const open = () => {
      const opened = openStream(`${root}${runPath(id)}/events?after=${last}`)
      stream = opened
      opened.onmessage = (message) => take(JSON.parse(message.data) as RunEvent)
      opened.onerror = () => {
        if (!stopped) void recover(opened)
      }
    }
    open()
    return () => {
      stopped = true
      buffer = []
      clearTimeout(batchTimer)
      clearTimeout(reopenTimer)
      stream.close()
    }
  }

  return {
    async listRuns() {
      return (await rows()).map(notesOf)
    },
    async loadRun(id) {
      const [{ events }, all] = await Promise.all([send<{ events: RunEvent[] }>(`${runPath(id)}/events`), rows()])
      const row = all.find((r) => r.id === id)
      return { events, notes: row ? notesOf(row) : { id } }
    },
    live: {
      history: async () => newestFirst(await rows()),
      options: () => send<RunOptions>('options'),
      submit: async (submission) => (await post<{ id: string }>('runs', submission)).id,
      cancel: (id) => post<RunRow>(`${runPath(id)}/cancel`),
      follow,
    },
  }
}

/** The backend's reason for a refusal: FastAPI's `detail`, a string or a list of field errors. */
async function reason(response: Response): Promise<string> {
  const fallback = `${response.status} ${response.statusText}`.trim()
  let body: unknown
  try {
    body = await response.json()
  } catch {
    return fallback
  }
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => [Array.isArray(d.loc) ? d.loc.join('.') : null, d.msg].filter(Boolean).join(': '))
      .join('; ')
  }
  return fallback
}
