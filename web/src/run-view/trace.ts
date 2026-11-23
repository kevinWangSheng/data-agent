// The run's Langfuse trace, for the run page's and the chat page's "open in Langfuse" link
// (.scratch/observability/spec.md "网页"). The contract is ticket 111's; ticket 114 implements it.

import type { RunEvent } from './model'

/** The trace a run exported: its `trace` event's id and the URL that opens it in Langfuse. */
export type TraceLink = { traceId: string; url: string }

/** The link of the run whose events these are: from its `trace` event, null when it has none (the
 * export was off). The chat page passes one turn's events at a time. */
export function traceLink(events: readonly RunEvent[]): TraceLink | null {
  void events
  throw new Error('not implemented: ticket 114')
}
