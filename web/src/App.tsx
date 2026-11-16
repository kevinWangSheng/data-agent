// The app shell: the data source, the address, the theme and the top bar around the pages.
//
// Online pages: the base address is the first screen, which replays the short query run with the
// benchmark scores above it and stays at the base address while it plays; `runs/<id>` opens a run
// with everything shown; `eval` is the evaluation page.

import { Monitor, Moon, Sun } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'

import { firstScreenRun, type Home } from './data/home'
import { staticSource, type RunData } from './data/source'
import { evalUrl, isEvalPath, readTrialFilter, type TrialFilter } from './eval-page/trials'
import { RunPage } from './run-page/RunPage'
import { readRoute, routeUrl, type Route } from './run-page/route'
import { HomeStrip } from './site/HomeStrip'
import { Link } from './site/Link'
import { NewRunUnavailable } from './site/NewRunUnavailable'
import { useTheme } from './theme'

// The evaluation page and its chart library load only when it is opened, not on the first screen.
const EvalPage = lazy(() => import('./eval-page/EvalPage').then((m) => ({ default: m.EvalPage })))

const BASE = import.meta.env.BASE_URL
const source = staticSource(`${BASE}data/`)

type Place = { page: 'eval'; filter: TrialFilter } | { page: 'run'; route: Route }

const currentPlace = (): Place =>
  isEvalPath(window.location.pathname, BASE)
    ? { page: 'eval', filter: readTrialFilter(window.location.search) }
    : { page: 'run', route: readRoute(window.location.pathname, window.location.search, BASE) }

const runHref = (id: string) => routeUrl({ id, step: null, filter: 'all' }, BASE)

export default function App() {
  const [place, setPlace] = useState<Place>(currentPlace)
  const [home, setHome] = useState<Home | null>(null)
  const [loaded, setLoaded] = useState<Record<string, RunData>>({})
  const [error, setError] = useState<string | null>(null)
  const [theme, cycleTheme] = useTheme()

  useEffect(() => {
    const onPop = () => setPlace(currentPlace())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    source
      .home()
      .then(setHome)
      .catch((e: unknown) => setError(String(e)))
  }, [])

  /** Goes to an in-app address, as a new history entry. */
  const go = useCallback((href: string) => {
    if (href !== window.location.pathname + window.location.search) window.history.pushState(null, '', href)
    setError(null)
    setPlace(currentPlace())
  }, [])

  const featured = home?.runs ?? []
  const route = place.page === 'run' ? place.route : null
  const onFirstScreen = route !== null && route.id === null
  const id = route === null ? null : (route.id ?? firstScreenRun(featured)?.id ?? null)
  const run = id !== null ? loaded[id] : undefined

  useEffect(() => {
    if (id === null || loaded[id]) return
    setError(null)
    source
      .loadRun(id)
      .then((data) => setLoaded((all) => ({ ...all, [id]: data })))
      .catch((e: unknown) => setError(String(e)))
  }, [id, loaded])

  useEffect(() => {
    if (place.page !== 'run') return
    const title = onFirstScreen ? null : run?.notes.title
    document.title = title ? `${title} · data-agent` : 'data-agent'
  }, [place.page, onFirstScreen, run])

  // A run page's own changes (selected step, filter) replace the address. The first screen keeps
  // the base address: its route has no id, and routeUrl gives the base for that.
  const navigate = useCallback((patch: Partial<Route>, push = false) => {
    setPlace((current) => {
      if (current.page !== 'run') return current
      const next = { ...current.route, ...patch }
      const url = routeUrl(next, BASE)
      if (url !== window.location.pathname + window.location.search) {
        if (push) window.history.pushState(null, '', url)
        else window.history.replaceState(null, '', url)
      }
      return { page: 'run', route: next }
    })
  }, [])

  const setFilter = useCallback((filter: TrialFilter) => {
    window.history.replaceState(null, '', evalUrl(filter, BASE))
    setPlace({ page: 'eval', filter })
  }, [])

  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor
  const evalHref = evalUrl({ config: null, outcome: 'all' }, BASE)
  return (
    <div className="h-full flex flex-col">
      <header className="flex items-center gap-x-4 gap-y-1.5 px-4 min-h-11 py-1.5 border-b border-line bg-panel shrink-0 flex-wrap">
        <Link href={BASE} onGo={go} className="mono font-semibold text-[13px] tracking-tight hover:underline" title="First screen">
          data-agent
        </Link>
        {featured.length > 0 && (
          <nav className="seg max-w-full overflow-x-auto" aria-label="Featured runs">
            {featured.map((r) => (
              <button
                type="button"
                key={r.id}
                className={!onFirstScreen && r.id === id ? 'on' : ''}
                aria-current={!onFirstScreen && r.id === id ? 'page' : undefined}
                onClick={() => r.id && go(runHref(r.id))}
                title={r.task ?? undefined}
              >
                {r.task_type && <span className="mono text-[10px] opacity-80 mr-1">{r.task_type}</span>}
                {r.title ?? r.id}
              </button>
            ))}
          </nav>
        )}
        <Link href={evalHref} onGo={go} className={`text-[12.5px] hover:underline ${place.page === 'eval' ? 'font-semibold text-ink' : 'text-ink-2'}`}>
          Evaluation
        </Link>
        <div className="flex-1" />
        <NewRunUnavailable />
        <button type="button" className="btn" onClick={cycleTheme} title="Theme: system → light → dark" aria-label={`Theme: ${theme}`}>
          <ThemeIcon className="h-3 w-3" /> {theme}
        </button>
      </header>
      {onFirstScreen && home && <HomeStrip home={home} evalHref={evalHref} onGo={go} />}
      {error && <div className="banner banner-fail m-3">Could not load the data: {error}</div>}
      {place.page === 'eval' ? (
        <Suspense fallback={<div className="p-8 text-ink-3">Loading the evaluation page…</div>}>
          <EvalPage source={source} filter={place.filter} onFilter={setFilter} runHref={runHref} onGo={go} />
        </Suspense>
      ) : run && route ? (
        <RunPage key={`${onFirstScreen ? 'first' : 'run'}:${id}`} run={run} route={{ ...route, id }} navigate={navigate} autoplay={onFirstScreen} />
      ) : (
        !error && <div className="p-8 text-ink-3">{id === null ? 'Loading…' : `Loading ${id}…`}</div>
      )}
    </div>
  )
}
