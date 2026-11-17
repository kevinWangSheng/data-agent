// The history list (spec "历史列表"): one row a run, newest start first; a row opens the run page.
// Locally it lists every run; online, the featured runs (the dev trials open from the evaluation
// page).

import { useEffect, useState } from 'react'

import type { DataSource, RunRow } from '../data/source'
import { errorText, fmtCost, fmtDur, fmtStart } from '../format'
import { StatusChip } from '../run-page/ui'

const dash = <span className="text-ink-3">—</span>

export function HistoryPage({
  source,
  empty,
  runUrl,
  onOpen,
}: {
  source: Pick<DataSource, 'history'>
  /** What to say when there is no run. */
  empty: string
  runUrl: (id: string) => string
  onOpen: (id: string) => void
}) {
  const [rows, setRows] = useState<RunRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let gone = false
    source
      .history()
      .then((r) => !gone && setRows(r))
      .catch((e: unknown) => !gone && setError(errorText(e)))
    return () => {
      gone = true
    }
  }, [source])

  return (
    <main className="flex-1 min-h-0 overflow-auto p-4">
      <div className="pane max-w-[1280px] mx-auto">
        <div className="sec flex items-center gap-3">
          <h1 className="text-[14px] font-semibold">Runs</h1>
          {rows !== null && <span className="mono text-[11px] text-ink-3">{rows.length}</span>}
        </div>
        {error && <div className="banner banner-fail m-3">Could not list the runs: {error}</div>}
        {rows === null && !error && <div className="sec text-ink-3">Loading runs…</div>}
        {rows !== null && rows.length === 0 && <div className="sec text-ink-3">{empty}</div>}
        {rows !== null && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="runs-tbl" aria-label="Runs">
              <thead>
                <tr>
                  <th className="w-[40%]">Task</th>
                  <th className="w-[7%]">Type</th>
                  <th className="w-[11%]">Config</th>
                  <th className="w-[10%]">Status</th>
                  <th className="w-[9%] num">Duration</th>
                  <th className="w-[8%] num">Cost</th>
                  <th className="w-[15%]">Started</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} onClick={() => onOpen(row.id)}>
                    <td>
                      <a
                        href={runUrl(row.id)}
                        className="block truncate hover:underline"
                        title={row.task ?? row.id}
                        onClick={(e) => {
                          if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return e.stopPropagation()
                          e.preventDefault()
                        }}
                      >
                        {row.task ?? <span className="text-ink-3">{row.id}</span>}
                      </a>
                    </td>
                    <td className="mono text-[11.5px]">{row.task_type ?? dash}</td>
                    <td className="mono text-[11.5px] truncate">{row.config ?? dash}</td>
                    <td>
                      <StatusChip status={row.status} />
                    </td>
                    <td className="num">{row.duration_ms === null ? dash : fmtDur(row.duration_ms)}</td>
                    <td className="num">{row.cost_usd === null ? dash : fmtCost(row.cost_usd)}</td>
                    <td className="mono text-[11.5px] whitespace-nowrap truncate" title={row.started_at ?? undefined}>
                      {fmtStart(row.started_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </main>
  )
}
