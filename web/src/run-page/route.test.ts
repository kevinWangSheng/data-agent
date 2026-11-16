import { describe, expect, test } from 'vitest'

import { readRoute, routeUrl } from './route'

describe('readRoute', () => {
  test('a run under the root base', () => {
    expect(readRoute('/runs/query-shop', '', '/')).toEqual({ id: 'query-shop', step: null, filter: 'all' })
  })

  test('a run under a sub-path base, with a step and a filter', () => {
    expect(readRoute('/data-agent/runs/2026-11-06T16-38_ab12', '?step=call_1&filter=sql', '/data-agent/')).toEqual({
      id: '2026-11-06T16-38_ab12',
      step: 'call_1',
      filter: 'sql',
    })
  })

  test('the base itself names no run', () => {
    expect(readRoute('/data-agent/', '', '/data-agent/')).toEqual({ id: null, step: null, filter: 'all' })
    expect(readRoute('/data-agent', '', '/data-agent/')).toEqual({ id: null, step: null, filter: 'all' })
  })

  test('a path outside the base names no run', () => {
    expect(readRoute('/runs/x', '', '/data-agent/').id).toBeNull()
  })

  test('an unknown filter reads as all', () => {
    expect(readRoute('/runs/x', '?filter=bogus', '/').filter).toBe('all')
  })

  test('decodes an escaped run id', () => {
    expect(readRoute('/runs/a%20b', '', '/').id).toBe('a b')
  })
})

describe('routeUrl', () => {
  test('round-trips through readRoute', () => {
    const route = { id: 'a b', step: 'call_1', filter: 'issues' as const }
    const url = new URL(routeUrl(route, '/data-agent/'), 'http://x')
    expect(url.pathname).toBe('/data-agent/runs/a%20b')
    expect(readRoute(url.pathname, url.search, '/data-agent/')).toEqual(route)
  })

  test('leaves out the defaults', () => {
    expect(routeUrl({ id: 'r', step: null, filter: 'all' }, '/')).toBe('/runs/r')
    expect(routeUrl({ id: null, step: null, filter: 'all' }, '/data-agent/')).toBe('/data-agent/')
  })
})
