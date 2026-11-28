import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test } from 'vitest'

import type { ConfigSummary, LiveSource } from '../data/source'
import { configLine, NewRun } from './NewRun'

const DEFAULT: ConfigSummary = {
  name: 'default',
  layers: { skill: true, verifiers: true, context: true },
  model: 'deepseek-flash',
  effort: null,
  max_budget_usd: 1,
  max_turns: { query: 50, dbt: 200 },
  agent_timeout_sec: null,
  network: true,
}

describe('the run form picks no config (product-config ticket 144)', () => {
  test('the summary names the config, its layers, model and limits', () => {
    expect(configLine(DEFAULT)).toBe('config default · all layers on · deepseek-flash · spend cap 1 USD')
    const tight = { ...DEFAULT, name: 'team', layers: { skill: true, verifiers: false, context: true }, effort: 'max', agent_timeout_sec: 600 }
    expect(configLine(tight)).toBe('config team · layers: skill, context · deepseek-flash · effort max · spend cap 1 USD · time limit 600 s')
    expect(configLine({ ...DEFAULT, layers: { skill: false, verifiers: false, context: false } })).toContain('no layers')
  })

  test('the form has no config select', () => {
    const stubLive = { options: () => new Promise(() => {}) } as unknown as LiveSource
    const html = renderToStaticMarkup(createElement(NewRun, { live: stubLive, onStarted: () => {}, onClose: () => {} }))
    expect(html).not.toContain('Config')
    expect(html.match(/<select/g)?.length).toBe(2)
  })
})
