// The evaluation page (spec "评测页"): the two leaderboard totals, the holdout and the whole
// benchmark as totals only, the dev-set ablation, every dev trial with a link to its replay, and
// DAB by dataset. It reads `eval.json` when opened, never on the first screen.

import { useEffect, useMemo, useState } from 'react'

import type { Board, Contribution, Dab, DevSummary, Evaluation } from '../data/evaluation'
import type { SiteSource } from '../data/source'
import { fmtCost, fmtDur } from '../format'
import { Link } from '../site/Link'
import { AblationChart } from './AblationChart'
import { dabTotals, fmtDabComparison, fmtPass1, fmtPct, fmtPoints } from './scores'
import { filterTrials, OUTCOMES, sortTrials, type Outcome, type TrialFilter } from './trials'

/** Where DAB's trajectories are (README, "Results"); they are not on this site. */
const DAB_RELEASE = 'https://github.com/kevinWangSheng/data-agent/releases/tag/dab-submission-2026-11-15'
const ADR_0004 = 'https://github.com/kevinWangSheng/data-agent/blob/main/docs/adr/0004-ablation-holdout.md'

/** Columns a phone-width screen drops, so the totals fit without scrolling sideways. */
const NARROW_HIDDEN = 'max-[520px]:hidden'

const OUTCOME_LABELS: Record<Outcome, string> = { all: 'All', passed: 'Passed', failed: 'Failed' }

function Section({ id, title, note, children }: { id: string; title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="pane p-4" aria-labelledby={id}>
      <h2 id={id} className="text-[15px] font-semibold">
        {title}
      </h2>
      {note && <p className="text-ink-2 text-[12.5px] mt-0.5 mb-3 max-w-[78ch]">{note}</p>}
      {!note && <div className="h-3" />}
      {children}
    </section>
  )
}

function Tile({ label, value, unit, lines }: { label: string; value: string; unit: string; lines: string[] }) {
  return (
    <div className="pane p-4 min-w-0">
      <div className="text-ink-2 text-[12.5px]">{label}</div>
      <div className="flex items-baseline gap-2 mt-1 flex-wrap">
        <span className="font-semibold text-[28px] leading-none tabular-nums">{value}</span>
        <span className="mono text-[12px] text-ink-2">{unit}</span>
      </div>
      <ul className="mt-2 space-y-0.5 text-[12px] text-ink-2">
        {lines.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  )
}

function Significance({ significant }: { significant: boolean }) {
  return significant ? <span className="chip chip-ok">significant</span> : <span className="chip chip-na">not significant</span>
}

function Leaderboards({ board, dab }: { board: Board | null; dab: Dab | null }) {
  const lb = board?.leaderboard
  const candidate = dab?.candidate
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {lb?.accuracy !== undefined ? (
        <Tile
          label="data-eng-bench, every task"
          value={fmtPct(lb.accuracy)}
          unit={lb.accuracy_stderr !== undefined ? `± ${lb.accuracy_stderr.toFixed(1)} (1 SE) accuracy` : 'accuracy'}
          lines={[
            `${lb.n_tasks ?? '?'} tasks × ${board?.trials_per_task ?? '?'} trials = ${lb.n_trials ?? '?'}; ${lb.n_succeeded ?? '?'} passed${board?.complete === false ? ' (incomplete run)' : ''}`,
            ...(lb.pass_at_3 !== undefined ? [`pass@3 ${lb.pass_at_3.toFixed(3)}`] : []),
            [board?.config && `config ${board.config}`, board?.model && `model ${board.model}`].filter(Boolean).join(' · '),
          ].filter(Boolean)}
        />
      ) : (
        <div className="pane p-4 text-ink-3">No data-eng-bench score in this data.</div>
      )}
      {candidate ? (
        <Tile
          label="DataAgentBench"
          value={fmtPass1(candidate.pass_at_1)}
          unit="Pass@1"
          lines={[
            `${dabTotals(candidate).queries} queries, ${dabTotals(candidate).runs} runs`,
            ...(dab?.baseline ? [`baseline ${fmtPass1(dab.baseline.pass_at_1)}`] : []),
            ...(dab?.comparison
              ? [`scored − baseline: ${fmtDabComparison(dab.comparison)}`]
              : []),
          ]}
        />
      ) : (
        <div className="pane p-4 text-ink-3">No DataAgentBench score in this data.</div>
      )}
    </div>
  )
}

