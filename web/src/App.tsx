// The app shell: the data source, the address, the theme and the top bar around the run page.

import { Monitor, Moon, Sun } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { staticSource, type RunData } from './data/source'
import type { RunNotes } from './run-view/model'
import { RunPage } from './run-page/RunPage'
import { readRoute, routeUrl, type Route } from './run-page/route'
import { useTheme } from './theme'

const BASE = import.meta.env.BASE_URL
const source = staticSource(`${BASE}data/`)

const currentRoute = () => readRoute(window.location.pathname, window.location.search, BASE)

export default function App() {
  const [route, setRoute] = useState<Route>(currentRoute)
  const [runs, setRuns] = useState<RunNotes[] | null>(null)
  const [loaded, setLoaded] = useState<Record<string, RunData>>({})
  const [error, setError] = useState<string | null>(null)
  const [theme, cycleTheme] = useTheme()

  useEffect(() => {
    const onPop = () => setRoute(currentRoute())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    source
      .listRuns()
      .then(setRuns)
      .catch((e: unknown) => setError(String(e)))
  }, [])

  const featured = runs?.filter((r) => r.featured) ?? []
  // The base address shows the first featured run (or the first run).
  const id = route.id ?? featured[0]?.id ?? runs?.[0]?.id ?? null
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
    const title = run?.notes.title
    document.title = title ? `${title} · data-agent` : 'data-agent'
  }, [run])

  const navigate = useCallback(
    (patch: Partial<Route>, push = false) => {
      setRoute((current) => {
        const next = { ...current, id, ...patch }
        const url = routeUrl(next, BASE)
        if (url !== window.location.pathname + window.location.search) {
          if (push) window.history.pushState(null, '', url)
          else window.history.replaceState(null, '', url)
        }
        return next
      })
    },
    [id],
  )

  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor
  return (
    <div className="h-full flex flex-col">
      <header className="flex items-center gap-4 px-4 min-h-11 py-1.5 border-b border-line bg-panel shrink-0 flex-wrap">
        <span className="mono font-semibold text-[13px] tracking-tight">data-agent</span>
        {featured.length > 0 && (
          <nav className="seg max-w-full overflow-x-auto" aria-label="Featured runs">
            {featured.map((r) => (
              <button
                type="button"
                key={r.id}
                className={r.id === id ? 'on' : ''}
                aria-current={r.id === id ? 'page' : undefined}
                onClick={() => navigate({ id: r.id ?? null, step: null, filter: 'all' }, true)}
                title={r.task ?? undefined}
              >
                {r.task_type && <span className="mono text-[10px] opacity-80 mr-1">{r.task_type}</span>}
                {r.title ?? r.id}
              </button>
            ))}
          </nav>
        )}
        <div className="flex-1" />
        <button type="button" className="btn" onClick={cycleTheme} title="Theme: system → light → dark" aria-label={`Theme: ${theme}`}>
          <ThemeIcon className="h-3 w-3" /> {theme}
        </button>
      </header>
      {error && <div className="banner banner-fail m-3">Could not load the run data: {error}</div>}
      {run ? (
        <RunPage key={id} run={run} route={{ ...route, id }} navigate={navigate} />
      ) : (
        !error && <div className="p-8 text-ink-3">{runs === null ? 'Loading runs…' : id === null ? 'No runs to show.' : `Loading ${id}…`}</div>
      )}
    </div>
  )
}
