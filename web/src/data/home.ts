// The online site's first screen reads `home.json`, made at build time from the export
// (site-data.ts): the featured runs and the two leaderboard summaries. The export's own files are
// too big for a first screen: `index.json` holds every dev trial's notes and task text, and
// `eval.json` the 270-row trial table, so those load only where they are shown. The recorded
// conversations' indexes are small, and come along whole (ticket 96).

import type { ConversationIndex } from '../chat-view/model'
import type { RunNotes } from '../run-view/model'
import type { Board, Dab, Evaluation } from './evaluation'

/** A recorded conversation of the export (evals/export.py `--conversation`): its index, with the
 *  maintainer's title. Each turn's run is in the export as any run is. */
export type RecordedConversation = ConversationIndex & { title: string }

export type Home = {
  /** The featured runs, in the index's order; the online history list shows these. */
  runs: RunNotes[]
  data_eng_bench: Board | null
  dab: Dab | null
  /** The recorded conversations, in the index's order; absent from an export without any. */
  conversations?: RecordedConversation[]
}

export function homeData(index: { runs: RunNotes[]; conversations?: RecordedConversation[] }, evaluation: Evaluation | null): Home {
  return {
    runs: index.runs.filter((r) => r.featured),
    data_eng_bench: evaluation?.data_eng_bench ?? null,
    dab: evaluation?.dab ?? null,
    ...(index.conversations?.length ? { conversations: index.conversations } : {}),
  }
}

/** The featured run the first screen replays: the first query run (the short one), else the first
 * featured run; none only before `home.json` loads, as the site build stops on an export without
 * featured runs (site-data.ts). */
export function firstScreenRun(featured: readonly RunNotes[]): RunNotes | null {
  return featured.find((r) => r.task_type === 'query') ?? featured[0] ?? null
}
