// The local run form (spec "运行页", 本地版): task, type and data source. Data sources are offered
// by name only; a refused submit shows the backend's reason. The form picks no config: every run
// gets the server's, which the form shows as a summary (product-config ticket 144).

import { useEffect, useRef, useState, type FormEvent } from 'react'

import type { ConfigSummary, LiveSource, RunOptions } from '../data/source'
import { errorText } from '../format'
import type { TaskType } from '../run-view/model'

export function NewRun({ live, onStarted, onClose }: { live: LiveSource; onStarted: (id: string) => void; onClose: () => void }) {
  const [options, setOptions] = useState<RunOptions | null>(null)
  const [task, setTask] = useState('')
  const [taskType, setTaskType] = useState<TaskType>('query')
  const [dataSource, setDataSource] = useState('')
  const [network, setNetwork] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const taskBox = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    let gone = false
    live
      .options()
      .then((o) => {
        if (gone) return
        setOptions(o)
        setDataSource((d) => d || (o.data_sources[0] ?? ''))
      })
      .catch((e: unknown) => !gone && setError(`Could not load the form's options: ${errorText(e)}`))
    taskBox.current?.focus()
    return () => {
      gone = true
    }
  }, [live])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // A server without the network refuses a run asking for it (409): such a server's runs never ask.
  const allowed = options === null ? null : options.config.network
  const asked = askedNetwork(network, allowed)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onStarted(await live.submit({ task, task_type: taskType, data_source: dataSource, network: asked }))
    } catch (err: unknown) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const noSources = options !== null && options.data_sources.length === 0
  return (
    <form className="pane p-3 flex flex-col gap-2.5 w-[560px] max-w-[calc(100vw-24px)] shadow-lg" onSubmit={submit} aria-label="New run">
      <h2 className="text-[13px] font-semibold">New run</h2>
      <label className="field">
        Task
        <textarea ref={taskBox} value={task} onChange={(e) => setTask(e.target.value)} placeholder="A business question, or a dbt task" required />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="field">
          Type
          <select value={taskType} onChange={(e) => setTaskType(e.target.value as TaskType)}>
            {(options?.task_types ?? []).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Data source
          <select value={dataSource} onChange={(e) => setDataSource(e.target.value)} disabled={noSources}>
            {noSources && <option value="">none configured</option>}
            {options?.data_sources.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {options && <ConfigLine config={options.config} />}
      <NetworkBox allowed={allowed} checked={asked} onChange={setNetwork} />
      {noSources && (
        <div className="text-[11.5px] text-ink-3">
          The server's config has no data source: add a <code className="mono">[sources.&lt;name&gt;]</code> table to it and start{' '}
          <code className="mono">data-agent web</code> again.
        </div>
      )}
      {error && (
        <div className="banner banner-fail" role="alert">
          {error}
        </div>
      )}
      <div className="flex items-center gap-2">
        <div className="flex-1" />
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
        <button type="submit" className="btn btn-primary" disabled={busy || options === null}>
          {busy ? 'Starting…' : 'Start run'}
        </button>
      </div>
    </form>
  )
}

/** Whether a run from the form asks for the network: as the box is, never on a server whose switch
 *  is off (`allowed` false), which refuses such a run (409). */
export function askedNetwork(box: boolean, allowed: boolean | null): boolean {
  return box && allowed !== false
}

/** The run's network switch. `allowed` is the server's switch (null until the options load): off,
 *  the box is off and disabled, and says so. */
export function NetworkBox({ allowed, checked, onChange }: { allowed: boolean | null; checked: boolean; onChange: (on: boolean) => void }) {
  const off = allowed === false
  return (
    <label className={`flex items-center gap-2 text-[12px] text-ink-2 select-none${off ? '' : ' cursor-pointer'}`}>
      <input type="checkbox" data-testid="new-run-network" checked={checked && !off} disabled={off} onChange={(e) => onChange(e.target.checked)} />
      <span>Network access</span>
      <span className="text-[11px] text-ink-3">{off ? '(off on this server)' : '(sandbox network, WebSearch & WebFetch)'}</span>
    </label>
  )
}

const LAYERS = ['skill', 'verifiers', 'context'] as const
const TURN_KINDS = ['query', 'dbt', 'large'] as const

/** The server's config every run of the form gets, in one line: its name, the layers on, the model
 *  and the limits it sets. */
export function configLine(config: ConfigSummary): string {
  const on = LAYERS.filter((layer) => config.layers[layer])
  const layers = on.length === LAYERS.length ? 'all layers on' : on.length === 0 ? 'no layers' : `layers: ${on.join(', ')}`
  const parts = [`config ${config.name}`, layers]
  if (config.model) parts.push(config.model)
  if (config.effort) parts.push(`effort ${config.effort}`)
  if (config.max_budget_usd !== null) parts.push(`spend cap ${config.max_budget_usd} USD`)
  const turns = TURN_KINDS.flatMap((kind) => (config.max_turns[kind] === null ? [] : [`${kind} ${config.max_turns[kind]}`]))
  if (turns.length > 0) parts.push(`max turns ${turns.join(', ')}`)
  if (config.agent_timeout_sec !== null) parts.push(`time limit ${config.agent_timeout_sec} s`)
  return parts.join(' · ')
}

function ConfigLine({ config }: { config: ConfigSummary }) {
  return (
    <div className="text-[11.5px] text-ink-3" data-testid="new-run-config">
      {configLine(config)}
    </div>
  )
}
