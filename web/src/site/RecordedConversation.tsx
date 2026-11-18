// The online site's recorded conversation (spec decision 4, ticket 96): the chat page's transcript
// (../chat-page/Transcript.tsx) replayed on the run page's replay clock and controls, read only. It
// opens playing, skipping idle gaps, at the speed that plays it in about a minute, and stops with
// everything shown. There is no message box, and the agent's clarification options cannot be
// clicked: the site has no agent to send to (`chats/<id>`, src/App.tsx).

import { useEffect, useMemo, useRef, useState } from 'react'

import { recordingAt, recordingTimes, CONVERSATION_AUTOPLAY_MS } from '../chat-page/replay'
import { Transcript } from '../chat-page/Transcript'
import { buildConversationView } from '../chat-view/model'
import type { Recording, SiteSource } from '../data/source'
import { errorText } from '../format'
import { ReplayControls } from '../run-page/ReplayControls'
import { autoplaySpeed, replayClock, revealedAt, speedsWith } from '../run-page/replay'

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
  const [skipIdle, setSkipIdle] = useState(true)
  const clock = useMemo(() => replayClock(times, skipIdle), [times, skipIdle])
  const total = clock.at(-1) ?? 0
  // Two significant digits, so the speed reads as a short label, as on the run page.
  const [autoSpeed] = useState(() => Number(autoplaySpeed(replayClock(times, true).at(-1) ?? 0, CONVERSATION_AUTOPLAY_MS).toPrecision(2)))
  const speeds = useMemo(() => speedsWith(autoplay ? autoSpeed : null), [autoplay, autoSpeed])
  const [speed, setSpeed] = useState<number>(autoplay ? autoSpeed : 10)
  // The clock reading; null once everything is shown.
  const [reading, setReading] = useState<number | null>(autoplay ? 0 : null)
  const [playing, setPlaying] = useState(autoplay)
  const stick = useRef(true)

  const revealed = reading === null ? times.length : revealedAt(clock, reading)
  const replaying = revealed < times.length
  const view = useMemo(() => {
    const frame = recordingAt(recording, revealed)
    return buildConversationView(frame.index, frame.runs)
  }, [recording, revealed])

  // The run page's replay loop (../run-page/RunPage.tsx).
  const lastFrame = useRef<number | null>(null)
  useEffect(() => {
    if (!playing) return
    let frame = 0
    const tick = (now: number) => {
      const dt = lastFrame.current === null ? 0 : now - lastFrame.current
      lastFrame.current = now
      setReading((r) => {
        if (r === null) return null
        const next = r + dt * speed
        return next >= total ? null : next
      })
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
      lastFrame.current = null
    }
  }, [playing, speed, total])
  useEffect(() => {
    if (playing && reading === null) setPlaying(false)
  }, [playing, reading])

  const play = (from: number | null) => {
    if (from !== null) setReading(from)
    stick.current = true
    setPlaying(true)
  }

  return (
    <Transcript
      view={view}
      title={recording.conversation.title}
      stick={stick}
      canAnswer={() => false}
      onPick={() => {}}
      runHref={runHref}
      onGo={onGo}
      bar={
        <>
          {replaying && <span className="chip chip-info">REPLAY</span>}
          <ReplayControls
            elapsed={reading ?? total}
            totalMs={total}
            playing={playing}
            showingAll={reading === null}
            speed={speed}
            speeds={speeds}
            skipIdle={skipIdle}
            onSpeedChange={setSpeed}
            onTogglePlay={() => (playing ? setPlaying(false) : play(reading === null ? 0 : null))}
            onRestart={() => play(0)}
            onScrub={(ms) => {
              setReading(ms >= total ? null : ms)
              if (ms >= total) setPlaying(false)
            }}
            onSkipIdleChange={(skip) => {
              // Stay on the same event when the clock changes under the replay.
              if (reading !== null) setReading(replayClock(times, skip)[revealed - 1] ?? 0)
              setSkipIdle(skip)
            }}
            onShowAll={() => {
              setPlaying(false)
              setReading(null)
            }}
          />
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
