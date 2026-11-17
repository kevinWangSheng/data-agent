// The local history as a conversation list (spec decision 6): every conversation, and every run
// started on its own (command line or run form, dbt included) as a one-turn conversation that takes
// no follow-up; newest first, as the backend gives them. The top button starts a conversation.

import { MessageSquarePlus } from 'lucide-react'

import type { ConversationRow } from '../data/source'
import { conversationTitle } from '../chat-page/layout'
import { fmtStart } from '../format'
import { StatusChip } from '../run-page/ui'
import { Link } from '../site/Link'

export function ConversationList({
  rows,
  error,
  selected,
  starting,
  onNew,
  href,
  onGo,
}: {
  /** null while loading. */
  rows: ConversationRow[] | null
  error: string | null
  selected: string | null
  starting: boolean
  onNew: () => void
  href: (id: string) => string
  onGo: (href: string) => void
}) {
  return (
    <nav className="flex min-h-0 flex-col border-r border-line bg-panel-2" aria-label="Conversations">
      <div className="p-2">
        <button
          type="button"
          data-testid="new-conversation"
          onClick={onNew}
          disabled={starting}
          className="flex w-full items-center gap-2 rounded-md border border-line-2 bg-panel px-2.5 py-1.5 text-[12.5px] enabled:hover:border-accent"
        >
          <MessageSquarePlus className="h-3.5 w-3.5" /> {starting ? 'Starting…' : 'New conversation'}
        </button>
      </div>
      <div className="eyebrow px-3 pb-1">Conversations{rows !== null && <span className="mono ml-1.5 text-ink-3">{rows.length}</span>}</div>
      <div className="min-h-0 flex-1 overflow-auto">
        {error && <div className="banner banner-fail m-2 text-[12px]">Could not list the conversations: {error}</div>}
        {rows === null && !error && <div className="px-3 py-2 text-[12px] text-ink-3">Loading…</div>}
        {rows !== null && rows.length === 0 && <div className="px-3 py-2 text-[12px] text-ink-3">No conversations yet.</div>}
        {rows?.map((row) => {
          const last = row.turns.at(-1)
          const title = conversationTitle(row.id, row.single_run, row.turns[0]?.message)
          return (
            <Link
              key={row.id}
              href={href(row.id)}
              onGo={onGo}
              title={title}
              className={`flex w-full flex-col gap-0.5 px-3 py-2 text-left ${row.id === selected ? 'bg-[var(--sel-strong)]' : 'hover:bg-panel-3'}`}
            >
              <span className="truncate text-[13px] font-medium">{title}</span>
              <span className="flex items-center gap-1.5 text-[11px] text-ink-3">
                {row.running ? <StatusChip status="running" /> : last && <StatusChip status={last.status} />}
                <span>{row.single_run ? 'single run' : `${row.turns.length} turn${row.turns.length === 1 ? '' : 's'}`}</span>
                <span className="mono truncate">· {fmtStart(row.created_at)}</span>
              </span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
