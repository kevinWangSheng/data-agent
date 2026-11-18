// A conversation's transcript: its header with the cumulative cost (spec decision 7) and the turns in
// one column, kept scrolled to the latest while the reader is there. The live conversation
// (./Conversation.tsx) puts its message box under it; the online site's recorded conversation
// (../site/RecordedConversation.tsx) its replay controls in the header and no box.

import { useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react'

import type { ConversationView } from '../chat-view/model'
import { fmtCost } from '../format'
import { Turn } from './Turn'

/** How close to the bottom (px) still counts as reading the latest, so new content scrolls into view. */
const STICK_PX = 160

export function Transcript({
  view,
  title,
  stick,
  readOnly = false,
  canAnswer,
  onPick,
  runHref,
  onGo,
  bar,
  notice,
  empty,
  footer,
}: {
  view: ConversationView
  title: string
  /** Whether the reader is at the latest; the owner sets it to scroll there (after a message). */
  stick: RefObject<boolean>
  /** No message can be sent (the online site), so nothing speaks of a next message. */
  readOnly?: boolean
  /** Whether turn `i`'s clarification options can be clicked now; never when not given (read only). */
  canAnswer?: (i: number) => boolean
  onPick?: (option: string) => void
  runHref: (runId: string) => string
  onGo: (href: string) => void
  /** More of the header, after the title. */
  bar?: ReactNode
  /** A banner under the header. */
  notice?: ReactNode
  /** What a conversation without turns shows. */
  empty?: ReactNode
  /** Under the turns: the message box, or what stands in for it. */
  footer: ReactNode
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const size = view.turns.reduce((n, t) => n + t.run.steps.length + t.run.narration.length + t.steers.length + 1, 0)
  useLayoutEffect(() => {
    const el = scroller.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [size, view.running, stick])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-panel px-4 py-2">
        <h1 className="min-w-0 max-w-[60%] truncate text-[14px] font-semibold" title={title}>
          {title}
        </h1>
        {view.singleRun && <span className="chip chip-na">SINGLE RUN</span>}
        {bar}
        <div className="flex-1" />
        <span className="text-[12px] text-ink-2" title="The sum of the turns' costs">
          Conversation cost <span className="mono font-medium">{view.costUsd === null ? '—' : fmtCost(view.costUsd)}</span>
        </span>
      </div>
      {notice}
      <div
        ref={scroller}
        className="min-h-0 flex-1 overflow-auto px-4 pt-5 pb-10"
        onScroll={(e) => {
          const el = e.currentTarget
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX
        }}
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          {view.turns.map((turn, i) => (
            <Turn key={turn.runId} turn={turn} readOnly={readOnly} answerable={canAnswer?.(i) ?? false} onPick={onPick ?? (() => {})} runHref={runHref(turn.runId)} onGo={onGo} />
          ))}
          {view.turns.length === 0 && empty}
        </div>
      </div>
      <div className="border-t border-line bg-panel px-4 py-3">{footer}</div>
    </div>
  )
}
