// The online site's first screen reads `home.json`, made at build time from the export
// (site-data.ts): the featured runs and the two leaderboard summaries. The export's own files are
// too big for a first screen: `index.json` holds every dev trial's notes and task text, and
// `eval.json` the 270-row trial table, so those load only where they are shown.

import type { RunNotes } from '../run-view/model'
import type { Board, Dab, Evaluation } from './evaluation'

export type Home = {
  /** The featured runs, in the index's order; the online history list shows these. */
  runs: RunNotes[]
  data_eng_bench: Board | null
  dab: Dab | null
}

export function homeData(index: { runs: RunNotes[] }, evaluation: Evaluation | null): Home {
  return {
    runs: index.runs.filter((r) => r.featured),
    data_eng_bench: evaluation?.data_eng_bench ?? null,
    dab: evaluation?.dab ?? null,
  }
}

/** The run the first screen replays: the first query run (the short one), else the first run. */
export function firstScreenRun(runs: readonly RunNotes[]): RunNotes | null {
  return runs.find((r) => r.task_type === 'query') ?? runs[0] ?? null
}
