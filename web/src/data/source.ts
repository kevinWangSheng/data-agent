// The data source the pages read through (.scratch/web-ui/spec.md, "一套前端，两个数据来源"). This
// ticket brings the static one, which reads the export command's layout: per run
// `runs/<id>/events.jsonl` and `runs/<id>/meta.json`, and the evaluation page's `eval.json`; plus
// `home.json`, which the site build makes from the export for the first screen (./home.ts). The
// export's `index.json` is not read: it holds every dev trial, and the online history list shows
// only the featured runs (spec "历史列表"). The local backend's source comes with ticket 79.

import type { RunEvent, RunNotes } from '../run-view/model'
import type { Evaluation } from './evaluation'
import type { Home } from './home'

export type RunData = { notes: RunNotes; events: RunEvent[] }

export interface DataSource {
  /** The notes of every run the source offers, in its order. */
  listRuns(): Promise<RunNotes[]>
  loadRun(id: string): Promise<RunData>
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
