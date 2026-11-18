// The online site's recorded conversation (spec decision 4, ticket 96): the chat page's transcript
// (../chat-page/Transcript.tsx) replayed on the run page's replay clock and controls, read only. It
// opens playing, skipping idle gaps, at the speed that plays it in about a minute, and stops with
// everything shown. There is no message box, and the agent's clarification options cannot be
// clicked: the site has no agent to send to (`chats/<id>`, src/App.tsx).

import { useEffect, useMemo, useRef, useState } from 'react'

import { CONVERSATION_AUTOPLAY_MS, recordingAt, recordingTimes } from '../chat-page/replay'
import { Transcript } from '../chat-page/Transcript'
import { buildConversationView } from '../chat-view/model'
import type { Recording, SiteSource } from '../data/source'
import { errorText } from '../format'
import { ReplayControls } from '../run-page/ReplayControls'
import { useReplay } from '../run-page/useReplay'

type Links = {
  /** The three-pane run page of a turn ("Full run view"). */
  runHref: (runId: string) => string
  onGo: (href: string) => void
}

/** Loads the recorded conversation `id` and replays it. */
export function RecordedConversationPage({ site, id, ...links }: { site: SiteSource; id: string } & Links) {
  const [loaded, setLoaded] = useState<{ id: string; recording?: Recording; error?: string } | null>(null)
  useEffect(() => {
    let gone = false
    site
      .recording(id)
      .then((recording) => !gone && setLoaded({ id, recording }))
      .catch((e: unknown) => !gone && setLoaded({ id, error: errorText(e) }))
    return () => {
      gone = true
    }
  }, [site, id])

  const current = loaded?.id === id ? loaded : null
  if (current?.error) return <div className="banner banner-fail m-3">Could not load the conversation: {current.error}</div>
  if (!current?.recording) return <div className="p-8 text-ink-3">Loading {id}…</div>
  return <RecordedConversation key={id} recording={current.recording} {...links} />
}

/** `autoplay` false opens it with everything shown. */
export function RecordedConversation({ recording, autoplay = true, runHref, onGo }: { recording: Recording; autoplay?: boolean } & Links) {
  const times = useMemo(() => recordingTimes(recording), [recording])
  const stick = useRef(true)
  // Playing (again) scrolls to the latest.
  const { revealed, replaying, controls } = useReplay(times, autoplay, {
    targetMs: CONVERSATION_AUTOPLAY_MS,
    onPlay: () => {
      stick.current = true
    },
  })
  const view = useMemo(() => {
    const frame = recordingAt(recording, revealed)
    return buildConversationView(frame.index, frame.runs)
  }, [recording, revealed])

  return (
    <Transcript
      view={view}
      title={recording.conversation.title}
      stick={stick}
      runHref={runHref}
      onGo={onGo}
      bar={
        <>
          {replaying && <span className="chip chip-info">REPLAY</span>}
          <ReplayControls {...controls} />
        </>
      }
      footer={
        <div className="mx-auto max-w-3xl text-[12px] text-ink-3">
          A replay of a recorded conversation with the agent, on its real events; this site sends no messages. Run <span className="mono">data-agent web</span> to ask your own questions about your data.
        </div>
      }
    />
  )
}
