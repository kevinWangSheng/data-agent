// The run page's address: `<base>runs/<id>?step=<id>&filter=<filter>`. The base is the build's base
// path (`/` locally, `/data-agent/` online), so links work under a sub-path.

export type Filter = 'all' | 'sql' | 'issues' | 'verifier'

export const FILTERS: readonly Filter[] = ['all', 'sql', 'issues', 'verifier']

export type Route = {
  /** The run's id; null at the base address. */
  id: string | null
  /** The selected outline row; null shows the key moments. */
  step: string | null
  filter: Filter
}

export function readRoute(pathname: string, search: string, base: string): Route {
  const root = base.endsWith('/') ? base : `${base}/`
  const path = `${pathname}/`.startsWith(root) ? pathname.slice(root.length) : null
  const match = path === null ? null : /^runs\/([^/]+)\/?$/.exec(path)
  const query = new URLSearchParams(search)
  const filter = query.get('filter') as Filter | null
  return {
    id: match ? decodeURIComponent(match[1]) : null,
    step: query.get('step'),
    filter: filter !== null && FILTERS.includes(filter) ? filter : 'all',
  }
}

export function routeUrl(route: Route, base: string): string {
  const root = base.endsWith('/') ? base : `${base}/`
  if (route.id === null) return root
  const query = new URLSearchParams()
  if (route.step !== null) query.set('step', route.step)
  if (route.filter !== 'all') query.set('filter', route.filter)
  const qs = query.toString()
  return `${root}runs/${encodeURIComponent(route.id)}${qs ? `?${qs}` : ''}`
}
