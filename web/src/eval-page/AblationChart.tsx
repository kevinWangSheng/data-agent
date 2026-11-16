// Each config's dev-set pass rate, a bar with a ±1 standard error bar (spec "评测页"). One series,
// so one colour and no legend; the value sits past the error bar; hover or focus shows the counts.
// The table under the chart holds the same numbers.

import { Bar, BarChart, CartesianGrid, ErrorBar, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import type { ConfigRow } from '../data/evaluation'
import { fmtPct } from './scores'

type Datum = ConfigRow & { label: string; err: number }

function ValueLabel(props: { x?: number | string; y?: number | string; width?: number | string; height?: number | string; index?: number; data: Datum[] }) {
  const { data, index = 0 } = props
  const [x, y, width, height] = [props.x, props.y, props.width, props.height].map(Number)
  const d = data[index]
  if (!d || [x, y, width, height].some(Number.isNaN)) return null
  // The bar grows from 0, so the error bar's half-length is the same share of its width.
  const errPx = d.accuracy > 0 ? (width * d.err) / d.accuracy : 0
  return (
    <text x={x + width + errPx + 8} y={y + height / 2} dominantBaseline="central" className="mono" fontSize={11.5} fill="var(--ink)">
      {fmtPct(d.accuracy)}
      <tspan fill="var(--ink-3)"> ± {d.err.toFixed(1)}</tspan>
    </text>
  )
}

function Tip({ active, payload }: { active?: boolean; payload?: readonly { payload?: Datum }[] }) {
  const d = payload?.[0]?.payload
  if (!active || !d) return null
  return (
    <div className="pane px-2.5 py-1.5 shadow-md text-[11.5px]">
      <div className="mono font-semibold text-[13px]">
        {fmtPct(d.accuracy)} <span className="font-normal text-ink-2">± {d.err.toFixed(1)}</span>
      </div>
      <div className="text-ink-2">{d.label}</div>
      <div className="mono text-ink-3">
        {d.n_succeeded} of {d.n_trials} trials passed · {d.n_tasks} tasks
      </div>
    </div>
  )
}

export function AblationChart({ configs, reference }: { configs: ConfigRow[]; reference: string }) {
  const data: Datum[] = configs.map((c) => ({ ...c, label: c.config === reference ? `${c.config} (reference)` : c.config, err: c.accuracy_stderr }))
  return (
    <div className="w-full" style={{ height: 44 * data.length + 36 }} role="img" aria-label="Pass rate by config on the dev set, with one standard error">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 96, bottom: 4, left: 4 }} accessibilityLayer>
          <CartesianGrid horizontal={false} stroke="var(--line)" strokeWidth={1} />
          <XAxis type="number" domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v: number) => `${v}%`} stroke="var(--line-2)" tick={{ fill: 'var(--ink-3)', fontSize: 11 }} tickLine={false} />
          <YAxis type="category" dataKey="label" width={130} stroke="var(--line-2)" tick={{ fill: 'var(--ink-2)', fontSize: 12 }} tickLine={false} />
          <Tooltip cursor={{ fill: 'var(--sel)' }} content={<Tip />} isAnimationActive={false} />
          <Bar dataKey="accuracy" fill="var(--chart-bar)" barSize={20} radius={[0, 4, 4, 0]} isAnimationActive={false}>
            <ErrorBar dataKey="err" direction="x" width={6} stroke="var(--ink-2)" strokeWidth={1.5} />
            <LabelList dataKey="accuracy" content={<ValueLabel data={data} />} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
