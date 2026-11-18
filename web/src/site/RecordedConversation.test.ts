// The online site's recorded conversation (ticket 96): the chat page's transcript, replayed, with no
// way to send a message. Rendered to markup (react-dom/server) from the static data source over the
// layout `evals export --conversation` writes, on the hand-made conversation of
// tests/fixtures/chat/conversation/.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test } from 'vitest'

import { Transcript } from '../chat-page/Transcript'
import { buildConversationView, type ConversationIndex } from '../chat-view/model'
import { homeData } from '../data/home'
import { staticSource, type Recording } from '../data/source'
import type { RunNotes } from '../run-view/model'
import { RecordedConversation } from './RecordedConversation'

const CHAT = join(import.meta.dirname, '../../../tests/fixtures/chat/conversation')
const index = JSON.parse(readFileSync(join(CHAT, 'conversation.json'), 'utf8')) as ConversationIndex
const CANCELLED = index.turns[3].run_id
const TITLE = 'Revenue, then customers'

/** The export's files as the site serves them: home.json (made as site-data.ts makes it) and each
 *  turn's run. */
function exported(): Record<string, string> {
  const featured: RunNotes = { id: 'q', featured: true, task_type: 'query' }
  const home = homeData({ runs: [featured], conversations: [{ ...index, title: TITLE }] }, null)
  const files: Record<string, string> = { 'home.json': JSON.stringify(home) }
  for (const t of index.turns) {
    const meta: RunNotes = { id: t.run_id, title: t.message, featured: false, cancelled: t.run_id === CANCELLED, failed: false }
    files[`runs/${t.run_id}/meta.json`] = JSON.stringify(meta)
    files[`runs/${t.run_id}/events.jsonl`] = readFileSync(join(CHAT, t.run_id, 'events.jsonl'), 'utf8')
  }
  return files
}

const fakeFetch = (files: Record<string, string>): typeof fetch =>
  (async (url: string) => {
    const body = files[url.replace(/^\/data\//, '')]
    return new Response(body ?? 'Not Found', { status: body === undefined ? 404 : 200 })
  }) as typeof fetch

const site = staticSource('/data/', fakeFetch(exported()))

const render = (recording: Recording, autoplay: boolean) =>
  renderToStaticMarkup(createElement(RecordedConversation, { recording, autoplay, runHref: (id: string) => `/runs/${id}`, onGo: () => {} }))

const count = (html: string, testId: string) => html.split(`data-testid="${testId}"`).length - 1

describe('the static source', () => {
  test("reads a recorded conversation and every turn's run", async () => {
    const { conversation, runs } = await site.recording(index.id)
    expect(conversation.title).toBe(TITLE)
    expect(Object.keys(runs)).toEqual(index.turns.map((t) => t.run_id))
    expect(runs[CANCELLED].notes?.cancelled).toBe(true)
    expect(runs[index.turns[0].run_id].events.length).toBeGreaterThan(0)
  })

  test('an id home.json does not list is refused', async () => {
    await expect(site.recording('nope')).rejects.toThrow('no recorded conversation nope')
  })
})

describe('RecordedConversation', () => {
  test('opens replaying: the first turn only, with the replay controls', async () => {
    const html = render(await site.recording(index.id), true)
    expect(count(html, 'turn')).toBe(1)
    expect(count(html, 'replay-controls')).toBe(1)
    expect(html).toContain('REPLAY')
    expect(html).toContain(TITLE)
  })

  test('shown whole: every turn, the answers and the question the agent asked', async () => {
    const html = render(await site.recording(index.id), false)
    expect(count(html, 'turn')).toBe(index.turns.length)
    expect(count(html, 'clarification')).toBe(1)
    expect(count(html, 'answer')).toBeGreaterThan(0)
    expect(html).not.toContain('REPLAY')
  })

  test('no way to send a message: no message box, no send button, the options cannot be clicked', async () => {
    for (const autoplay of [true, false]) {
      const html = render(await site.recording(index.id), autoplay)
      expect(html).not.toContain('<textarea')
      expect(count(html, 'chat-input')).toBe(0)
      expect(count(html, 'chat-send')).toBe(0)
      expect(html).not.toMatch(/<button[^>]*>\s*(<svg[^]*?<\/svg>)?\s*Send/)
    }
    const whole = render(await site.recording(index.id), false)
    const clarification = whole.slice(whole.indexOf('data-testid="clarification"'))
    const options = clarification.slice(0, clarification.indexOf('</section>')).match(/<button[^>]*>/g) ?? []
    expect(options.length).toBeGreaterThan(0)
    for (const option of options) expect(option).toContain('disabled')
    expect(whole).toContain('replay of a recorded conversation')
  })

  test('the cancelled turn reads "Cancelled." with no word of a next message, which the site cannot send', async () => {
    const recording = await site.recording(index.id)
    const html = render(recording, false)
    expect(html).toContain('Cancelled.</div>')
    expect(html).not.toContain('next message')
    // The live chat page, with its message box, still says what the next message does.
    const view = buildConversationView(recording.conversation, recording.runs)
    const live = renderToStaticMarkup(createElement(Transcript, { view, title: TITLE, stick: { current: true }, runHref: (id: string) => id, onGo: () => {}, footer: null }))
    expect(live).toContain('Cancelled. The next message starts a new turn.')
  })
})
