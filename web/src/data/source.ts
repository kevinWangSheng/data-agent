// The data source the pages read through (.scratch/web-ui/spec.md, "一套前端，两个数据来源"). This
// ticket brings the static one, which reads the export command's layout: `index.json` =
// `{"runs": [notes, ...]}`, and per run `runs/<id>/events.jsonl` and `runs/<id>/meta.json`. The
// local backend's source comes with ticket 79.

import type { RunEvent, RunNotes } from '../run-view/model'

export type RunData = { notes: RunNotes; events: RunEvent[] }

export interface DataSource {
  /** The notes of every run the source offers, in its order. */
  listRuns(): Promise<RunNotes[]>
  loadRun(id: string): Promise<RunData>
}

export function parseEventLines(text: string): RunEvent[] {
  return text
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as RunEvent)
}

/** Reads the static files under `root` (a URL path ending in `/`). */
export function staticSource(root: string, fetchFn: typeof fetch = fetch): DataSource {
  const get = async (path: string) => {
    const response = await fetchFn(`${root}${path}`)
    if (!response.ok) throw new Error(`${root}${path}: ${response.status} ${response.statusText}`)
    return response
  }
  return {
    async listRuns() {
      const index = (await (await get('index.json')).json()) as { runs: RunNotes[] }
      return index.runs
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
