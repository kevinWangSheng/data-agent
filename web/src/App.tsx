// The app shell: the data source, the address, the theme and the top bar around the pages. The
// build picks the data source (src/data/source.ts): `VITE_DATA_SOURCE=local` for `data-agent web`,
// the static files otherwise. Locally the base address is the history list, the bar has New run,
// and a running run follows its events live and can be cancelled.

import { Monitor, Moon, Plus, Sun } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { isLive, localSource, notesOf, staticSource, type DataSource, type LiveSource, type RunData } from './data/source'
import { HistoryPage } from './local/HistoryPage'
import { NewRun } from './local/NewRun'
import type { RunEvent, RunNotes } from './run-view/model'
import { RunPage } from './run-page/RunPage'
import { readRoute, routeUrl, type Route } from './run-page/route'
import { useTheme } from './theme'

const BASE = import.meta.env.BASE_URL
const source: DataSource = import.meta.env.VITE_DATA_SOURCE === 'local' ? localSource(`${BASE}api/`) : staticSource(`${BASE}data/`)
const live = source.live

const currentRoute = () => readRoute(window.location.pathname, window.location.search, BASE)
const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

type Loaded = { id: string; run?: RunData; error?: string }

/** Loads the run; a live one then follows its events until it ends. */
function useRun(id: string | null): [Loaded | null, (notes: RunNotes) => void] {
  const [loaded, setLoaded] = useState<Loaded | null>(null)

  useEffect(() => {
    if (id === null) return
    let gone = false
    let stop = () => {}
    setLoaded({ id })
    const update = (change: (run: RunData) => RunData) =>
      setLoaded((current) => (current?.id === id && current.run ? { id, run: change(current.run) } : current))
    source
      .loadRun(id)
      .then((run) => {
        if (gone) return
        setLoaded({ id, run })
        if (!live || !isLive(run)) return
        stop = live.follow(id, run.events.at(-1)?.seq ?? -1, {
          events: (batch: RunEvent[]) => update((r) => ({ ...r, events: [...r.events, ...batch] })),
          notes: (notes) => update((r) => ({ ...r, notes })),
        })
      })
      .catch((e: unknown) => !gone && setLoaded({ id, error: message(e) }))
    return () => {
      gone = true
      stop()
    }
  }, [id])

  const setNotes = useCallback(
    (notes: RunNotes) => setLoaded((current) => (current?.id === id && current.run ? { id, run: { ...current.run, notes } } : current)),
    [id],
  )
  return [loaded?.id === id ? loaded : null, setNotes]
}

function CancelButton({ live, id, onCancelled }: { live: LiveSource; id: string; onCancelled: (notes: RunNotes) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const cancel = async () => {
    setBusy(true)
    setError(null)
    try {
      onCancelled(notesOf(await live.cancel(id)))
    } catch (e: unknown) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <button type="button" className="btn btn-sm" onClick={cancel} disabled={busy}>
        {busy ? 'Cancelling…' : 'Cancel run'}
      </button>
      {error && (
        <span className="text-[11.5px] text-fail" role="alert">
          {error}
        </span>
      )}
    </>
  )
}

export default function App() {
  const [route, setRoute] = useState<Route>(currentRoute)
  const [runs, setRuns] = useState<RunNotes[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [newRun, setNewRun] = useState(false)
  const [theme, cycleTheme] = useTheme()

  useEffect(() => {
    const onPop = () => setRoute(currentRoute())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    // Locally the base address is the history list, which reads the runs itself.
    if (live) return
    source
      .listRuns()
      .then(setRuns)
      .catch((e: unknown) => setError(String(e)))
  }, [])

  const featured = runs?.filter((r) => r.featured) ?? []
  // The static site's base address shows the first featured run (or the first run).
  const id = live ? route.id : (route.id ?? featured[0]?.id ?? runs?.[0]?.id ?? null)
  const [loaded, setNotes] = useRun(id)
  const run = loaded?.run

  useEffect(() => {
    const title = run?.notes.title ?? (live && id === null ? 'Runs' : null)
    document.title = title ? `${title} · data-agent` : 'data-agent'
  }, [run, id])

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
  const openRun = useCallback((runId: string | null) => navigate({ id: runId, step: null, filter: 'all' }, true), [navigate])

  const shownError = error ?? loaded?.error ?? null
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor
  return (
    <div className="h-full flex flex-col">
      <header className="relative flex items-center gap-4 px-4 min-h-11 py-1.5 border-b border-line bg-panel shrink-0 flex-wrap">
        {live ? (
          <a
            href={routeUrl({ id: null, step: null, filter: 'all' }, BASE)}
            className="mono font-semibold text-[13px] tracking-tight hover:underline"
            title="All runs"
            onClick={(e) => {
              e.preventDefault()
              openRun(null)
            }}
          >
            data-agent
          </a>
        ) : (
          <span className="mono font-semibold text-[13px] tracking-tight">data-agent</span>
        )}
        {live && (
          <nav className="seg" aria-label="Pages">
            <button type="button" className={id === null ? 'on' : ''} aria-current={id === null ? 'page' : undefined} onClick={() => openRun(null)}>
              Runs
            </button>
          </nav>
        )}
        {featured.length > 0 && (
          <nav className="seg max-w-full overflow-x-auto" aria-label="Featured runs">
            {featured.map((r) => (
              <button
                type="button"
                key={r.id}
                className={r.id === id ? 'on' : ''}
                aria-current={r.id === id ? 'page' : undefined}
                onClick={() => openRun(r.id ?? null)}
                title={r.task ?? undefined}
              >
                {r.task_type && <span className="mono text-[10px] opacity-80 mr-1">{r.task_type}</span>}
                {r.title ?? r.id}
              </button>
            ))}
          </nav>
        )}
        <div className="flex-1" />
        {live && (
          <button type="button" className={`btn ${newRun ? 'on' : ''}`} onClick={() => setNewRun((open) => !open)} aria-expanded={newRun}>
            <Plus className="h-3 w-3" /> New run
          </button>
        )}
        <button type="button" className="btn" onClick={cycleTheme} title="Theme: system → light → dark" aria-label={`Theme: ${theme}`}>
          <ThemeIcon className="h-3 w-3" /> {theme}
        </button>
        {live && newRun && (
          <div className="absolute right-3 top-full mt-1.5 z-20">
            <NewRun
              live={live}
              onClose={() => setNewRun(false)}
              onStarted={(runId) => {
                setNewRun(false)
                openRun(runId)
              }}
            />
          </div>
        )}
      </header>
      {shownError && <div className="banner banner-fail m-3">Could not load the run data: {shownError}</div>}
      {live && id === null ? (
        <HistoryPage live={live} runUrl={(runId) => routeUrl({ id: runId, step: null, filter: 'all' }, BASE)} onOpen={openRun} />
      ) : run && id !== null ? (
        <RunPage
          key={id}
          run={run}
          route={{ ...route, id }}
          navigate={navigate}
          actions={live && isLive(run) ? <CancelButton live={live} id={id} onCancelled={setNotes} /> : undefined}
        />
      ) : (
        !shownError && (
          <div className="p-8 text-ink-3">{!live && runs === null ? 'Loading runs…' : id === null ? 'No runs to show.' : `Loading ${id}…`}</div>
        )
      )}
    </div>
  )
}