function Totals({ board }: { board: Board | null }) {
  const rows: [string, Board['holdout']][] = board
    ? [
        ['Holdout', board.holdout],
        ['Whole benchmark', board.leaderboard],
      ]
    : []
  return (
    <Section
      id="totals"
      title="Holdout and the whole benchmark: totals only"
      note="The holdout's tasks are kept unseen while the agent is changed on the dev set, so its score stays a fair check of those changes; that is why its per-task results are not published, and the whole benchmark, which contains the holdout, is shown as a total too."
    >
      {rows.length > 0 ? (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Set</th>
                <th>Tasks</th>
                <th className={NARROW_HIDDEN}>Trials</th>
                <th className={NARROW_HIDDEN}>Passed</th>
                <th>Accuracy</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([name, t]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="num">{t.n_tasks ?? '—'}</td>
                  <td className={`num ${NARROW_HIDDEN}`}>{t.n_trials ?? '—'}</td>
                  <td className={`num ${NARROW_HIDDEN}`}>{t.n_succeeded ?? '—'}</td>
                  <td className="num">{t.accuracy !== undefined ? fmtPct(t.accuracy) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-ink-3">No data-eng-bench totals in this data.</p>
      )}
      <p className="text-[12px] text-ink-3 mt-2">
        The rule is{' '}
        <a className="link text-[12px]" href={ADR_0004}>
          ADR-0004
        </a>
        .
      </p>
    </Section>
  )
}

function ContributionRow({ c, reference }: { c: Contribution; reference: string }) {
  return (
    <li className="flex items-center gap-2 flex-wrap">
      <span className="font-medium">{c.layer}</span>
      <span className="mono text-[11.5px] text-ink-3">
        {reference} − {c.config}
      </span>
      <span className="mono">{fmtPoints(c.diff)} points</span>
      <span className="mono text-[11.5px] text-ink-2">
        95% CI {fmtPoints(c.ci_low)} to {fmtPoints(c.ci_high)}
      </span>
      <Significance significant={c.significant} />
    </li>
  )
}

function Ablation({ dev }: { dev: DevSummary }) {
  const tasks = dev.configs[0]?.n_tasks
  return (
    <Section
      id="ablation"
      title="Ablation on the dev set"
      note={`Each config's pass rate${tasks ? ` over ${tasks} dev tasks` : ''}, with one standard error. What a layer adds is the reference config minus the config without it, from a paired bootstrap over tasks; a 95% interval that includes zero is marked not significant.`}
    >
      {dev.configs.length > 0 ? <AblationChart configs={dev.configs} reference={dev.reference} /> : <p className="text-ink-3">No dev configs in this data.</p>}
      {dev.contributions.length > 0 && (
        <ul className="mt-3 space-y-1.5 text-[12.5px]" aria-label="What each layer adds">
          {dev.contributions.map((c) => (
            <ContributionRow key={c.config} c={c} reference={dev.reference} />
          ))}
        </ul>
      )}
      {dev.configs.length > 0 && (
        <div className="tbl-wrap mt-3">
          <table className="tbl">
            <thead>
              <tr>
                <th>Config</th>
                <th>Pass rate</th>
                <th>± SE</th>
                <th>pass@3</th>
                <th>Tasks</th>
                <th>Trials</th>
                <th>Passed</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {dev.configs.map((c) => (
                <tr key={c.config}>
                  <td className="mono">
                    {c.config}
                    {c.config === dev.reference && <span className="text-ink-3"> (reference)</span>}
                  </td>
                  <td className="num">{fmtPct(c.accuracy)}</td>
                  <td className="num">{c.accuracy_stderr.toFixed(1)}</td>
                  <td className="num">{c.pass_at_3?.toFixed(3) ?? '—'}</td>
                  <td className="num">{c.n_tasks}</td>
                  <td className="num">{c.n_trials}</td>
                  <td className="num">{c.n_succeeded}</td>
                  <td className="num">{c.cost_usd != null ? fmtCost(c.cost_usd) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}

function Trials({ dev, filter, onFilter, runHref, onGo }: { dev: DevSummary; filter: TrialFilter; onFilter: (f: TrialFilter) => void; runHref: (id: string) => string; onGo: (href: string) => void }) {
  const configs = [...new Set(dev.trials.map((t) => t.config).filter((c): c is string => c !== null))].sort()
  const sorted = useMemo(() => sortTrials(dev.trials), [dev.trials])
  const rows = filterTrials(sorted, filter)
  return (
    <Section id="trials" title="Every dev trial" note="Each row opens the trial's full replay. Filter by config and outcome.">
      <div className="flex items-center gap-3 flex-wrap mb-2">
        <div className="seg" role="group" aria-label="Config">
          {[null, ...configs].map((c) => (
            <button type="button" key={c ?? 'all'} className={filter.config === c ? 'on' : ''} aria-pressed={filter.config === c} onClick={() => onFilter({ ...filter, config: c })}>
              {c ?? 'All configs'}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Outcome">
          {OUTCOMES.map((o) => (
            <button type="button" key={o} className={filter.outcome === o ? 'on' : ''} aria-pressed={filter.outcome === o} onClick={() => onFilter({ ...filter, outcome: o })}>
              {OUTCOME_LABELS[o]}
            </button>
          ))}
        </div>
        <span className="mono text-[11.5px] text-ink-3" aria-live="polite">
          {rows.length} of {dev.trials.length} trials
        </span>
      </div>
      <div className="tbl-wrap max-h-[70vh] overflow-auto">
        <table className="tbl w-full">
          <thead className="sticky top-0">
            <tr>
              <th>Task</th>
              <th>Config</th>
              <th>Trial</th>
              <th>Outcome</th>
              <th>Cost</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id}>
                <td>
                  <Link href={runHref(t.id)} onGo={onGo} className="link text-[12px] mono" title={`Replay ${t.id}`}>
                    {t.task}
                  </Link>
                </td>
                <td className="mono">{t.config ?? '—'}</td>
                <td className="num">#{t.attempt}</td>
                <td>{t.passed ? <span className="chip chip-ok">PASS</span> : <span className="chip chip-fail">FAIL</span>}</td>
                <td className="num">{t.cost_usd != null ? fmtCost(t.cost_usd) : '—'}</td>
                <td className="num">{t.duration_seconds != null ? fmtDur(t.duration_seconds * 1000) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <p className="p-3 text-ink-3">No trial matches this filter.</p>}
      </div>
    </Section>
  )
}

function DabDatasets({ dab }: { dab: Dab }) {
  const outputs = (['baseline', 'candidate'] as const).filter((k) => dab[k])
  const names = [...new Set(outputs.flatMap((k) => Object.keys(dab[k]!.datasets)))].sort((a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' }))
  return (
    <Section
      id="dab"
      title="DataAgentBench by dataset"
      note="Pass@1 per dataset for the scored output and its baseline. Per-query trajectories are not on this site; all of them are in the release."
    >
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Queries</th>
              {outputs.map((k) => (
                <th key={k}>{k === 'candidate' ? 'Scored' : 'Baseline'} Pass@1</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {names.map((n) => (
              <tr key={n}>
                <td className="mono">{n}</td>
                <td className="num">{dab.candidate?.datasets[n]?.queries ?? dab.baseline?.datasets[n]?.queries ?? '—'}</td>
                {outputs.map((k) => {
                  const d = dab[k]!.datasets[n]
                  return (
                    <td key={k} className="num">
                      {d?.pass_at_1 != null ? `${d.pass_at_1.toFixed(2)} (${d.passed}/${d.runs})` : '—'}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[12.5px]">
        <a className="link text-[12.5px]" href={DAB_RELEASE}>
          Trajectories of every DAB run (release, data-agent-dab-traces.zip) →
        </a>
      </p>
    </Section>
  )
}

export function EvalPage({ source, filter, onFilter, runHref, onGo }: { source: SiteSource; filter: TrialFilter; onFilter: (f: TrialFilter) => void; runHref: (id: string) => string; onGo: (href: string) => void }) {
  const [evaluation, setEvaluation] = useState<Evaluation | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    source
      .evaluation()
      .then(setEvaluation)
      .catch((e: unknown) => setError(String(e)))
  }, [source])
  useEffect(() => {
    document.title = 'Evaluation · data-agent'
  }, [])

  if (error) return <div className="banner banner-fail m-3">Could not load the evaluation summary: {error}</div>
  if (evaluation === undefined) return <div className="p-8 text-ink-3">Loading the evaluation summary…</div>
  if (evaluation === null) return <div className="p-8 text-ink-3">This build has no evaluation summary (no eval.json in its data).</div>
  return (
    <main className="flex-1 min-h-0 overflow-auto">
      <div className="max-w-[1200px] mx-auto p-4 space-y-3">
        <div>
          <h1 className="text-[20px] font-semibold">Evaluation</h1>
          <p className="text-ink-2 text-[12.5px] max-w-[78ch]">Local runs, graded by each benchmark's own scripts.</p>
        </div>
        <Leaderboards board={evaluation.data_eng_bench} dab={evaluation.dab} />
        <Totals board={evaluation.data_eng_bench} />
        <Ablation dev={evaluation.dev} />
        <Trials dev={evaluation.dev} filter={filter} onFilter={onFilter} runHref={runHref} onGo={onGo} />
        {evaluation.dab && <DabDatasets dab={evaluation.dab} />}
      </div>
    </main>
  )
}
