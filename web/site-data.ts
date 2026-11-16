// The site's data at `<base>data/`, in the layout the static data source reads (src/data/source.ts).
//
// - `SITE_DATA=<dir>`: an export of `evals export` (evals/export.py). `pnpm dev` and `pnpm preview`
//   serve it; `pnpm build` copies it into `dist/data/`, all but `index.json`. Either way
//   `home.json`, the first screen's data (src/data/home.ts), is made from its `index.json` and
//   `eval.json`.
// - Without it: the test fixture runs (tests/fixtures/web/real/) for `pnpm dev` and `pnpm preview`,
//   all featured and without an evaluation summary; `pnpm build` writes no data.
//
// The published site's build gets the export from a release (ticket 82).

import { cpSync, existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { join, resolve } from 'node:path'
import { gunzipSync } from 'node:zlib'
import type { Plugin } from 'vite'

import type { Evaluation } from './src/data/evaluation.ts'
import { homeData } from './src/data/home.ts'
import type { RunNotes } from './src/run-view/model.ts'

const FIXTURES = join(import.meta.dirname, '../tests/fixtures/web/real')

type File = { body: Buffer; type: string }
const json = (value: unknown): File => ({ body: Buffer.from(JSON.stringify(value)), type: 'application/json' })
const RUN_FILE = /^runs\/([^/]+)\/(meta\.json|events\.jsonl)$/

/** An export directory: the files the site reads (a run's two files, `eval.json`) and `home.json`
 * made from them; null for anything else. */
export function exportFiles(dir: string): (path: string) => File | null {
  const readJson = (name: string) => JSON.parse(readFileSync(join(dir, name), 'utf8')) as unknown
  return (path) => {
    if (path === 'home.json') return json(exportHome(dir))
    if (path === 'eval.json') return existsSync(join(dir, path)) ? json(readJson(path)) : null
    const match = RUN_FILE.exec(path)
    if (!match) return null
    const at = join(dir, 'runs', decodeURIComponent(match[1]), match[2])
    // The id comes from the address: keep it inside `runs/`.
    if (!at.startsWith(join(dir, 'runs') + '/') || !existsSync(at)) return null
    return { body: readFileSync(at), type: match[2] === 'meta.json' ? 'application/json' : 'application/x-ndjson' }
  }
}

function exportHome(dir: string) {
  const index = JSON.parse(readFileSync(join(dir, 'index.json'), 'utf8')) as { runs: RunNotes[] }
  const evalPath = join(dir, 'eval.json')
  const evaluation = existsSync(evalPath) ? (JSON.parse(readFileSync(evalPath, 'utf8')) as Evaluation) : null
  return homeData(index, evaluation)
}

function fixtureIds(): string[] {
  return readdirSync(FIXTURES, { withFileTypes: true })
    .filter((d) => d.isDirectory() && existsSync(join(FIXTURES, d.name, 'meta.json')))
    .map((d) => d.name)
    .sort()
}

/** The fixture runs; a gzipped event file is served unzipped. */
function fixtureFile(path: string): File | null {
  if (path === 'home.json') {
    const runs = fixtureIds().map((id) => JSON.parse(readFileSync(join(FIXTURES, id, 'meta.json'), 'utf8')) as RunNotes)
    return json(homeData({ runs }, null))
  }
  const match = RUN_FILE.exec(path)
  if (!match) return null
  const id = decodeURIComponent(match[1])
  if (!fixtureIds().includes(id)) return null
  const at = join(FIXTURES, id, match[2])
  if (match[2] === 'meta.json') return { body: readFileSync(at), type: 'application/json' }
  const body = existsSync(at) ? readFileSync(at) : gunzipSync(readFileSync(`${at}.gz`))
  return { body, type: 'application/x-ndjson' }
}

function exportDir(): string | null {
  const given = process.env.SITE_DATA
  if (!given) return null
  const dir = resolve(given)
  if (!existsSync(join(dir, 'index.json'))) throw new Error(`SITE_DATA=${given}: no index.json there; give an \`evals export\` directory`)
  return dir
}

export function siteData(): Plugin {
  let prefix = '/data/'
  let outDir = 'dist'
  const dir = exportDir()
  const file = dir ? exportFiles(dir) : fixtureFile
  const serve = (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const path = (req.url ?? '').split('?')[0]
    if (!path.startsWith(prefix)) return next()
    const found = file(path.slice(prefix.length))
    // Missing data is a 404, not the app's index.html from Vite's single-page fallback.
    res.statusCode = found ? 200 : 404
    res.setHeader('Content-Type', found ? found.type : 'text/plain')
    res.end(found ? found.body : 'Not Found')
  }
  return {
    name: 'site-data',
    configResolved(config) {
      prefix = `${config.base}data/`
      outDir = resolve(config.root, config.build.outDir)
    },
    configureServer(server) {
      server.middlewares.use(serve)
    },
    configurePreviewServer(server) {
      // A build that holds its data serves it as built.
      if (!existsSync(join(outDir, 'data'))) server.middlewares.use(serve)
    },
    closeBundle() {
      if (!dir) return
      const target = join(outDir, 'data')
      // Not index.json: it holds every trial's notes, and the site reads home.json instead.
      cpSync(dir, target, { recursive: true, filter: (src) => src !== join(dir, 'index.json') })
      writeFileSync(join(target, 'home.json'), JSON.stringify(exportHome(dir)))
    },
  }
}
