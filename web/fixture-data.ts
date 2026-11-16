// Development data: serves the test fixture runs (tests/fixtures/web/real/) at `<base>data/` in the
// layout the static data source reads (src/data/source.ts), for `pnpm dev` and `pnpm preview`. The
// index is made from each run's meta.json; a gzipped event file is served unzipped. The published
// site gets its data from the export command instead (tickets 80, 82).

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { join } from 'node:path'
import { gunzipSync } from 'node:zlib'
import type { Plugin } from 'vite'

const RUNS = join(import.meta.dirname, '../tests/fixtures/web/real')

function runIds(): string[] {
  return readdirSync(RUNS, { withFileTypes: true })
    .filter((d) => d.isDirectory() && existsSync(join(RUNS, d.name, 'meta.json')))
    .map((d) => d.name)
    .sort()
}

function file(path: string): { body: Buffer; type: string } | null {
  if (path === 'index.json') {
    const runs = runIds().map((id) => JSON.parse(readFileSync(join(RUNS, id, 'meta.json'), 'utf8')) as unknown)
    return { body: Buffer.from(JSON.stringify({ runs })), type: 'application/json' }
  }
  const match = /^runs\/([^/]+)\/(meta\.json|events\.jsonl)$/.exec(path)
  if (!match) return null
  const id = decodeURIComponent(match[1])
  if (!runIds().includes(id)) return null
  const at = join(RUNS, id, match[2])
  if (match[2] === 'meta.json') return { body: readFileSync(at), type: 'application/json' }
  const body = existsSync(at) ? readFileSync(at) : gunzipSync(readFileSync(`${at}.gz`))
  return { body, type: 'application/x-ndjson' }
}

export function fixtureData(): Plugin {
  let prefix = '/data/'
  const serve = (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const path = (req.url ?? '').split('?')[0]
    const found = path.startsWith(prefix) ? file(path.slice(prefix.length)) : null
    if (!found) return next()
    res.setHeader('Content-Type', found.type)
    res.end(found.body)
  }
  return {
    name: 'fixture-data',
    configResolved(config) {
      prefix = `${config.base}data/`
    },
    configureServer(server) {
      server.middlewares.use(serve)
    },
    configurePreviewServer(server) {
      server.middlewares.use(serve)
    },
  }
}
