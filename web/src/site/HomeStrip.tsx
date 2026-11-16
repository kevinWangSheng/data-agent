// The first screen's summary strip: what is playing, the two leaderboard scores, and the way to
// the evaluation page. Plain facts, no pitch (ticket 81: not a promotional landing page).

import type { Home } from '../data/home'
import { dabTotals, fmtPass1, fmtPct } from '../eval-page/scores'
import { Link } from './Link'

function Score({ name, value, detail }: { name: string; value: string; detail: string }) {
  return (
    <div className="flex items-baseline gap-1.5 flex-wrap min-w-0">
      <span className="text-ink-2">{name}</span>
      <span className="mono font-semibold text-[14px]">{value}</span>
      <span className="mono text-[11px] text-ink-3">{detail}</span>
    </div>
  )
}

export function HomeStrip({ home, evalHref, onGo }: { home: Home; evalHref: string; onGo: (href: string) => void }) {
  const board = home.data_eng_bench
  const dab = home.dab?.candidate
  const lb = board?.leaderboard
  return (
    <section className="flex items-center gap-x-6 gap-y-1.5 px-4 py-2 border-b border-line bg-panel-2 shrink-0 flex-wrap text-[12.5px]" aria-label="Benchmark scores">
      <span className="text-ink-3">An open-source data agent, replaying one recorded run.</span>
      {lb?.accuracy !== undefined ? (
        <Score
          name="data-eng-bench"
          value={`${fmtPct(lb.accuracy)}${lb.accuracy_stderr !== undefined ? ` ± ${lb.accuracy_stderr.toFixed(1)}` : ''}`}
          detail={`${lb.n_tasks ?? '?'} tasks × ${board?.trials_per_task ?? '?'}`}
        />
      ) : null}
      {dab ? (
        <Score name="DataAgentBench" value={`${fmtPass1(dab.pass_at_1)} Pass@1`} detail={`${dabTotals(dab).queries} queries, ${dabTotals(dab).runs} runs`} />
      ) : null}
      {!lb && !dab && <span className="text-ink-3">No benchmark scores in this data.</span>}
      <Link href={evalHref} onGo={onGo} className="link text-[12.5px] whitespace-nowrap">
        Evaluation: scores, ablation, every dev trial →
      </Link>
    </section>
  )
}
