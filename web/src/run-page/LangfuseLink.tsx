// The "open in Langfuse" link of a run's trace (.scratch/observability/spec.md "网页"): the run
// page's summary and each chat turn's meta line. A new tab; the page only links, it embeds nothing.

import type { TraceLink } from '../run-view/trace'

export function LangfuseLink({ trace }: { trace: TraceLink }) {
  return (
    <a href={trace.url} target="_blank" rel="noreferrer" className="link" title={`Langfuse trace ${trace.traceId}`}>
      Open in Langfuse ↗
    </a>
  )
}
