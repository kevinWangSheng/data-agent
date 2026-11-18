// One live conversation: its transcript (./Transcript.tsx) and the message box. A message sent while
// a turn runs goes into that turn (spec decision 9); the backend decides, and the box only says which
// it will be. A single run takes no follow-up.

import { Send, Square } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

import type { LiveSource } from '../data/source'
import { errorText } from '../format'
import { answerable, composer, conversationTitle, type Composer } from './layout'
import { Transcript } from './Transcript'
import { useConversation } from './useConversation'

/** The message box's words in each of its modes (a single run has no box). */
const BOX: Record<Exclude<Composer, 'none'>, { placeholder: string; send: string; hint: string }> = {
  turn: { placeholder: 'Ask a question about your data…', send: 'Send', hint: 'Enter sends, Shift+Enter starts a new line.' },
  steer: {
    placeholder: 'Add something for the running turn…',
    send: 'Send to the running turn',
    hint: 'A message sent now goes into the running turn; the agent reads it at its next step.',
  },
  settling: { placeholder: 'Ask a question about your data…', send: 'Send', hint: 'Waiting for this turn to wrap up; you can send once it has.' },
}

export function Conversation({
  live,
  id,
  runHref,
  onGo,
  onActivity,
}: {
  live: LiveSource
  id: string
  runHref: (runId: string) => string
  onGo: (href: string) => void
  /** A turn started or ended: the conversation list shows that. */
  onActivity: () => void
}) {
  const { row, view, error, refresh, takeRow } = useConversation(live, id)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const stick = useRef(true)

  const running = row?.running ?? false
  const followUp = view?.canFollowUp ?? false
  const mode = view ? composer(view, running) : 'none'
  const canSend = mode === 'turn' || mode === 'steer'
  const box = BOX[mode === 'none' ? 'turn' : mode]

  useEffect(() => {
    if (followUp) input.current?.focus()
  }, [followUp])

  // The list shows each conversation's state: tell it when a turn starts or ends.
  const wasRunning = useRef<boolean | null>(null)
  useEffect(() => {
    if (row === null) return
    if (wasRunning.current !== null && wasRunning.current !== running) onActivity()
    wasRunning.current = running
  }, [row, running, onActivity])

  const post = async (text: string, fromBox: boolean) => {
    setBusy(true)
    setActionError(null)
    try {
      await live.send(id, text)
      if (fromBox) setDraft('')
      stick.current = true
      await refresh()
    } catch (e: unknown) {
      setActionError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    setBusy(true)
    setActionError(null)
    try {
      takeRow(await live.cancelTurn(id))
    } catch (e: unknown) {
      setActionError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const submit = () => {
    const text = draft.trim()
    if (text && !busy && canSend) void post(text, true)
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  if (!view) {
    return <div className="p-8 text-ink-3">{error ? <div className="banner banner-fail">Could not load the conversation: {error}</div> : `Loading ${id}…`}</div>
  }
  return (
    <Transcript
      view={view}
      title={conversationTitle(id, view.singleRun, view.turns[0]?.message)}
      stick={stick}
      canAnswer={(i) => !busy && answerable(view, i, running)}
      onPick={(option) => void post(option, false)}
      runHref={runHref}
      onGo={onGo}
      notice={error && <div className="banner banner-fail mx-4 mt-2 text-[12px]">Lost touch with the backend: {error}</div>}
      empty={<div className="py-16 text-center text-ink-3">Ask a question about your data. The agent picks the data sources it needs.</div>}
      footer={
        view.singleRun ? (
          <div className="mx-auto max-w-3xl text-[12px] text-ink-3">This run was started on its own (command line or the run form), so it takes no follow-up. Start a new conversation to ask more.</div>
        ) : (
          <>
            {/* Below 900px the buttons get their own row under the box, so the box keeps the width. */}
            <div className="mx-auto flex max-w-3xl items-end gap-2 max-[900px]:flex-col max-[900px]:items-stretch">
              <textarea
                ref={input}
                data-testid="chat-input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKey}
                rows={2}
                placeholder={box.placeholder}
                aria-label="Message"
                className="min-h-[44px] flex-1 resize-none rounded-md border border-line-2 bg-panel-2 px-2.5 py-1.5 text-[13px] text-ink placeholder:text-ink-3"
              />
              <div className="flex shrink-0 gap-2 max-[900px]:justify-end">
                {running && (
                  <button type="button" className="btn h-9" onClick={() => void cancel()} disabled={busy}>
                    <Square className="h-3 w-3" /> Cancel
                  </button>
                )}
                <button type="button" data-testid="chat-send" className="btn btn-primary h-9" onClick={submit} disabled={busy || !canSend || !draft.trim()}>
                  <Send className="h-3.5 w-3.5" /> {box.send}
                </button>
              </div>
            </div>
            <div className="mx-auto mt-1 max-w-3xl text-[10.5px] text-ink-3">{box.hint}</div>
            {actionError && (
              <div className="banner banner-fail mx-auto mt-2 max-w-3xl text-[12px]" role="alert">
                {actionError}
              </div>
            )}
          </>
        )
      }
    />
  )
}
