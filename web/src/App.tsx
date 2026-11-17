// The app shell: the data source, the address, the theme and the top bar around the pages. The
// build picks the data source (src/data/source.ts): `VITE_DATA_SOURCE=local` for `data-agent web`,
// the static files otherwise.
//
// Online pages: the base address is the first screen, which replays the short query run with the
// benchmark scores above it and stays at the base address while it plays; `runs/<id>` opens a run
// with everything shown; `runs` is the history list of the featured runs; `eval` is the evaluation
// page.
//
// Local pages: the base address (and `runs`) is the chat page, its conversation list on the left
// (every run started on its own shows there as a one-turn conversation); `chats/<id>` opens a
// conversation; `runs/<id>` is a run's three-pane page (each answer's full view). The bar keeps New
// run (dbt tasks and single runs), and a running run follows its events live and can be cancelled.

import { Monitor, Moon, Plus, Sun } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'

import { ChatPage } from './chat-page/ChatPage'
import { chatId, chatUrl } from './chat-page/route'
import { firstScreenRun, type Home } from './data/home'
import { isLive, localSource, notesOf, staticSource, type DataSource, type LiveSource, type RunData } from './data/source'
import { evalUrl, isEvalPath, readTrialFilter, type TrialFilter } from './eval-page/trials'
import { errorText } from './format'
import { HistoryPage } from './history-page/HistoryPage'
import { NewRun } from './local/NewRun'
import type { RunEvent, RunNotes } from './run-view/model'
import { RunPage } from './run-page/RunPage'
import { historyUrl, isHistoryPath, readRoute, routeUrl, type Route } from './run-page/route'
import { HomeStrip } from './site/HomeStrip'
import { Link } from './site/Link'
import { NewRunUnavailable } from './site/NewRunUnavailable'
import { useTheme } from './theme'

// The evaluation page and its chart library load only when it is opened, not on the first screen.
const EvalPage = lazy(() => import('./eval-page/EvalPage').then((m) => ({ default: m.EvalPage })))

const BASE = import.meta.env.BASE_URL
/** The online site's data source; null in the local build. */
const site = import.meta.env.VITE_DATA_SOURCE === 'local' ? null : staticSource(`${BASE}data/`)
const source: DataSource = site ?? localSource(`${BASE}api/`)
const live = source.live

type Place = { page: 'eval'; filter: TrialFilter } | { page: 'history' } | { page: 'chat'; id: string | null } | { page: 'run'; route: Route }

const currentPlace = (): Place => {
  const { pathname, search } = window.location
  if (site && isEvalPath(pathname, BASE)) return { page: 'eval', filter: readTrialFilter(search) }
  const chat = live ? chatId(pathname, BASE) : null
  if (chat !== null) return { page: 'chat', id: chat }
  const route = readRoute(pathname, search, BASE)
  if (live && (isHistoryPath(pathname, BASE) || route.id === null)) return { page: 'chat', id: null }
  if (isHistoryPath(pathname, BASE)) return { page: 'history' }
  return { page: 'run', route }
}

const runHref = (id: string) => routeUrl({ id, step: null, filter: 'all' }, BASE)
const chatHref = (id: string) => chatUrl(id, BASE)
const historyHref = live ? BASE : historyUrl(BASE)

type Loaded = { id: string; run?: RunData; error?: string }

