// "New run" on the online site: shown, and marked unavailable, with how to run tasks locally
// (spec user story 24: a demo, not a broken button).

import { Lock } from 'lucide-react'
import type { CSSProperties } from 'react'

// Inline: `.code` is unlayered CSS, so it would win over Tailwind's utility classes.
const WRAP: CSSProperties = { fontSize: 11, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }

export function NewRunUnavailable() {
  return (
    <details className="relative">
      <summary className="btn list-none opacity-70" title="Not available in the online demo" aria-label="New run: not available in the online demo">
        <Lock className="h-3 w-3" /> New run
        <span className="chip chip-na">local only</span>
      </summary>
      <div className="absolute right-0 top-full mt-1 z-20 w-[min(360px,calc(100vw-24px))] pane p-3 shadow-lg text-[12px] leading-relaxed">
        <p className="mb-2">
          This online version only replays recorded runs; it cannot start one. To run your own tasks, start the web UI on your machine:
        </p>
        <pre className="code" style={WRAP}>
          {'git clone https://github.com/kevinWangSheng/data-agent\ncd data-agent && uv sync\nuv run data-agent web --runs-dir runs/web \\\n  --data-sources sources.toml'}
        </pre>
        <p className="mt-2 text-ink-3">It listens on this machine only. The TOML file names the databases the run form offers, by name:</p>
        <pre className="code" style={{ ...WRAP, marginTop: 4 }}>{'[sources.shop]\nkind = "duckdb"\npath = "shop.duckdb"'}</pre>
      </div>
    </details>
  )
}
