// The chat page's main flow in a browser (.scratch/chat-ui/spec.md "Testing Decisions",
// "浏览器"): on the page, send a message, watch the step cards appear while the agent works, click a
// clarification option, ask a follow-up. A real backend and model (playwright.config.ts); no style
// is asserted. Written by ticket 88; the main flow is expected to fail (TICKET_95) until ticket 95
// builds the page, which then turns `TICKET_95(...)` into `test(...)` and changes nothing else. The
// test that the server serves the built UI is not marked: it passes now.
//
// The page's contract with this test is its test ids (data-testid), not its look:
//   new-conversation   button: starts a conversation
//   chat-input         textbox: the message being written
//   chat-send          button: sends it
//   turn               one per turn, in order; inside it:
//     turn-message       the user message that started the turn
//     step-card          one card per tool step, shown while the turn runs
//     clarification      the question the turn ended with; its options are the buttons inside it
//     answer             the turn's final answer
//
// The data and the numbers are the end-to-end tests' (tests/acceptance/test_chat_e2e.py,
// tests/fixtures/chat/orders.sql).

import { expect, type Locator, type Page, test } from '@playwright/test'

const TURN_TIMEOUT = 15 * 60_000

const REVENUE_QUESTION = 'What was the total revenue from orders placed in 2024?'
// SELECT sum(amount) FROM sales.orders WHERE year(order_date) = 2024 [AND status <> 'refunded']
const NET_2024 = 1474.5
const GROSS_2024 = 2121.75
const FOLLOW_UP = 'How many orders does that figure cover?'
// SELECT count(*) FROM sales.orders WHERE year(order_date) = 2024 [AND status <> 'refunded']
const NET_ORDERS_2024 = 12
const GROSS_ORDERS_2024 = 18

const NET_WORDS = /\bnet\b|exclud|without|minus|subtract|deduct|less refund/i
const GROSS_WORDS = /\bgross\b|includ|all orders|regardless/i

function numbersIn(text: string): number[] {
  return (text.match(/-?\d[\d,]*(?:\.\d+)?/g) ?? []).map((n) => Number(n.replaceAll(',', '')))
}

function mentions(text: string, value: number): boolean {
  return numbersIn(text).some((n) => Math.abs(n - value) <= 0.01)
}

/** An option that clearly reads as net or gross revenue, net first. */
function choose(options: string[]): { option: string; net: boolean } {
  for (const [words, other, net] of [
    [NET_WORDS, GROSS_WORDS, true],
    [GROSS_WORDS, NET_WORDS, false],
  ] as const) {
    const option = options.find((o) => words.test(o) && !other.test(o))
    if (option !== undefined) return { option, net }
  }
  throw new Error(`no option reads as net or gross revenue: ${JSON.stringify(options)}`)
}

type ConversationRow = {
  single_run: boolean
  running: boolean
  turns: { message: string | null; run_id: string; status: string }[]
}

/** The backend's row of the conversation this message started (`GET /api/conversations`,
 *  tests/acceptance/test_chat_api.py). */
async function conversationStartedBy(page: Page, message: string): Promise<ConversationRow> {
  const response = await page.request.get('/api/conversations')
  expect(response.status()).toBe(200)
  const rows = (await response.json()) as ConversationRow[]
  const row = rows.find((r) => !r.single_run && r.turns[0]?.message === message)
  if (!row) throw new Error(`no conversation started with ${JSON.stringify(message)}`)
  return row
}

/** When the run wrote its `result` event, in milliseconds since the epoch. */
async function resultTime(page: Page, runId: string): Promise<number> {
  const response = await page.request.get(`/api/runs/${runId}/events`)
  expect(response.status()).toBe(200)
  const { events } = (await response.json()) as { events: { type: string; ts: string }[] }
  const result = events.find((e) => e.type === 'result')
  if (!result) throw new Error(`run ${runId} has no result event`)
  return Date.parse(result.ts)
}

/** Until the conversation's turn has ended and its process exited: the next message is a new turn. */
async function waitIdle(page: Page, message: string): Promise<void> {
  await expect
    .poll(async () => (await conversationStartedBy(page, message)).running, { timeout: TURN_TIMEOUT })
    .toBe(false)
}

async function send(input: Locator, button: Locator, text: string): Promise<void> {
  await input.fill(text)
  await button.click()
}

// Not expected to fail: the server is up and serves the built UI, so the main flow's failure is
// the chat page's own.
test('the server serves the built UI', async ({ page }) => {
  const response = await page.goto('/')
  expect(response?.status()).toBe(200)
})

test('send, see the cards live, pick a clarification option, follow up', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('new-conversation').click()
  const input = page.getByTestId('chat-input')
  const sendButton = page.getByTestId('chat-send')
  const turns = page.getByTestId('turn')

  // 1. The question; its first step card is on the page before the turn writes its `result`.
  await send(input, sendButton, REVENUE_QUESTION)
  const first = turns.nth(0)
  await expect(first.getByTestId('turn-message')).toHaveText(REVENUE_QUESTION)
  await expect(first.getByTestId('step-card').first()).toBeVisible({ timeout: TURN_TIMEOUT })
  const cardShownAt = Date.now()

  // 2. The turn ends with a question; one of its options is clicked.
  const clarification = first.getByTestId('clarification')
  await expect(clarification).toBeVisible({ timeout: TURN_TIMEOUT })
  await waitIdle(page, REVENUE_QUESTION)
  const [firstTurn] = (await conversationStartedBy(page, REVENUE_QUESTION)).turns
  expect(cardShownAt).toBeLessThan(await resultTime(page, firstTurn.run_id))
  const options = await clarification.getByRole('button').allInnerTexts()
  expect(options.length).toBeGreaterThanOrEqual(2)
  expect(options.length).toBeLessThanOrEqual(5)
  const { option, net } = choose(options.map((o) => o.trim()))
  await clarification.getByRole('button', { name: option, exact: true }).click()

  // The clicked option is the next user message, and the answer follows it.
  const second = turns.nth(1)
  await expect(second.getByTestId('turn-message')).toHaveText(option)
  const answer = second.getByTestId('answer')
  await expect(answer).toBeVisible({ timeout: TURN_TIMEOUT })
  expect(mentions(await answer.innerText(), net ? NET_2024 : GROSS_2024)).toBe(true)

  // 3. A follow-up that only makes sense after the answer.
  await waitIdle(page, REVENUE_QUESTION)
  await send(input, sendButton, FOLLOW_UP)
  const third = turns.nth(2)
  await expect(third.getByTestId('turn-message')).toHaveText(FOLLOW_UP)
  const followUp = third.getByTestId('answer')
  await expect(followUp).toBeVisible({ timeout: TURN_TIMEOUT })
  expect(mentions(await followUp.innerText(), net ? NET_ORDERS_2024 : GROSS_ORDERS_2024)).toBe(true)
  await expect(turns).toHaveCount(3)
})