/** Loads the run; a live one then follows its events until it ends. */
function useRun(id: string | null): [Loaded | null, (notes: RunNotes) => void] {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  /** Changes the loaded run, if it is still this one. */
  const update = useCallback(
    (change: (run: RunData) => RunData) =>
      setLoaded((current) => (current?.id === id && current.run ? { id, run: change(current.run) } : current)),
    [id],
  )

  useEffect(() => {
    if (id === null) return
    let gone = false
    let stop = () => {}
    setLoaded({ id })
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
      .catch((e: unknown) => !gone && setLoaded({ id, error: errorText(e) }))
    return () => {
      gone = true
      stop()
    }
  }, [id, update])

  const setNotes = useCallback((notes: RunNotes) => update((r) => ({ ...r, notes })), [update])
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
      setError(errorText(e))
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
  const [place, setPlace] = useState<Place>(currentPlace)
  const [home, setHome] = useState<Home | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [newRun, setNewRun] = useState(false)
  const [theme, cycleTheme] = useTheme()

  useEffect(() => {
    const onPop = () => setPlace(currentPlace())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    site
      ?.home()
      .then(setHome)
      .catch((e: unknown) => setError(errorText(e)))
  }, [])

  /** Goes to an in-app address, as a new history entry. */
  const go = useCallback((href: string) => {
    if (href !== window.location.pathname + window.location.search) window.history.pushState(null, '', href)
    setError(null)
    setPlace(currentPlace())
  }, [])

  const featured = home?.runs ?? []
  const route = place.page === 'run' ? place.route : null
  // Online the base address is the first screen (locally it is the history list).
  const onFirstScreen = route !== null && route.id === null
  const id = route === null ? null : (route.id ?? firstScreenRun(featured)?.id ?? null)
  const [loaded, setNotes] = useRun(id)
  const run = loaded?.run

  useEffect(() => {
    if (place.page === 'eval') return
    const title = place.page === 'history' ? 'Runs' : place.page === 'chat' ? 'Conversations' : onFirstScreen ? null : run?.notes.title
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

  const shownError = error ?? loaded?.error ?? null
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor
  const evalHref = evalUrl({ config: null, outcome: 'all' }, BASE)
  return (
    <div className="h-full flex flex-col">
      <header className="relative flex items-center gap-x-4 gap-y-1.5 px-4 min-h-11 py-1.5 border-b border-line bg-panel shrink-0 flex-wrap">
        <Link href={BASE} onGo={go} className="mono font-semibold text-[13px] tracking-tight hover:underline" title={live ? 'Conversations' : 'First screen'}>
          data-agent
        </Link>
        <Link
          href={historyHref}
          onGo={go}
          className={`text-[12.5px] hover:underline ${place.page === 'history' || place.page === 'chat' ? 'font-semibold text-ink' : 'text-ink-2'}`}
        >
          {live ? 'Conversations' : 'Runs'}
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
        {site && (
          <Link href={evalHref} onGo={go} className={`text-[12.5px] hover:underline ${place.page === 'eval' ? 'font-semibold text-ink' : 'text-ink-2'}`}>
            Evaluation
          </Link>
        )}
        <div className="flex-1" />
        {live ? (
          <button type="button" className={`btn ${newRun ? 'on' : ''}`} onClick={() => setNewRun((open) => !open)} aria-expanded={newRun}>
            <Plus className="h-3 w-3" /> New run
          </button>
        ) : (
          <NewRunUnavailable />
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
                go(runHref(runId))
              }}
            />
          </div>
        )}
      </header>
      {onFirstScreen && home && <HomeStrip home={home} evalHref={evalHref} onGo={go} />}
      {shownError && <div className="banner banner-fail m-3">Could not load the data: {shownError}</div>}
      {place.page === 'eval' && site ? (
        <Suspense fallback={<div className="p-8 text-ink-3">Loading the evaluation page…</div>}>
          <EvalPage source={site} filter={place.filter} onFilter={setFilter} runHref={runHref} onGo={go} />
        </Suspense>
      ) : place.page === 'chat' && live ? (
        <ChatPage live={live} id={place.id} chatHref={chatHref} runHref={runHref} onGo={go} />
      ) : place.page === 'history' ? (
        <HistoryPage source={source} runUrl={runHref} onOpen={(runId) => go(runHref(runId))} />
      ) : run && route && id !== null ? (
        <RunPage
          key={`${onFirstScreen ? 'first' : 'run'}:${id}`}
          run={run}
          route={{ ...route, id }}
          navigate={navigate}
          autoplay={onFirstScreen}
          actions={live && isLive(run) ? <CancelButton live={live} id={id} onCancelled={setNotes} /> : undefined}
        />
      ) : (
        !shownError && <div className="p-8 text-ink-3">{id === null ? 'Loading…' : `Loading ${id}…`}</div>
      )}
    </div>
  )
}
