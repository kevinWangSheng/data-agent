import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test } from 'vitest'

import type { ConfigSummary, LiveSource } from '../data/source'
import { askedNetwork, configLine, NetworkBox, NewRun } from './NewRun'

const DEFAULT: ConfigSummary = {
  name: 'default',
  layers: { skill: true, verifiers: true, context: true },
  model: 'deepseek-flash',
  effort: null,
  max_budget_usd: 1,
  max_turns: { query: 50, dbt: 200, large: 75 },
  agent_timeout_sec: null,
  network: true,
}

describe('the run form picks no config (product-config ticket 144)', () => {
  test('the summary names the config, its layers, model and limits', () => {
    expect(configLine(DEFAULT)).toBe(
      'config default · all layers on · deepseek-flash · spend cap 1 USD · max turns query 50, dbt 200, large 75',
    )
    const tight = {
      ...DEFAULT,
      name: 'team',
      layers: { skill: true, verifiers: false, context: true },
      effort: 'max',
      agent_timeout_sec: 600,
      max_turns: { query: 5, dbt: null, large: null },
    }
    expect(configLine(tight)).toBe(
      'config team · layers: skill, context · deepseek-flash · effort max · spend cap 1 USD · max turns query 5 · time limit 600 s',
    )
    expect(configLine({ ...DEFAULT, layers: { skill: false, verifiers: false, context: false } })).toContain('no layers')
    expect(configLine({ ...DEFAULT, max_turns: { query: null, dbt: null, large: null } })).not.toContain('max turns')
  })

  test('the form has no config select', () => {
    const stubLive = { options: () => new Promise(() => {}) } as unknown as LiveSource
    const html = renderToStaticMarkup(createElement(NewRun, { live: stubLive, onStarted: () => {}, onClose: () => {} }))
    // The old form's label and select (`Config` above a select of the run configs).
    expect(html).not.toMatch(/>\s*Config\s*</)
    expect(html.match(/<select/g)?.length).toBe(2)
  })
})

describe('the network box follows the server switch', () => {
  const box = (allowed: boolean | null, checked: boolean) =>
    renderToStaticMarkup(createElement(NetworkBox, { allowed, checked, onChange: () => {} }))

  test('a server with the network off: the box is off, disabled and says why', () => {
    const html = box(false, true)
    expect(html).toMatch(/<input[^>]*disabled/)
    expect(html).not.toMatch(/<input[^>]*checked/)
    expect(html).toContain('off on this server')
    expect(html).not.toContain('cursor-pointer')
  })

  test('a server with the network on: the box is as the user leaves it', () => {
    expect(box(true, true)).toMatch(/<input[^>]*checked/)
    expect(box(true, true)).not.toMatch(/<input[^>]*disabled/)
    expect(box(true, false)).not.toMatch(/<input[^>]*checked/)
    expect(box(true, true)).not.toContain('off on this server')
    expect(box(true, true)).toContain('cursor-pointer')
  })

  test('a run never asks a server with the network off for it; else it asks as the box is', () => {
    expect(askedNetwork(true, false)).toBe(false)
    expect(askedNetwork(true, true)).toBe(true)
    expect(askedNetwork(false, true)).toBe(false)
    expect(askedNetwork(true, null)).toBe(true)
  })

  test('before the options load, the box is not yet disabled', () => {
    expect(box(null, true)).not.toMatch(/<input[^>]*disabled/)
  })
})
