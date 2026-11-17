// The local build's main page (spec decisions 1, 5, 6): the conversation list on the left, the open
// conversation on the right. `<base>chats/<id>` opens one (./route.ts); the base address opens none.

import { MessageSquarePlus } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ConversationRow, LiveSource } from '../data/source'
import { errorText } from '../format'
import { ConversationList } from '../history-page/ConversationList'
import { Conversation } from './Conversation'

/** How often the list is read again while one of its rows is running. */
const LIST_POLL_MS = 3000

export function ChatPage({
  live,
  id,
  chatHref,
  runHref,
  onGo,
}: {
  live: LiveSource
  /** The open conversation; null for none. */
  id: string | null
  chatHref: (id: string) => string
  runHref: (runId: string) => string
  onGo: (href: string) => void
}) {
  const [rows, setRows] = useState<ConversationRow[] | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

  /** The latest read; an earlier one answering late is dropped, as is any after unmount. */
  const latest = useRef(0)
  const reload = useCallback(() => {
    const read = ++latest.current
    live
      .conversations()
      .then((r) => {
        if (read !== latest.current) return
        setRows(r)
        setListError(null)
      })
      .catch((e: unknown) => read === latest.current && setListError(errorText(e)))
  }, [live])

  useEffect(() => {
    reload()
  }, [reload, id])
  useEffect(
    () => () => {
      latest.current = -1
    },
    [],
  )

  const anyRunning = rows?.some((r) => r.running) ?? false
  useEffect(() => {
    if (!anyRunning) return
    const timer = setInterval(reload, LIST_POLL_MS)
    return () => clearInterval(timer)
  }, [anyRunning, reload])

  const start = async () => {
    setStarting(true)
    setStartError(null)
    try {
      onGo(chatHref(await live.startConversation()))
    } catch (e: unknown) {
      setStartError(errorText(e))
    } finally {
      setStarting(false)
    }
  }

  // Narrow screens (the run page's 900 px break): the list above, at most a third of the height.
  return (
    <div className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)] max-[900px]:grid-cols-1 max-[900px]:grid-rows-[auto_minmax(0,1fr)]">
      <ConversationList rows={rows} error={listError} selected={id} starting={starting} onNew={() => void start()} href={chatHref} onGo={onGo} />
      <main className="flex min-h-0 flex-col">
        {startError && (
          <div className="banner banner-fail m-3 text-[12px]" role="alert">
            Could not start a conversation: {startError}
          </div>
        )}
        {id === null ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center text-ink-3">
            <div className="text-[14px] text-ink-2">Ask questions about your data in a conversation.</div>
            <div className="max-w-md text-[12.5px]">
              The agent sees every configured data source and picks the ones a question needs. dbt tasks and single runs start from New run, and show here as one-turn conversations.
            </div>
            <button type="button" className="btn btn-primary" onClick={() => void start()} disabled={starting}>
              <MessageSquarePlus className="h-3.5 w-3.5" /> New conversation
            </button>
          </div>
        ) : (
          <Conversation key={id} live={live} id={id} runHref={runHref} onGo={onGo} onActivity={reload} />
        )}
      </main>
    </div>
  )
}
