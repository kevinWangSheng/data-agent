// The chat page's main flow in a browser (.scratch/chat-ui/spec.md "Testing Decisions",
// "浏览器"): on the page, send a message, watch the step cards appear while the agent works, click a
// clarification option, ask a follow-up. A real backend and model (playwright.config.ts); no style
// is asserted. Written by ticket 88; the test is expected to fail (TICKET_95) until ticket 95 builds
// the page, which then turns `TICKET_95(...)` into `test(...)` and changes nothing else.
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

import { expect, type Locator, test } from '@playwright/test'

const TICKET_95 = test.fail

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

function states(text: string, value: number): boolean {
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

async function send(input: Locator, button: Locator, text: string): Promise<void> {
  await input.fill(text)
  await button.click()
}

TICKET_95('send, see the cards live, pick a clarification option, follow up', async ({ page }) => {
  // The server serves the built UI: whatever fails below is the chat page's.
  const response = await page.goto('/')
  expect(response?.status()).toBe(200)
  await page.getByTestId('new-conversation').click()
  const input = page.getByTestId('chat-input')
  const sendButton = page.getByTestId('chat-send')
  const turns = page.getByTestId('turn')

  // 1. The question; its step cards show up while the agent is still working.
  await send(input, sendButton, REVENUE_QUESTION)
  const first = turns.nth(0)
  await expect(first.getByTestId('turn-message')).toHaveText(REVENUE_QUESTION)
  await expect(first.getByTestId('step-card').first()).toBeVisible({ timeout: TURN_TIMEOUT })
  expect(await first.getByTestId('answer').count()).toBe(0)

  // 2. The turn ends with a question; one of its options is clicked.
  const clarification = first.getByTestId('clarification')
  await expect(clarification).toBeVisible({ timeout: TURN_TIMEOUT })
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
  expect(states(await answer.innerText(), net ? NET_2024 : GROSS_2024)).toBe(true)

  // 3. A follow-up that only makes sense after the answer.
  await send(input, sendButton, FOLLOW_UP)
  const third = turns.nth(2)
  await expect(third.getByTestId('turn-message')).toHaveText(FOLLOW_UP)
  const followUp = third.getByTestId('answer')
  await expect(followUp).toBeVisible({ timeout: TURN_TIMEOUT })
  expect(states(await followUp.innerText(), net ? NET_ORDERS_2024 : GROSS_ORDERS_2024)).toBe(true)
  await expect(turns).toHaveCount(3)
})
